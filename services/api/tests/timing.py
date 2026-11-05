"""Time budgets for the work a test measures.

A full garbage collection walks every object the test process holds. Late in
a session that takes longer than many budgets allow, and it runs in whichever
block of work happens to allocate when one falls due, so a budget timed with a
bare clock fails at random. Every time budget in the tests is measured here.
"""

import gc
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass
class Timing:
    seconds: float = 0.0


@contextmanager
def measured(clock: Callable[[], float] = time.process_time) -> Iterator[Timing]:
    """Time a block by ``clock``, charging it only for collecting its own objects.

    The objects that exist as the block starts are frozen out of garbage
    collection until it ends, so a collection the block sets off walks what
    the block allocated. ``Timing.seconds`` holds the block's time once it ends.
    """
    timing = Timing()
    gc.freeze()
    started = clock()
    try:
        yield timing
    finally:
        timing.seconds = clock() - started
        gc.unfreeze()
