"""Keen Mitigator's interested-party drafts against the existing register.

A party is a duplicate only when both its name and its nature of interest
match the suggestion; a party with the same name and another nature is
offered as a related match and the draft stays available to create.
"""

from app.db.models import InterestedParty, InterestedPartyName, InterestedPartyNature

from tests.test_risk_mitigator_analysis import _analyse

ISSUE = "The regulator may fine us after a customer database leak."


def _party(db, name, nature):
    row = InterestedParty(
        name_id=_named(db, InterestedPartyName, name).id,
        nature_id=_named(db, InterestedPartyNature, nature).id,
    )
    db.add(row)
    db.flush()
    return row


def _named(db, model, name):
    row = db.query(model).filter_by(name=name).one_or_none()
    if row is None:
        row = model(name=name)
        db.add(row)
        db.flush()
    return row


def test_a_party_with_the_same_name_and_another_nature_is_not_a_duplicate(
    contract_db, monkeypatch
):
    oversight = _party(contract_db, "Regulators", "Oversight")

    result = _analyse(contract_db, monkeypatch, issue=ISSUE)

    (draft,) = result["interested_party_suggestions"]
    assert (draft["name"], draft["nature"]) == ("Regulators", "Enforcement")
    assert draft["duplicate_exact"] is False
    (related,) = result["existing_matches"]["interested_parties"]
    assert related["id"] == str(oversight.id)
    assert related["label"] == "Regulators - Oversight"
    # The name match alone earns the related party its place.
    assert related["score"] >= 8

    _party(contract_db, "Regulators", "Enforcement")
    again = _analyse(contract_db, monkeypatch, issue=ISSUE)
    assert again["interested_party_suggestions"][0]["duplicate_exact"] is True
