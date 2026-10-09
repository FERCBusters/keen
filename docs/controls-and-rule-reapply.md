# Incremental controls and rule reapplication fix

Apply this patch to the code from the previously delivered maintainability
archive (`keen2.tar.gz`). It does not repeat that refactor.

- Related controls are grouped by framework. Choose a framework to see its
  controls, ten at a time. Rationale is available under “Why related?”. Direct
  links remain removable by admins; derived relationships are read-only.
- “Apply saved rule to past evidence” now reconciles existing mappings with exact
  system-rule provenance. Removed targets or nonmatching events lose that rule's
  mapping unless another active saved rule still justifies it. Manual/imported
  mappings and mappings with unrecognized provenance are retained. Previously
  matched events are revisited even if the source or collector changes. Cache
  invalidation runs after deletions and updates, as well as insertions.
- The source-wide option is now “All collected events (any collection)”. An inline
  explanation distinguishes matching from collection, reports missing collections,
  and offers “Set up collections”. Forgejo's empty state and ingester message
  explain how to add a user, organisation or repository feed.

The mapping bug was persisted database state: the old worker only inserted
missing mappings and never reconciled old ones. A surviving control may still
be justified by another rule or by cross-framework inheritance; this change
preserves those relationships.

After applying, rebuild/redeploy the UI, API and Celery worker so all processes
use the new code. No database migration is needed. Save the changed rule, run
“Apply saved rule to past evidence”, wait for completion and refresh the event.
The existing job counter reports newly created mappings, not the number removed.

Verification: 765 backend tests passed (plus 16 unittest subtests); 184 frontend
tests passed. The same 42 PostgreSQL-dependent tests remain skipped in this local
environment, so the full PostgreSQL/Valkey GitHub Actions gate remains required.
New regression tests exercise removal, overlapping rules, manual/imported
mapping protection, changed conditions/source, batching, superseded jobs,
framework pagination, safe rendering and Forgejo collection setup.
