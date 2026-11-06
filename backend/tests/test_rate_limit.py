from app.core.rate_limit import SlidingWindowRateLimiter


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_limit_is_per_key_and_window_slides():
    clock = _Clock()
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60, clock=clock)

    assert limiter.hit("a") and limiter.hit("a")
    assert not limiter.hit("a")
    assert limiter.hit("b")

    clock.now += 60
    assert limiter.hit("a")


def test_zero_limit_disables_limiting():
    limiter = SlidingWindowRateLimiter(limit=0)
    assert all(limiter.hit("a") for _ in range(100))
