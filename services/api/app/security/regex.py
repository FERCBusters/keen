"""The one boundary through which Keen compiles regular expressions.

Patterns written in Keen's code compile through scan_pattern, which rejects a
shape whose matching memory or backtracking grows with the text it scans.
Supplied match patterns compile through compile_pattern: mapping rules and
selectors match in Python under a runtime timeout; event filters match in
PostgreSQL, whose statements their callers bound.
"""

import re
import time
from dataclasses import dataclass
from functools import lru_cache
from re import _parser  # type: ignore[attr-defined]  # CPython's parser has no stub
from typing import Any, Literal, overload

import regex

# A greedy or lazy repeat of anything wider than one character keeps
# backtracking state for every repetition, about a hundred bytes each, so a few
# megabytes of matching text would take hundreds of megabytes. A possessive
# repeat keeps none, and a bounded one keeps at most its bound, multiplied by
# the bounds of the repeats around it.
SCAN_REPEATS_HELD = 1024
# Python's engine matches one character, any character, a set or a class
# without keeping state per repetition.
_ONE_CHARACTER = {_parser.ANY, _parser.IN, _parser.LITERAL, _parser.NOT_LITERAL}
_REPEATS = (_parser.MAX_REPEAT, _parser.MIN_REPEAT, _parser.POSSESSIVE_REPEAT)
_HELD_REPEAT = (
    "Repeat a group, capture or alternatives possessively (*+, ++ or {m,n}+), "
    f"or at most {SCAN_REPEATS_HELD} times in all: matching keeps state for "
    "every repetition"
)
_NESTED_REPEAT = (
    "Make an unbounded repeat inside another repeat possessive: nested repeats "
    "backtrack exponentially"
)

MATCH_TIMEOUT_SECONDS = 0.025
# A pattern reads at most this many characters of a value: the length of the
# field values rule suggestions offer and of a field condition's own value. A
# search costs up to the square of its input, so evidence of any length leaves
# an ordinary pattern far inside the time budget.
MATCH_WINDOW_CHARS = 4096
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
# PostgreSQL matches event-filter patterns with its own engine. It reads a
# backslash and digits as a back-reference once that many groups precede them,
# where Python reads an octal escape, and back-references and lookaround
# constraints take the most time there as the text grows. Both are found in the
# pattern's text, so a reading that differs from Python's cannot hide them.
_DATABASE_BACKREFERENCE = re.compile(r"\\[1-9]")
_DATABASE_LOOKAROUND = ("(?=", "(?!", "(?<=", "(?<!")
# In expanded mode Python reads a{1, 2} as literal text, while the regex
# package and PostgreSQL read a quantifier, so the checks below would not see
# what actually runs.
_EXPANDED_MODE = (
    "Expanded mode (the x flag) is unsupported; write the pattern without it"
)


def _one_character(items):
    if len(items) != 1:
        return False
    op, value = items[0]
    if op == _parser.SUBPATTERN:
        return value[0] is None and _one_character(value[-1])
    return op in _ONE_CHARACTER


def check_scan_pattern(pattern, flags=0):
    """Reject a code pattern whose matching memory or backtracking can grow.

    The pattern holds state for at most SCAN_REPEATS_HELD repetitions of
    anything wider than one character, and an unbounded repeat inside another
    repeat is possessive unless the outer repeat is.
    """
    try:
        parsed = _parser.parse(pattern, flags)
    except (re.error, RecursionError) as exc:
        raise ValueError(f"Invalid pattern: {exc}") from exc

    def check(items, held, repeated):
        for op, value in items:
            if op in _REPEATS:
                high, body = value[1], value[2]
                if op == _parser.POSSESSIVE_REPEAT:
                    # Each repetition commits, so its state lasts one repetition.
                    check(body, 1, False)
                    continue
                unbounded = high == _parser.MAXREPEAT
                if unbounded and repeated:
                    raise ValueError(_NESTED_REPEAT)
                inner = held
                if not _one_character(body):
                    inner = SCAN_REPEATS_HELD + 1 if unbounded else held * high
                    if inner > SCAN_REPEATS_HELD:
                        raise ValueError(_HELD_REPEAT)
                check(body, inner, repeated or high > 1)
            elif op == _parser.SUBPATTERN:
                check(value[-1], held, repeated)
            elif op == _parser.ATOMIC_GROUP:
                check(value, held, repeated)
            elif op == _parser.BRANCH:
                for branch in value[1]:
                    check(branch, held, repeated)
            elif op in (_parser.ASSERT, _parser.ASSERT_NOT):
                check(value[1], held, repeated)
            elif op == _parser.GROUPREF_EXISTS:
                check(value[1], held, repeated)
                if value[2] is not None:
                    check(value[2], held, repeated)

    check(parsed, 1, False)


@overload
def scan_pattern(
    pattern: str, flags: int = 0, *, timed: Literal[False] = False
) -> re.Pattern[str]: ...
@overload
def scan_pattern(
    pattern: bytes, flags: int = 0, *, timed: Literal[False] = False
) -> re.Pattern[bytes]: ...
@overload
def scan_pattern(pattern: str, flags: int = 0, *, timed: Literal[True]) -> Any: ...
def scan_pattern(pattern, flags=0, *, timed=False):
    """Compile a pattern written in Keen's code once it passes the scan checks.

    Every regular expression in the application compiles here, at import for a
    fixed pattern, so a pattern that could hold memory or backtrack in step
    with its input never runs. With timed=True the pattern compiles with the
    regex package, whose matches accept a timeout.
    """
    return _scan_compiled(pattern, flags, timed)


@lru_cache(maxsize=512)
def _scan_compiled(pattern, flags, timed):
    check_scan_pattern(pattern, flags)
    if timed:
        return regex.compile(pattern, flags | regex.VERSION0)
    return re.compile(pattern, flags)


def validate_pattern(pattern):
    if len(pattern) > 512:
        raise ValueError("Pattern must be at most 512 characters")
    try:
        parsed = _parser.parse(pattern, 0)
    except (re.error, RecursionError) as exc:
        raise ValueError(f"Invalid pattern: {exc}") from exc
    if parsed.state.flags & re.VERBOSE:
        raise ValueError(_EXPANDED_MODE)

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
                if value[1] & re.VERBOSE:
                    raise ValueError(_EXPANDED_MODE)
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


def validate_database_pattern(pattern):
    """Check a pattern that PostgreSQL matches, as event filters do.

    The pattern meets the mapping-rule checks and contains neither a
    back-reference nor a lookaround as PostgreSQL reads them.
    """
    validate_pattern(pattern)
    if _DATABASE_BACKREFERENCE.search(pattern):
        raise ValueError(
            "Backreferences are unsupported, and the database reads a backslash "
            "followed by a digit as one; write a character code as a hexadecimal "
            "escape such as \\x41"
        )
    if any(construct in pattern for construct in _DATABASE_LOOKAROUND):
        raise ValueError("Lookahead and lookbehind are unsupported")


class MatchBudgetExceeded(ValueError):
    """A match used its time budget: the pattern, not the input, needs changing.

    The input is already bounded by MATCH_WINDOW_CHARS, so only a pattern whose
    cost grows steeply with its input reaches this. Evidence callers treat it as
    no match for that one rule, so one record never halts a collection run.
    """


@dataclass(frozen=True)
class BoundedPattern:
    compiled: Any  # a regex.Pattern; the regex package ships no type hints

    def search(self, value):
        value = value[:MATCH_WINDOW_CHARS]
        for _ in range(MATCH_ATTEMPTS):
            started = time.thread_time()
            try:
                return self.compiled.search(
                    value, timeout=MATCH_TIMEOUT_SECONDS, concurrent=MATCH_CONCURRENTLY
                )
            except TimeoutError:
                if time.thread_time() - started >= MATCH_TIMEOUT_SECONDS:
                    break
        raise MatchBudgetExceeded(
            "Regex match exceeded its time budget; simplify the rule"
        )


@lru_cache(maxsize=256)
def compile_pattern(pattern):
    validate_pattern(pattern)
    try:
        return BoundedPattern(regex.compile(pattern, regex.VERSION0))
    except (regex.error, RecursionError) as exc:
        raise ValueError("Invalid match pattern") from exc
