from __future__ import annotations

import threading
import uuid

from ground_truth.domain import Quote


class FakeNews:
    def __init__(self) -> None:
        self.google: list = []
        self.feeds: dict[str, list] = {}
        self.filings: list[tuple[str, object]] = []
        self.calls: list[tuple[str, str]] = []

    def google_news(self, topic: str) -> list:
        self.calls.append(("google", topic))
        return list(self.google)

    def rss(self, url: str) -> list:
        self.calls.append(("rss", url))
        return list(self.feeds.get(url, []))

    def edgar(self, ticker: str) -> list:
        self.calls.append(("edgar", ticker))
        return [item for owner, item in self.filings if owner == ticker]


class FakeYou:
    def __init__(self) -> None:
        self.search_hits: list = [{"title": "corroborated"}]
        self.fail = False
        self.calls: list[tuple] = []
        self._lock = threading.Lock()

    def search(self, query: str) -> list:
        with self._lock:
            self.calls.append(("search", query))
        if self.fail:
            raise RuntimeError("you.com unavailable")
        return list(self.search_hits)

    def research(self, tier: str, query: str) -> dict:
        with self._lock:
            self.calls.append(("research", tier, query))
        if self.fail:
            raise RuntimeError("you.com unavailable")
        return {"tier": tier, "query": query, "text": "recorded finding"}


class FakePages:
    def __init__(self, text: str = "") -> None:
        self.text = text

    def fetch(self, url: str) -> str:
        return self.text


class FakeQuotes:
    def __init__(self) -> None:
        self.prices: dict[tuple[str, object], tuple[float, float]] = {}

    def quote(self, ticker: str, on: object) -> Quote:
        price, volume = self.prices.get((ticker, on), (100.0, 1_000_000.0))
        return Quote(price, volume)


class FakeBoard:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []
        self._lock = threading.Lock()

    def post(self, text: str) -> dict[str, str]:
        with self._lock:
            message = {"id": uuid.uuid4().hex, "text": text}
            self.messages.append(message)
            return message

    def fetch_all(self) -> list[dict[str, str]]:
        with self._lock:
            return list(self.messages)


class FakeSessions:
    def __init__(self) -> None:
        self.rows: dict[str, str] = {}
        self._lock = threading.Lock()

    def store_session(self, session_id: str, text: str) -> None:
        with self._lock:
            self.rows[session_id] = text

    def fetch_session(self, session_id: str) -> str:
        with self._lock:
            return self.rows[session_id]

    def search(self, query: str) -> list[str]:
        with self._lock:
            return [key for key, value in self.rows.items() if query.lower() in value.lower()]


class FakeDiscord:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []
        self._lock = threading.Lock()

    def push(self, text: str, url: str) -> None:
        with self._lock:
            self.sent.append({"text": text, "url": url})


class FakeLlm:
    def __init__(self) -> None:
        self.suggestions: list[dict] = []
        self.answer = "new"

    def suggest_topics(self, tickers: list[str]) -> list[dict]:
        return list(self.suggestions)

    def judge(self, title: str, events: list) -> str:
        return self.answer


class FakeRecording:
    def __init__(self) -> None:
        self.result = {"ok": True, "detail": "claude session visible in OpenCrawl"}

    def check(self, harness_name: str) -> dict:
        return dict(self.result)
