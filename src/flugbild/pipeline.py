"""Run frame stages on all CPU cores while keeping the frame order.

Every temporal stage computes an output frame from a short window of input
frames around it, never from its own earlier output. The frames of one stage
are therefore independent of each other and can be worked on side by side.
OpenCV and NumPy release Python's interpreter lock during their array work, so
threads scale across cores without copying frames between processes.

One shared pool serves every stage. Only the consuming thread waits for
results; pool threads never wait for other tasks, so chained stages cannot
block each other.
"""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
import os
import threading
from typing import Callable, Iterable, Iterator, TypeVar

import cv2


Item = TypeVar("Item")
Result = TypeVar("Result")

_lock = threading.Lock()
_pool: ThreadPoolExecutor | None = None


def workers() -> int:
    """Pool size; ``FLUGBILD_THREADS`` overrides the CPU count (1 = sequential)."""
    configured = os.environ.get("FLUGBILD_THREADS", "")
    count = int(configured) if configured.isdigit() and int(configured) > 0 else (os.cpu_count() or 2)
    return max(1, min(count, 32))


def pool() -> ThreadPoolExecutor | None:
    global _pool
    if workers() == 1:
        return None
    with _lock:
        if _pool is None:
            # Frames are worked on in parallel; OpenCV's own threads inside one
            # call would only compete with them.
            cv2.setNumThreads(1)
            _pool = ThreadPoolExecutor(workers(), thread_name_prefix="flugbild")
        return _pool


def submit(function: Callable[..., Result], *args) -> Future:
    """Start ``function`` on the pool, or run it at once without one."""
    executor = pool()
    if executor is not None:
        return executor.submit(function, *args)
    future: Future = Future()
    future.set_result(function(*args))
    return future


def ordered_map(function: Callable[[Item], Result], items: Iterable[Item],
                ahead: int | None = None) -> Iterator[Result]:
    """``map`` over the pool with at most ``ahead`` items in flight, in input order."""
    executor = pool()
    if executor is None:
        yield from map(function, items)
        return
    limit = ahead or 2 * workers()
    pending: deque[Future] = deque()
    for item in items:
        pending.append(executor.submit(function, item))
        if len(pending) >= limit:
            yield pending.popleft().result()
    while pending:
        yield pending.popleft().result()


def windows(frames: Iterable[Item], radius: int) -> Iterator[tuple[list[Item], int]]:
    """Each frame with up to ``radius`` neighbours on either side, as (window, centre index)."""
    window: deque[Item] = deque()
    first = emitted = 0
    for count, frame in enumerate(frames, start=1):
        window.append(frame)
        while emitted + radius < count:
            yield list(window), emitted - first
            emitted += 1
            while emitted - first > radius:
                window.popleft()
                first += 1
    while emitted < first + len(window):
        yield list(window), emitted - first
        emitted += 1
        while emitted - first > radius and len(window) > 1:
            window.popleft()
            first += 1


def window_map(frames: Iterable[Item], radius: int, prepare: Callable[[Item], object],
               work: Callable[[list, int], Result]) -> Iterator[Result]:
    """Yield ``work(window, centre)`` for every frame, both steps run on the pool.

    ``prepare`` turns each frame into the data its windows share; it runs once
    per frame. ``work`` must not change the prepared data it is given.
    """
    prepared = ordered_map(prepare, frames)
    yield from ordered_map(lambda job: work(*job), windows(prepared, radius))


class PerThread:
    """One instance of a stateful helper (an optical-flow object) per pool thread."""

    def __init__(self, factory: Callable[[], object]) -> None:
        self.factory = factory
        self.local = threading.local()

    def get(self):
        helper = getattr(self.local, "helper", None)
        if helper is None:
            helper = self.local.helper = self.factory()
        return helper
