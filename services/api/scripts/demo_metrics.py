"""Offline measured-assurance examples, with dated synthetic source evidence."""
from datetime import datetime, time, timedelta
from app.db.models import (ControlItem, Event, Mapping, IsmsEffectivenessMeasure,
    IsmsEffectivenessMetricEntry, IsmsEntityControlLink, Audit, AuditScopedControl, AuditEvidence)

SPECS = {
    'availability': dict(title='Service availability SLA', target=99.9, unit='%', operator='gte',
        values=[99.95, 99.97, 99.72, 99.93, 99.98, 99.99],
        method='Calendar-month availability = 100 × (eligible minutes − unavailable minutes) / eligible minutes. All minutes included; no maintenance exclusions. Synthetic monitor coverage is 100%.',
        action='Breach: simulated failover delay; repair routing and verify next month’s result.'),
    'patch_sla': dict(title='Critical updates installed within 14 days', target=95, unit='%', operator='gte',
        values=[88, 92, 96, 98, 100, 97],
        method='100 synthetic critical-update obligations due each month. Count those installed within 14 days of release, divided by all obligations due, × 100. No exceptions excluded.',
        action='Breach: delayed maintenance windows; assign overdue updates and review the next monthly report.'),
    'recovery': dict(title='Verified restore time against recovery objective', target=60, unit='minutes', operator='lte',
        values=[52, 74, 65, 48, 42, 39],
        method='One scheduled synthetic restore test per month. Elapsed minutes from test declaration to restored-service validation, including checksum and application checks. Target ≤60 minutes.',
        action='Breach: restore exceeded the objective; improve recovery automation and repeat the measured exercise.'),
}
LINKS = {
    'iso_27001_2022': {'availability':'A.8.14', 'patch_sla':'A.8.8', 'recovery':'A.8.13'},
    'KEEN-AF:1.0': {'availability':'TPM-2', 'patch_sla':'VPM-2', 'recovery':'BCD-2'},
    'CYBER-ESSENTIALS:2026': {'patch_sla':'A6.4'},
}


def seed_metrics(seeder, frameworks):
    for framework in frameworks:
        audit = seeder.upsert(Audit, {'framework_slug':framework, 'title':seeder.label(framework+' Measured assurance review')},
            dict(status='in_progress', audit_type='internal', start_date=seeder.seed_date,
                 end_date=seeder.seed_date+timedelta(days=7), created_by_user_id=seeder.user.id))
        for key, ref in LINKS[framework].items():
            spec = SPECS[key]
            control = seeder.db.query(ControlItem).filter_by(framework_slug=framework, ref=ref).one()
            identity = f'{seeder.account_code("SLA")}-{framework}-{key}'
            measure = seeder.upsert(IsmsEffectivenessMeasure, {'metric_key':identity}, dict(
                framework_slug=framework, summary=seeder.label(spec['title']),
                description='Fictional worked example: measured performance, source evidence and follow-up. These targets are example organisational objectives, not prescribed certification thresholds.',
                effectiveness_measure=spec['method'], metric=spec['title'],
                target_value=spec['target'], target_unit=spec['unit'], threshold_operator=spec['operator'],
                frequency='Monthly', owner_user_id=seeder.user.id, created_by_user_id=seeder.user.id,
                notes='Six completed reporting periods, including breaches and subsequent improvement. Every observation links to synthetic source evidence.'))
            seeder.ensure_link(IsmsEntityControlLink, dict(entity_type='effectiveness_measure', entity_id=measure.id,
                framework_slug=framework, control_item_id=control.id), dict(created_by_user_id=seeder.user.id))
            seeder.ensure_link(AuditScopedControl, dict(audit_id=audit.id, control_item_id=control.id))
            seeder.ensure_link(AuditEvidence, dict(audit_id=audit.id, entity_type='isms_effectiveness_measure', entity_id=measure.id),
                dict(title=measure.summary, notes='Review the method and target alongside the dated observations.', added_by_user_id=seeder.user.id))
            for offset, value in enumerate(spec['values']):
                start, end = seeder.month_period(6-offset)
                stamp = datetime.combine(end,time(12))
                passed = value >= spec['target'] if spec['operator']=='gte' else value <= spec['target']
                raw = dict(synthetic=True, reporting_period_start=str(start), reporting_period_end=str(end),
                    value=value, unit=spec['unit'], target=spec['target'], operator=spec['operator'],
                    assessment='met' if passed else 'breach', measurement_coverage='Complete synthetic reporting period')
                if key=='availability':
                    minutes=((end-start).days+1)*1440
                    raw.update(eligible_minutes=minutes, unavailable_minutes=round(minutes*(1-value/100),4), monitor_coverage_percent=100)
                elif key=='patch_sla':raw.update(due_obligations=100, installed_within_14_days=value)
                else:raw.update(scheduled_tests=1, completed_tests=1, integrity_check='passed', application_check='passed')
                note = ('Target met; reviewer checked the synthetic source report.' if passed else spec['action'])
                event = seeder.upsert(Event, dict(source='demo', external_id=f'{identity}-{start}'), dict(
                    timestamp=stamp, system='fictional.example.invalid', actor='demo-assurance-reviewer',
                    action='effectiveness_measurement', outcome='success' if passed else 'warning', severity=1 if passed else 3,
                    summary=seeder.label(f"{spec['title']}: {value} {spec['unit']} ({start:%Y-%m})"),
                    raw_pointer={'seed':'demo','prefix':seeder.prefix}, normalized_payload={**raw,'method':spec['method'],'review_notes':note}))
                seeder.ensure_link(Mapping, dict(event_id=event.id, control_item_id=control.id), dict(confidence=1,
                    method='seeded-demo', mapped_by=seeder.user.username, rationale='Synthetic measured-assurance evidence; a breach remains relevant evidence and is not a compliance pass.'))
                metric = seeder.upsert(IsmsEffectivenessMetricEntry, dict(measure_id=measure.id, source_reference=f'{identity}-{start}'), dict(
                    period_start=start, period_end=end, recorded_at=stamp, metric_value=value, metric_unit=spec['unit'],
                    source_type='manual', source_title=seeder.label('Synthetic monthly measurement report'), source_event_id=event.id,
                    source_url=f'/event.html?id={event.id}', notes=note, raw_payload=raw, created_by_user_id=seeder.user.id))
                if not passed or offset==5:
                    seeder.ensure_link(AuditEvidence, dict(audit_id=audit.id, entity_type='isms_effectiveness_metric', entity_id=metric.id),
                        dict(title=event.summary, notes='Compare this observation with its source report and threshold; review follow-up after any breach.', added_by_user_id=seeder.user.id))
