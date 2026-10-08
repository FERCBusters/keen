"""Framework-aware home overview, with permission-scoped attention items."""
from datetime import datetime, timezone
import math
import operator
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.core.config import settings
from app.db.session import get_db
from app.db.models import (ControlItem, FrameworkClause, Audit, IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry, IntegrationCollector, EffectiveCrossFrameworkControlLink)
from app.security.auth import require_authenticated
from app.security.roles import attach_effective_role
from app.security.permissions import has_permission
from app.services.control_evidence_stats import get_control_evidence_stats_by_id
from app.services.control_inheritance import evidence_pairs, has_inherited_evidence
from .stats import stats_summary

router = APIRouter()


def outside_threshold(value, target, comparison, unit, target_unit):
    operations = {'lt': operator.lt, 'lte': operator.le, 'eq': operator.eq, 'gte': operator.ge, 'gt': operator.gt}
    if comparison not in operations or value is None or target is None or unit.strip() != target_unit.strip():
        return False
    if not math.isfinite(value) or not math.isfinite(target):
        return False
    return not operations[comparison](value, target)


@router.get('/v1/home')
def home_overview(request: Request, framework: str = settings.default_framework_slug,
                  db: Session = Depends(get_db), user=Depends(require_authenticated)):
    admin = attach_effective_role(db, user) == 'admin'
    def permitted(code):
        return admin or has_permission(db, user, code)
    risk = permitted('risk.read') or permitted('risk.manage')
    isms = risk or permitted('isms.read') or permitted('isms.manage')
    audit = permitted('audits.read') or permitted('audits.manage')
    events = permitted('events.read')
    controls = db.query(ControlItem).filter(ControlItem.framework_slug == framework, ControlItem.type != 'clause').all()
    clauses = db.query(func.count(FrameworkClause.id)).filter(FrameworkClause.framework_slug == framework).scalar()
    result = {'framework': framework, 'control_count': len(controls), 'clause_count': int(clauses or 0),
              'coverage': None, 'events': None, 'attention': [], 'checked_at': datetime.now(timezone.utc).isoformat()}
    if events:
        inherited = has_inherited_evidence(db, framework)
        if inherited:
            pairs = evidence_pairs(framework, db)
            supported = {row[0] for row in db.query(pairs.c.control_id).distinct().all()}
        else:
            stats = get_control_evidence_stats_by_id(db, framework)
            supported = {key for key, value in stats.items() if value.get('evidence_count', 0) > 0}
        scoped = [c for c in controls if c.in_scope]
        covered = sum(c.id in supported for c in scoped)
        result['coverage'] = {'in_scope': len(scoped), 'with_evidence': covered, 'without_evidence': len(scoped)-covered}
        result['events'] = stats_summary(request=request, framework=framework, db=db)['events']
        for c in sorted((c for c in scoped if c.id not in supported), key=lambda c: c.ref)[:5]:
            result['attention'].append({'kind':'gap','label':f'{c.ref} {c.title}', 'detail':'In-scope control without evidence', 'href':f'/control.html?id={c.id}'})
    if audit:
        overdue = db.query(Audit).filter(Audit.framework_slug == framework, Audit.status.in_(['open','in_progress']), Audit.end_date < datetime.now(timezone.utc).date())
        result['overdue_audits'] = overdue.count()
        for row in overdue.order_by(Audit.end_date, Audit.id).limit(5):
            result['attention'].append({'kind':'audit','label':row.title,'detail':f'Audit end date passed · {row.end_date}', 'href':f'/audit.html?id={row.id}'})
    if isms:
        # One latest entry per measure; avoid loading complete metric histories.
        ranked = db.query(IsmsEffectivenessMetricEntry.measure_id.label('measure_id'), IsmsEffectivenessMetricEntry.metric_value.label('value'), IsmsEffectivenessMetricEntry.metric_unit.label('unit'), func.row_number().over(partition_by=IsmsEffectivenessMetricEntry.measure_id, order_by=(IsmsEffectivenessMetricEntry.recorded_at.desc(),IsmsEffectivenessMetricEntry.created_at.desc(),IsmsEffectivenessMetricEntry.id.desc())).label('rank')).join(IsmsEffectivenessMeasure, IsmsEffectivenessMeasure.id == IsmsEffectivenessMetricEntry.measure_id).filter(IsmsEffectivenessMeasure.framework_slug == framework).subquery()
        rows = db.query(IsmsEffectivenessMeasure, ranked.c.value, ranked.c.unit).join(ranked, ranked.c.measure_id == IsmsEffectivenessMeasure.id).filter(ranked.c.rank == 1).order_by(IsmsEffectivenessMeasure.summary).all()
        failures = [(m,v,u) for m,v,u in rows if outside_threshold(v,m.target_value,m.threshold_operator,u or '',m.target_unit or '')]
        result['measures_outside_threshold'] = len(failures)
        for m,v,u in failures[:5]:
            result['attention'].append({'kind':'measure','label':m.summary or m.metric or 'Effectiveness measure','detail':f'Latest value {v:g} {u} is outside its threshold','href':f'/isms-effectiveness-measure.html?id={m.id}'})
    if admin:
        failed = db.query(IntegrationCollector).filter(IntegrationCollector.enabled.is_(True), IntegrationCollector.failures > 0)
        result['collectors_with_failures'] = failed.count()
        for row in failed.order_by(IntegrationCollector.name).limit(5):
            result['attention'].append({'kind':'collection','label':row.name,'detail':f'API collector · {row.failures} consecutive failures · installation-wide','href':'/admin.html#integrations'})
    return result


def control_tree(rows, prefix=''):
    groups = {}
    leaves = []
    for row in rows:
        ref = row.ref or ''
        if prefix and ref != prefix and not ref.startswith(prefix + '.'):
            continue
        remainder = ref[len(prefix)+1:] if prefix and ref != prefix else ref if not prefix else ''
        if '.' in remainder:
            child = (prefix + '.' if prefix else '') + remainder.split('.', 1)[0]
            groups[child] = groups.get(child, 0) + 1
        else:
            leaves.append({'id':str(row.id),'ref':ref,'title':row.title,'in_scope':row.in_scope})
    from app.api.utils import ref_sort_key
    return {'groups':[{'id':key,'title':key,'count':groups[key]} for key in sorted(groups,key=ref_sort_key)],
            'items':sorted(leaves,key=lambda row:ref_sort_key(row['ref']))}


@router.get('/v1/home/controls')
def home_controls(framework: str = settings.default_framework_slug, prefix: str = '',
                  db: Session = Depends(get_db), user=Depends(require_authenticated)):
    rows = db.query(ControlItem).filter(ControlItem.framework_slug == framework, ControlItem.type != 'clause').all()
    return control_tree(rows, prefix)
