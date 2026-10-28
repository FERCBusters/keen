"""Who may call each Keen API operation.

Every HTTP operation the API serves appears here, keyed by method and route
pattern. A new route fails both the backend inventory test
(services/api/tests/test_access_policy_inventory.py) and the deployment check
(test_09_access_policy.py) until it is given a rule, so each endpoint gets an
explicit decision about its permissions.

Rules:

- PUBLIC: no session; the route authenticates its own credentials or ceremony
  (services/api/app/security/route_policy.py lists the same operations).
- SIGNED_IN: any active account.
- ADMIN: administrators only. No combination of permissions grants access.
- any_of(...): an administrator, or an account holding any one of the permissions.
- all_of(...): an administrator, or an account holding all of the permissions.
- either({...}, {...}): an administrator, or an account holding every
  permission of at least one set.

Administrators pass every rule except PUBLIC, which does not depend on accounts.
Ownership and object visibility add to these rules for some operations; see
OBJECT_RULES. The deployment check covers the permission gate of those
operations, and other modules and the backend tests cover their object rules.
"""

from __future__ import annotations

from dataclasses import dataclass

PERMISSIONS = (
    "events.read",
    "risk.read",
    "risk.manage",
    "isms.read",
    "isms.manage",
    "pestle.read",
    "pestle.manage",
    "interested_parties.read",
    "interested_parties.manage",
    "audits.read",
    "audits.manage",
    "audits.admin",
    "audittrail.read",
    "incident.create",
    "incident.delete",
    "question.create",
    "question.delete",
    "stories.read",
    "stories.manage",
    "stories.admin",
    "quiz.read",
    "quiz.manage",
)


@dataclass(frozen=True)
class Rule:
    kind: str  # public, signed-in, admin, permissions, outside
    alternatives: tuple = ()  # frozensets of permission codes


def any_of(*codes):
    return Rule("permissions", tuple(frozenset([code]) for code in codes))


def all_of(*codes):
    return Rule("permissions", (frozenset(codes),))


def either(*sets):
    return Rule("permissions", tuple(frozenset(s) for s in sets))


PUBLIC = Rule("public")
SIGNED_IN = Rule("signed-in")
ADMIN = Rule("admin")
# Served routes the API schema omits; checked by the backend inventory test and
# by test_01 (documentation) and test_02 (WebSocket).
OUTSIDE = Rule("outside")

# risk.read and risk.manage extend to the ISMS, PESTLE(E) and interested-party
# registers (SECURITY.md); audits.admin and audits.manage include audit reads.
EVENTS_READ = any_of("events.read")
RISK_READ = any_of("risk.read", "risk.manage")
RISK_MANAGE = any_of("risk.manage")
ISMS_READ = any_of("isms.read", "isms.manage", "risk.read", "risk.manage")
ISMS_MANAGE = any_of("isms.manage", "risk.manage")
PESTLE_READ = any_of("pestle.read", "pestle.manage", "risk.read", "risk.manage")
PESTLE_MANAGE = any_of("pestle.manage", "risk.manage")
PARTIES_READ = any_of(
    "interested_parties.read", "interested_parties.manage", "risk.read", "risk.manage"
)
PARTIES_MANAGE = any_of("interested_parties.manage", "risk.manage")
AUDITS_READ = any_of("audits.read", "audits.manage", "audits.admin")
AUDITS_MANAGE = any_of("audits.manage", "audits.admin")
AUDITS_ADMIN = any_of("audits.admin")
AUDIT_TRAIL = any_of("audittrail.read")
# stories.manage and stories.admin include story reads; see OBJECT_RULES for
# the ownership rule that limits stories.manage to its holder's own stories.
STORIES_READ = any_of("stories.read", "stories.manage", "stories.admin")
STORIES_MANAGE = any_of("stories.manage", "stories.admin")
# quiz.manage includes quiz reads; a person sits the quizzes assigned to them
# without either permission (OBJECT_RULES).
QUIZ_READ = any_of("quiz.read", "quiz.manage")
QUIZ_MANAGE = any_of("quiz.manage")

# Additional object rules. The matrix probe account owns nothing, so these
# operations are checked with their permission gate only.
OBJECT_RULES = {
    "GET /v1/risks/{risk_id}": "Members of the risk's owning organisation role can read it.",
    "PATCH /v1/isms/org-nodes/{node_id}": "Changing members of a role that owns risks needs risk.manage.",
    "PATCH /v1/audits/{audit_id}": "audits.manage can edit audits its holder created.",
    "POST /v1/audits/{audit_id}/report": "audits.manage can upload reports to audits its holder created.",
    "POST /v1/audits/{audit_id}/evidence": (
        "audits.manage can sample into audits its holder created; sampling an event "
        "needs events.read and sampling a record needs read access to it."
    ),
    "PATCH /v1/audits/scheduled/{audit_id}": "audits.manage can edit templates its holder created.",
    "DELETE /v1/questions/{thread_id}": "The question's target must be visible to the account.",
    "POST /v1/questions/{thread_id}/posts": "The question's author can reply as well as administrators.",
    "POST /v1/questions/entities/{entity_type}/{entity_id}": (
        "The target record must be readable; the check uses a risk, so risk.read applies."
    ),
    "GET /v1/questions/entities/{entity_type}/{entity_id}": "As above, for reading questions.",
    "GET /v1/isms/bookstack-sections/{section_id}/snapshot": (
        "Audit readers can also download snapshots sampled into an audit."
    ),
    "GET /v1/statement-of-applicability": "Risk, PESTLE(E) and party sections follow their read permissions.",
    "GET /v1/stats/summary": "Evidence totals need events.read.",
    "GET /v1/stories/{story_id}": (
        "Each item is shown only when the account can read the referenced record; "
        "other items appear as placeholders without the record's identifier."
    ),
    "GET /v1/stories/related/{kind}/{entity_id}": (
        "The referenced record must be readable by the account, as on its own page."
    ),
    "PATCH /v1/stories/{story_id}": "stories.manage can edit stories its holder created.",
    "DELETE /v1/stories/{story_id}": "stories.manage can delete stories its holder created.",
    "POST /v1/stories/{story_id}/items": (
        "stories.manage can add to stories its holder created; each item needs read "
        "access to the referenced event or record."
    ),
    "PATCH /v1/stories/{story_id}/items/{item_id}": "As above, for the story's own items.",
    "DELETE /v1/stories/{story_id}/items/{item_id}": "As above.",
    "PUT /v1/stories/{story_id}/layout": "As above; positions and links name the story's own items.",
    "DELETE /v1/stories/{story_id}/comments/{comment_id}": (
        "The comment's author can delete it; stories.admin can delete any comment."
    ),
    "GET /v1/stats/controls": "Evidence counts need events.read.",
    "GET /v1/quizzes/{quiz_id}": "quiz.read sees the questions without their answers.",
    "GET /v1/quizzes/{quiz_id}/results": (
        "quiz.read sees rounds that have closed with every response marked and at "
        "least three completed, without names, answers or option choices."
    ),
    "PUT /v1/quizzes/{quiz_id}/answers/{answer_id}/grade": (
        "A quiz manager cannot grade their own answers."
    ),
    "POST /v1/me/quizzes/rounds/{round_id}/attempt": (
        "The round must be assigned to the account's linked People record."
    ),
    "GET /v1/me/quizzes/attempts/{attempt_id}": (
        "The attempt must belong to the account's linked People record; scores and "
        "answers appear only as the quiz allows."
    ),
    "PUT /v1/me/quizzes/attempts/{attempt_id}/pages/{page}": (
        "As above, while the attempt is in progress and its round is open."
    ),
    "POST /v1/me/quizzes/attempts/{attempt_id}/submit": "As above.",
    "GET /v1/controls/{control_id}/evidence/summary": (
        "Diary entries count only when the account can see them, as in the evidence list."
    ),
}

OUTSIDE_SCHEMA = {
    "GET /openapi.json",
    "GET /docs",
    "GET /docs/oauth2-redirect",
    "GET /redoc",
    "GET /v1/openapi.json",
    "GET /v1/docs",
    "GET /v1/docs/oauth2-redirect",
    "GET /v1/redoc",
    "WEBSOCKET /ws/notifications",
}

POLICY = {
    **{key: OUTSIDE for key in OUTSIDE_SCHEMA},
    # --- Public: credentials or ceremonies checked by the route itself ----------
    "GET /health": PUBLIC,
    "GET /v1/auth/methods": PUBLIC,
    "GET /v1/auth/logout": PUBLIC,
    "POST /v1/auth/logout": PUBLIC,
    "POST /v1/auth/login": PUBLIC,
    "GET /v1/auth/oidc/start": PUBLIC,
    "GET /v1/auth/oidc/callback": PUBLIC,
    "GET /v1/auth/oidc/logout": PUBLIC,
    "POST /v1/auth/oidc/logout": PUBLIC,
    "GET /v1/auth/sso/{provider_key}/start": PUBLIC,
    "GET /v1/auth/sso/{provider_key}/callback": PUBLIC,
    "POST /v1/auth/mfa/manage/start": PUBLIC,
    "POST /v1/auth/mfa/state": PUBLIC,
    "POST /v1/auth/mfa/verify-code": PUBLIC,
    "POST /v1/auth/mfa/totp/start": PUBLIC,
    "POST /v1/auth/mfa/totp/confirm": PUBLIC,
    "POST /v1/auth/mfa/webauthn/options": PUBLIC,
    "POST /v1/auth/mfa/webauthn/verify": PUBLIC,
    "POST /v1/auth/mfa/webauthn/register-options": PUBLIC,
    "POST /v1/auth/mfa/webauthn/register": PUBLIC,
    "POST /v1/auth/mfa/remove": PUBLIC,
    "POST /v1/auth/mfa/recovery/regenerate": PUBLIC,
    "POST /v1/auth/mfa/finish": PUBLIC,
    "POST /v1/auth/mfa/cancel": PUBLIC,
    "POST /v1/otlp/logs": PUBLIC,
    "POST /v1/agents/heartbeat": PUBLIC,
    "POST /v1/agents/enroll": PUBLIC,
    "POST /v1/agents/renew": PUBLIC,
    "POST /v1/webhooks/{provider}/{event_type}": PUBLIC,
    "POST /v1/webhooks/connections/{connection_id}/{provider}/{event_type}": PUBLIC,
    "POST /v1/webhooks/isms/effectiveness-metrics/{metric_key}": PUBLIC,
    # --- Session checks used by nginx before serving pages -------------------------
    "GET /v1/auth/check": SIGNED_IN,
    # Audit-trail readers reach the administration page's audit-trail view.
    "GET /v1/auth/check-admin": AUDIT_TRAIL,
    # --- Administration ------------------------------------------------------------
    "GET /v1/admin/adapter-settings/{adapter}": ADMIN,
    "PUT /v1/admin/adapter-settings/{adapter}": ADMIN,
    "GET /v1/admin/agent-enrollment-profiles": ADMIN,
    "POST /v1/admin/agent-enrollment-profiles": ADMIN,
    "POST /v1/admin/agent-enrollment-profiles/{ident}/{action}": ADMIN,
    "GET /v1/admin/agents": ADMIN,
    "POST /v1/admin/agents": ADMIN,
    "PATCH /v1/admin/agents/{agent_id}": ADMIN,
    "POST /v1/admin/agents/{agent_id}/revoke": ADMIN,
    "POST /v1/admin/agents/{agent_id}/rotate": ADMIN,
    "GET /v1/admin/agents/{agent_id}/evidence-bundle": ADMIN,
    "PATCH /v1/admin/agents/{agent_id}/evidence": ADMIN,
    "GET /v1/admin/audit": AUDIT_TRAIL,
    "GET /v1/admin/bookstack/catalog": ADMIN,
    "PATCH /v1/admin/clauses/{clause_id}/controls": ADMIN,
    "PATCH /v1/admin/clauses/{clause_id}/evidence-urls": ADMIN,
    "PATCH /v1/admin/clauses/{clause_id}/upstream-url": ADMIN,
    "GET /v1/admin/configuration-revisions/{name}": ADMIN,
    "POST /v1/admin/configuration-revisions/{name}/{revision}/restore": ADMIN,
    "POST /v1/admin/control-suggestions": ADMIN,
    "POST /v1/admin/controls/import": ADMIN,
    "PATCH /v1/admin/controls/{control_id}/clauses": ADMIN,
    "PATCH /v1/admin/controls/{control_id}/justification": ADMIN,
    "PATCH /v1/admin/controls/{control_id}/upstream-url": ADMIN,
    "POST /v1/admin/diary": ADMIN,
    "PATCH /v1/admin/diary/{event_id}": ADMIN,
    "GET /v1/admin/entity-changelog": AUDIT_TRAIL,
    "GET /v1/admin/evidence-connections/{key}/removal": ADMIN,
    "POST /v1/admin/evidence-connections/{key}/remove": ADMIN,
    "GET /v1/admin/evidence-definitions": ADMIN,
    "PUT /v1/admin/evidence-definitions/{rule_id}": ADMIN,
    "GET /v1/admin/evidence-event-samples": ADMIN,
    "GET /v1/admin/evidence-retention": ADMIN,
    "PUT /v1/admin/evidence-retention": ADMIN,
    "POST /v1/admin/evidence-retention/cancel": ADMIN,
    "POST /v1/admin/evidence-retention/preview": ADMIN,
    "POST /v1/admin/evidence-retention/preview-all": ADMIN,
    "POST /v1/admin/evidence-retention/purge": ADMIN,
    "POST /v1/admin/evidence-retention/retry-storage": ADMIN,
    "GET /v1/admin/framework-selection": ADMIN,
    "PUT /v1/admin/framework-selection": ADMIN,
    "GET /v1/admin/frameworks/{slug}/nodes": ADMIN,
    "DELETE /v1/admin/frameworks/{slug}/nodes/{kind}/{ref}": ADMIN,
    "PUT /v1/admin/frameworks/{slug}/nodes/{kind}/{ref}": ADMIN,
    "GET /v1/admin/groups": ADMIN,
    "POST /v1/admin/groups": ADMIN,
    "DELETE /v1/admin/groups/{group_id}": ADMIN,
    "GET /v1/admin/groups/{group_id}": ADMIN,
    "PATCH /v1/admin/groups/{group_id}": ADMIN,
    "PUT /v1/admin/groups/{group_id}/members": ADMIN,
    "PUT /v1/admin/groups/{group_id}/permissions": ADMIN,
    "POST /v1/admin/ingest/bookstack/run": ADMIN,
    "POST /v1/admin/ingest/cloudwatch_logs/run": ADMIN,
    "POST /v1/admin/ingest/forgejo/run": ADMIN,
    "POST /v1/admin/ingest/gitea/run": ADMIN,
    "POST /v1/admin/ingest/github/run": ADMIN,
    "POST /v1/admin/ingest/gitlab/run": ADMIN,
    "POST /v1/admin/ingest/google_workspace/run": ADMIN,
    "POST /v1/admin/ingest/jenkins/run": ADMIN,
    "POST /v1/admin/ingest/loki/reset": ADMIN,
    "POST /v1/admin/ingest/loki/run": ADMIN,
    "POST /v1/admin/ingest/redmine/run": ADMIN,
    "POST /v1/admin/ingest/riskledger/run": ADMIN,
    "POST /v1/admin/ingest/imap/run": ADMIN,
    "POST /v1/admin/ingest/rss/run": ADMIN,
    "GET /v1/admin/ingest/status": ADMIN,
    "POST /v1/admin/ingest/taiga/run": ADMIN,
    "GET /v1/admin/integrations": ADMIN,
    "POST /v1/admin/integrations/collectors": ADMIN,
    "PUT /v1/admin/integrations/collectors/{key}": ADMIN,
    "GET /v1/admin/integrations/collectors/{key}/revisions": ADMIN,
    "POST /v1/admin/integrations/collectors/{key}/{action}": ADMIN,
    "POST /v1/admin/integrations/connections": ADMIN,
    "PUT /v1/admin/integrations/connections/{key}": ADMIN,
    "POST /v1/admin/integrations/preview-sample": ADMIN,
    "GET /v1/admin/integrations/runs/{key}": ADMIN,
    "GET /v1/admin/managed-configurations": ADMIN,
    "GET /v1/admin/managed-configurations/{name}": ADMIN,
    "PUT /v1/admin/managed-configurations/{name}": ADMIN,
    "GET /v1/admin/mapping-rules": ADMIN,
    "GET /v1/admin/mapping-rules/export.yaml": ADMIN,
    "POST /v1/admin/mapping-rules/preview": ADMIN,
    "DELETE /v1/admin/mapping-rules/{rule_id}": ADMIN,
    "PUT /v1/admin/mapping-rules/{rule_id}": ADMIN,
    "POST /v1/admin/mapping-rules/{rule_id}/backfill": ADMIN,
    "GET /v1/admin/mapping-rules/{rule_id}/revisions": ADMIN,
    "POST /v1/admin/mapping-rules/{rule_id}/revisions/{revision}/restore": ADMIN,
    "GET /v1/admin/features": ADMIN,
    "PUT /v1/admin/features": ADMIN,
    "GET /v1/admin/permissions": ADMIN,
    "POST /v1/admin/permissions": ADMIN,
    "GET /v1/admin/questions": ADMIN,
    "GET /v1/admin/questions/summary": ADMIN,
    "PATCH /v1/admin/questions/{thread_id}": ADMIN,
    "POST /v1/admin/remap/run": ADMIN,
    "GET /v1/admin/rule-backfills": ADMIN,
    "GET /v1/admin/rule-backfills/{job_id}": ADMIN,
    "POST /v1/admin/rule-backfills/{job_id}/cancel": ADMIN,
    "GET /v1/admin/source-catalogue": ADMIN,
    "GET /v1/admin/source-connections": ADMIN,
    "POST /v1/admin/source-connections": ADMIN,
    "DELETE /v1/admin/source-connections/{key}": ADMIN,
    "PUT /v1/admin/source-connections/{key}": ADMIN,
    "POST /v1/admin/source-connections/{key}/run": ADMIN,
    "PUT /v1/admin/sources/{source}/ingestion-state": ADMIN,
    "GET /v1/admin/stale-rule-mappings": ADMIN,
    "POST /v1/admin/stale-rule-mappings/prune": ADMIN,
    "GET /v1/users": ADMIN,
    "POST /v1/users": ADMIN,
    "DELETE /v1/users/{user_id}": ADMIN,
    "PATCH /v1/users/{user_id}": ADMIN,
    "GET /v1/users/{user_id}/access": ADMIN,
    "PUT /v1/users/{user_id}/access": ADMIN,
    "POST /v1/users/{user_id}/password": ADMIN,
    # --- Audits --------------------------------------------------------------------
    "GET /v1/audits": AUDITS_READ,
    "POST /v1/audits": AUDITS_MANAGE,
    "GET /v1/audits/by-entity/{entity_type}/{entity_id}": AUDITS_READ,
    "GET /v1/audits/by-event/{event_id}": either(
        {"audits.read", "events.read"},
        {"audits.manage", "events.read"},
        {"audits.admin", "events.read"},
    ),
    "GET /v1/audits/scheduled": AUDITS_READ,
    "POST /v1/audits/scheduled": AUDITS_MANAGE,
    "DELETE /v1/audits/scheduled/{audit_id}": AUDITS_ADMIN,
    "PATCH /v1/audits/scheduled/{audit_id}": AUDITS_ADMIN,
    "GET /v1/audits/scope-coverage": AUDITS_READ,
    "GET /v1/audits/users": AUDITS_MANAGE,
    "DELETE /v1/audits/{audit_id}": AUDITS_ADMIN,
    "GET /v1/audits/{audit_id}": AUDITS_READ,
    "PATCH /v1/audits/{audit_id}": AUDITS_ADMIN,
    "POST /v1/audits/{audit_id}/attendees": AUDITS_ADMIN,
    "DELETE /v1/audits/{audit_id}/attendees/{attendee_id}": AUDITS_ADMIN,
    "PUT /v1/audits/{audit_id}/controls": AUDITS_ADMIN,
    "POST /v1/audits/{audit_id}/evidence": all_of("audits.admin", "events.read"),
    "DELETE /v1/audits/{audit_id}/evidence/{evidence_id}": AUDITS_ADMIN,
    "PATCH /v1/audits/{audit_id}/evidence/{evidence_id}": AUDITS_ADMIN,
    "POST /v1/audits/{audit_id}/findings": AUDITS_ADMIN,
    "DELETE /v1/audits/{audit_id}/findings/{finding_id}": AUDITS_ADMIN,
    "PATCH /v1/audits/{audit_id}/findings/{finding_id}": AUDITS_ADMIN,
    "GET /v1/audits/{audit_id}/report": AUDITS_READ,
    "POST /v1/audits/{audit_id}/report": AUDITS_ADMIN,
    "PUT /v1/audits/{audit_id}/scope": AUDITS_ADMIN,
    "GET /v1/audits/{audit_id}/scope-map": AUDITS_READ,
    # --- Events and evidence -------------------------------------------------------
    "GET /v1/events": EVENTS_READ,
    "GET /v1/events/export": EVENTS_READ,
    "GET /v1/events/facets": EVENTS_READ,
    "GET /v1/events/{event_id}": EVENTS_READ,
    "GET /v1/events/{event_id}/control-suggestions": EVENTS_READ,
    "GET /v1/events/{event_id}/ueu-bundle": EVENTS_READ,
    "POST /v1/events/{event_id}/incident": all_of("incident.create", "events.read"),
    "DELETE /v1/events/{event_id}/incidents/{incident_id}": all_of(
        "incident.delete", "events.read"
    ),
    "GET /v1/events/{event_id}/questions": EVENTS_READ,
    "POST /v1/events/{event_id}/questions": all_of("question.create", "events.read"),
    "POST /v1/events/{event_id}/questions/mark-seen": EVENTS_READ,
    # --- Frameworks, controls and other reads --------------------------------------
    "GET /v1/artifacts/{artifact_id}/download": EVENTS_READ,
    "GET /v1/artifacts/{artifact_id}/preview": EVENTS_READ,
    "GET /v1/charts/event_volume_timeseries": EVENTS_READ,
    "GET /v1/clauses": SIGNED_IN,
    "GET /v1/clauses/{clause_id}": SIGNED_IN,
    "GET /v1/clauses/{clause_id}/changelog": SIGNED_IN,
    "GET /v1/clauses/{clause_id}/controls": SIGNED_IN,
    "GET /v1/clauses/{clause_id}/isms": ISMS_READ,
    "GET /v1/clauses/{clause_id}/pestle": PESTLE_READ,
    "GET /v1/controls": SIGNED_IN,
    "GET /v1/controls/{control_id}": SIGNED_IN,
    "GET /v1/controls/{control_id}/changelog": SIGNED_IN,
    "GET /v1/controls/{control_id}/effectiveness-measures": ISMS_READ,
    "GET /v1/controls/{control_id}/evidence": EVENTS_READ,
    "GET /v1/controls/{control_id}/evidence/summary": EVENTS_READ,
    "GET /v1/controls/{control_id}/interested-parties": PARTIES_READ,
    "GET /v1/controls/{control_id}/isms": ISMS_READ,
    "GET /v1/controls/{control_id}/risks": RISK_READ,
    "GET /v1/controls/{target_id}/cross-framework": SIGNED_IN,
    "POST /v1/controls/{target_id}/cross-framework": ADMIN,
    "DELETE /v1/controls/{target_id}/cross-framework/{link_id}": ADMIN,
    "GET /v1/evidence/latest": EVENTS_READ,
    "GET /v1/frameworks": SIGNED_IN,
    "POST /v1/frameworks": ADMIN,
    "GET /v1/graph/clause_control": SIGNED_IN,
    "GET /v1/graph/clause_source": EVENTS_READ,
    "GET /v1/graph/control_source": EVENTS_READ,
    "GET /v1/graph/risk_control": RISK_READ,
    "GET /v1/home": SIGNED_IN,
    "GET /v1/home/controls": SIGNED_IN,
    "GET /v1/sources": EVENTS_READ,
    "GET /v1/sources/meta": EVENTS_READ,
    "GET /v1/sources/{source_name}/effectiveness-measures": ISMS_READ,
    "GET /v1/sources/{source_name}/insights": EVENTS_READ,
    "GET /v1/statement-of-applicability": SIGNED_IN,
    "GET /v1/stats/controls": SIGNED_IN,
    "GET /v1/stories": STORIES_READ,
    "POST /v1/stories": STORIES_MANAGE,
    "GET /v1/stories/choices/{kind}": STORIES_MANAGE,
    "GET /v1/stories/related/{kind}/{entity_id}": STORIES_READ,
    "GET /v1/stories/{story_id}": STORIES_READ,
    "PATCH /v1/stories/{story_id}": STORIES_MANAGE,
    "DELETE /v1/stories/{story_id}": STORIES_MANAGE,
    "GET /v1/stories/{story_id}/changelog": STORIES_READ,
    "POST /v1/stories/{story_id}/items": STORIES_MANAGE,
    "PATCH /v1/stories/{story_id}/items/{item_id}": STORIES_MANAGE,
    "DELETE /v1/stories/{story_id}/items/{item_id}": STORIES_MANAGE,
    "PUT /v1/stories/{story_id}/layout": STORIES_MANAGE,
    "GET /v1/stories/{story_id}/comments": STORIES_READ,
    "POST /v1/stories/{story_id}/comments": STORIES_READ,
    "DELETE /v1/stories/{story_id}/comments/{comment_id}": STORIES_READ,
    "GET /v1/stats/summary": SIGNED_IN,
    # --- Quizzes -------------------------------------------------------------------
    "GET /v1/quizzes": QUIZ_READ,
    "POST /v1/quizzes": QUIZ_MANAGE,
    "GET /v1/quizzes/calendar": QUIZ_READ,
    "GET /v1/quizzes/assignees": QUIZ_MANAGE,
    "GET /v1/quizzes/{quiz_id}": QUIZ_READ,
    "PATCH /v1/quizzes/{quiz_id}": QUIZ_MANAGE,
    "DELETE /v1/quizzes/{quiz_id}": QUIZ_MANAGE,
    "PUT /v1/quizzes/{quiz_id}/questions": QUIZ_MANAGE,
    "PUT /v1/quizzes/{quiz_id}/audience": QUIZ_MANAGE,
    "POST /v1/quizzes/{quiz_id}/rounds": QUIZ_MANAGE,
    "PATCH /v1/quizzes/{quiz_id}/rounds/{round_id}": QUIZ_MANAGE,
    "DELETE /v1/quizzes/{quiz_id}/rounds/{round_id}": QUIZ_MANAGE,
    "GET /v1/quizzes/{quiz_id}/results": QUIZ_READ,
    "GET /v1/quizzes/{quiz_id}/attempts/{attempt_id}": QUIZ_MANAGE,
    "GET /v1/quizzes/{quiz_id}/grading": QUIZ_MANAGE,
    "PUT /v1/quizzes/{quiz_id}/answers/{answer_id}/grade": QUIZ_MANAGE,
    # --- ISMS ----------------------------------------------------------------------
    "GET /v1/isms/access-control-matrix": ISMS_READ,
    "POST /v1/isms/access-control-matrix": ISMS_MANAGE,
    "DELETE /v1/isms/access-control-matrix/{entry_id}": ISMS_MANAGE,
    "GET /v1/isms/access-control-matrix/{entry_id}": ISMS_READ,
    "PATCH /v1/isms/access-control-matrix/{entry_id}": ISMS_MANAGE,
    "GET /v1/isms/asset-categories": ISMS_READ,
    "POST /v1/isms/asset-categories": ISMS_MANAGE,
    "POST /v1/isms/asset-subcategories": ISMS_MANAGE,
    "GET /v1/isms/assets": ISMS_READ,
    "POST /v1/isms/assets": ISMS_MANAGE,
    "DELETE /v1/isms/assets/{asset_id}": ISMS_MANAGE,
    "GET /v1/isms/assets/{asset_id}": ISMS_READ,
    "PATCH /v1/isms/assets/{asset_id}": ISMS_MANAGE,
    "GET /v1/isms/assurance/meta": ISMS_READ,
    "GET /v1/isms/aws-accounts": ISMS_READ,
    "POST /v1/isms/aws-accounts": ISMS_MANAGE,
    "DELETE /v1/isms/aws-accounts/{account_id}": ISMS_MANAGE,
    "PATCH /v1/isms/aws-accounts/{account_id}": ISMS_MANAGE,
    "GET /v1/isms/bookstack-catalog": ISMS_MANAGE,
    "GET /v1/isms/bookstack-sections": ISMS_READ,
    "POST /v1/isms/bookstack-sections": ISMS_MANAGE,
    "PATCH /v1/isms/bookstack-sections/{section_id}": ISMS_MANAGE,
    "GET /v1/isms/bookstack-sections/{section_id}/snapshot": any_of(
        "isms.read", "isms.manage"
    ),
    "GET /v1/isms/business-processes": ISMS_READ,
    "POST /v1/isms/business-processes": ISMS_MANAGE,
    "GET /v1/isms/document-folders": ISMS_READ,
    "POST /v1/isms/document-folders": ISMS_MANAGE,
    "DELETE /v1/isms/document-folders/{folder_id}": ISMS_MANAGE,
    "PATCH /v1/isms/document-folders/{folder_id}": ISMS_MANAGE,
    "GET /v1/isms/documents": ISMS_READ,
    "POST /v1/isms/documents": ISMS_MANAGE,
    "DELETE /v1/isms/documents/{document_id}": ISMS_MANAGE,
    "GET /v1/isms/documents/{document_id}": ISMS_READ,
    "PATCH /v1/isms/documents/{document_id}": ISMS_MANAGE,
    "GET /v1/isms/documents/{document_id}/comments": ISMS_READ,
    "POST /v1/isms/documents/{document_id}/comments": ISMS_MANAGE,
    "GET /v1/isms/documents/{document_id}/file": ISMS_READ,
    "POST /v1/isms/documents/{document_id}/file": ISMS_MANAGE,
    "GET /v1/isms/documents/{document_id}/revisions": ISMS_READ,
    "GET /v1/isms/documents/{document_id}/revisions/{version}": ISMS_READ,
    "GET /v1/isms/effectiveness-measures": ISMS_READ,
    "POST /v1/isms/effectiveness-measures": ISMS_MANAGE,
    "DELETE /v1/isms/effectiveness-measures/{measure_id}": ISMS_MANAGE,
    "GET /v1/isms/effectiveness-measures/{measure_id}": ISMS_READ,
    "PATCH /v1/isms/effectiveness-measures/{measure_id}": ISMS_MANAGE,
    "GET /v1/isms/effectiveness-measures/{measure_id}/metrics": ISMS_READ,
    "POST /v1/isms/effectiveness-measures/{measure_id}/metrics": ISMS_MANAGE,
    "DELETE /v1/isms/effectiveness-metrics/{entry_id}": ISMS_MANAGE,
    "GET /v1/isms/effectiveness-metrics/{entry_id}": ISMS_READ,
    "PATCH /v1/isms/effectiveness-metrics/{entry_id}": ISMS_MANAGE,
    "GET /v1/isms/licenses": ISMS_READ,
    "POST /v1/isms/licenses": ISMS_MANAGE,
    "DELETE /v1/isms/licenses/{license_id}": ISMS_MANAGE,
    "PATCH /v1/isms/licenses/{license_id}": ISMS_MANAGE,
    "GET /v1/isms/meetings": ISMS_READ,
    "POST /v1/isms/meetings": ISMS_MANAGE,
    "DELETE /v1/isms/meetings/{meeting_id}": ISMS_MANAGE,
    "GET /v1/isms/meetings/{meeting_id}": ISMS_READ,
    "PATCH /v1/isms/meetings/{meeting_id}": ISMS_MANAGE,
    "GET /v1/isms/meta": ISMS_READ,
    "GET /v1/isms/swot": ISMS_READ,
    "POST /v1/isms/swot": ISMS_MANAGE,
    "GET /v1/isms/swot-choices/{target_type}": ISMS_READ,
    "GET /v1/isms/swot/{item_id}": ISMS_READ,
    "PATCH /v1/isms/swot/{item_id}": ISMS_MANAGE,
    "DELETE /v1/isms/swot/{item_id}": ISMS_MANAGE,
    "POST /v1/isms/swot/{item_id}/links": ISMS_MANAGE,
    "DELETE /v1/isms/swot/{item_id}/links/{target_type}/{target_id}": ISMS_MANAGE,
    "GET /v1/isms/objectives": ISMS_READ,
    "POST /v1/isms/objectives": ISMS_MANAGE,
    "DELETE /v1/isms/objectives/{objective_id}": ISMS_MANAGE,
    "GET /v1/isms/objectives/{objective_id}": ISMS_READ,
    "PATCH /v1/isms/objectives/{objective_id}": ISMS_MANAGE,
    "GET /v1/isms/org-nodes": ISMS_READ,
    "POST /v1/isms/org-nodes": ISMS_MANAGE,
    "DELETE /v1/isms/org-nodes/{node_id}": RISK_MANAGE,
    "PATCH /v1/isms/org-nodes/{node_id}": ISMS_MANAGE,
    "GET /v1/isms/people": ISMS_READ,
    "GET /v1/isms/people/{person_id}": ISMS_READ,
    "GET /v1/isms/vendors/{vendor_id}": ISMS_READ,
    "GET /v1/isms/people/{person_id}/quizzes": Rule(
        "permissions", tuple(p | {"quiz.manage"} for p in ISMS_READ.alternatives)
    ),
    "GET /v1/isms/people/{person_id}/workstation-posture": Rule(
        "permissions", tuple(p | {"events.read"} for p in ISMS_READ.alternatives)
    ),
    "POST /v1/isms/people": ISMS_MANAGE,
    "DELETE /v1/isms/people/{person_id}": ISMS_MANAGE,
    "PATCH /v1/isms/people/{person_id}": ISMS_MANAGE,
    "POST /v1/isms/people/{person_id}/assurances": ISMS_MANAGE,
    "DELETE /v1/isms/people/{person_id}/assurances/{assurance_id}": ISMS_MANAGE,
    "PATCH /v1/isms/people/{person_id}/assurances/{assurance_id}": ISMS_MANAGE,
    "GET /v1/isms/summary": ISMS_READ,
    "GET /v1/isms/vendors": ISMS_READ,
    "POST /v1/isms/vendors": ISMS_MANAGE,
    "DELETE /v1/isms/vendors/{vendor_id}": ISMS_MANAGE,
    "PATCH /v1/isms/vendors/{vendor_id}": ISMS_MANAGE,
    "GET /v1/isms/{entity_type}/{entity_id}/changelog": ISMS_READ,
    # --- Interested parties --------------------------------------------------------
    "GET /v1/interested-parties": PARTIES_READ,
    "POST /v1/interested-parties": PARTIES_MANAGE,
    "GET /v1/interested-parties/meta": SIGNED_IN,
    "GET /v1/interested-parties/names": PARTIES_READ,
    "POST /v1/interested-parties/names": PARTIES_MANAGE,
    "DELETE /v1/interested-parties/names/{name_id}": PARTIES_MANAGE,
    "PATCH /v1/interested-parties/names/{name_id}": PARTIES_MANAGE,
    "GET /v1/interested-parties/natures": PARTIES_READ,
    "POST /v1/interested-parties/natures": PARTIES_MANAGE,
    "DELETE /v1/interested-parties/natures/{nature_id}": PARTIES_MANAGE,
    "PATCH /v1/interested-parties/natures/{nature_id}": PARTIES_MANAGE,
    "GET /v1/interested-parties/visualisations": PARTIES_READ,
    "DELETE /v1/interested-parties/{party_id}": PARTIES_MANAGE,
    "GET /v1/interested-parties/{party_id}": PARTIES_READ,
    "PATCH /v1/interested-parties/{party_id}": PARTIES_MANAGE,
    "GET /v1/interested-parties/{party_id}/changelog": PARTIES_READ,
    "GET /v1/interested-parties/{party_id}/communications": PARTIES_READ,
    "PATCH /v1/interested-parties/{party_id}/communications": PARTIES_MANAGE,
    "GET /v1/interested-parties/{party_id}/controls": PARTIES_READ,
    "PATCH /v1/interested-parties/{party_id}/controls": PARTIES_MANAGE,
    # --- PESTLE(E) -----------------------------------------------------------------
    "GET /v1/pestle/business-processes": PESTLE_READ,
    "POST /v1/pestle/business-processes": PESTLE_MANAGE,
    "DELETE /v1/pestle/business-processes/{process_id}": PESTLE_MANAGE,
    "PATCH /v1/pestle/business-processes/{process_id}": PESTLE_MANAGE,
    "GET /v1/pestle/business-processes/{process_id}/changelog": PESTLE_READ,
    "GET /v1/pestle/items": PESTLE_READ,
    "POST /v1/pestle/items": PESTLE_MANAGE,
    "DELETE /v1/pestle/items/{item_id}": PESTLE_MANAGE,
    "GET /v1/pestle/items/{item_id}": PESTLE_READ,
    "PATCH /v1/pestle/items/{item_id}": PESTLE_MANAGE,
    "GET /v1/pestle/items/{item_id}/business-processes": PESTLE_READ,
    "PATCH /v1/pestle/items/{item_id}/business-processes": PESTLE_MANAGE,
    "GET /v1/pestle/items/{item_id}/changelog": PESTLE_READ,
    "GET /v1/pestle/items/{item_id}/clauses": PESTLE_READ,
    "PATCH /v1/pestle/items/{item_id}/clauses": PESTLE_MANAGE,
    "GET /v1/pestle/meta": PESTLE_READ,
    "GET /v1/pestle/visualisations": PESTLE_READ,
    # --- Questions -----------------------------------------------------------------
    "GET /v1/questions/entities/{entity_type}/{entity_id}": RISK_READ,
    "POST /v1/questions/entities/{entity_type}/{entity_id}": either(
        {"question.create", "risk.read"}, {"question.create", "risk.manage"}
    ),
    "POST /v1/questions/entities/{entity_type}/{entity_id}/mark-seen": RISK_READ,
    "DELETE /v1/questions/{thread_id}": all_of("question.delete", "events.read"),
    "POST /v1/questions/{thread_id}/posts": ADMIN,
    # --- Risks ---------------------------------------------------------------------
    "GET /v1/risks": RISK_READ,
    "POST /v1/risks": RISK_MANAGE,
    "GET /v1/risks/assets": RISK_READ,
    "POST /v1/risks/assets": RISK_MANAGE,
    "DELETE /v1/risks/assets/{asset_id}": RISK_MANAGE,
    "PATCH /v1/risks/assets/{asset_id}": RISK_MANAGE,
    "GET /v1/risks/categories": RISK_READ,
    "POST /v1/risks/categories": RISK_MANAGE,
    "DELETE /v1/risks/categories/{category_id}": RISK_MANAGE,
    "PATCH /v1/risks/categories/{category_id}": RISK_MANAGE,
    "GET /v1/risks/controls-without-risks": RISK_READ,
    "GET /v1/risks/library": RISK_READ,
    "POST /v1/risks/library": RISK_MANAGE,
    "DELETE /v1/risks/library/{item_id}": RISK_MANAGE,
    "POST /v1/risks/mitigator/analyse": RISK_MANAGE,
    "GET /v1/risks/register": RISK_READ,
    "GET /v1/risks/register/export.csv": RISK_READ,
    "GET /v1/risks/register/heatmap": RISK_READ,
    "POST /v1/risks/register/import.csv": RISK_MANAGE,
    "PATCH /v1/risks/register/settings": RISK_MANAGE,
    "GET /v1/risks/subcategories": RISK_READ,
    "POST /v1/risks/subcategories": RISK_MANAGE,
    "DELETE /v1/risks/subcategories/{subcategory_id}": RISK_MANAGE,
    "PATCH /v1/risks/subcategories/{subcategory_id}": RISK_MANAGE,
    "GET /v1/risks/users": RISK_MANAGE,
    "GET /v1/risks/parties": RISK_MANAGE,
    "DELETE /v1/risks/{risk_id}": RISK_MANAGE,
    "GET /v1/risks/{risk_id}": RISK_READ,
    "PATCH /v1/risks/{risk_id}": RISK_MANAGE,
    "GET /v1/risks/{risk_id}/changelog": RISK_READ,
    "GET /v1/risks/{risk_id}/controls": RISK_READ,
    "PATCH /v1/risks/{risk_id}/controls": RISK_MANAGE,
    "PATCH /v1/risks/{risk_id}/register": RISK_MANAGE,
    # --- The signed-in user's own data ---------------------------------------------
    "GET /v1/me": SIGNED_IN,
    "GET /v1/me/audits": SIGNED_IN,
    "GET /v1/me/isms-assets": SIGNED_IN,
    "GET /v1/me/isms-meetings": SIGNED_IN,
    "GET /v1/me/isms-objectives": SIGNED_IN,
    "GET /v1/me/isms-role": SIGNED_IN,
    "GET /v1/me/mfa": SIGNED_IN,
    "POST /v1/me/password": SIGNED_IN,
    "GET /v1/me/preferences": SIGNED_IN,
    "PATCH /v1/me/preferences": SIGNED_IN,
    "GET /v1/me/questions": SIGNED_IN,
    "GET /v1/me/questions/summary": SIGNED_IN,
    "GET /v1/me/quizzes": SIGNED_IN,
    "POST /v1/me/quizzes/rounds/{round_id}/attempt": SIGNED_IN,
    "GET /v1/me/quizzes/attempts/{attempt_id}": SIGNED_IN,
    "PUT /v1/me/quizzes/attempts/{attempt_id}/pages/{page}": SIGNED_IN,
    "POST /v1/me/quizzes/attempts/{attempt_id}/submit": SIGNED_IN,
    "GET /v1/me/risks": SIGNED_IN,
    "GET /v1/me/saved-searches": SIGNED_IN,
    "POST /v1/me/saved-searches": SIGNED_IN,
    "DELETE /v1/me/saved-searches/{saved_search_id}": SIGNED_IN,
    "GET /v1/me/sso-emails": SIGNED_IN,
    "POST /v1/me/sso-emails/confirm": SIGNED_IN,
    "POST /v1/me/sso-emails/remove": SIGNED_IN,
    "POST /v1/me/sso-emails/request": SIGNED_IN,
}
