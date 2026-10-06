"""Explainable matching against the live risk library, with no external AI service.

Only the user's issue drives relevance. Asset context breaks ties; CIA defaults
and expanded control vocabulary cannot fabricate a library match.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

_STOP = set('a an and are as at be been being by can could do does for from had has have how if in into is it its may might no not of on or our should that the their them there these they this to was we were what when which who will with without would you your lack failure loss risk risks security information management process processes system systems organisation organization company business ensure use using'.split())
_ALIASES = {'policies': 'policy', 'responsibilities': 'responsibility', 'vulnerabilities': 'vulnerability', 'unauthorized': 'unauthorised', 'authorization': 'authorisation', 'organizations': 'organisation', 'organization': 'organisation', 'employees': 'staff', 'employee': 'staff', 'suppliers': 'supplier', 'vendors': 'supplier', 'vendor': 'supplier', 'backups': 'backup', 'backed': 'backup', 'stolen': 'theft', 'leaving': 'leaver', 'former': 'leaver', 'auditing': 'audit', 'hallucinating': 'hallucination', 'hallucinations': 'hallucination', 'restoration': 'restore', 'restoring': 'restore', 'restored': 'restore', 'patching': 'patch', 'patched': 'patch', 'phishing': 'phishing', 'passwords': 'password', 'credentials': 'credential', 'laptops': 'laptop', 'devices': 'device', 'contracts': 'contract', 'agreements': 'agreement', 'privileges': 'privilege', 'privileged': 'privilege', 'training': 'training'}


def catalogue_tokens(value: Any) -> set[str]:
    words = re.findall(r"[a-z0-9]+", str(value or '').lower())
    result = set()
    for word in words:
        if word in _STOP or word.isdigit() or (len(word) < 3 and word not in {'ai', 'av'}):
            continue
        word = _ALIASES.get(word, word)
        if len(word) > 4 and word.endswith('s') and not word.endswith(('ss', 'us')):
            word = word[:-1]
        if word not in _STOP:
            result.add(word)
    return result


def _value(row, key, default=''):
    return row.get(key, default) if isinstance(row, dict) else getattr(row, key, default)


def rank_library(entries, *, issue: str, asset_name: str = '', rules=None, limit=6):
    """Rank current templates by distinctive issue words and explicit synonyms.

    Return reviewable candidates, never apply their ratings or create records.
    Two matching concepts are required unless a single word is rare in the corpus.
    """
    query = catalogue_tokens(issue)
    if not query:
        return []
    expanded = set(query)
    expansions = []
    for group in (rules or {}).get('catalogue_synonyms', []):
        members = [catalogue_tokens(term) for term in group]
        triggers = [term for term in members if term and term <= query]
        if triggers:
            values = set().union(*members)
            expanded.update(values)
            expansions.append((set().union(*triggers), members))
    documents = []
    frequency = Counter()
    for entry in entries:
        # Deliberately exclude mapped controls: scoring them here feeds the answer
        # back into the query and overstates unrelated scenarios' relevance.
        tokens = catalogue_tokens(' '.join(str(_value(entry, key)) for key in ('name', 'threat_summary', 'treatment_guidance')))
        documents.append((entry, tokens))
        frequency.update(tokens)
    count = len(documents)
    if not count:
        return []
    asset_tokens = catalogue_tokens(asset_name)
    matches = []
    for entry, tokens in documents:
        hits = query & tokens
        # Distinct input concepts, not the number of synonyms, establish relevance.
        direct = query & tokens
        concepts = set(direct)
        for triggers, members in expansions:
            matched_members = [member for member in members if member and member <= tokens]
            if matched_members:
                concepts.update(triggers)
                hits.update(set().union(*matched_members))
        if not concepts:
            continue
        rare = any(frequency[word] <= max(2, count * .04) for word in hits)
        if len(concepts) < 2 and not rare:
            continue
        score = sum(4 * (1 + math.log((count + 1) / (frequency[word] + 1))) for word in direct)
        synonym_only = hits - direct
        if synonym_only:
            score += max(2 * (1 + math.log((count + 1) / (frequency[word] + 1))) for word in synonym_only)
        score += 6 * len(query & catalogue_tokens(_value(entry, 'threat_summary')))
        assessment = _value(entry, 'suggested_assessment', {}) or {}
        score += min(4, len(asset_tokens & catalogue_tokens(assessment.get('asset', ''))) * 2)
        if 'keen_af_control_refs' in assessment:
            # An explicitly cleared current mapping must not resurrect seed refs.
            refs = assessment['keen_af_control_refs']
            refs = [ref for ref in refs if isinstance(ref, str)] if isinstance(refs, list) else []
        else:
            refs = re.findall(r'\b[A-Z]{2,5}-\d+\b', str(assessment.get('auditor_control_refs', '')))
        refs = sorted(set(refs))
        matches.append({
            'id': str(_value(entry, 'id', _value(entry, 'reference'))),
            'name': _value(entry, 'name'), 'threat_summary': _value(entry, 'threat_summary'),
            'treatment_guidance': _value(entry, 'treatment_guidance'),
            'risk_types': _value(entry, 'risk_types', []), 'suggested_assessment': assessment,
            'control_refs': refs, 'score': round(score, 2),
            'matched_terms': sorted(hits),
            'reason': 'Your scenario matches words or configured synonyms in this reusable risk template. Review applicability and example ratings before use.',
        })
    matches.sort(key=lambda row: (-row['score'], row['name'], row['id']))
    return matches[:limit]
