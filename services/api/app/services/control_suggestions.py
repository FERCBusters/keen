"""Deterministic evidence-to-control relevance, using the live catalogue.

Mapping names carry more weight than optional sample text. Related phrases are
matched one hop through the curated taxonomy, with word boundaries and no
recursive expansion. No model, external API, regex from users or writes occur.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from functools import lru_cache

from app.services.evidence_taxonomy import CONCEPTS

_STOP = set(
    "a an and are as at be been by can for from has have in into is it its of on or our that the their this to was we were with without your all activity activities event events evidence control controls security information management system systems process processes review record report collection collected successful success failed failure demonstrate demonstrates ensure ensures general".split()
)
_ALIASES = {
    "organisation": "organization",
    "organisational": "organizational",
    "authorisation": "authorization",
    "unauthorised": "unauthorized",
    "synchronisation": "synchronization",
    "centralised": "centralized",
    "sanitisation": "sanitization",
    "pseudonymisation": "pseudonymization",
    "anonymisation": "anonymization",
    "labelling": "labeling",
    "patching": "patch",
    "patched": "patch",
    "restoring": "restore",
    "restored": "restore",
    "backups": "backup",
}


def normalise(text: str) -> str:
    words = re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold())
    result = []
    for word in words:
        if len(word) > 4 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif (
            len(word) > 3
            and word.endswith("s")
            and not word.endswith(("ss", "us", "is"))
        ):
            word = word[:-1]
        result.append(_ALIASES.get(word, word))
    return " ".join(result)


def _tokens(text):
    return {
        w for w in text.split() if len(w) >= 2 and not w.isdigit() and w not in _STOP
    }


def _has(text, phrase):
    return f" {phrase} " in f" {text} "


@lru_cache(maxsize=1)
def _taxonomy():
    return tuple(
        (
            c,
            tuple(
                (raw, phrase)
                for phrase, raw in {
                    normalise(raw): raw for raw in reversed(c.aliases + c.targets)
                }.items()
            ),
            tuple((raw, normalise(raw)) for raw in c.targets),
        )
        for c in CONCEPTS
    )


def _strings(value):
    # Only bounded human-readable values; never include URLs, IDs or metadata keys.
    if isinstance(value, str):
        return [value[:8000]]
    if isinstance(value, list):
        return [v[:1000] for v in value[:100] if isinstance(v, str)]
    return []


def _catalogue(control):
    meta = control.meta if isinstance(control.meta, dict) else {}
    values = []
    for key in ("description", "justification", "guidance", "objective", "keywords"):
        values.extend(_strings(meta.get(key)))
    tags = control.tags
    if isinstance(tags, dict):
        for value in list(tags.values())[:100]:
            values.extend(_strings(value))
    else:
        values.extend(_strings(tags))
    return normalise(control.title or ""), normalise(" ".join(values)[:24000])


def rank_controls(
    controls, *, description: str, sample_summary: str = "", limit: int = 10
):
    documents = [(c, *_catalogue(c)) for c in controls]
    frequency = Counter()
    for _, title, body in documents:
        frequency.update(_tokens(title + " " + body))
    queries = [
        ("mapping name", normalise(description[:4000]), 1.0),
        ("sample", normalise(sample_summary[:4000]), 0.25),
    ]
    prepared = []
    for origin, text, weight in queries:
        active = []
        for concept, aliases, targets in _taxonomy():
            triggers = [raw for raw, phrase in aliases if _has(text, phrase)]
            if triggers:
                # Prefer the most specific trigger for the explanation.
                active.append(
                    (
                        concept,
                        max(
                            triggers, key=lambda x: (len(normalise(x).split()), len(x))
                        ),
                        targets,
                    )
                )
        prepared.append((origin, _tokens(text), weight, active))
    ranked = []
    for control, title, body in documents:
        title_tokens, body_tokens = _tokens(title), _tokens(body)
        score = name_score = 0.0
        direct = set()
        related = []
        for origin, query, weight, active in prepared:
            hits = query & (title_tokens | body_tokens)
            direct.update(hits)
            direct_score = sum(
                (3 if hit in title_tokens else 1)
                * (1 + math.log((len(documents) + 1) / (frequency[hit] + 1)))
                for hit in hits
            )
            concept_score = 0
            for concept, trigger, targets in active:
                matches = [
                    (raw, field)
                    for field, text in (("title", title), ("description", body))
                    for raw, phrase in targets
                    if _has(text, phrase)
                ]
                if not matches:
                    continue
                target, field = min(
                    matches, key=lambda m: (m[1] != "title", -len(m[0]))
                )
                concept_score += (
                    (
                        12
                        + 6
                        * len(normalise(target).split())
                        / max(1, len(title.split()))
                    )
                    if field == "title"
                    else 4
                )
                related.append(
                    {
                        "concept": concept.name,
                        "query_term": trigger,
                        "catalogue_term": target,
                        "origin": origin,
                    }
                )
            contribution = weight * (min(direct_score, 24) + min(concept_score, 48))
            score += contribution
            if origin == "mapping name":
                name_score = contribution
        if not score:
            continue
        terms = sorted(direct)[:8]
        reasons = []
        if terms:
            reasons.append("Catalogue words: " + ", ".join(terms))
        if related:
            reasons.append(
                "Related concepts: "
                + "; ".join(
                    f"{r['query_term']} → {r['catalogue_term']} ({r['origin']})"
                    for r in related[:4]
                )
            )
        ranked.append(
            {
                "id": str(control.id),
                "framework": control.framework_slug,
                "ref": control.ref,
                "title": control.title,
                "score": round(score, 3),
                "matched_terms": terms,
                "matched_concepts": related[:6],
                "reason": ". ".join(reasons) + ".",
                "_name_score": name_score,
            }
        )
    ranked.sort(
        key=lambda row: (-row["_name_score"], -row["score"], row["ref"], row["id"])
    )
    for row in ranked:
        row.pop("_name_score")
    return ranked[:limit]
