"""Automatic diary entries for every seeded record with framework links.

Saving a record's framework links in the application keeps one diary entry per
record, and the entry's mappings count the linked controls towards control
coverage. The seed writes links directly, so this step records each entry as
the application does: for documents, SWOT items, objectives, assets,
organisation chart nodes, meeting minutes, effectiveness measures, risks and
interested parties alike.
"""

from datetime import timedelta

from app.db.models import (
    Event,
    InterestedParty,
    IsmsDocument,
    IsmsEffectivenessMeasure,
    IsmsMeeting,
    IsmsObjective,
    IsmsOrgNode,
    IsmsSwot,
    Risk,
    RiskAsset,
)
from app.security.diary_visibility import DIARY_SOURCE, link_entry_external_id
from app.services.isms.link_diary import sync_entity_link_diary

# Record types that keep an automatic entry, with the model of each record.
# Other link types, such as access matrix rows, have no entry in the app.
RECORDS = {
    "document": IsmsDocument,
    "swot": IsmsSwot,
    "objective": IsmsObjective,
    "asset": RiskAsset,
    "org_node": IsmsOrgNode,
    "meeting": IsmsMeeting,
    "effectiveness_measure": IsmsEffectivenessMeasure,
    "risk": Risk,
    "interested_party": InterestedParty,
}


def _entry(db, external_id):
    return (
        db.query(Event)
        .filter_by(source=DIARY_SOURCE, external_id=external_id)
        .one_or_none()
    )


def seed_link_diary(seeder):
    """Record the automatic diary entry of each seeded record with links.

    The application's own link synchronisation writes each entry, its JSON
    artefact and its automatic mappings. A new entry takes the time its record
    was linked; a later run leaves existing entries to the application. A dry
    run skips this step because artefacts are stored outside the database
    transaction.
    """
    if seeder.config.dry_run:
        seeder.stats["link diary entries.skipped in dry run"] += 1
        return
    db = seeder.db
    for record_type, record_id in sorted(
        seeder.linked_records, key=lambda record: (record[0], str(record[1]))
    ):
        model = RECORDS.get(record_type)
        if model is None:
            continue
        external_id = link_entry_external_id(record_type, record_id)
        existed = _entry(db, external_id) is not None
        sync_entity_link_diary(db, record_type, record_id, seeder.user)
        event = _entry(db, external_id)
        if event is None:
            continue
        name = record_type.replace("_", " ")
        if existed:
            seeder.stats[f"{name} link diary entries.unchanged"] += 1
            continue
        linked = db.get(model, record_id).created_at + timedelta(minutes=12)
        if linked < event.timestamp:
            event.timestamp = linked
            db.flush()
        seeder.stats[f"{name} link diary entries.created"] += 1
