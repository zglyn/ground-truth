from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from ground_truth.domain import NewsItem, Quote

def edgar_user_agent(contact: str | None = None) -> str:
    name = (contact if contact is not None else os.environ.get("EDGAR_CONTACT", "")).strip()
    if name:
        return f"GroundTruth/0.1 ({name})"
    return "GroundTruth/0.1 (set EDGAR_CONTACT)"


USER_AGENT = "GroundTruth/0.1 (research)"


def _get(url: str, user_agent: str = USER_AGENT) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


class LiveNews:
    @property
    def edgar_enabled(self) -> bool:
        """The SEC blocks automated requests that do not name a contact, so EDGAR is off without one."""
        return bool(os.environ.get("EDGAR_CONTACT", "").strip())

    def google_news(self, topic: str) -> list[NewsItem]:
        query = urllib.parse.quote(topic)
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        return _rss_items(_get(url), "google-news")

    def rss(self, url: str) -> list[NewsItem]:
        return _rss_items(_get(url), "rss")

    def edgar(self, ticker: str) -> list[NewsItem]:
        query = urllib.parse.urlencode(
            {"action": "getcompany", "CIK": ticker, "type": "", "dateb": "", "owner": "include", "count": "10", "output": "atom"}
        )
        url = f"https://www.sec.gov/cgi-bin/browse-edgar?{query}"
        return _rss_items(_get(url, edgar_user_agent()), "sec-edgar")


def _rss_items(payload: bytes, source: str) -> list[NewsItem]:
    root = ET.fromstring(payload)
    items: list[NewsItem] = []
    for node in root.iter():
        if node.tag.endswith("item") or node.tag.endswith("entry"):
            title = ""
            link = ""
            for child in node:
                if child.tag.endswith("title") and child.text:
                    title = child.text.strip()
                if child.tag.endswith("link"):
                    link = child.attrib.get("href") or (child.text or "")
            if title and link:
                items.append(NewsItem(title=title, url=link.strip(), source=source, topic=""))
    return items


class LiveYou:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key if api_key is not None else os.environ.get("YDC_API_KEY", "")

    def search(self, query: str) -> list[dict[str, str]]:
        body = json.dumps({"query": query}).encode()
        request = urllib.request.Request(
            "https://api.you.com/v1/search",
            data=body,
            headers={"X-API-Key": self.api_key, "Content-Type": "application/json", "User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode())
        hits = payload.get("results") or payload.get("hits") or payload.get("web") or []
        return list(hits)

    def research(self, tier: str, query: str) -> dict:
        body = json.dumps({"input": query, "research_effort": tier}).encode()
        request = urllib.request.Request(
            "https://api.you.com/v1/research",
            data=body,
            headers={"X-API-Key": self.api_key, "Content-Type": "application/json", "User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read().decode())


class LivePages:
    def fetch(self, url: str) -> str:
        return _get(url).decode("utf-8", errors="replace")


class LiveQuotes:
    """Daily quotes from Stooq, keyed by the date asked for.

    For today it uses the latest quote, and only if that quote is dated today.
    For past dates it uses the daily history and returns that day's close.
    When there is no price for the requested day it returns None rather than
    reusing another day's price.
    """

    def __init__(
        self,
        get: Callable[[str], bytes] | None = None,
        today: Callable[[], date] | None = None,
    ) -> None:
        self._get = get or (lambda url: _get(url))
        self._today = today or (lambda: datetime.now(timezone.utc).date())

    def quote(self, ticker: str, on: date) -> Quote | None:
        symbol = urllib.parse.quote(f"{ticker.lower()}.us")
        if on >= self._today():
            return self._latest(symbol, on)
        return self._historical(symbol, on)

    def _latest(self, symbol: str, on: date) -> Quote | None:
        text = self._get(f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv").decode()
        for cells in _csv_rows(text):
            if len(cells) < 8 or cells[1] != on.isoformat():
                continue
            price, volume = _number(cells[6]), _number(cells[7])
            if price:
                return Quote(price, volume or 0.0)
        return None

    def _historical(self, symbol: str, on: date) -> Quote | None:
        start = on - timedelta(days=7)
        url = f"https://stooq.com/q/d/l/?s={symbol}&d1={start:%Y%m%d}&d2={on:%Y%m%d}&i=d"
        for cells in _csv_rows(self._get(url).decode()):
            if len(cells) < 6 or cells[0] != on.isoformat():
                continue
            price, volume = _number(cells[4]), _number(cells[5])
            if price:
                return Quote(price, volume or 0.0)
        return None


def _csv_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.split(",")]
        if not line.strip() or cells[0].lower() in ("symbol", "date"):
            continue
        rows.append(cells)
    return rows


def _number(raw: str) -> float | None:
    if raw in ("", "N/D"):
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value == value and value not in (float("inf"), float("-inf")) else None
