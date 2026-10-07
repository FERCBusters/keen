from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from webauthn import (generate_registration_options, verify_registration_response,
                      generate_authentication_options, verify_authentication_response, options_to_json)
from webauthn.helpers.structs import PublicKeyCredentialDescriptor, AuthenticatorSelectionCriteria, UserVerificationRequirement
import io
import json
import pyotp
import qrcode
import qrcode.image.svg

from app.db.session import get_db
from app.db.models import User, MfaCredential, MfaRecoveryCode
from app.security import mfa
from app.security.auth import require_authenticated, get_current_user_from_request
from app.security.passwords import verify_password

router=APIRouter()

class Password(BaseModel):
    password: str = Field(min_length=1,max_length=1024)

class Code(BaseModel):
    code: str = Field(min_length=1,max_length=128)
    kind: Literal['totp', 'recovery'] = 'totp'

class Credential(BaseModel):
    credential: dict
    name: str = Field(default='Security key / passkey',min_length=1,max_length=100)

class Remove(BaseModel):
    id: str = Field(min_length=1,max_length=100)


def no_cache(response):
    response.headers['Cache-Control']='no-store'
    return response


@router.get('/v1/me/mfa')
def status(request: Request, db: Session=Depends(get_db), user=Depends(require_authenticated)):
    from app.core.config import settings
    return {'enabled':user.mfa_enabled,'totp':bool(user.mfa_totp_secret),
        'credentials':[{'id':str(c.id),'name':c.name,'last_used_at':c.last_used_at} for c in mfa.registered(db,user)],
        'recovery_codes_remaining':db.query(MfaRecoveryCode).filter(MfaRecoveryCode.user_id==user.id).count(),
        'policy':settings.mfa_policy,'required':mfa.required(db,user),'local_enabled':settings.local_auth_enabled}


@router.post('/v1/auth/mfa/manage/start')
def start_management(payload: Password,request: Request,db: Session=Depends(get_db)):
    mfa.check_origin(request)
    user=get_current_user_from_request(request,db)
    if not user:raise HTTPException(401,'Sign in before managing local account security')
    mfa.limit(request,user.id)
    if not verify_password(payload.password,user.password_hash):raise HTTPException(401,'Invalid password')
    return mfa.start(db,user,request,'manage')


@router.post('/v1/auth/mfa/state')
def state(request: Request,db: Session=Depends(get_db)):
    row,user=mfa.pending(db,request)
    return {'stage':row.stage,'purpose':row.purpose,'username':user.username,
        'totp':bool(user.mfa_totp_secret),'required':mfa.required(db,user),
        'credentials':[{'id':str(c.id),'name':c.name,'last_used_at':c.last_used_at} for c in mfa.registered(db,user)],
        'recovery_codes_remaining':db.query(MfaRecoveryCode).filter(MfaRecoveryCode.user_id==user.id).count()}


@router.post('/v1/auth/mfa/verify-code')
def verify_code(payload: Code,request: Request,db: Session=Depends(get_db)):
    row,user=mfa.pending(db,request)
    if row.stage!='verify':raise HTTPException(400,'Start a second-factor verification first')
    mfa.limit(request,user.id)
    if not mfa.verify_code(db,user,payload.code.strip(),payload.kind):
        raise HTTPException(400,'Invalid or already-used code')
    mfa.verified(row);mfa.audit(db,user,'verified-'+payload.kind,request);db.commit()
    return {'ok':True}


@router.post('/v1/auth/mfa/totp/start')
def start_totp(request: Request,db: Session=Depends(get_db)):
    row,user=mfa.pending(db,request);mfa.management(row)
    if user.mfa_totp_secret:raise HTTPException(409,'Remove the existing authenticator before adding another')
    mfa.limit(request,user.id)
    secret=pyotp.random_base32()
    uri=pyotp.TOTP(secret).provisioning_uri(name=user.username,issuer_name='KEEN')
    row.data={**row.data,'totp_secret':mfa.cipher().encrypt(secret.encode()).decode()}
    db.commit()
    image=qrcode.make(uri,image_factory=qrcode.image.svg.SvgPathImage)
    output=io.BytesIO();image.save(output)
    import base64
    return {'secret':secret,'qr':'data:image/svg+xml;base64,'+base64.b64encode(output.getvalue()).decode()}


@router.post('/v1/auth/mfa/totp/confirm')
def confirm_totp(payload: Code,request: Request,db: Session=Depends(get_db)):
    row,user=mfa.pending(db,request);mfa.management(row);mfa.limit(request,user.id)
    secret=row.data.get('totp_secret')
    if not secret or user.mfa_totp_secret:raise HTTPException(400,'Start authenticator setup first')
    # Assignment is rolled back on invalid proof; enabling happens only after proof.
    user.mfa_totp_secret=secret;user.mfa_totp_last_step=-1
    if not mfa.verify_code(db,user,payload.code.strip(),'totp'):raise HTTPException(400,'Invalid authenticator code')
    return mfa.activate(db,row,user,request)


@router.post('/v1/auth/mfa/webauthn/options')
def authentication_options(request: Request,db: Session=Depends(get_db)):
    row,user=mfa.pending(db,request)
    if row.stage!='verify':raise HTTPException(400,'Start verification first')
    mfa.limit(request,user.id)
    credentials=mfa.registered(db,user)
    if not credentials:raise HTTPException(400,'No security key or passkey is registered')
    _,rp=mfa.origin_config()
    options=generate_authentication_options(rp_id=rp,allow_credentials=[PublicKeyCredentialDescriptor(id=mfa.unb64(c.credential_id)) for c in credentials])
    row.data={'authenticate':mfa.b64(options.challenge)};db.commit()
    return json.loads(options_to_json(options))


@router.post('/v1/auth/mfa/webauthn/verify')
def authentication_verify(payload: Credential,request: Request,db: Session=Depends(get_db)):
    row,user=mfa.pending(db,request)
    if row.stage!='verify':raise HTTPException(400,'Start verification first')
    mfa.limit(request,user.id)
    challenge=row.data.get('authenticate');row.data={}
    try:
        if not challenge:raise ValueError()
        mfa.check_client_data(payload.credential,user)
        credential=db.query(MfaCredential).filter(MfaCredential.user_id==user.id,MfaCredential.credential_id==payload.credential.get('id')).one_or_none()
        if not credential:raise ValueError()
        origin,rp=mfa.origin_config()
        result=verify_authentication_response(credential=payload.credential,expected_challenge=mfa.unb64(challenge),
            expected_rp_id=rp,expected_origin=origin,credential_public_key=mfa.unb64(credential.public_key),
            credential_current_sign_count=credential.sign_count,require_user_verification=False)
    except Exception:
        db.commit()  # consume ceremony even when the assertion is invalid
        raise HTTPException(400,'Security key verification failed; request another challenge')
    credential.sign_count=result.new_sign_count;credential.last_used_at=mfa.now()
    mfa.verified(row);mfa.audit(db,user,'verified-webauthn',request);db.commit()
    return {'ok':True}


@router.post('/v1/auth/mfa/webauthn/register-options')
def registration_options(request: Request,db: Session=Depends(get_db)):
    row,user=mfa.pending(db,request);mfa.management(row);mfa.limit(request,user.id)
    credentials=mfa.registered(db,user)
    if len(credentials)>=10:raise HTTPException(400,'A maximum of ten security keys/passkeys can be registered')
    _,rp=mfa.origin_config()
    options=generate_registration_options(rp_id=rp,rp_name='KEEN',user_id=user.id.bytes,user_name=user.username,
        exclude_credentials=[PublicKeyCredentialDescriptor(id=mfa.unb64(c.credential_id)) for c in credentials],
        authenticator_selection=AuthenticatorSelectionCriteria(user_verification=UserVerificationRequirement.PREFERRED))
    row.data={**row.data,'register':mfa.b64(options.challenge)};db.commit()
    return json.loads(options_to_json(options))


@router.post('/v1/auth/mfa/webauthn/register')
def registration_verify(payload: Credential,request: Request,db: Session=Depends(get_db)):
    row,user=mfa.pending(db,request);mfa.management(row);mfa.limit(request,user.id)
    challenge=row.data.get('register');row.data={k:v for k,v in row.data.items() if k!='register'}
    try:
        if not challenge:raise ValueError()
        mfa.check_client_data(payload.credential)
        origin,rp=mfa.origin_config()
        result=verify_registration_response(credential=payload.credential,expected_challenge=mfa.unb64(challenge),
            expected_rp_id=rp,expected_origin=origin,require_user_presence=True,require_user_verification=False)
    except Exception:
        db.commit()
        raise HTTPException(400,'Registration failed; request another challenge')
    if db.query(MfaCredential).filter(MfaCredential.credential_id==mfa.b64(result.credential_id)).first():
        db.commit();raise HTTPException(409,'This credential is already registered')
    db.add(MfaCredential(user_id=user.id,credential_id=mfa.b64(result.credential_id),public_key=mfa.b64(result.credential_public_key),
        sign_count=result.sign_count,name=payload.name.strip() or 'Security key / passkey'))
    return mfa.activate(db,row,user,request)


@router.post('/v1/auth/mfa/remove')
def remove(payload: Remove,request: Request,db: Session=Depends(get_db)):
    row,user=mfa.pending(db,request);mfa.management(row)
    credentials=mfa.registered(db,user)
    total=len(credentials)+int(bool(user.mfa_totp_secret))
    if total<=1 and mfa.required(db,user):raise HTTPException(400,'Register a replacement before removing your last required factor')
    if payload.id=='totp' and user.mfa_totp_secret:
        user.mfa_totp_secret=None;user.mfa_totp_last_step=-1
    else:
        credential=next((c for c in credentials if str(c.id)==payload.id),None)
        if not credential:raise HTTPException(404,'Authenticator not found')
        db.delete(credential)
    user.mfa_enabled=total>1
    if not user.mfa_enabled:
        db.query(MfaRecoveryCode).filter(MfaRecoveryCode.user_id==user.id).delete(synchronize_session=False)
    mfa.bump(db,user,row);row.data={};mfa.audit(db,user,'factor-removed',request);db.commit()
    return {'ok':True}


@router.post('/v1/auth/mfa/recovery/regenerate')
def regenerate(request: Request,db: Session=Depends(get_db)):
    row,user=mfa.pending(db,request);mfa.management(row)
    if not user.mfa_enabled:raise HTTPException(400,'Enrol an authenticator first')
    codes=mfa.recovery_codes(db,user);mfa.bump(db,user,row)
    mfa.audit(db,user,'recovery-codes-regenerated',request);db.commit()
    return {'recovery_codes':codes}


@router.post('/v1/auth/mfa/finish')
def finish(request: Request,db: Session=Depends(get_db)):
    row,user=mfa.pending(db,request)
    if row.stage not in {'ready','manage'} or (mfa.required(db,user) and not user.mfa_enabled):
        raise HTTPException(403,'Complete second-factor verification or enrolment first')
    # Commit consumption before issuing a full session; Redis failure fails closed.
    verified_version, enabled = row.version, user.mfa_enabled
    db.delete(row);db.commit()
    from .auth import complete_local_login
    response=complete_local_login(db,user,request,mfa_verified=enabled,verified_version=verified_version)
    response.delete_cookie(mfa.COOKIE,path='/')
    return no_cache(response)


@router.post('/v1/auth/mfa/cancel')
def cancel(request: Request, db: Session=Depends(get_db)):
    from fastapi.responses import JSONResponse
    mfa.check_origin(request)
    return mfa.cancel(db, request, JSONResponse({'ok': True}))
