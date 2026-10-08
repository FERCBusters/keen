# Migration consolidation audit

All 90 historical revisions were replayed in order to `0090_osa_catalogue` against PostgreSQL via PGlite. The resulting schema was compared with the supplied PostgreSQL 16.15 schema dump. Historical sources remain in `alembic/legacy_versions` for audit and regression checks; Alembic does not execute them during ordinary startup.

Compared: 769 columns, 326 constraints (plus column nullability), 361 indexes, five application functions, six triggers and four sequences. Columns include types, nullability, defaults and identity/generated flags. Constraint comparisons include foreign-key actions and checks. PostgreSQL 16/18 format varchar-array enum casts differently; these are semantically equivalent and explicitly normalised by the verifier. No other schema differences were found.

The schema migration uses the independently supplied final schema, including functions, triggers, indexes and sequence ownership, rather than ORM metadata. The seed migration imports final rows produced by the full chain, including permissions, risk categories/assets/library, PESTLE levels, business processes, framework catalogues, crosswalks and retention settings. Managed configurations are read from the deployment's YAML, as before. Fresh organisation selection now enables only KEEN-AF by default.

Historical data conversions, obsolete triggers and repair/backfill work do not run on fresh installations. Existing databases require verified adoption; the seed migration must never be replayed over them.

PGlite validates PostgreSQL SQL and transactional migrations but is not a native PostgreSQL 16 performance or concurrency test. CI must exercise the release on native PostgreSQL 16 before publishing.

## Complete historical inventory

Operation names below include downgrade operations; the replay executes upgrade functions and their helpers only.

| Revision | Purpose | Alembic operations in source |
|---|---|---|
| `0001_initial` | initial | create_index, create_table, drop_table |
| `0002_artifact_lineage` | artifact lineage + metadata | add_column, create_index, drop_column, drop_index |
| `0003_audit_logs` | audit logs | create_index, create_table, drop_index, drop_table |
| `0004_users` | users | create_index, create_table, drop_index, drop_table |
| `0005_saved_searches` | saved searches | create_index, create_table, drop_index, drop_table |
| `0006_user_pref_auto_apply` | user_pref_auto_apply_filters | add_column, alter_column, drop_column |
| `0007_user_pref_source_colors` | user_pref_source_colors | add_column, alter_column, drop_column |
| `0008_user_pref_viz_and_landing` | user_pref_viz_and_landing | add_column, alter_column, drop_column |
| `0009_user_pref_landing_len` | user_pref_landing_page_length | batch_alter_table |
| `0010_user_pref_default_vis` | user_pref_default_vis | add_column, drop_column |
| `0011_groups_perms_diary_acl` | groups_perms_diary_acl | bulk_insert, create_index, create_table, drop_index, drop_table |
| `0012_default_diary_audit_perms` | default_diary_audit_perms | bulk_insert, get_bind |
| `0013_group_roles` | group_roles | add_column, drop_column |
| `0014_event_questions` | event_questions | create_index, create_table, drop_index, drop_table, execute |
| `0015_event_question_notif` | event_question_notifications | add_column, create_index, drop_column, drop_index |
| `0016_user_pref_framework` | user_pref_framework | add_column, drop_column |
| `0017_query_performance_indexes` | query performance indexes | execute |
| `0018_audit_engagements` | audit engagements | create_index, create_table, drop_index, drop_table, execute |
| `0019_audittrail_perms` | audittrail permission rename and audit executive summary | add_column, drop_column, execute, get_bind |
| `0020_framework_clauses` | framework clauses and control applicability links | create_index, create_table, drop_index, drop_table, execute |
| `0021_clause_graph_cache` | clause graph performance and parent link cleanup | execute |
| `0022_audit_scoped_clauses` | audit scoped clauses | create_index, create_table, drop_index, drop_table |
| `0023_event_incidents` | event incidents | bulk_insert, create_index, create_table, drop_index, drop_table, get_bind |
| `0024_risks` | risks | bulk_insert, create_index, create_table, drop_index, drop_table, get_bind |
| `0025_risk_assets` | risk assets and subcategories | add_column, alter_column, bulk_insert, create_foreign_key, create_index, create_table, drop_column, drop_constraint, drop_index, drop_table, get_bind |
| `0026_risk_scores_allow_zero` | allow zero risk scores | create_check_constraint, drop_constraint, execute |
| `0027_audit_attendee_users` | link audit attendees to Keen users | add_column, create_foreign_key, create_index, drop_column, drop_constraint, drop_index |
| `0028_event_created_at_index` | index event ingestion time for latest evidence ticker | create_index, drop_index |
| `0029_event_search_performance` | event search performance indexes | execute, get_context |
| `0030_audit_type` | audit type and clause-linked audit controls | add_column, create_check_constraint, drop_column, drop_constraint, execute |
| `0031_scheduled_audit_templates` | scheduled audit templates | add_column, create_check_constraint, create_foreign_key, create_index, drop_column, drop_constraint, drop_index, execute |
| `0032_scheduled_audit_fuzzy_dates` | Scheduled audit fuzzy date rules | add_column, alter_column, create_check_constraint, drop_column, drop_constraint |
| `0033_user_pref_date_format` | User preference for visible date input format | add_column, alter_column, create_check_constraint, drop_column, drop_constraint |
| `0034_entity_changelogs` | Entity changelogs for controls clauses and risks | alter_column, create_index, create_table, drop_index, drop_table |
| `0035_scheduled_audit_days` | Scheduled audit named weekday rules | create_check_constraint, drop_constraint, execute |
| `0036_risk_mitigator` | risk mitigator support | add_column, bulk_insert, drop_column, execute, get_bind, get_context |
| `0037_pestle_assessments` | pestle impact assessments | bulk_insert, create_index, create_table, drop_index, drop_table, get_bind |
| `0038_pestle_questions_changes` | Extend questions to risks and PESTLE entities | add_column, alter_column, create_index, drop_column, drop_index, execute |
| `0039_interested_parties` | interested parties risk assessment | bulk_insert, create_index, create_table, drop_index, drop_table, get_bind |
| `0040_user_email` | add email address to Keen users | add_column, drop_column |
| `0041_interested_party_notes` | add notes to interested parties | add_column, drop_column |
| `0042_control_evidence_stats` | durable control evidence aggregate stats | create_index, create_table, drop_index, drop_table, execute |
| `0043_isms_module` | add ISMS module | bulk_insert, create_index, create_table, drop_table, get_bind |
| `0044_isms_objective` | allow ISMS objective target to be periodic text | alter_column |
| `0045_isms_goal_metric` | allow longer ISMS objective goal and metric fields | alter_column |
| `0046_unify_isms_assets` | unify ISMS assets with risk assets | add_column, create_foreign_key, create_index, create_table, drop_column, drop_constraint, drop_index, drop_table, get_bind |
| `0047_isms_licenses` | manage ISMS licenses as asset entities | add_column, create_foreign_key, create_index, create_table, drop_column, drop_constraint, drop_index, drop_table, get_bind |
| `0048_user_delete` | user deletion and audit scoped ISMS documents | alter_column, create_foreign_key, create_index, create_table, drop_constraint, drop_index, drop_table, get_bind |
| `0049_question_delete` | question delete permission | execute |
| `0050_framework_event_stats` | durable framework event aggregate stats | create_table, drop_table, execute |
| `0051_drop_diary_acl` | drop diary visibility acl tables | bulk_insert, create_index, create_table, execute, get_bind |
| `0052_repair_framework_stats` | repair durable framework event aggregate stats | execute |
| `0053_isms_access_control` | add ISMS access control matrix | create_index, create_table, drop_index, drop_table |
| `0054_backfill_access` | repair ISMS access control matrix role links | create_index, create_table, drop_index, drop_table, get_bind |
| `0055_audit_sample_entities` | Allow audits to sample non-event Keen entities. | add_column, create_index, create_unique_constraint, drop_column, drop_constraint, drop_index |
| `0056_isms_effectiveness` | add ISMS effectiveness measures | create_index, create_table, drop_index, drop_table |
| `0057_drop_eff_control_ref` | drop free-text ISMS effectiveness measure control ref | add_column, drop_column, get_bind |
| `0058_eff_desc` | add description to ISMS effectiveness measures | add_column, drop_column, get_bind |
| `0059_eff_src` | default effectiveness metric source to other | alter_column, get_bind |
| `0060_repair_unmapped` | repair unmapped event counters and cache-era stats | execute |
| `0061_native_oidc` | native OIDC auth with identity mapping | create_index, create_table, drop_index, drop_table, get_bind |
| `0062_permission_updates` | permission updates for questions, incidents and event reads | execute |
| `0063_user_authz_version` | store user authz version in database | add_column, alter_column, drop_column, get_bind |
| `0064_repair_triggers` | repair framework event stats triggers | execute |
| `0065_drop_triggers` | Stop maintaining global mapped/unmapped event counters with triggers. | execute |
| `0066_framework_editor` | Persist framework metadata for the admin editor. | add_column, drop_column |
| `0067_managed_configurations` | Store managed connector configurations and mapping rules. | create_index, create_table, drop_index, drop_table |
| `0068_unified_evidence` | Seed unified evidence definitions from existing database overrides or YAML. | get_bind |
| `0069_assurance_records` | Add independent people, personnel assurance, vendors and asset relationships. | add_column, create_index, create_table, drop_column, drop_index, drop_table |
| `0070_control_links` | Explicit one-way cross-framework control relationships. | create_index, create_table, drop_index, drop_table |
| `0071_risk_register` | Conventional risk register, treatment and reusable risk scenarios. | add_column, create_check_constraint, create_table, drop_column, drop_constraint, drop_table |
| `0072_bookstack_sections` | Keep policy section permalinks and immutable page-version snapshots. | create_index, create_table, drop_index, drop_table |
| `0073_native_documents` | Native policy editing, hierarchy, tags, comments and content revisions. | add_column, create_index, create_table, drop_column, drop_index, drop_table |
| `0074_seed_frameworks` | Seed the shipped framework catalogues without replacing administrator edits. | get_bind |
| `0075_people_attendance` | Link audit and meeting attendees to optional directory people. | add_column, create_index, create_table, drop_column, drop_index, drop_table, execute |
| `0076_assurance_risks` | Seed auditor donated risk scenarios as reusable templates, preserving existing assessments. | add_column, drop_column, get_bind |
| `0077_unified_risks_assets` | Unify risk ratings, supply reusable asset choices, retire application configuration. | get_bind |
| `0078_library_control_links` | Carry the auditor's Risk-sheet Vanta control IDs into KEEN-AF templates. | get_bind |
| `0079_control_crosswalk` | Seed reviewed, directed control evidence links once; preserve administrator edits. | get_bind |
| `0080_integration_builder` | Versioned HTTP integrations and bounded run history. | create_index, create_table, drop_table |
| `0081_keen_agent` | Scoped KEEN Agent identities; credentials stored as hashes. | create_table, drop_table |
| `0082_forgejo_feed_identity` | Restore collection provenance for existing Forgejo API events. | execute |
| `0083_evidence_retention` | Opt-in evidence retention, durable object cleanup, and audit retention holds. | execute |
| `0084_local_mfa` | Local MFA credentials and short-lived, unprivileged authentication challenges. | execute |
| `0085_security_notifications` | Successful-login IP history and durable security notification outbox. | execute |
| `0086_source_ingestion_pause` | Persistent operator pause switches for source ingestion. | create_table, drop_table |
| `0087_sso_verified_emails` | Verified addresses for safe SSO account linking. | create_index, create_table, drop_table |
| `0088_ldap_risk_roles` | LDAP account backend and organisational risk owners. | add_column, create_check_constraint, create_foreign_key, create_index, drop_column, drop_constraint |
| `0089_hipaa_soc2` | Seed HIPAA regulations and the attributed OSA SOC 2 catalogue. |  |
| `0090_osa_catalogue` | Replace pre-production framework catalogues with the frozen OSA catalogue. | create_index, create_table, get_bind |

## Existing installations

This is a pre-production baseline replacement. The supplied database is at
`0090_osa_catalogue`; adoption is supported only from that revision. Older
installations must use the previous release candidate to reach it first.
Keep the previous application image and a full database/artefact backup for rollback.

From the `keen` directory, with the updated source applied:

```sh
# Prepare local images before the maintenance window.
docker compose -f docker-compose.yml -f docker-compose.build.yml build
# Stop application writers; leave PostgreSQL and Valkey running.
docker compose stop keen-ui keen-api keen-worker keen-beat
# Back up the complete database, not just its schema.
docker compose exec -T postgres pg_dump -U keen -d keen -Fc > keen-before-baseline.dump
# Read-only schema check using the new local API image.
docker compose -f docker-compose.yml -f docker-compose.build.yml run --rm --no-deps --entrypoint python keen-api scripts/adopt_release_baseline.py
# Only after a successful check, adopt the new revision marker.
docker compose -f docker-compose.yml -f docker-compose.build.yml run --rm --no-deps --entrypoint python keen-api scripts/adopt_release_baseline.py --adopt
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d
```

If verification reports drift, stop and investigate; do not use a blind stamp.
The script changes no application rows. Saved framework selections remain as-is.
Use Admin → Frameworks to select only KEEN Assurance on an existing installation
if desired. Adoption can be repeated safely. After publishing verified GHCR images,
omit the local-build override and use `docker compose pull` before the same check.
Never start an old application image against the new revision marker.

## Validation

The historical chain replay took approximately 9.9 seconds; the new baseline
approximately 4.4 seconds in the same PostgreSQL-WASM test environment. These
are indicative development timings, not production PostgreSQL 16 benchmarks.
All final seed rows were compared with the historical replay, using their real
UUID relationships. A second upgrade preserved an administrator-edited title.
Schema adoption and rejection of a deliberately added column were tested.
Native PostgreSQL integration tests remain part of CI and must pass before release.
The schema SQL originates from the supplied PostgreSQL 16.15 dump; Alembic's
version table and pg_dump client/session directives are excluded.
