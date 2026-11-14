from __future__ import annotations

import threading
from typing import Any, Callable, Protocol

from ground_truth.store import Store


class Clock(Protocol):
    def now(self) -> Any: ...


class BudgetExceeded(Exception):
    pass


class _Flight:
    def __init__(self) -> None:
        self.done = threading.Event()
        self.result: dict[str, Any] | None = None
        self.error: BaseException | None = None


class ResearchExecutor:
    """Single place that spends money on You.com.

    It checks the query log first, lets only one caller pay for a given query
    at a time (others wait for that result), refuses calls the monthly budget
    cannot cover, retries transient failures, and records every paid call.
    """

    def __init__(
        self,
        store: Store,
        you: Callable[[], Any],
        clock: Clock,
        *,
        allows: Callable[[str], bool],
        retries: int = 1,
    ) -> None:
        self._store = store
        self._you = you
        self._clock = clock
        self._allows = allows
        self._retries = retries
        self._lock = threading.Lock()
        self._flights: dict[str, _Flight] = {}

    def research(self, tier: str, query: str) -> tuple[dict[str, Any], bool]:
        """Return (result, paid). Raises BudgetExceeded or the last adapter error."""
        cached = self._store.query_get(query)
        if cached is not None:
            return {"cached": True, **cached}, False
        with self._lock:
            flight = self._flights.get(query)
            owner = flight is None
            if owner:
                flight = _Flight()
                self._flights[query] = flight
        assert flight is not None
        if not owner:
            flight.done.wait()
            if flight.error is not None:
                raise flight.error
            assert flight.result is not None
            return {"cached": True, **flight.result}, False
        try:
            cached = self._store.query_get(query)
            if cached is not None:
                flight.result = cached
                return {"cached": True, **cached}, False
            if not self._allows("research"):
                raise BudgetExceeded("monthly budget reached")
            result = self._with_retries(lambda: self._you().research(tier, query))
            self._store.query_put(query, result, self._clock.now())
            self._store.add_cost("research", self._clock.now())
            flight.result = result
            return result, True
        except BaseException as exc:
            flight.error = exc
            raise
        finally:
            with self._lock:
                self._flights.pop(query, None)
            flight.done.set()

    def search(self, query: str) -> list[Any]:
        if not self._allows("search"):
            raise BudgetExceeded("monthly budget reached")
        hits = self._with_retries(lambda: self._you().search(query))
        self._store.add_cost("search", self._clock.now())
        return list(hits)

    def _with_retries(self, call: Callable[[], Any]) -> Any:
        attempts = self._retries + 1
        for attempt in range(attempts):
            try:
                return call()
            except Exception:
                if attempt == attempts - 1:
                    raise
        raise RuntimeError("unreachable")
