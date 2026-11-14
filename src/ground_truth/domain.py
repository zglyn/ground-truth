from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Literal

SCOUT_INTERVAL_SECONDS = 300
EVENT_QUIET_HOURS = 72
RESEARCH_CALL_BUDGET = 4
RESEARCH_TIME_BUDGET_SECONDS = 180
DISCLAIMER = "Not financial advice."
QUESTIONS = ("exposure", "magnitude", "timing", "counter-evidence")

_STOP = frozenset(
    "the a an of in on and or to for from with by at as is are was were be been being "
    "that this it its their his her after before over into about than then not no nor "
    "will would can may new says said report reports".split()
)


class Stance(str, Enum):
    BULL = "Bull"
    BEAR = "Bear"
    NEUTRAL = "Neutral"


class Action(str, Enum):
    ADD = "Add"
    HOLD = "Hold"
    TRIM = "Trim"
    EXIT = "Exit"
    HEDGE = "Hedge"


class EventStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class Holding:
    ticker: str
    weight: float


@dataclass(frozen=True)
class WatchTopic:
    id: str
    name: str
    holdings: tuple[str, ...]
    feeds: tuple[str, ...]
    status: Literal["suggested", "active", "rejected"]


@dataclass(frozen=True)
class NewsItem:
    title: str
    url: str
    source: str
    topic: str


@dataclass(frozen=True)
class Quote:
    price: float
    volume: float


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {word for word in words if len(word) > 2 and word not in _STOP}


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def classify_sighting(
    title: str,
    topic: str,
    linked_tickers: tuple[str, ...],
    open_events: list[tuple[str, str]],
    closed_events: list[tuple[str, str]],
) -> tuple[str, str | None]:
    sight = tokens(title)
    topic_tokens = tokens(topic) | {ticker.lower() for ticker in linked_tickers}
    scored = [(jaccard(sight, tokens(text)), event_id) for event_id, text in open_events]
    scored.sort(reverse=True)
    if scored and scored[0][0] >= 0.4 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.15):
        return ("attach", scored[0][1])
    close = [event_id for score, event_id in scored if score >= 0.25]
    if len(close) >= 2:
        return ("ambiguous", None)
    if sight.isdisjoint(topic_tokens):
        return ("dismiss", f"unrelated to Watch Topic {topic}")
    related = None
    best = 0.0
    for event_id, text in closed_events:
        score = jaccard(sight, tokens(text))
        if score > best:
            best = score
            related = event_id
    if best < 0.4:
        related = None
    return ("new", related)


def add_trading_days(day: date, count: int) -> date:
    cursor = day
    left = count
    while left:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            left -= 1
    return cursor


def previous_trading_day(day: date) -> date:
    cursor = day - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def grade_stance(stance: Stance, move: float) -> bool:
    if stance is Stance.BULL:
        return move > 0
    if stance is Stance.BEAR:
        return move < 0
    return abs(move) < 0.01


def cap_confidence(confidence: Confidence, missing: list[str]) -> Confidence:
    if missing and confidence is Confidence.HIGH:
        return Confidence.MEDIUM
    return confidence


def portfolio_net(impacts: list[tuple[Stance, float, Confidence]]) -> float:
    weight = {Confidence.HIGH: 1.0, Confidence.MEDIUM: 0.6, Confidence.LOW: 0.3}
    sign = {Stance.BULL: 1.0, Stance.BEAR: -1.0, Stance.NEUTRAL: 0.0}
    return sum(sign[stance] * holding_weight * weight[confidence] for stance, holding_weight, confidence in impacts)


def portfolio_color(net: float) -> Literal["bull", "bear", "neutral"]:
    if net > 0.02:
        return "bull"
    if net < -0.02:
        return "bear"
    return "neutral"


def parse_stance(value: str) -> Stance:
    return Stance(value)


def parse_action(value: str) -> Action:
    return Action(value)


def parse_confidence(value: str) -> Confidence:
    return Confidence(value)


def quiet_deadline(last_sighting_at: datetime) -> datetime:
    return last_sighting_at + timedelta(hours=EVENT_QUIET_HOURS)


_TOPIC_HINTS: dict[str, tuple[str, ...]] = {
    "TSLA": ("EV supply chain", "lithium"),
    "ALB": ("lithium",),
    "SQM": ("lithium",),
}


def topics_for_holdings(tickers: list[str]) -> list[dict[str, list[str]]]:
    grouped: dict[str, list[str]] = {}
    for ticker in tickers:
        name = ticker.upper()
        hints = _TOPIC_HINTS.get(name, (name,))
        for hint in hints:
            holdings = grouped.setdefault(hint, [])
            if name not in holdings:
                holdings.append(name)
    return [{"name": hint, "holdings": holdings} for hint, holdings in grouped.items()]


def holding_effect(ticker: str, title: str) -> dict[str, str]:
    text = title.lower()
    symbol = ticker.upper()
    if "lithium" in text and symbol in {"ALB", "SQM"}:
        return {
            "stance": Stance.BULL.value,
            "action": Action.ADD.value,
            "direction": "up",
            "expected": "A tighter lithium market can lift a producer.",
        }
    if "lithium" in text and symbol == "TSLA":
        return {
            "stance": Stance.BEAR.value,
            "action": Action.TRIM.value,
            "direction": "down",
            "expected": "A tighter lithium market can raise an EV maker's input cost.",
        }
    return {
        "stance": Stance.NEUTRAL.value,
        "action": Action.HOLD.value,
        "direction": "flat",
        "expected": "The evidence does not fix a direction.",
    }


def spend_usd(search_count: int, research_count: int, search_rate: float, research_rate: float) -> float:
    return search_count * search_rate + research_count * research_rate
