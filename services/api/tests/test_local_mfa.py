"""Local MFA security regression tests. Use an isolated PostgreSQL test database.

KEEN_MFA_TEST_DATABASE_URL=postgresql+psycopg2://... python -m unittest tests.test_local_mfa
Creates and drops a random schema; never uses production tables.
"""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import time
import unittest
import uuid
from datetime import timedelta
from unittest.mock import patch

import cbor2
import fakeredis
import pyotp
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes
from fastapi import FastAPI, Request, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.config import settings
from app.db.session import Base, get_db
from app.db.models import User, MfaChallenge, MfaCredential, MfaRecoveryCode
from app.security import mfa, auth as identity
from app.security.passwords import hash_password
from app.security.sessions import create_session
from app.api.routes import auth as login_routes, mfa as routes

URL=os.environ.get('KEEN_MFA_TEST_DATABASE_URL')

@unittest.skipUnless(URL,'Set KEEN_MFA_TEST_DATABASE_URL to an isolated PostgreSQL test database')
class LocalMfaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine=create_engine(URL,connect_args={'options':'-c timezone=UTC'})
        cls.schema='test_mfa_'+uuid.uuid4().hex
        with cls.engine.begin() as conn:conn.execute(text(f'CREATE SCHEMA {cls.schema}'))
        cls.engine.dispose()
        cls.engine=create_engine(URL,connect_args={'options':f'-c search_path={cls.schema} -c timezone=UTC'})
        cls.Session=sessionmaker(bind=cls.engine)
        names={'users','groups','permissions','user_groups','user_permissions','group_permissions','audit_logs'}
        Base.metadata.create_all(cls.engine,tables=[t for t in Base.metadata.sorted_tables if t.name in names])
        # Exercise the shipped migration against the pre-MFA user shape.
        with cls.engine.begin() as conn:
            conn.execute(text('ALTER TABLE users DROP COLUMN mfa_enabled,DROP COLUMN mfa_version,DROP COLUMN mfa_totp_secret,DROP COLUMN mfa_totp_last_step'))
            spec=importlib.util.spec_from_file_location('mfa_migration',Path(__file__).parents[1]/'alembic/versions/0084_local_mfa.py')
            migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
            with patch.object(migration,'op',Operations(MigrationContext.configure(conn))):migration.upgrade()
            spec=importlib.util.spec_from_file_location('notification_migration',Path(__file__).parents[1]/'alembic/versions/0085_security_notifications.py')
            migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
            with patch.object(migration,'op',Operations(MigrationContext.configure(conn))):migration.upgrade()
        cls.app=FastAPI();cls.app.include_router(login_routes.router);cls.app.include_router(routes.router)
        def database():
            with cls.Session() as db:yield db
        cls.app.dependency_overrides[get_db]=database
        @cls.app.get('/protected')
        def protected(request:Request,db=Depends(get_db)):
            user=identity.get_current_user_from_request(request,db)
            return {'authenticated':user is not None}

    @classmethod
    def tearDownClass(cls):
        with cls.engine.begin() as conn:conn.execute(text(f'DROP SCHEMA {cls.schema} CASCADE'))
        cls.engine.dispose()

    def setUp(self):
        self.redis=fakeredis.FakeRedis(decode_responses=True)
        self.patches=[patch.object(mfa,'get_valkey',return_value=self.redis),patch.object(identity,'get_valkey',return_value=self.redis),patch.object(login_routes,'get_valkey',return_value=self.redis),patch.object(settings,'mfa_policy','optional'),patch.object(settings,'mfa_origin','https://keen.example'),patch.object(settings,'mfa_encryption_key',Fernet.generate_key().decode()),patch.object(settings,'local_auth_enabled',True),patch.object(settings,'trust_remote_user',False)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.client=TestClient(self.app,base_url='https://keen.example',headers={'Origin':'https://keen.example'})
        self.username='u'+uuid.uuid4().hex;self.password='a-long-local-password-123'
        with self.Session() as db:
            user=User(username=self.username,password_hash=hash_password(self.password),role='normal');db.add(user);db.commit();self.uid=user.id

    def post(self,path,data=None):return self.client.post('/v1/auth/mfa/'+path,json=data or {})
    def login(self):return self.client.post('/v1/auth/login',json={'username':self.username,'password':self.password})
    def start(self,purpose='manage'):
        with self.Session() as db:
            user=db.get(User,self.uid)
            request=Request({'type':'http','method':'POST','path':'/','headers':[(b'origin',b'https://keen.example')],'client':('127.0.0.1',123)})
            response=mfa.start(db,user,request,purpose)
            from http.cookies import SimpleCookie
            cookie=SimpleCookie();cookie.load(response.headers['set-cookie']);self.client.cookies.set(mfa.COOKIE,cookie[mfa.COOKIE].value,domain='keen.example',path='/')
    def enrol(self):
        self.start();response=self.post('totp/start');self.assertEqual(response.status_code,200,response.text)
        self.secret=response.json()['secret'];result=self.post('totp/confirm',{'code':pyotp.TOTP(self.secret).now()});self.assertEqual(result.status_code,200,result.text)
        self.codes=result.json()['recovery_codes'];self.redis.flushall()
    def allow_next_totp(self):
        with self.Session() as db:db.get(User,self.uid).mfa_totp_last_step=-1;db.commit()

    def test_totp_enrolment_encrypted_and_recovery_hashed(self):
        self.enrol()
        with self.Session() as db:
            user=db.get(User,self.uid);self.assertTrue(user.mfa_enabled);self.assertNotIn(self.secret,user.mfa_totp_secret);self.assertEqual(mfa.decrypt(user.mfa_totp_secret),self.secret)
            hashes_=[x.code_hash for x in db.query(MfaRecoveryCode).filter_by(user_id=self.uid)];self.assertEqual(len(hashes_),10);self.assertNotIn(self.codes[0],hashes_)
        self.assertEqual(self.post('finish').status_code,200)
        self.assertTrue(self.client.get('/protected').json()['authenticated'])
        self.assertEqual(self.post('finish').status_code,401)

    def test_invalid_enrolment_does_not_enable(self):
        self.start();self.post('totp/start');self.assertEqual(self.post('totp/confirm',{'code':'invalid'}).status_code,400)
        with self.Session() as db:self.assertFalse(db.get(User,self.uid).mfa_enabled);self.assertIsNone(db.get(User,self.uid).mfa_totp_secret)

    def test_pending_cannot_access_and_totp_cannot_replay(self):
        self.enrol();self.client.cookies.clear()
        self.assertTrue(self.login().json()['mfa_required'])
        self.assertFalse(self.client.get('/protected').json()['authenticated'])
        self.assertEqual(self.post('finish').status_code,403)
        self.assertEqual(self.post('verify-code',{'code':pyotp.TOTP(self.secret).now()}).status_code,400)
        self.allow_next_totp();self.assertEqual(self.post('verify-code',{'code':pyotp.TOTP(self.secret).now()}).status_code,200)
        self.assertEqual(self.post('finish').status_code,200)
        self.assertTrue(self.client.get('/protected').json()['authenticated'])

    def test_recovery_is_single_use(self):
        self.enrol();self.start('login');self.assertEqual(self.post('verify-code',{'kind':'recovery','code':self.codes[0]}).status_code,200)
        self.start('login');self.assertEqual(self.post('verify-code',{'kind':'recovery','code':self.codes[0]}).status_code,400)
        self.assertEqual(self.post('verify-code',{'kind':'recovery','code':self.codes[1]}).status_code,200)

    def test_origin_expiry_and_password_change(self):
        self.start();self.assertEqual(self.client.post('/v1/auth/mfa/state',headers={'Origin':'https://evil.example'},json={}).status_code,403)
        with self.Session() as db:
            row=db.query(MfaChallenge).filter_by(user_id=self.uid).one();row.expires_at=mfa.now()-timedelta(seconds=1);db.commit()
        self.assertEqual(self.post('state').status_code,401)
        self.start()
        with self.Session() as db:db.get(User,self.uid).password_hash=hash_password('another-password-123');db.commit()
        self.assertEqual(self.post('finish').status_code,401)

    def test_policy_blocks_old_local_sessions_but_not_sso(self):
        with self.Session() as db:
            user=db.get(User,self.uid)
            self.assertTrue(mfa.local_session_allowed(db,user,{'auth_method':'local'}))
            with patch.object(settings,'mfa_policy','all'):
                self.assertFalse(mfa.local_session_allowed(db,user,{'auth_method':'local'}));self.assertTrue(mfa.local_session_allowed(db,user,{'auth_method':'oidc'}))
            with patch.object(settings,'mfa_policy','admins'):
                self.assertFalse(mfa.required(db,user));user.role='admin';self.assertTrue(mfa.required(db,user))
        with patch.object(settings,'mfa_policy','all'):
            self.assertTrue(self.login().json()['mfa_required']);self.assertEqual(self.post('state').json()['stage'],'enrol');self.assertEqual(self.post('finish').status_code,403)

    def test_factor_change_revokes_existing_sessions(self):
        sid=create_session(self.redis,user_id=str(self.uid),ttl_seconds=300,auth_method='local',mfa_version=0)
        self.client.cookies.set(settings.session_cookie_name,sid)
        self.assertTrue(self.client.get('/protected').json()['authenticated']);self.enrol()
        self.assertFalse(self.client.get('/protected').json()['authenticated'])

    def test_rate_limit_and_missing_key_fail_closed(self):
        self.start()
        with patch.object(settings,'mfa_encryption_key',''):
            self.assertEqual(self.post('totp/start').status_code,503)
        for _ in range(16):result=self.post('totp/start')
        self.assertEqual(result.status_code,429)
        with patch.object(mfa,'get_valkey',side_effect=RuntimeError('offline')):
            self.assertEqual(self.post('totp/start').status_code,503)

    def test_management_requires_password_and_factor(self):
        self.assertEqual(self.post('manage/start',{'password':self.password}).status_code,401)
        self.assertEqual(self.login().status_code,200)
        self.assertEqual(self.post('manage/start',{'password':'wrong'}).status_code,401)
        self.assertEqual(self.post('manage/start',{'password':self.password}).status_code,200)
        self.enrol();self.post('finish')
        self.assertEqual(self.post('manage/start',{'password':self.password}).status_code,200)
        self.assertEqual(self.post('recovery/regenerate').status_code,403)
        self.assertEqual(self.post('remove',{'id':'totp'}).status_code,403)

    def test_required_factor_cannot_be_removed_and_codes_replace(self):
        self.enrol()
        with patch.object(settings,'mfa_policy','all'):
            self.assertEqual(self.post('remove',{'id':'totp'}).status_code,400)
        result=self.post('recovery/regenerate');self.assertEqual(result.status_code,200)
        self.start('login');self.assertEqual(self.post('verify-code',{'kind':'recovery','code':self.codes[0]}).status_code,400)
        self.assertEqual(self.post('verify-code',{'kind':'recovery','code':result.json()['recovery_codes'][0]}).status_code,200)

    def test_cancel_and_logout_invalidate_pending_challenge(self):
        self.start(); token=self.client.cookies.get(mfa.COOKIE)
        self.assertEqual(self.post('cancel').status_code,200)
        self.client.cookies.set(mfa.COOKIE,token,domain='keen.example',path='/')
        self.assertEqual(self.post('state').status_code,401)
        self.start();token=self.client.cookies.get(mfa.COOKIE)
        self.assertEqual(self.client.post('/v1/auth/logout').status_code,204)
        self.client.cookies.set(mfa.COOKIE,token,domain='keen.example',path='/')
        self.assertEqual(self.post('state').status_code,401)

    def test_full_application_middleware_boundary(self):
        from app import main
        main.app.dependency_overrides[get_db]=self.app.dependency_overrides[get_db]
        self.addCleanup(main.app.dependency_overrides.clear)
        with patch.object(main,'SessionLocal',self.Session), patch.object(settings,'mfa_policy','all'):
            client=TestClient(main.app,base_url='https://keen.example',headers={'Origin':'https://keen.example'})
            result=client.post('/v1/auth/login',json={'username':self.username,'password':self.password})
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(client.get('/v1/auth/check').status_code,401)
            state=client.post('/v1/auth/mfa/state',json={})
            self.assertEqual(state.status_code,200,state.text)
            self.assertEqual(state.headers.get('cache-control'),'no-store')
            self.assertEqual(client.post('/v1/auth/mfa/state',headers={'Origin':'https://evil.example'},json={}).status_code,403)

    def test_security_mail_login_once_per_ip_after_full_mfa(self):
        from app.services import security_notifications as mail
        from app.db.models import SecurityNotification, UserLoginIP
        with self.Session() as db:db.get(User,self.uid).email='alice@example.org';db.commit()
        with patch.object(mail,'smtp_configured',return_value=True), patch.object(mail,'request_ip',return_value='203.0.113.7'):
            self.enrol();self.client.cookies.clear();self.login()
            with self.Session() as db:self.assertEqual(db.query(UserLoginIP).filter_by(user_id=self.uid).count(),0)
            self.assertEqual(self.post('verify-code',{'kind':'recovery','code':self.codes[0]}).status_code,200)
            self.assertEqual(self.post('finish').status_code,200)
            self.start('login');self.post('verify-code',{'kind':'recovery','code':self.codes[1]});self.post('finish')
            with self.Session() as db:
                rows=db.query(SecurityNotification).filter_by(user_id=self.uid).all()
                self.assertEqual(sum('unfamiliar' in x.subject for x in rows),1)
                self.assertEqual(sum('authenticator was added' in x.subject for x in rows),1)
                self.assertFalse(any(self.secret in x.body or self.codes[0] in x.body for x in rows))

    def test_security_mail_smtp_failure_retries_then_stops(self):
        from app.services import security_notifications as mail
        from app.db.models import SecurityNotification
        with self.Session() as db:
            user=db.get(User,self.uid);user.email='alice@example.org'
            with patch.object(mail,'smtp_configured',return_value=True):mail.enqueue(db,user,'password-changed')
            db.commit()
        with patch.object(mail,'SessionLocal',self.Session),patch.object(mail,'smtp_configured',return_value=True),patch.object(mail,'send_email',side_effect=RuntimeError('secret SMTP detail')):
            for attempt in range(8):
                with self.Session() as db:
                    row=db.query(SecurityNotification).filter_by(user_id=self.uid).one();row.next_attempt_at=mfa.now()-timedelta(seconds=1);db.commit()
                mail.deliver_pending()
        with self.Session() as db:
            row=db.query(SecurityNotification).filter_by(user_id=self.uid).one();self.assertEqual(row.attempts,8);self.assertEqual(row.status,'failed');self.assertEqual(row.last_error,'RuntimeError')

    def test_security_mail_transaction_and_missing_smtp(self):
        from app.services import security_notifications as mail
        from app.db.models import SecurityNotification
        with self.Session() as db:
            user=db.get(User,self.uid);user.email='alice@example.org';db.commit()
            with patch.object(mail,'smtp_configured',return_value=False):mail.enqueue(db,user,'password-changed')
            db.commit();self.assertEqual(db.query(SecurityNotification).filter_by(user_id=self.uid).count(),0)
            with patch.object(mail,'smtp_configured',return_value=True):mail.enqueue(db,user,'password-changed')
            db.rollback();self.assertEqual(db.query(SecurityNotification).filter_by(user_id=self.uid).count(),0)

    def test_security_mail_delivery(self):
        from app.services import security_notifications as mail
        from app.db.models import SecurityNotification
        with self.Session() as db:
            user=db.get(User,self.uid);user.email='alice@example.org'
            with patch.object(mail,'smtp_configured',return_value=True):mail.enqueue(db,user,'recovery-codes-regenerated')
            db.commit()
        with patch.object(mail,'SessionLocal',self.Session),patch.object(mail,'smtp_configured',return_value=True),patch.object(mail,'send_email') as send:
            mail.deliver_pending();self.assertTrue(send.called)
        with self.Session() as db:self.assertEqual(db.query(SecurityNotification).filter_by(user_id=self.uid).one().status,'sent')

    def test_security_ip_trust_and_ipv6_normalisation(self):
        from app.services import security_notifications as mail
        request=Request({'type':'http','headers':[(b'x-forwarded-for',b'192.0.2.99, 203.0.113.8, 10.0.0.2')],'client':('10.0.0.3',1)})
        with patch.object(settings,'security_trusted_proxy_cidrs',''):
            self.assertEqual(mail.request_ip(request),'10.0.0.3')
        with patch.object(settings,'security_trusted_proxy_cidrs','10.0.0.0/24'):
            self.assertEqual(mail.request_ip(request),'203.0.113.8')
        self.assertEqual(mail.normalise_ip('::ffff:203.0.113.8'),'203.0.113.8')

    def test_password_routes_enqueue_security_alerts(self):
        from app.api.routes import me, users
        from app.api.payloads import ChangePasswordPayload, AdminSetPasswordPayload
        from app.services import security_notifications as mail
        from app.db.models import SecurityNotification
        request=Request({'type':'http','headers':[],'client':('203.0.113.5',1)})
        with self.Session() as db:
            user=db.get(User,self.uid);user.email='alice@example.org';user.role='admin';db.commit();request.state.user=user
            with patch.object(mail,'smtp_configured',return_value=True),patch.object(me,'get_valkey',return_value=self.redis):
                me.change_my_password(ChangePasswordPayload(current_password=self.password,new_password='a-new-password-12345'),request,db)
                users.set_user_password_admin(self.uid,AdminSetPasswordPayload(new_password='another-password-12345'),request,db)
            subjects=[x.subject for x in db.query(SecurityNotification).filter_by(user_id=self.uid)]
            self.assertTrue(any('password was changed' in x for x in subjects));self.assertTrue(any('password was reset' in x for x in subjects))

    def webauthn_registration(self):
        self.start();options=self.post('webauthn/register-options').json()
        private=ec.generate_private_key(ec.SECP256R1());public=private.public_key().public_numbers();cid=os.urandom(32)
        cose=cbor2.dumps({1:2,3:-7,-1:1,-2:public.x.to_bytes(32,'big'),-3:public.y.to_bytes(32,'big')})
        client=json.dumps({'type':'webauthn.create','challenge':options['challenge'],'origin':'https://keen.example','crossOrigin':False}).encode()
        authdata=hashlib.sha256(b'keen.example').digest()+b'\x41'+struct.pack('>I',0)+bytes(16)+struct.pack('>H',len(cid))+cid+cose
        credential={'id':mfa.b64(cid),'rawId':mfa.b64(cid),'type':'public-key','response':{'clientDataJSON':mfa.b64(client),'attestationObject':mfa.b64(cbor2.dumps({'fmt':'none','attStmt':{},'authData':authdata}))}}
        return private,cid,credential

    def assertion(self,private,cid,options,origin='https://keen.example',cross=False):
        client=json.dumps({'type':'webauthn.get','challenge':options['challenge'],'origin':origin,'crossOrigin':cross}).encode()
        authdata=hashlib.sha256(b'keen.example').digest()+b'\x01'+struct.pack('>I',1)
        signature=private.sign(authdata+hashlib.sha256(client).digest(),ec.ECDSA(hashes.SHA256()))
        return {'id':mfa.b64(cid),'rawId':mfa.b64(cid),'type':'public-key','response':{'clientDataJSON':mfa.b64(client),'authenticatorData':mfa.b64(authdata),'signature':mfa.b64(signature),'userHandle':mfa.b64(self.uid.bytes)}}

    def test_real_webauthn_registration_and_signature(self):
        private,cid,credential=self.webauthn_registration()
        response=self.post('webauthn/register',{'credential':credential,'name':'Test key'});self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(response.json()['recovery_codes']),10)
        self.assertEqual(self.post('webauthn/register',{'credential':credential}).status_code,400)
        self.start('login');options=self.post('webauthn/options').json()
        assertion=self.assertion(private,cid,options)
        self.assertEqual(self.post('webauthn/verify',{'credential':assertion}).status_code,200)
        self.assertEqual(self.post('webauthn/verify',{'credential':assertion}).status_code,400)
        self.assertEqual(self.post('finish').status_code,200)
        self.assertTrue(self.client.get('/protected').json()['authenticated'])

    def test_webauthn_wrong_origin_cross_origin_wrong_user_and_tampered_signature(self):
        private,cid,credential=self.webauthn_registration();self.assertEqual(self.post('webauthn/register',{'credential':credential}).status_code,200)
        for kind in ('origin','cross','user','signature'):
            self.redis.flushall();self.start('login');options=self.post('webauthn/options').json()
            assertion=self.assertion(private,cid,options,origin='https://evil.example' if kind=='origin' else 'https://keen.example',cross=kind=='cross')
            if kind=='user':assertion['response']['userHandle']=mfa.b64(uuid.uuid4().bytes)
            if kind=='signature':assertion['response']['signature']=mfa.b64(b'invalid')
            self.assertEqual(self.post('webauthn/verify',{'credential':assertion}).status_code,400)
            self.assertEqual(self.post('finish').status_code,403)

if __name__=='__main__':unittest.main()
