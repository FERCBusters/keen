# ISO 9001 edition catalogues

## Installation and scope

Apply the incremental patch from the platform root (the directory containing keen/ and portal/), then deploy the rebuilt KEEN API image and run the normal `alembic upgrade head` migration. API startup also runs migrations. Deploy the regenerated UI and portal help with the respective images.

Migration `0003_iso_9001` follows `0002_release_seed`. It installs both catalogues on fresh and existing databases. It inserts missing records, preserves administrator edits and leaves enabled/default framework selections unchanged. Enable the desired editions in Admin → Frameworks. If the deployment sets a KEEN_ENABLED_FRAMEWORKS allowlist, include the corresponding slugs there as well.

| Framework slug | Edition | Nodes |
| --- | --- | --- |
| iso_9001_2015 | ISO 9001:2015 including Amd 1:2024 | 37 |
| iso_9001_2026 | ISO 9001:2026 | 39 |

These are clause-level overview catalogues: clauses 4–10, their immediate subclauses, corrective-action subdivisions 10.2.1/10.2.2, and the 2026 subdivisions of 6.1. Each node has an independently worded summary, parent and source link. Detailed requirement paragraphs, definitions and informative annexes are available from the referenced standard. Clause numbers retain their edition-specific meaning. The two catalogues have independent node IDs, scope and mappings. No automatic equivalence or cross-framework evidence links are seeded.

Both FrameworkClause and ControlItem records are populated, supporting the clause tree, editor and mapping rules. The total installed framework catalogue is 94; the 88 OSA imports are unchanged. ISO 9001 summaries are KEEN editorial content, separately identified from OSA data.

## Edition comparison

ISO lists edition 6 as published on 16 September 2026. The following are the principal changes reflected in these overview entries and their context:

| Area | 2015 / Amd 1:2024 | 2026 |
| --- | --- | --- |
| Climate | The 2024 amendment affects 4.1 and 4.2. | Incorporated into the edition. |
| Leadership and people | Established leadership, support and awareness requirements. | Explicit quality culture and ethical behaviour, including leadership, awareness and process environment. |
| Risk and opportunity | Combined treatment in 6.1. | 6.1.1 determines risks/opportunities; 6.1.2 addresses risks; 6.1.3 addresses opportunities. |
| Change | Planned QMS changes under 6.3. | Stronger communication, monitoring, effectiveness evaluation and review. |
| Knowledge | Knowledge for processes and product/service conformity. | Broader connection to intended QMS results. |
| Improvement | 10.1 general improvement and 10.3 continual improvement. | Consolidated under 10.1; corrective action remains 10.2. |
| Reference material | ISO 9000 supplies vocabulary; Annex B lists related standards. | Core terms also appear in clause 3; Annex A expands explanations; Annex B is removed. |

The published 2026 contents also use “Quality management system” for 4.4, “Roles, responsibilities and authorities” for 5.3 and “Management review results” for 9.3.3. The catalogue represents 9.3 at overview level. Annex A supplies informative explanations of the requirements.

## Corrections to the supplied outline

- Added the missing first-level subclauses in Support (7.1–7.5), Operation (8.3–8.7) and Performance evaluation (9.1–9.3).
- 5.3 assigns and communicates responsibility and authority. A mandatory documented role-assignment record is not stated by that clause itself.
- 8.1 concerns operational planning and control; design and external provision have their own entries at 8.3 and 8.4.
- Removed the update-style description of 10.2.2; its entry describes the retained corrective-action evidence.
- Identified the climate amendment explicitly in the 2015 entries so it is not presented as originating in 2026.

## Sources checked on 10 October 2026

- ISO edition/status: https://www.iso.org/standard/9001
- ISO 2015 edition: https://www.iso.org/standard/62085.html
- 2024 amendment: https://www.iso.org/standard/88431.html
- ISO overview of the revision: https://www.iso.org/quality-management/iso-9001-2026
- ISO published sample/contents: https://cdn.standards.iteh.ai/samples/iso/iso-9001-2026/f019c820593640a0a84140a0acb8be6d/iso-9001-2026.pdf
- DNV's revision guidance: https://www.dnv.com/assurance/Management-Systems/new-iso/transition/iso-9001-revision/
- CQI's revision guidance: https://knowledge.quality.org/article/iso-90012026-revision-guidance

Descriptions are implementation summaries. Rewording alone does not establish copyright permission; normative ISO text has not been bundled.

## Validation

- Backend: 773 tests and 16 subtests passed using the local SQLite fallback; 42 PostgreSQL-dependent tests skipped.
- Frontend: 188 tests passed via tests-ui.sh.
- Shared help: both link/bookmark/navigation and mirror checks passed.
- Catalogue tests check edition-specific references, parent hierarchy, both clause representations, mapping-rule targets, selection preservation and repeat-seed preservation of local edits.
- The PostgreSQL CI release migration test checks the original 0002 snapshot, then upgrades to head and verifies 94 frameworks and the two new clause inventories. This PostgreSQL check remains to be run in CI.
- Patch application was checked against the preceding working tree and byte-compared with the resulting modified files.
