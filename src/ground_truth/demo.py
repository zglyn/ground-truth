from __future__ import annotations

import json
from typing import Any

from ground_truth.domain import QUESTIONS, NewsItem
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


def _researcher(event_id: str, prompt: str, tools: Any) -> dict:
    tools.research("lite", "chile lithium mine strike exposure")
    tools.check_receipt(PAGE_URL, PASSAGE)
    return {"answers": {question: "Recorded demo finding." for question in QUESTIONS}, "missing": []}


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
