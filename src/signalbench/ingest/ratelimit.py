import time
from collections.abc import Callable


class RateLimiter:
    """Spaces calls evenly so at most `calls` happen per `period` seconds."""

    def __init__(
        self,
        calls: int,
        period: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._interval = period / calls
        self._clock = clock
        self._sleep = sleep
        self._next_allowed = 0.0

    def wait(self) -> None:
        now = self._clock()
        if now < self._next_allowed:
            self._sleep(self._next_allowed - now)
            now = self._next_allowed
        self._next_allowed = now + self._interval
