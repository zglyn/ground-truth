from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date

from ground_truth.domain import NewsItem, Quote

USER_AGENT = "GroundTruth/0.1 (research; contact=local)"


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


class LiveNews:
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
        return _rss_items(_get(url), "sec-edgar")


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
    def quote(self, ticker: str, on: date) -> Quote:
        url = f"https://stooq.com/q/l/?s={ticker.lower()}.us&f=sd2t2ohlcv&h&e=csv"
        text = _get(url).decode()
        lines = [line for line in text.splitlines() if line and not line.lower().startswith("symbol")]
        if not lines:
            return Quote(0.0, 0.0)
        cells = lines[-1].split(",")
        price = float(cells[6]) if len(cells) > 6 and cells[6] not in ("", "N/D") else 0.0
        volume = float(cells[7]) if len(cells) > 7 and cells[7] not in ("", "N/D") else 0.0
        return Quote(price, volume)
