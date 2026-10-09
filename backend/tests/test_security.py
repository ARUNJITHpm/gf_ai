import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.security import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_rate_limiter_allows_burst_then_blocks() -> None:
    clock = FakeClock()
    limiter = RateLimiter(rate=3, per=60, clock=clock)
    assert [limiter.allow("a") for _ in range(4)] == [True, True, True, False]


def test_rate_limiter_refills_over_time() -> None:
    clock = FakeClock()
    limiter = RateLimiter(rate=3, per=60, clock=clock)
    for _ in range(3):
        limiter.allow("a")
    assert not limiter.allow("a")
    clock.now = 20  # one token per 20 s
    assert limiter.allow("a")
    assert not limiter.allow("a")


def test_rate_limiter_is_per_client() -> None:
    limiter = RateLimiter(rate=1, per=60, clock=FakeClock())
    assert limiter.allow("a")
    assert limiter.allow("b")
    assert not limiter.allow("a")


@pytest.mark.parametrize("calls,expected_last", [(2, 200), (3, 429)])
def test_rate_limiter_as_dependency(calls: int, expected_last: int) -> None:
    app = FastAPI()

    @app.get("/limited", dependencies=[Depends(RateLimiter(rate=2, per=60, clock=FakeClock()))])
    def limited() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        statuses = [client.get("/limited").status_code for _ in range(calls)]
    assert statuses[-1] == expected_last
