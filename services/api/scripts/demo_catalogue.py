"""Fictional, offline examples. Explicit framework references; no inferred equivalence."""
from datetime import datetime, time, timedelta
import hashlib
from html import escape
from app.db.models import (ControlItem, Event, Mapping, Risk, RiskControlLink, RiskAsset,
    RiskCategory, RiskAssetSubcategory, IsmsPerson, IsmsVendor, IsmsPersonAssurance, IsmsDocument, IsmsDocumentFolder,
    IsmsDocumentRevision, IsmsEntityControlLink, Audit, AuditScopedControl, AuditEvidence)

CATALOGUE = {
 'ISO27001:2022': [
  ('A.5.9','Asset register reconciliation','12 fictional devices reconciled; one retired laptop removed.'),
  ('A.5.18','Quarterly access review','17 fictional accounts reviewed; two revoked and one exception assigned an expiry.'),
  ('A.8.8','Vulnerability remediation','A simulated critical update was applied to three lab servers.'),
  ('A.8.13','Restore exercise','A synthetic dataset was restored and verified in an isolated test environment.'),
  ('A.5.24','Incident tabletop','Fictional responders exercised escalation and evidence preservation.'),
  ('A.6.3','Awareness completion','Five of six fictional staff completed training; one follow-up remains open.')],
 'KEEN-AF:1.0': [
  ('IAC-7','Access review exception','A temporary support role requires a documented review before renewal.'),
  ('BCD-2','Recovery rehearsal','Sample recovery plan exercised; measured restore time 42 minutes.'),
  ('TPM-2','Supplier assurance review','Fictional hosting supplier reviewed; a contract action remains open.'),
  ('SAT-1','Staff training review','Fictional training register reviewed by the assurance lead.'),
  ('VPM-2','Lab vulnerability review','Three synthetic findings triaged; one remediation is overdue.'),
  ('GOV-11','Policy review','The fictional board approved a revised information-security policy.')],
 'CYBER-ESSENTIALS:2026': [
  ('A4.6','Firewall rule review','Annual review of the fictional boundary removed an unused inbound rule.'),
  ('A5.1','Unused software removal','An unneeded lab service was uninstalled and its listener verified closed.'),
  ('A6.4','Security update timing','Synthetic critical OS updates installed within 10 days; no real host was scanned.'),
  ('A7.3','Leaver account closure','A fictional leaver account was disabled on the agreed departure date.'),
  ('A7.16','Administrator MFA check','All three fictional cloud administrators use MFA; test evidence only.'),
  ('A8.3','Malware scan review','A synthetic scan log records a successful scheduled scan of lab endpoints.')],
 'UK-DVSTF:1.0': [
  ('11.6.1.d','Information classification review','Fictional identity data classes have named owners and access rules.'),
  ('11.6.3.a','Recovery objectives review','Sample backup policy records an agreed recovery-time objective.'),
  ('11.8.1.a','Records policy approval','The fictional service approved its records management policy.'),
  ('11.8.3.a','Disposal exercise','Expired synthetic records were deleted according to a sample retention schedule.'),
  ('12.5.a','Identity-service incident exercise','A fictional service outage exercised triage, escalation and user communications.'),
  ('12.7.1.a','Privacy notice review','A sample notice was reviewed for fictional data flows and transparency gaps.')],
}


def seed_catalogue(seeder, frameworks):
    from demo_metrics import seed_metrics
    seed_metrics(seeder, frameworks)
    db=seeder.db
    folder=seeder.upsert(IsmsDocumentFolder, {'name':seeder.label('Sample policies'), 'parent_id':None})
    for name, role in [('Alex Example','Assurance lead'),('Sam Example','Platform engineer'),('Riley Example','Service owner'),('Jordan Example','Support analyst'),('Casey Example','Privacy lead'),('Morgan Example','Contractor')]:
        person=seeder.upsert(IsmsPerson,{'name':seeder.label(name)},dict(email=name.split()[0].lower()+'@example.invalid',position=role,notes='Fictional demonstration person. Not a login account.'))
        for category, title in [('Training','Security awareness'),('Policy acknowledgement','Acceptable use acknowledgement')]:
            seeder.upsert(IsmsPersonAssurance,{'person_id':person.id,'name':title},dict(category=category,status='pending' if name.startswith('Morgan') else 'met',source_system='demo',notes='Synthetic assurance record.',completed_at=None if name.startswith('Morgan') else seeder.seed_date-timedelta(days=14)))
    for name in ['Example Hosting','Example Identity Services','Example Device Supply']:
        seeder.upsert(IsmsVendor,{'name':seeder.label(name)},dict(description='Fictional supplier for demonstration only.',website='https://example.invalid',contact='support@example.invalid'))
    asset=db.query(RiskAsset).filter(RiskAsset.name.like(seeder.prefix+'%')).first()
    if asset is None:
        category=seeder.upsert(RiskCategory,{'name':seeder.label('Fictional services')})
        subcategory=seeder.upsert(RiskAssetSubcategory,{'category_id':category.id,'name':seeder.label('Lab systems')})
        asset=seeder.upsert(RiskAsset,{'name':seeder.label('Fictional service')},{'description':'Synthetic service asset','category_id':category.id,'subcategory_id':subcategory.id})
    for framework in frameworks:
        if framework not in CATALOGUE:
            raise ValueError('No reviewed demo catalogue for '+framework)
        examples=CATALOGUE[framework]
        for index,(ref,title,detail) in enumerate(examples):
            control=db.query(ControlItem).filter_by(framework_slug=framework,ref=ref).one_or_none()
            if control is None:
                raise ValueError(f'Missing seeded control {framework}/{ref}; run migrations first')
            key=f'{seeder.account_code("CATALOGUE")}-{framework}-{ref}'
            event=seeder.upsert(Event,{'source':'demo','external_id':key},dict(timestamp=datetime.combine(seeder.seed_date-timedelta(days=index+1),time(10,0)),system='fictional.example.invalid',actor='demo-reviewer',action='sample_control_review',outcome='warning' if index%3==0 else 'success',severity=3 if index%3==0 else 1,summary=seeder.label(title),raw_pointer={'seed':'demo','prefix':seeder.prefix},normalized_payload={'demo':True,'details':detail,'framework':framework,'control':ref,'notice':'Fictional evidence. Not proof of compliance.'}))
            seeder.ensure_link(Mapping,{'event_id':event.id,'control_item_id':control.id},dict(confidence=1.0,method='seeded-demo',mapped_by=seeder.user.username,rationale='Fictional evidence specifically illustrating this control.'))
            if index < 2:
                html=f'<h2>Fictional sample: {escape(title)}</h2><p><strong>DEMO ONLY — not a policy for real use.</strong></p><p>{escape(detail)}</p><h3>Responsibilities</h3><p>The fictional service owner reviews exceptions monthly.</p><h3>Review</h3><p>Record the reviewer, decision, scope and next review date. Adapt a real policy to the organisation before use.</p>'
                doc=seeder.upsert(IsmsDocument,{'title':seeder.label(framework+' '+title)},dict(document_type='policy',description='Fictional worked example',folder_id=folder.id,tags=['demo'],created_by_user_id=seeder.user.id))
                if not doc.content_version:
                    doc.content_html=html;doc.content_version=1
                    seeder.ensure_link(IsmsDocumentRevision,{'document_id':doc.id,'version':1},dict(content_html=html,sha256=hashlib.sha256(html.encode()).hexdigest(),created_by_user_id=seeder.user.id))
                seeder.ensure_link(IsmsEntityControlLink,{'entity_type':'document','entity_id':doc.id,'framework_slug':framework,'control_item_id':control.id},{'created_by_user_id':seeder.user.id})
                risk=seeder.upsert(Risk,{'asset_id':asset.id,'threat_summary':seeder.label(framework+' '+title+' gap')},dict(risk_types=['confidentiality','integrity','availability'],risk_owner_user_id=seeder.user.id,register_likelihood=3+index,register_impact=4,register_residual_likelihood=2,register_residual_impact=3,treatment_strategy='mitigate',treatment_status='open',treatment_plan='Fictional example: review the sample evidence and assign the remaining action.',treatment_due_at=seeder.seed_date+timedelta(days=14),note='Fictional demonstration scenario',created_by_user_id=seeder.user.id))
                seeder.ensure_link(RiskControlLink,{'risk_id':risk.id,'framework_slug':framework,'control_item_id':control.id})
                audit=seeder.upsert(Audit,{'title':seeder.label(framework+' '+title+' review'),'framework_slug':framework},dict(status='open',audit_type='internal',start_date=seeder.seed_date,end_date=seeder.seed_date+timedelta(days=7),created_by_user_id=seeder.user.id))
                seeder.ensure_link(AuditScopedControl,{'audit_id':audit.id,'control_item_id':control.id})
                seeder.ensure_link(AuditEvidence,{'audit_id':audit.id,'event_id':event.id},dict(title=event.summary,notes='Synthetic sample reviewed in this fictional audit.',added_by_user_id=seeder.user.id))
