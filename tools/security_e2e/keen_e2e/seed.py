"""Synthetic records created through the admin API and removed afterwards.

Every text field carries hostile payloads, so the same records serve the
authorisation checks and the browser rendering checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Seed:
    ids: dict = field(default_factory=dict)
    pages: list = field(default_factory=list)  # (label, path) for browser checks


def _id(response):
    body = response.json()
    for key in ("id", "risk_id", "audit_id", "item_id"):
        if isinstance(body, dict) and body.get(key):
            return str(body[key])
    for key in ("risk", "audit", "item", "party", "objective", "document", "meeting"):
        nested = body.get(key) if isinstance(body, dict) else None
        if isinstance(nested, dict) and nested.get("id"):
            return str(nested["id"])
    raise AssertionError("Created record has no id")


def build(admin, payloads, cleanup, *, evidence_writes=False):
    """Create one record of each kind, each carrying payloads in its text fields."""
    p, seed = payloads, Seed()
    text, short = p.text(), p.short()

    def create(label, path, body, delete_prefix, page=None, ok=(200, 201)):
        response = admin.ok(admin.post(path, json=body), *ok)
        ident = _id(response)
        seed.ids[label] = ident
        cleanup.add(f"{delete_prefix}/{ident}")
        if page:
            seed.pages.append((label, page.format(id=ident)))
        return ident

    # Organisation role first so a risk can be owned by it.
    create(
        "org_role",
        "/api/v1/isms/org-nodes",
        {"name": short, "node_type": "role", "description": text},
        "/api/v1/isms/org-nodes",
        "/isms.html?tab=org",
    )
    create(
        "risk",
        "/api/v1/risks",
        {
            "asset_name": short,
            "category_name": short[:100],
            "subcategory_name": short[:100],
            "risk_types": ["confidentiality"],
            "threat_summary": text,
            "note": text,
            "risk_owner_role_id": seed.ids["org_role"],
        },
        "/api/v1/risks",
        "/risk.html?id={id}",
    )
    seed.pages += [("risks", "/risks.html"), ("register", "/risk-register.html")]
    create(
        "pestle",
        "/api/v1/pestle/items",
        {"type": "Political", "lens": "Internal", "item": text, "rationale": text},
        "/api/v1/pestle/items",
        "/pestle_item.html?id={id}",
    )
    seed.pages.append(("pestle_list", "/pestle.html"))
    create(
        "party",
        "/api/v1/interested-parties",
        {"name": short, "nature": short, "note": text[:3900]},
        "/api/v1/interested-parties",
        "/interested_party.html?id={id}",
    )
    seed.pages.append(("parties", "/interested_parties.html"))
    create(
        "objective",
        "/api/v1/isms/objectives",
        {"requirement": text, "goal": text, "metric": text, "status": "not_started"},
        "/api/v1/isms/objectives",
        "/isms-objective.html?id={id}",
    )
    create(
        "document",
        "/api/v1/isms/documents",
        {
            "title": short,
            "description": text,
            "content_html": text,
            "tags": [short[:60]],
        },
        "/api/v1/isms/documents",
        "/isms-document.html?id={id}",
    )
    create(
        "meeting",
        "/api/v1/isms/meetings",
        {
            "title": short,
            "date": "2026-01-01",
            "agenda_minutes_notes": text,
            "links": [{"title": short, "url": "https://example.org/" + p.run_id}],
        },
        "/api/v1/isms/meetings",
        "/isms-meeting.html?id={id}",
    )
    seed.pages += [("isms", "/isms.html"), ("documents", "/documents.html")]
    create(
        "story",
        "/api/v1/stories",
        {
            "title": short,
            "summary": text,
            "items": [
                {
                    "kind": "risk",
                    "entity_id": seed.ids["risk"],
                    "label": short,
                    "note": text,
                },
                {"kind": "document", "entity_id": seed.ids["document"], "note": text},
                {"kind": "note", "label": short, "note": text},
            ],
        },
        "/api/v1/stories",
        "/story.html?id={id}",
    )
    # Deleting a story names the version it was last seen at; comments leave it.
    cleanup.items[-1] += "?expected_version=1"
    admin.ok(
        admin.post(
            f"/api/v1/stories/{seed.ids['story']}/comments", json={"body": text}
        ),
        200,
        201,
    )
    seed.pages.append(("stories", "/stories.html"))
    create(
        "audit",
        "/api/v1/audits",
        {"title": short, "notes": text, "executive_summary": text},
        "/api/v1/audits",
        "/audit.html?id={id}",
    )
    admin.ok(
        admin.post(
            f"/api/v1/audits/{seed.ids['audit']}/findings",
            json={"kind": "ofi", "title": short, "description": text},
        ),
        200,
        201,
    )
    for link in (
        "/" + "\\" + "evil.example/" + p.run_id,
        "https://example.org/" + p.run_id,
    ):
        admin.post(
            f"/api/v1/audits/{seed.ids['audit']}/evidence",
            json={"evidence_url": link, "title": short, "notes": text},
        )
    seed.pages += [
        ("audits", "/audits.html"),
        ("soa", "/statement-of-applicability.html"),
    ]

    if evidence_writes:
        # Evidence records have no delete route; they remain, labelled with the run ID.
        response = admin.ok(
            admin.post(
                "/api/v1/admin/diary",
                json={
                    "summary": text[:900],
                    "details": {"note": text, "nested": {"x": short}},
                    "links": [
                        {"url": "https://example.org/" + p.run_id, "text": short}
                    ],
                },
            ),
            200,
            201,
        )
        event_id = str(response.json().get("event_id") or response.json().get("id"))
        seed.ids["event"] = event_id
        seed.pages += [
            ("event", f"/event.html?id={event_id}"),
            ("events", "/events.html"),
        ]
    return seed
