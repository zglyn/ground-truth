"""Typed view model for the dashboard, built from Swarm.view().

Rendering code reads these objects only; it never reaches into the raw view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

STAGE_NAMES = {
    "startup": "Recording check",
    "scout": "Scout",
    "supervisor": "Supervisor",
    "research": "Research",
    "analysis": "Analysis",
    "notify": "Notifications",
    "grade": "Track Record grading",
}


def when(value: str | None) -> str:
    if not value:
        return "never"
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    return moment.strftime("%d %b %Y, %H:%M UTC").lstrip("0")


def percent(weight: float) -> str:
    text = f"{weight * 100:.1f}".rstrip("0").rstrip(".")
    return f"{text}%"


def safe_url(url: str | None) -> str | None:
    if not url:
        return None
    return url if urlparse(url).scheme in ("http", "https") else None


def hostname(url: str | None) -> str:
    if not url:
        return ""
    return urlparse(url).hostname or url


@dataclass(frozen=True)
class Status:
    state: str
    label: str
    detail: str
    last_checked: str
    failed_stage: str | None


@dataclass(frozen=True)
class ImpactRow:
    ticker: str
    stance: str
    action: str
    confidence: str
    expected: str
    direction: str
    day_move: str
    volume: str


@dataclass(frozen=True)
class Evidence:
    text: str
    url: str | None
    host: str
    passage: str
    retrieved: str


@dataclass(frozen=True)
class ThesisCard:
    id: str
    event_id: str
    headline: str
    version: int
    latest_version: int
    previous_id: str | None
    created: str
    changes: list[str]
    missing: list[str]
    impacts: list[ImpactRow]
    evidence: list[Evidence]
    inferences: list[str]
    receipt_count: int
    lean: str
    net: float
    color: str
    disclaimer: str
    dossier_version: int | None


@dataclass(frozen=True)
class HoldingRow:
    ticker: str
    weight: float
    percent: str


@dataclass(frozen=True)
class TopicRow:
    id: str
    name: str
    holdings: tuple[str, ...]
    feeds: tuple[str, ...]
    status: str


@dataclass(frozen=True)
class Activity:
    awaiting_research: int
    awaiting_analysis: int
    event_errors: list[dict[str, str]]
    stage_errors: list[dict[str, str]]


@dataclass(frozen=True)
class Costs:
    month_name: str
    search: int
    research: int
    spend: float
    lifetime_search: int
    lifetime_research: int
    lifetime_spend: float
    priced: bool
    budget: float
    search_rate: str
    research_rate: str

    def month_line(self) -> str:
        counts = f"You.com calls in {self.month_name}: {self.search} search, {self.research} research."
        if not self.priced:
            return counts + " Set both rates below to price them. Until then the monthly budget is not enforced."
        return counts + f" Spend ${self.spend:.2f} of ${self.budget:.2f} this month."

    def lifetime_line(self) -> str:
        counts = f"Lifetime, all months: {self.lifetime_search} search, {self.lifetime_research} research calls"
        if not self.priced:
            return counts + "."
        return counts + f", about ${self.lifetime_spend:.2f} at today's rates."


@dataclass(frozen=True)
class Horizon:
    days: int
    graded: int
    correct: int
    pending: int


@dataclass(frozen=True)
class Grade:
    ticker: str
    stance: str
    horizon: int
    move: float
    correct: bool
    thesis_id: str
    headline: str


@dataclass
class Dashboard:
    now: str
    status: Status
    runtime_label: str
    runtime_mode: str
    recording_ok: bool
    recording_detail: str
    harness: dict[str, str]
    holdings: list[HoldingRow]
    total_weight: float
    topics: list[TopicRow]
    theses: list[ThesisCard]
    activity: Activity
    costs: Costs
    alert_weight: float
    horizons: list[Horizon]
    grades: list[Grade]
    events: list[dict[str, Any]]
    sightings: list[dict[str, Any]]
    dismissed: list[dict[str, Any]]
    stages: dict[str, dict[str, Any]]
    headlines: dict[str, str] = field(default_factory=dict)
    demo: bool = False

    def latest_per_event(self) -> list[ThesisCard]:
        latest: dict[str, ThesisCard] = {}
        for card in self.theses:
            current = latest.get(card.event_id)
            if current is None or card.version > current.version:
                latest[card.event_id] = card
        return sorted(latest.values(), key=lambda card: card.created, reverse=True)

    def thesis(self, thesis_id: str) -> ThesisCard | None:
        return next((card for card in self.theses if card.id == thesis_id), None)

    def versions(self, event_id: str) -> list[ThesisCard]:
        return sorted((card for card in self.theses if card.event_id == event_id), key=lambda card: card.version)

    def event(self, event_id: str) -> dict[str, Any] | None:
        return next((event for event in self.events if event["id"] == event_id), None)

    def active_topics(self) -> list[TopicRow]:
        return [topic for topic in self.topics if topic.status == "active"]

    def suggested_topics(self) -> list[TopicRow]:
        return [topic for topic in self.topics if topic.status == "suggested"]

    def rejected_topics(self) -> list[TopicRow]:
        return [topic for topic in self.topics if topic.status == "rejected"]

    def tickers(self) -> list[str]:
        return [holding.ticker for holding in self.holdings]


def _status(view: dict[str, Any]) -> Status:
    stages = view.get("stages") or {}
    scout = stages.get("scout") or {}
    last_checked = when(scout.get("last_run_at")) if scout else "not yet"
    if view.get("blocked"):
        detail = (stages.get("startup") or {}).get("detail") or "The recording check failed."
        return Status("paused", "Paused", detail, last_checked, "startup")
    pipeline = {name: info for name, info in stages.items() if name != "startup"}
    if not pipeline:
        return Status("not-started", "Not started yet", "The first check runs shortly after start.", last_checked, None)
    for name in ("scout", "supervisor", "research", "analysis", "notify", "grade"):
        info = pipeline.get(name)
        if info and info["state"] == "failed":
            label = STAGE_NAMES.get(name, name)
            detail = f"{label} failed at {when(info['last_run_at'])}: {info['detail']}"
            return Status("attention", "Needs attention", detail, last_checked, name)
    for name in ("scout", "supervisor", "research", "analysis", "notify", "grade"):
        info = pipeline.get(name)
        if info and info["state"] == "degraded":
            label = STAGE_NAMES.get(name, name)
            return Status("degraded", "Running with problems", f"{label}: {info['detail']}", last_checked, name)
    return Status("running", "Running", "Every stage finished its last run.", last_checked, None)


def _impact(raw: dict[str, Any]) -> ImpactRow:
    move = raw.get("day_move")
    volume = raw.get("volume")
    return ImpactRow(
        ticker=raw["ticker"],
        stance=raw["stance"],
        action=raw["action"],
        confidence=str(raw["confidence"]).capitalize(),
        expected=raw["expected"],
        direction=raw["direction"],
        day_move="No price" if move is None else f"{move:+.2%}",
        volume="No volume" if volume is None else f"{volume:,.0f}",
    )


def build(view: dict[str, Any]) -> Dashboard:
    headlines = {event["id"]: event["title"] for event in view["events"]}
    latest_version: dict[str, int] = {}
    for thesis in view["theses"]:
        latest_version[thesis["event_id"]] = max(latest_version.get(thesis["event_id"], 0), thesis["version"])
    cards = []
    for thesis in view["theses"]:
        evidence = []
        inferences = []
        for claim in thesis["claims"]:
            if claim["kind"] == "evidence" and claim["url"]:
                evidence.append(
                    Evidence(
                        text=claim["text"],
                        url=safe_url(claim["url"]),
                        host=hostname(claim["url"]),
                        passage=claim["passage"] or "",
                        retrieved=when(claim["retrieved_at"]),
                    )
                )
            else:
                inferences.append(claim["text"])
        cards.append(
            ThesisCard(
                id=thesis["id"],
                event_id=thesis["event_id"],
                headline=headlines.get(thesis["event_id"], "Untitled Event"),
                version=thesis["version"],
                latest_version=latest_version[thesis["event_id"]],
                previous_id=thesis["previous_id"],
                created=thesis["created_at"],
                changes=list(thesis["changes"]),
                missing=list(thesis["missing"]),
                impacts=[_impact(impact) for impact in thesis["impacts"]],
                evidence=evidence,
                inferences=inferences,
                receipt_count=len(evidence),
                lean=thesis.get("lean", ""),
                net=thesis["net"],
                color=thesis["color"],
                disclaimer=thesis["disclaimer"],
                dossier_version=thesis.get("dossier_version"),
            )
        )
    costs = view["costs"]
    record = view["track_record"]
    grades = [
        Grade(
            ticker=grade.get("ticker", ""),
            stance=grade.get("stance", ""),
            horizon=grade["horizon"],
            move=grade["move"],
            correct=grade["correct"],
            thesis_id=grade.get("thesis_id", ""),
            headline=headlines.get(grade.get("event_id", ""), ""),
        )
        for grade in record["grades"]
    ]
    holdings = [HoldingRow(row["ticker"], float(row["weight"]), percent(float(row["weight"]))) for row in view["holdings"]]
    activity = view.get("activity") or {}
    recording = view.get("recording") or {}
    runtime = view.get("runtime") or {}
    return Dashboard(
        now=view.get("now", ""),
        status=_status(view),
        runtime_label=runtime.get("label", ""),
        runtime_mode=runtime.get("mode", ""),
        recording_ok=bool(recording.get("ok")),
        recording_detail=str(recording.get("detail", "")),
        harness=view.get("harness") or {},
        holdings=holdings,
        total_weight=sum(holding.weight for holding in holdings),
        topics=[
            TopicRow(topic["id"], topic["name"], tuple(topic["holdings"]), tuple(topic["feeds"]), topic["status"])
            for topic in view["topics"]
        ],
        theses=cards,
        activity=Activity(
            awaiting_research=int(activity.get("awaiting_research", 0)),
            awaiting_analysis=int(activity.get("awaiting_analysis", 0)),
            event_errors=list(activity.get("event_errors") or []),
            stage_errors=list(activity.get("stage_errors") or []),
        ),
        costs=Costs(
            month_name=costs.get("month_name", "this month"),
            search=costs["search"],
            research=costs["research"],
            spend=costs["spend_usd"],
            lifetime_search=costs.get("lifetime_search", costs["search"]),
            lifetime_research=costs.get("lifetime_research", costs["research"]),
            lifetime_spend=costs.get("lifetime_spend_usd", costs["spend_usd"]),
            priced=costs["priced"],
            budget=costs["budget_usd"],
            search_rate=str(costs.get("search_usd", "")),
            research_rate=str(costs.get("research_usd", "")),
        ),
        alert_weight=float(view["alert_weight"]),
        horizons=[
            Horizon(item["horizon"], item["graded"], item["correct"], item["pending"])
            for item in record.get("horizons") or []
        ],
        grades=grades,
        events=list(view["events"]),
        sightings=list(view["sightings"]),
        dismissed=list(view["dismissed"]),
        stages=dict(view.get("stages") or {}),
        headlines=headlines,
        demo=bool(view.get("demo")),
    )
