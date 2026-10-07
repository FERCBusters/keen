# Evidence retention and purging

Open **Administration → Evidence retention**. Only administrators can preview,
change retention or start a purge. Settings apply to the whole KEEN database,
across all sources and frameworks. Retention starts disabled.

Choose one policy:

- **Keep everything:** no automatic deletion.
- **Maximum event age:** delete events whose event timestamp is older than the
  chosen number of days, measured in UTC. Late-arriving old events qualify too.
- **Maximum number of events:** retain the newest N unsampled events by event
  timestamp (UUID breaks ties), plus every audit-protected event. This is a soft
  total limit: audit holds and references from retained evidence can exceed it.

Preview shows current eligibility. Saving an enabled policy confirms automatic,
permanent deletion. The Celery scheduler checks once a minute; the worker deletes
in bounded batches and continues larger jobs in the background. API, worker and
beat must all run the updated release and share the same database. Worker must
have the same local artifact volume or S3 access as API.

## Audit protection

Sampling an event creates a persistent retention hold for that audit. Existing
samples receive holds during migration. Removing a sample from the displayed
list does **not** remove its hold. Delete the audit to release the hold. Where
several audits sampled an event, all those audits must be deleted first. Events
linked to sampled effectiveness measurements are protected too; sampling a whole
measure also protects its linked measurement events.

Administrators can delete an audit from **Audits → Delete**, or its detail page's
**Delete audit** action. This deletes the audit, its scope, findings, attendees
and sample links. Its final report is queued for storage cleanup. Underlying
events become eligible for a later retention pass or purge; deleting an audit
alone does not delete those events. Finished and archived audits can also be
deleted by administrators. Scheduled templates use their existing delete action.

Holds cannot reconstruct samples removed before this feature was installed.
External evidence URLs are not downloaded or deleted. KEEN protects explicitly
linked event records; links embedded in arbitrary prose are not parsed as samples.

## Purge all

Type **PURGE EVIDENCE**, then choose **Preview and purge all evidence** and confirm.
The job covers events received up to its start time, preserving audit-held events.
New ingestion continues; pause agents and ingesters if you want an empty workspace.
Collectors can replay deleted records according to their upstream history windows;
purging does not reset or advance collection cursors.

Deletion removes events, their mappings, artifact metadata and stored artifact
files (including rendered artifacts and event question attachments). Event
questions and event incident links are deleted with their event. The separate
ISMS records (including measurements), framework definitions, mapping rules,
source configuration, credentials and administrative audit trail remain.
Measurement entries lose their source-event link when an unprotected originating
event is deleted. Shared artifact objects and retained artifact lineage are kept
while another record refers to them. Some older events therefore become eligible
only after their dependent events have been removed.

Cancel stops future database batches. It cannot undo deletion or cancel storage
cleanup already committed. To stop automatic retention, save **Keep everything**.

## Storage cleanup and recovery

Database deletion and its storage-deletion instructions commit in one database
transaction. The cleanup queue survives worker crashes, restarts and S3 outages.
Cleanup status separately shows removed database events and pending stored files;
“complete” describes the database job, not a guarantee that all S3 objects are gone.

Local files are removed from the configured artifact directory. Version-pinned
S3 objects are deleted by exact version ID, avoiding delete markers and preserving
other versions. Unversioned objects in unversioned buckets are removed normally.
Legacy URIs without a version ID in a versioned/suspended bucket are held for
operator review: KEEN cannot safely guess which version belongs to that record.
They remain visible in pending cleanup until the exact object version is resolved.

S3 Object Lock and legal holds are respected. KEEN never requests a governance
bypass. Locked or inaccessible objects remain queued, with bounded retries up to
once a day. After correcting IAM, connectivity or an expired storage lock, choose
**Retry stored-file cleanup**. Existing shared references retry hourly.

Grant the worker `s3:DeleteObject` and `s3:DeleteObjectVersion` for its artifact
object prefix, plus `s3:GetBucketVersioning` on the artifact bucket when cleaning
legacy unversioned URIs. Existing write-only evidence IAM policies need updating.
This patch changes only KEEN; it does not alter portal infrastructure IAM.
Do not configure an independent S3 lifecycle rule to remove audit-held evidence.
KEEN cannot override lifecycle policies, administrator actions, backups or replicas.
Deletion here covers KEEN's primary database and configured artifact storage.

For older versionless URIs, resolve their exact versions using the original object
metadata/checksum, then have an operator repair the queued URI. Do not bulk-delete
all bucket versions: other retained evidence may refer to them.

## Deployment and verification

Back up the database, deploy the new API/worker/beat/UI, and run the normal
`alembic upgrade head` migration before allowing retention tasks to run. Migration
0083 creates disabled settings, persistent audit holds, job history and the storage
cleanup queue. It performs no evidence deletion. Stop old worker/beat processes
during the upgrade so the release is consistent.

Database aggregate counters continue to use KEEN's existing deletion triggers;
retention also invalidates dashboard caches. Job errors and pending cleanup appear
on the retention screen. With no worker/scheduler, queued jobs stay queued.

The included integration tests run in a temporary PostgreSQL schema using
`KEEN_RETENTION_TEST_DATABASE_URL`. They require permission to create/drop that
schema. Use a dedicated development database. From `services/api`:

```sh
KEEN_RETENTION_TEST_DATABASE_URL=postgresql+psycopg2://... \
  python -m unittest discover -s tests -p test_evidence_retention.py -v
```

Normal KEEN development environment settings are also required for imports.
