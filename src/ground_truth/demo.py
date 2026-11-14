from __future__ import annotations

import json
from typing import Any

from datetime import date

from ground_truth.domain import NewsItem, Quote
from ground_truth.swarm import Swarm

PASSAGE = "Workers at a Chile lithium mine stopped work."
PAGE_URL = "https://example.test/lithium-strike"
PAGE = "Workers at a Chile lithium mine stopped work. Output will fall for months."
STORY = NewsItem(
    title="Chile lithium mine strike cuts supply",
    url=PAGE_URL,
    source="demo",
    topic="lithium",
)


class RecordedYou:
    def search(self, query: str) -> list[dict[str, str]]:
        return [{"title": "Chile lithium strike confirmed", "url": PAGE_URL}]

    def research(self, tier: str, query: str) -> dict[str, str]:
        return {"tier": tier, "query": query, "text": PAGE}


class RecordedPages:
    def fetch(self, url: str) -> str:
        return PAGE


class RecordedQuotes:
    """Staged prices: TSLA already down on the day, ALB up. Earlier days get the prior close."""

    MOVES = {"TSLA": (250.0, 241.0), "ALB": (80.0, 83.2)}

    def __init__(self, today: Any) -> None:
        self._today = today

    def quote(self, ticker: str, on: date) -> Quote:
        prior, current = self.MOVES.get(ticker.upper(), (100.0, 100.0))
        if on < self._today():
            return Quote(prior, 900_000.0)
        return Quote(current, 1_400_000.0)


class NoNews:
    """The demo's Scout sees only the staged story, never live headlines."""

    def google_news(self, topic: str) -> list:
        return []

    def rss(self, url: str) -> list:
        return []

    def edgar(self, ticker: str) -> list:
        return []


DEMO_ANSWERS = {
    "exposure": "Tesla buys lithium hydroxide from several suppliers, some of it refined from Chilean brine.",
    "magnitude": "Recorded demo finding: the struck mine is a mid-sized producer; a long strike tightens spot supply.",
    "timing": "Recorded demo finding: union talks are scheduled within two weeks.",
    "counter-evidence": "Recorded demo finding: battery makers hold several weeks of inventory.",
}


def _researcher(event_id: str, prompt: str, tools: Any) -> dict:
    tools.research("lite", "chile lithium mine strike exposure")
    tools.check_receipt(PAGE_URL, PASSAGE)
    return {"answers": dict(DEMO_ANSWERS), "missing": []}


def _analyst(event_id: str, prompt: str, tools: Any) -> dict:
    info = json.loads(prompt)
    body = json.loads(tools.fetch_session(info["session"]))
    receipt_id = body["receipts"][0]
    return {
        "impacts": [
            {
                "ticker": "TSLA",
                "stance": "Bear",
                "action": "Trim",
                "expected": "Battery costs rise if the strike holds.",
                "direction": "down",
                "confidence": "high",
            },
            {
                "ticker": "ALB",
                "stance": "Bull",
                "action": "Add",
                "expected": "A lithium producer can charge more.",
                "direction": "up",
                "confidence": "high",
            },
        ],
        "claims": [{"text": PASSAGE, "receipt_id": receipt_id}],
        "missing": [],
        "changes": [],
    }


def prepare(swarm: Swarm) -> None:
    """Configure a swarm as a self-contained stage demo with staged inputs."""
    swarm.demo_mode = True
    swarm.harness.mode = "scripted"
    swarm.news = NoNews()
    swarm.quotes = RecordedQuotes(lambda: swarm.clock.now().date())
    if not swarm.store.holdings():
        swarm.set_holdings([("TSLA", 0.4), ("ALB", 0.2)])
    if swarm.store.topic_named("lithium") is None:
        swarm.add_topic("lithium", ["TSLA", "ALB"])


def inject(swarm: Swarm) -> str:
    """Inject the staged story. Falls back to canned output if the recorded run fails."""
    if swarm.store.sighting_by_url(STORY.url):
        return "already"
    try:
        run_demo(swarm, "recorded")
    except Exception:
        run_demo(swarm, "canned")
        return "canned"
    if not swarm.store.theses():
        run_demo(swarm, "canned")
        return "canned"
    return "recorded"


def run_demo(swarm: Swarm, mode: str) -> None:
    if mode == "canned":
        _canned(swarm)
        return
    if mode != "recorded":
        raise ValueError(mode)
    swarm.you = RecordedYou()
    swarm.pages = RecordedPages()
    swarm.harness.on("researcher", _researcher)
    swarm.harness.on("analyst", _analyst)
    swarm.inject_news(STORY)
    swarm.tick()


def _canned(swarm: Swarm) -> None:
    if swarm.store.topic_named("lithium") is None:
        swarm.add_topic("lithium", ["TSLA", "ALB"])
    swarm.inject_news(STORY)
    swarm._scout()
    row = swarm.store.sightings()[-1]
    event_id = swarm.store.create_event(title=row["title"], opened_at=swarm.clock.now(), related_event_id=None)
    swarm.store.attach_sighting(row["id"], event_id)
    swarm.store.set_corroboration(event_id, "corroborated")
    swarm._accept_thesis(
        event_id,
        {"answers": {}, "missing": [], "receipts": []},
        set(),
        None,
        [],
        {
            "impacts": [
                {
                    "ticker": "TSLA",
                    "stance": "Bear",
                    "action": "Trim",
                    "expected": "Battery costs rise if the strike holds.",
                    "direction": "down",
                    "confidence": "high",
                },
                {
                    "ticker": "ALB",
                    "stance": "Bull",
                    "action": "Add",
                    "expected": "A lithium producer can charge more.",
                    "direction": "up",
                    "confidence": "high",
                },
            ],
            "claims": [{"text": "Staged output. The live pipeline did not run.", "receipt_id": None}],
            "missing": [],
            "changes": [],
        },
    )
