"""Every time budget in the tests is measured through tests.timing.measured."""

import ast
import gc
import time
from pathlib import Path

from tests.timing import measured

TESTS = Path(__file__).resolve().parent
CLOCKS = frozenset({"monotonic", "perf_counter", "process_time", "thread_time"})


def _clock_call(node) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr.removesuffix("_ns") in CLOCKS and (
            isinstance(func.value, ast.Name) and func.value.id == "time"
        )
    return isinstance(func, ast.Name) and func.id.removesuffix("_ns") in CLOCKS


def hand_timed(source: str) -> list[int]:
    """Lines that time an interval with a bare clock.

    A clock reading less a recorded start, ``time.process_time() - started``,
    is a measured interval. In a ``while`` condition it paces a loop, such as
    a test server dripping a response or work spinning until a deadline.
    """
    tree = ast.parse(source)
    pacing = {
        id(node)
        for loop in ast.walk(tree)
        if isinstance(loop, ast.While)
        for node in ast.walk(loop.test)
    }
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Sub)
        and _clock_call(node.left)
        and isinstance(node.right, (ast.Name, ast.Attribute))
        and id(node) not in pacing
    )


def test_time_budgets_are_measured_through_the_shared_timer():
    found = [
        f"{path.relative_to(TESTS).as_posix()}:{line}"
        for path in sorted(TESTS.rglob("*.py"))
        if path.name != "timing.py"
        for line in hand_timed(path.read_text())
    ]
    assert found == [], (
        "Time these with tests.timing.measured, which keeps collection of "
        "the rest of the test process out of the budget: " + ", ".join(found)
    )


def test_the_check_finds_a_budget_timed_with_a_bare_clock():
    source = (
        "started = time.process_time()\n"
        "work()\n"
        "assert time.process_time() - started < 1.0\n"
        "elapsed = perf_counter() - self.began\n"
        "while time.monotonic() - started < DRIP_SECONDS:\n"
        "    drip()\n"
        "client.deadline = time.monotonic() - 1\n"
    )
    assert hand_timed(source) == [3, 4]


def test_measured_work_collects_only_the_objects_it_allocates():
    before = [[] for _ in range(1_000)]
    with measured(time.perf_counter) as timing:
        assert gc.get_freeze_count() >= len(before)
        made = [[] for _ in range(1_000)]
    assert gc.get_freeze_count() == 0
    assert len(made) == len(before) and 0 < timing.seconds
