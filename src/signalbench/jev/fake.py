"""A JevClient test double (spec 03 testing; spec 05 end-to-end tests reuse it)."""

import threading
from collections.abc import Callable

from signalbench.jev.client import JevResult


def fake_result(
    *,
    p_negative: float = 0.1,
    p_neutral: float = 0.2,
    p_positive: float = 0.7,
    event_type: str = "earnings",
    p_routine: float = 0.1,
    cost_usd: float = 0.00002,
    model_resolved: str = "typesafe/jev-1.13-20260917",
) -> JevResult:
    """A valid q1 result; override the fields a test cares about."""
    return JevResult(
        response_id="gen-dec-fake",
        model_resolved=model_resolved,
        p_negative=p_negative,
        p_neutral=p_neutral,
        p_positive=p_positive,
        event_type=event_type,
        p_routine=p_routine,
        answers={
            "impact": {
                "type": "choice",
                "choice": max(
                    ("negative", p_negative), ("neutral", p_neutral), ("positive", p_positive),
                    key=lambda item: item[1],
                )[0],
                "probabilities": {
                    "negative": p_negative, "neutral": p_neutral, "positive": p_positive,
                },
            },
            "event_type": {"type": "choice", "choice": event_type},
            "routine": {"type": "noul", "noul": p_routine},
        },
        input_tokens=480,
        cost_usd=cost_usd,
        latency_ms=5,
    )


Respond = Callable[[str], JevResult | Exception]


class FakeJevClient:
    """Answers every state with `respond(state)` (default: `fake_result()`), raising it when it
    is an exception. Records each state in `calls`; safe to call from worker threads."""

    def __init__(self, respond: Respond | None = None) -> None:
        self._respond: Respond = respond or (lambda _state: fake_result())
        self._lock = threading.Lock()
        self.calls: list[str] = []
        self.threads: list[int] = []

    def read(self, state: str) -> JevResult:
        with self._lock:
            self.calls.append(state)
            self.threads.append(threading.get_ident())
        answer = self._respond(state)
        if isinstance(answer, Exception):
            raise answer
        return answer
