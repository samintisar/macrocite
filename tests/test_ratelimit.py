from signalbench.ingest.ratelimit import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _limiter(fake: FakeClock) -> RateLimiter:
    return RateLimiter(calls=8, period=1.0, clock=fake.clock, sleep=fake.sleep)


def test_first_call_does_not_sleep() -> None:
    fake = FakeClock()
    _limiter(fake).wait()
    assert fake.sleeps == []


def test_calls_are_spaced_by_period_over_calls() -> None:
    fake = FakeClock()
    limiter = _limiter(fake)
    limiter.wait()
    limiter.wait()
    limiter.wait()
    assert fake.sleeps == [0.125, 0.125]


def test_no_sleep_when_caller_is_already_slow() -> None:
    fake = FakeClock()
    limiter = _limiter(fake)
    limiter.wait()
    fake.now += 1.0
    limiter.wait()
    assert fake.sleeps == []
