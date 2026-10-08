# KEEN 1.0.0

## Framework catalogue

Fresh installations run two migrations: `0001_release_schema` and
`0002_release_seed`. They install 88 OSA frameworks, 3,608 OSA clause/control
entries and 23,140 NIST associations, plus KEEN Assurance, DVSTF, Cyber Essentials
and the Risk Ledger catalogue placeholder. Only **KEEN Assurance Framework** is
enabled by default. Explicit environment defaults/restrictions are respected;
all other catalogues remain available for administrators to enable.

Framework selectors show human-friendly names while IDs remain stable. Admin →
Frameworks includes Select all and Deselect all; Save applies the pending choices.
Environment-excluded frameworks cannot be selected. At least one enabled
framework and an enabled default are required.

Existing installations at `0090_osa_catalogue` must follow
[migration adoption](migration-consolidation.md#existing-installations) before
starting the new application. Adoption verifies the schema and changes only the
Alembic marker; existing catalogues, edits, evidence and saved selections remain.
Do not delete the database, rerun seeds, or stamp without verification.
Earlier versions must first reach `0090_osa_catalogue` using the previous release
candidate. Historical sources are retained outside Alembic's active versions folder.

OSA supplies HIPAA Security Rule (`hipaa_sr`); its published mapped catalogues
are not a claim of complete normative standard coverage. Ordinary restarts
never download catalogues or overwrite administrator edits.

OSA's canonical API associations link framework clauses through shared NIST
controls. These derived links expose related evidence in control details, event
views and coverage summaries. Counts deduplicate events and inheritance remains
one hop. A common NIST reference is not a declaration of full equivalence. OSA's
coverage percentages, rationale, gaps and provenance remain on each control.
KEEN's reviewed bridges to its retained catalogues are separately attributed.

Source snapshot: 8 October 2026, `opensecurityarchitecture/osa-data` commit
`73c978aec9d998d8abc51f650e9e051b8fa38fda`. The API's full framework list was fetched
from <https://www.opensecurityarchitecture.org/api/v1/frameworks>. Its mapping
counts were checked against every framework's control-level `compliance_mappings`
in the source dataset used to generate the API. Coverage cards disagree with the
API in parts of five frameworks; API associations drive KEEN's crosswalk, while
`coverage_controls` preserves the coverage cards' original lists.

Data from [Open Security Architecture](https://www.opensecurityarchitecture.org/),
licensed [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
KEEN's adapted catalogue and derived crosswalk are distributed under the same
terms. Adaptations include framework/control database records, descriptions
assembled from source fields, display-title shortening (full wording retained),
reference-based hierarchy and shared-NIST related-control links. See
`services/api/alembic/catalogues/OSA-LICENSE.txt`; KEEN's software licence is separate.

## Purge coordination

A manual purge pauses all collectors, agents and webhook intake when queued,
through deletion, and for five minutes after completion or cancellation. The
pause survives process restarts. Existing operator pause flags are never changed.
Queued collectors check the pause; already-running collectors are checked again
at evidence writes. Receivers return HTTP 503 with `Retry-After: 60`.

Application transactions take a shared PostgreSQL advisory transaction lock;
purge batches take its exclusive counterpart before any row locks. This lets
in-flight work finish before deletion and prevents ingestion/remapping from
interleaving with DELETEs. A batch waits at most five seconds for a lock and the
worker retries. A failed active purge keeps intake paused until it succeeds or
an administrator cancels it. Automatic retention also takes the exclusive batch
lock, but does not impose the manual purge's five-minute cooldown.

Start all API/worker/beat processes from the new image together; mixed old/new
workers do not share this coordination protocol. Audit holds and protected
artifacts remain protected. Sources can resend deleted data after intake resumes.

## GitHub Actions and GHCR

The workflow files belong at `.github/workflows/` in the KEEN repository root.
The Forgejo test workflow is replaced. Every upstream action is pinned to a full
commit SHA, with its major-version label in a comment. CI runs on GitHub-hosted
Ubuntu with a Debian container, PostgreSQL 16 and Valkey, using the existing
locked-dependency test script. The release workflow runs the tests for the exact
release ref before publishing.

When ready, commit the completed changes to the `FERCBusters` GitHub repository,
then create and push the tag:

```sh
git tag -a v1.0.0 -m 'KEEN 1.0.0'
git push origin v1.0.0
```

The tag triggers **Publish release images**. It builds AMD64 and ARM64 images,
pushes commit-SHA candidate tags and adds these version tags after both builds
succeed:

- `ghcr.io/fercbusters/keen-api:1.0.0` — API, worker and beat use the same image.
- `ghcr.io/fercbusters/keen-ui:1.0.0` — static frontend.

No registry password secret is needed: the workflow uses `GITHUB_TOKEN` with
`packages: write`. GitHub org policy must allow that permission. After the first
publish, set both GHCR packages to **Public** if users should pull anonymously;
otherwise they need a registry login with package read access. Publishing a GitHub
Release for the existing tag is optional; the tag push drives image publication.
The workflow can also be dispatched manually with an existing `vMAJOR.MINOR.PATCH`
tag to retry. Do not move a release tag to different source code.

The release version is checked against `VERSION`, `services/api/pyproject.toml`
and `services/ui/package.json`. For subsequent releases, update these, the API
version in `app/main.py`, the UI lockfile version, and Compose/example image tags.

Compose defaults to the versioned GHCR URLs. Configure `.env` and start with:

```sh
docker compose pull
docker compose up -d
```

For an existing installation, stop API, worker and beat before applying the new
images, then start the stack. The API runs Alembic before becoming healthy;
worker and beat wait for that health check. Image names can be overridden with
`KEEN_API_IMAGE` and `KEEN_UI_IMAGE`; upgrade both together.

For local development builds:

```sh
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

The UI fetches the public `FERCBusters/mosp-design-system` repository as an npm
Git dependency, pinned to commit `6aa799dcab70dee51267459041b1783a850443c9`
in `package.json` and `package-lock.json`. `npm ci` needs no registry credentials
or sibling checkout. The Dockerfile builds the package's assets, and its licence
ships with the frontend assets. The portal's image builder uses the same workflow;
its deployment still builds ECR images, while GHCR is the default for standalone
Compose. To update the design system, select and review a new upstream commit,
update the npm dependency and regenerate the lockfile before a release.

The upstream design-system commit currently lacks the dark-theme and statistics
helpers included in the supplied platform archive. A small reviewed source patch
in `services/ui/patches/mosp-design-system.patch` preserves those features during
the npm build. It applies only to that pinned dependency and fails the build if
it no longer applies. Upstream table-sorting improvements are retained. Remove or
refresh this patch when those changes are merged into the design-system repo.

## Mapping-rule revisions

The rule editor previously displayed global rules-configuration snapshot numbers
and restored the entire rules document. It now lists only changes to the selected
rule, numbered from 1, and restores only that rule. Edits to other rules and
no-op saves do not inflate its history. Deleting and recreating an ID starts a
new history. Existing recorded snapshots supply this history without a database
migration; unrecorded YAML edits cannot be reconstructed. The global version is
still used internally for concurrent-edit protection. Historical evidence is not
automatically remapped by restoring a rule.

## Read performance

Dashboard and control-statistics fast paths now check for actual inherited
evidence from enabled frameworks rather than merely the existence of catalogue
cross-references. Unmapped events do not generate cross-framework mapping rows.
An agent backlog still incurs database, artefact storage and rule-evaluation work.
Observe queue drain rate and active database queries before increasing concurrency.
The change addresses an identified query path; it does not prove the cause of
an unobserved CPU spike on an existing installation.
