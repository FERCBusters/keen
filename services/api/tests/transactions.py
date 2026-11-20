"""Whether a collector holds a transaction open while an upstream answers.

Every transaction takes the shared ingestion lease, which an evidence purge
must have to itself, so a collector ends its transaction before each request
and none is left open while the remote service answers.
"""


def open_while_answering(monkeypatch, upstream: type, db) -> list[bool]:
    """Record, for each request ``upstream`` answers, whether ``db`` had a
    transaction open. ``upstream`` is a test's stand-in API class, whose
    instances answer requests when called."""
    seen: list[bool] = []
    answer = upstream.__call__

    def watched(self, request):
        seen.append(db.in_transaction())
        return answer(self, request)

    monkeypatch.setattr(upstream, "__call__", watched)
    return seen
