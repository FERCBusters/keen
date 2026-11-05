"""Rebuilding the framework event counters after bulk remapping.

By default the rebuild first clears the cached aggregates and mirrored
control statistics that the remapping made stale, then stores fresh
counters. Callers that clear the caches themselves ask it not to.
"""

from app.core.cache import make_cache_key
from app.core.config import settings
from app.services import control_evidence_stats as stats

# Imported fixture: evidence mapped to controls, with Valkey replaced in memory.
from tests.test_control_evidence_stats import FW, evidence  # noqa: F401


def test_rebuilding_counters_clears_stale_caches_unless_asked_not_to(evidence):  # noqa: F811
    redis = evidence.redis
    aggregate = make_cache_key("dashboard", {"framework": FW})
    mirror = stats.control_evidence_stats_cache_key(FW)
    counters = make_cache_key(
        "stats:framework-event-counts",
        stats._framework_event_counts_cache_parts(settings.default_framework_slug),
    )
    redis.set(aggregate, "{}")
    redis.hset(mirror, "stale", "1")

    kept = stats.rebuild_framework_event_stats(evidence.db, clear_cache=False)

    assert redis.get(aggregate) == "{}"
    assert redis.hgetall(mirror) == {"stale": "1"}
    assert redis.get(counters) is not None

    rebuilt = stats.rebuild_framework_event_stats(evidence.db)

    assert rebuilt == kept and rebuilt["framework_event_counter_cache"] >= 1
    assert redis.get(aggregate) is None
    assert redis.hgetall(mirror) == {}
    # The fresh counters are stored after the clear.
    assert redis.get(counters) is not None
