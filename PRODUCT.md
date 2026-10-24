# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The primary user is the ISMS or compliance lead at an organisation that runs its own assurance programme: they maintain controls across frameworks, keep evidence mapped and current, run risk registers and Statement of Applicability upkeep, and prepare for and manage audits. Secondary audiences are administrators who configure sources, integrations and features; auditors and reviewers who assess evidence quality and sufficiency during audits; and general staff who answer audit questions, complete quizzes and view their own records.

## Product Purpose

Keen (Key Evidence ENgine) brings operational evidence, assurance frameworks, risk management and ISMS records into one workspace. It collects activity from an organisation's systems, maps evidence to controls with inspectable rules, and supports reviewing that evidence during audits. Success is a continuously evidenced ISMS: quiet sources get noticed, coverage is grounded in real collected events, and audit preparation flows from records the organisation already holds.

## Positioning

Self-hosted, evidence-first compliance: an organisation runs Keen itself, keeps its data, and sees exactly how each event is mapped to controls. The defensible mechanism is transparent, editable mapping rules over a broad built-in framework catalogue (94 frameworks, including 88 from Open Security Architecture) combined with wide source collection (development activity, logs, mail, webhooks, a fleet agent). Neighbouring SaaS GRC products cannot truthfully offer self-hosting with full control over credentials, data residency and mapping logic.

## Operating Context

Deployed by an organisation on its own infrastructure, typically with Docker Compose and a TLS reverse proxy. Evidence collection runs through Celery workers on schedules set per source; administrators work in the Admin areas (Sources & Evidence Mapping, Ingest runners, Features, security setup); day-to-day review lives in the source pages, control pages, audits, risks, SWOT and episode stories. Operational and administration guidance lives inside the product's Help section, including the administrator handbook. Usage spans steady-state background collection punctuated by audit windows when reviewers sample evidence and record findings.

## Capabilities and Constraints

Confirmed capabilities: multi-framework control catalogue with editable controls and definitions; source connections with per-source inputs, mapping rules with previews and backfills, and connection identity recorded on events; quiet-source detection, quiet periods, daily spikes and repeated summaries; Statement of Applicability and framework scoping; risk registers, treatments, risk library and Keen Mitigator; policies, assets, objectives, interested parties, measurements and meetings; SWOT hierarchies with quadrant, status-board, timeline and relationship views; audit planning, scheduling, email reminders, sampling, findings, questions, reports and document revisions; quizzes with scheduled rounds, blind-graded written answers and per-round effectiveness measures; episode stories with timeline and drag-and-drop flow chart; Keen Agent collection and enrolment profiles; workspaces and subsections enabled or disabled per deployment.

Confirmed constraints: self-hosted deployment is the operating model; credentials are encrypted at rest with a deployment-held Fernet key, so key loss loses stored source credentials; built-in HTTP collection is HTTPS-only with response size limits and configurable private-network restrictions; features are individually enableable and the UI must not assume an enabled room is present; artefact integrity relies on SHA-256 checksums; product copy is written in Australian or British English.

Undecided: no confirmed deployment-size assumptions beyond self-hosting, and no decision recorded on marketing-facing surfaces.

## Brand Commitments

The product name is Keen (Key Evidence ENgine). Documentation and guidance are delivered inside the product's Help section. No voice, tone, logo, palette or other identity commitments have been made binding.

## Evidence on Hand

The README describes capabilities and setup accurately and is maintained; the Help content inside the product carries the operator handbook; the framework catalogue data lives in the repository (`frameworks/`, with attribution in NOTICE and bundled licences); source and mapping configuration examples live in `config/`. There are no marketing assets, testimonials, case studies or customer quotes on hand — future work must not fabricate any of these.

## Product Principles

- Evidence before assertion: show the collected events behind every coverage count, match and claim.
- Trust through transparency: keep mapping rules, collection behaviour, and credentials handling inspectable and under the operator's control.
- Respect the operator's environment: self-hosted means no assumed connectivity, size or feature availability; degrade cleanly when a room or source is disabled.
- Care in the details: this is trusted production software for security work — correctness, copy precision and quiet competence matter more than flourish.
- Fair review: blind grading, anonymised results and reviewer judgement are treated as first-class product behaviours, not bolt-ons.

## Accessibility & Inclusion

Product copy uses Australian or British English. The UI is expected to meet WCAG-level accessibility (keyboard operable, screen-reader usable, sufficient contrast) for the desk-based compliance and administration work it serves.
