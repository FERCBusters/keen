"""One bounded execution boundary for administrator-supplied match patterns."""

import re
import time
from dataclasses import dataclass
from functools import lru_cache
from re import _parser  # type: ignore[attr-defined]  # CPython's parser has no stub
from typing import Any

import regex

MATCH_TIMEOUT_SECONDS = 0.025
MAX_INPUT_CHARS = 65536
# The regex package measures a timeout in processor time for the whole process.
# A match that released the interpreter lock would wait for it again while other
# threads ran, and their work would count against its budget, so a trivial match
# could time out. Matches keep the lock; other threads' Python work is not
# charged to them.
MATCH_CONCURRENTLY = False
# Native work elsewhere in the process, such as hashing or TLS, still counts
# against a match's budget, so a timeout stops the run only once the matching
# thread has itself used the budget; until then the match is tried again, at
# most this many times in all.
MATCH_ATTEMPTS = 3


def validate_pattern(pattern):
    if len(pattern) > 512:
        raise ValueError("Pattern must be under 512 characters")
    try:
        parsed = _parser.parse(pattern, 0)
    except (re.error, RecursionError) as exc:
        raise ValueError(f"Invalid pattern: {exc}") from exc

    def check(items, repeated=False):
        unbounded = 0
        for op, value in items:
            if op in (
                _parser.MAX_REPEAT,
                _parser.MIN_REPEAT,
                _parser.POSSESSIVE_REPEAT,
            ):
                if value[1] != _parser.MAXREPEAT and value[1] > 1000:
                    raise ValueError("Bounded repetition must be at most 1000")
                if repeated:
                    raise ValueError("Nested repetition can stall ingestion")
                if value[1] == _parser.MAXREPEAT:
                    unbounded += 1
                check(value[2], repeated=True)
            elif op == _parser.ATOMIC_GROUP:
                check(value, repeated)
            elif op == _parser.SUBPATTERN:
                check(value[-1], repeated)
            elif op == _parser.BRANCH:
                if repeated:
                    raise ValueError("Repeated alternatives can stall ingestion")
                for branch in value[1]:
                    check(branch, repeated)
            elif op in (_parser.GROUPREF, _parser.GROUPREF_EXISTS):
                raise ValueError("Backreferences are unsupported")
            elif op in (_parser.ASSERT, _parser.ASSERT_NOT):
                check(value[1], repeated)
        if unbounded > 1:
            raise ValueError("Use one unbounded repetition at most")

    check(parsed)


@dataclass(frozen=True)
class BoundedPattern:
    compiled: Any  # a regex.Pattern; the regex package ships no type hints

    def search(self, value):
        if len(value) > MAX_INPUT_CHARS:
            raise ValueError("Regex input exceeds 65536 characters; narrow the input")
        for _ in range(MATCH_ATTEMPTS):
            started = time.thread_time()
            try:
                return self.compiled.search(
                    value, timeout=MATCH_TIMEOUT_SECONDS, concurrent=MATCH_CONCURRENTLY
                )
            except TimeoutError:
                if time.thread_time() - started >= MATCH_TIMEOUT_SECONDS:
                    break
        # Stop the run rather than silently dropping a required mapping.
        raise ValueError("Regex match exceeded its time budget; simplify the rule")


@lru_cache(maxsize=256)
def compile_pattern(pattern):
    validate_pattern(pattern)
    try:
        return BoundedPattern(regex.compile(pattern, regex.VERSION0))
    except (regex.error, RecursionError) as exc:
        raise ValueError("Invalid match pattern") from exc
