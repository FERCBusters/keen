"""Framework home tests. Set KEEN_HOME_TEST_DATABASE_URL to an isolated PostgreSQL DB."""
from tests.db_helpers import schema_engine
import os
import unittest
import uuid
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import Request
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.api.routes import home
from app.db.session import Base
from app.db.models import (ControlItem, FrameworkClause, Audit, IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry, IntegrationConnection, IntegrationCollector,
    CrossFrameworkControlLink)

class HomeLogicTests(unittest.TestCase):
    def test_threshold_operators_and_incomparable_values(self):
        for op, value, target, failed in [('lt',5,5,True),('lte',5,5,False),('eq',4,5,True),('gte',4,5,True),('gt',6,5,False)]:
            self.assertEqual(home.outside_threshold(value,target,op,'ms','ms'),failed)
        for value,target,op,u,tu in [(None,5,'lt','ms','ms'),(5,5,'','ms','ms'),(5,5,'lt','s','ms'),(float('nan'),5,'lt','',''),(5,float('inf'),'lt','','')]:
            self.assertFalse(home.outside_threshold(value,target,op,u,tu))

    def test_hierarchy_and_numeric_sort(self):
        rows=[SimpleNamespace(id=r,ref=r,title=r,in_scope=True) for r in ['A.10.1','A.5.2','A.5.1','A.50.1','flat']]
        root=home.control_tree(rows)
        self.assertEqual(root['groups'],[{'id':'A','title':'A','count':4}])
        self.assertEqual([r['ref'] for r in root['items']],['flat'])
        self.assertEqual([r['id'] for r in home.control_tree(rows,'A')['groups']],['A.5','A.10','A.50'])
        self.assertEqual([r['ref'] for r in home.control_tree(rows,'A.5')['items']],['A.5.1','A.5.2'])
        self.assertEqual(home.control_tree(rows,'missing'),{'groups':[],'items':[]})

@unittest.skipUnless(os.environ.get('KEEN_HOME_TEST_DATABASE_URL'),'Set KEEN_HOME_TEST_DATABASE_URL')
class HomeDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url=os.environ['KEEN_HOME_TEST_DATABASE_URL']; cls.schema='home_test_'+uuid.uuid4().hex
        engine=create_engine(url)
        with engine.begin() as c:c.execute(text(f'CREATE SCHEMA {cls.schema}'))
        engine.dispose()
        cls.engine=schema_engine(url, cls.schema)
        names={m.__tablename__ for m in (ControlItem,FrameworkClause,Audit,IsmsEffectivenessMeasure,IsmsEffectivenessMetricEntry,IntegrationConnection,IntegrationCollector,CrossFrameworkControlLink)}
        while True:
            more={fk.column.table.name for n in names for fk in Base.metadata.tables[n].foreign_keys}
            if more<=names:break
            names|=more
        Base.metadata.create_all(cls.engine,tables=[t for t in Base.metadata.sorted_tables if t.name in names])
        cls.Session=sessionmaker(bind=cls.engine)

    @classmethod
    def tearDownClass(cls):
        with cls.engine.begin() as c:c.execute(text(f'DROP SCHEMA {cls.schema} CASCADE'))
        cls.engine.dispose()

    def setUp(self):
        self.db=self.Session();self.addCleanup(self.db.close)
        self.fw='TEST:'+uuid.uuid4().hex
        self.request=Request({'type':'http','headers':[]})
        self.user=SimpleNamespace()
        self.stats=patch.object(home,'stats_summary',return_value={'events':{'total':3,'mapped':1,'unmapped':2}}).start()
        self.coverage=patch.object(home,'get_control_evidence_stats_by_id',return_value={}).start()
        self.role=patch.object(home,'attach_effective_role',return_value='admin').start()
        self.permission=patch.object(home,'has_permission',return_value=False).start()
        self.addCleanup(patch.stopall)

    def overview(self):return home.home_overview(self.request,self.fw,self.db,self.user)

    def test_framework_scope_clauses_and_coverage(self):
        a=ControlItem(framework_slug=self.fw,type='custom',ref='A.1',title='One')
        b=ControlItem(framework_slug=self.fw,type='custom',ref='A.2',title='Two')
        self.db.add_all([a,b,ControlItem(framework_slug=self.fw,type='clause',ref='4',title='Legacy clause'),ControlItem(framework_slug=self.fw,type='custom',ref='B.1',in_scope=False),ControlItem(framework_slug='OTHER',type='custom',ref=str(uuid.uuid4())),FrameworkClause(framework_slug=self.fw,ref='4',title='Context')]);self.db.flush()
        self.coverage.return_value={a.id:{'evidence_count':1}}
        result=self.overview()
        self.assertEqual((result['control_count'],result['clause_count']),(3,1))
        self.assertEqual(result['coverage'],{'in_scope':2,'with_evidence':1,'without_evidence':1})
        self.assertEqual([x['label'] for x in result['attention'] if x['kind']=='gap'],['A.2 Two'])
        tree=home.home_controls(self.fw,'',self.db,self.user)
        self.assertEqual([x['id'] for x in tree['groups']],['A','B'])
        self.assertFalse(tree['items'])

    def test_no_permissions_exposes_structure_only(self):
        self.role.return_value='normal'
        self.db.add(FrameworkClause(framework_slug=self.fw,ref='4',title='Context'));self.db.flush()
        result=self.overview()
        self.assertEqual((result['control_count'],result['clause_count']),(0,1))
        self.assertIsNone(result['coverage']);self.assertIsNone(result['events'])
        self.assertEqual(result['attention'],[])
        self.assertNotIn('overdue_audits',result);self.assertNotIn('collectors_with_failures',result)
        self.stats.assert_not_called();self.coverage.assert_not_called()

    def test_attention_uses_latest_value_and_open_overdue_audits(self):
        now=datetime.now(timezone.utc).replace(tzinfo=None)
        for fw,status,end in [(self.fw,'open',-1),(self.fw,'completed',-1),(self.fw,'template',-1),(self.fw,'in_progress',1),('OTHER','open',-1)]:
            self.db.add(Audit(framework_slug=fw,title='Audit',status=status,end_date=(now+timedelta(days=end)).date()))
        for i,(fw,values,unit) in enumerate([(self.fw,[2,9],'ms'),(self.fw,[9,2],'ms'),(self.fw,[9],'s'),('OTHER',[9],'ms')]):
            m=IsmsEffectivenessMeasure(framework_slug=fw,summary=f'Measure {i}',threshold_operator='lte',target_value=5,target_unit='ms');self.db.add(m);self.db.flush()
            for j,value in enumerate(values):self.db.add(IsmsEffectivenessMetricEntry(measure_id=m.id,metric_value=value,metric_unit=unit,recorded_at=now+timedelta(minutes=j)))
        c=IntegrationConnection(id=uuid.uuid4().hex,name='Test',base_url='https://example.com');self.db.add(c);self.db.flush()
        for enabled in [True,False]:self.db.add(IntegrationCollector(id=uuid.uuid4().hex,connection_id=c.id,name='Collector',draft={},enabled=enabled,failures=2))
        self.db.flush();result=self.overview()
        self.assertEqual(result['overdue_audits'],1)
        self.assertEqual(result['measures_outside_threshold'],1)
        self.assertEqual([x['label'] for x in result['attention'] if x['kind']=='measure'],['Measure 0'])
        self.assertEqual(result['collectors_with_failures'],1)
        self.assertIn('installation-wide',next(x['detail'] for x in result['attention'] if x['kind']=='collection'))
