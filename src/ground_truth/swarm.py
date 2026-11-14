from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from ground_truth.domain import (
    DISCLAIMER,
    QUESTIONS,
    SCOUT_INTERVAL_SECONDS,
    Action,
    Confidence,
    Stance,
    NewsItem,
    add_trading_days,
    cap_confidence,
    classify_sighting,
    finite_money,
    finite_unit,
    grade_stance,
    holding_effect,
    is_trading_day,
    parse_action,
    parse_confidence,
    parse_stance,
    portfolio_color,
    portfolio_lean,
    portfolio_net,
    spend_usd,
    previous_trading_day,
    quiet_deadline,
)
from ground_truth.research import BudgetExceeded, ResearchExecutor
from ground_truth.store import Store

RESEARCH_CALL_BUDGET = 4
RESEARCH_TIME_BUDGET_SECONDS = 180
MAX_WORKERS = 4
HORIZONS = (1, 5, 20)
STAGES = ("scout", "supervisor", "research", "analysis", "notify", "grade")


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """Production clock: real UTC time, stored without tzinfo like every other timestamp."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)


class ManualClock:
    def __init__(self, start: datetime):
        self._now = start
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def advance(self, *, hours: float = 0, minutes: float = 0, trading_days: int = 0) -> None:
        with self._lock:
            self._now += timedelta(hours=hours, minutes=minutes)
            left = trading_days
            while left:
                self._now += timedelta(days=1)
                if is_trading_day(self._now.date()):
                    left -= 1


class Toolset:
    def __init__(self, swarm: Swarm, event_id: str, started_at: datetime):
        self._swarm = swarm
        self.event_id = event_id
        self.started_at = started_at
        self.paid_calls = 0
        self.lite_done = False
        self.receipts: list[str] = []

    def research(self, tier: str, query: str) -> dict[str, Any]:
        if tier not in ("lite", "standard"):
            return {"error": "tier"}
        if self.paid_calls >= RESEARCH_CALL_BUDGET:
            return {"error": "budget"}
        elapsed = (self._swarm.clock.now() - self.started_at).total_seconds()
        if elapsed > RESEARCH_TIME_BUDGET_SECONDS:
            return {"error": "time"}
        if tier == "standard" and not self.lite_done:
            return {"error": "lite required first"}
        try:
            result, paid = self._swarm.executor.research(tier, query)
        except BudgetExceeded:
            return {"error": "monthly budget"}
        if paid:
            self.paid_calls += 1
        if tier == "lite":
            self.lite_done = True
        return result

    def check_receipt(self, url: str, passage: str) -> dict[str, Any]:
        page = self._swarm.pages.fetch(url)
        if passage not in page:
            return {"ok": False}
        receipt_id = self._swarm.store.add_receipt(url, passage, self._swarm.clock.now())
        self.receipts.append(receipt_id)
        return {"ok": True, "receipt_id": receipt_id}

    def fetch_session(self, session_id: str) -> str:
        return self._swarm.sessions.fetch_session(session_id)

    def search_memory(self, query: str) -> list[str]:
        return self._swarm.sessions.search(query)

    def quote(self, ticker: str) -> dict[str, float] | None:
        today = self._swarm.clock.now().date()
        current = self._swarm.quotes.quote(ticker, today)
        if current is None:
            return None
        return {"price": current.price, "volume": current.volume}


class Swarm:
    def __init__(
        self,
        path: str,
        *,
        clock: Clock,
        news: Any,
        you: Any,
        pages: Any,
        quotes: Any,
        board: Any,
        sessions: Any,
        harness: Any,
        discord: Any,
        llm: Any,
        recording: Any,
        dashboard_url: str = "http://127.0.0.1:8080",
    ):
        self.store = Store(path)
        self.clock = clock
        self.news = news
        self.you = you
        self.pages = pages
        self.quotes = quotes
        self.board = board
        self.sessions = sessions
        self.harness = harness
        self.discord = discord
        self.llm = llm
        self.recording_probe = recording
        self.dashboard_url = dashboard_url
        self.injected: list[Any] = []
        self._inject_lock = threading.Lock()
        self.inject_enabled = False
        self._tick_lock = threading.Lock()
        self.recording: dict[str, Any] = {"ok": False, "detail": "not checked"}
        self.blocked = False
        self.demo_mode = False
        self.executor = ResearchExecutor(self.store, lambda: self.you, clock, allows=self._budget_allows)
        if self.store.setting("alert_weight", "") == "":
            self.store.set_setting("alert_weight", "0.25")
        if self.store.setting("researcher_harness", "") == "":
            self.store.set_setting("researcher_harness", "claude")
        if self.store.setting("analyst_harness", "") == "":
            self.store.set_setting("analyst_harness", "claude")

    # ----- startup and configuration -------------------------------------

    def start(self) -> dict[str, Any]:
        harness_name = self.store.setting("researcher_harness", "claude")
        if harness_name == "cursor":
            self.recording = {"ok": False, "detail": "Cursor is not used for the Researcher"}
        else:
            self.recording = self.recording_probe.check(harness_name)
        self.blocked = not self.recording.get("ok")
        if self.blocked:
            self.store.stage_report(
                "startup",
                "failed",
                f"Recording check failed: {self.recording.get('detail', '')}. Research is paused.",
                self.clock.now(),
            )
        else:
            self.store.stage_report("startup", "ok", str(self.recording.get("detail", "")), self.clock.now())
        return self.recording

    def configure(self, *, researcher: str = "claude", analyst: str = "claude") -> None:
        if researcher == "cursor":
            raise ValueError("Cursor is not used for the Researcher")
        self.store.set_setting("researcher_harness", researcher)
        self.store.set_setting("analyst_harness", analyst)

    def set_holdings(self, holdings: list[tuple[str, float]]) -> None:
        rows = [(ticker.upper(), finite_unit(weight, "weight")) for ticker, weight in holdings]
        self.store.set_holdings(rows)

    def save_holding(self, ticker: str, weight: float) -> None:
        self.store.upsert_holding(ticker.upper(), finite_unit(weight, "weight"))

    def save_rates(self, search_usd: str, research_usd: str, budget_usd: str) -> None:
        for raw, field in ((search_usd, "you_search_usd"), (research_usd, "you_research_usd")):
            if raw.strip():
                finite_money(raw, field)
        finite_money(budget_usd, "monthly_budget_usd")
        self.store.set_setting("you_search_usd", search_usd.strip())
        self.store.set_setting("you_research_usd", research_usd.strip())
        self.store.set_setting("monthly_budget_usd", budget_usd.strip())

    def remove_holding(self, ticker: str) -> None:
        self.store.remove_holding(ticker.upper())

    def suggest_topics(self) -> list[dict[str, Any]]:
        suggestions = self.llm.suggest_topics([row["ticker"] for row in self.store.holdings()])
        existing = {(topic["name"].lower(), topic["status"]) for topic in self.store.topics()}
        created = []
        for suggestion in suggestions:
            name = suggestion["name"]
            if (name.lower(), "suggested") in existing or (name.lower(), "active") in existing:
                continue
            topic_id = self.store.add_topic(name, list(suggestion["holdings"]), [], "suggested")
            created.append({"id": topic_id, "name": name, "holdings": tuple(suggestion["holdings"])})
        return created

    def accept_topic(self, topic_id: str, *, name: str | None = None, holdings: list[str] | None = None) -> None:
        self.store.update_topic(topic_id, name=name, holdings=holdings, status="active")

    def reject_topic(self, topic_id: str) -> None:
        self.store.update_topic(topic_id, name=None, holdings=None, status="rejected")

    def update_topic(self, topic_id: str, *, holdings: list[str], feeds: list[str]) -> None:
        self.store.update_topic(topic_id, name=None, holdings=holdings, status=None, feeds=feeds)

    def add_topic(self, name: str, holdings: list[str], feeds: list[str] | None = None) -> str:
        return self.store.add_topic(name, holdings, list(feeds or []), "active")

    def set_alert_weight(self, weight: float) -> None:
        self.store.set_setting("alert_weight", str(finite_unit(weight, "alert_weight")))

    def inject_news(self, item: Any) -> None:
        with self._inject_lock:
            self.injected.append(item)

    def run_demo(self, mode: str) -> None:
        from ground_truth.demo import run_demo

        run_demo(self, mode)

    @property
    def runtime(self) -> dict[str, str]:
        mode = getattr(self.harness, "mode", "scripted")
        labels = {
            "custom-code": (
                "Custom-code fallback (ADR-0001): rule-based Researcher and Analyst. "
                "Sessions are kept in this app, not recorded in OpenCrawl."
            ),
            "scripted": "Scripted demonstration: Researcher and Analyst outputs are scripted.",
            "recorded": "Recorded harness sessions in OpenCrawl.",
        }
        return {"mode": mode, "label": labels.get(mode, mode)}

    # ----- the pipeline ----------------------------------------------------

    def tick(self) -> None:
        with self._tick_lock:
            self._tick()

    def run_now(self) -> bool:
        """Start a tick in the background unless one is already running."""
        if not self._tick_lock.acquire(blocking=False):
            return False

        def work() -> None:
            try:
                self._tick()
            finally:
                self._tick_lock.release()

        threading.Thread(target=work, daemon=True).start()
        return True

    def _tick(self) -> None:
        if self.blocked:
            self.store.stage_report(
                "startup",
                "failed",
                f"Recording check failed: {self.recording.get('detail', '')}. Research is paused.",
                self.clock.now(),
            )
            return
        self._stage("scout", self._scout)
        self._stage("supervisor", self._supervise)
        self._stage("research", self._research_ready)
        self._stage("supervisor", self._consume_board)
        self._stage("analysis", self._analyze_ready)
        self._stage("notify", self._notify)
        self._stage("grade", self._grade)

    def _stage(self, name: str, work: Callable[[], str | None]) -> None:
        at = self.clock.now()
        try:
            warning = work()
        except Exception as exc:
            self.store.stage_report(name, "failed", f"{type(exc).__name__}: {exc}", at)
            return
        self.store.stage_report(name, "degraded" if warning else "ok", warning or "", at)

    def _supervise(self) -> str | None:
        self._consume_board()
        self._heal_pending()
        self._close_stale()
        return None

    def grade(self) -> None:
        self._grade()

    # ----- view ------------------------------------------------------------

    def view(self) -> dict[str, Any]:
        holdings = [{"ticker": row["ticker"], "weight": row["weight"]} for row in self.store.holdings()]
        weights = {row["ticker"]: row["weight"] for row in holdings}
        theses = []
        for thesis in self.store.theses():
            body = json.loads(thesis["body"])
            impacts = []
            for impact in self.store.impacts_for(thesis["id"]):
                impacts.append(
                    {
                        "ticker": impact["ticker"],
                        "stance": impact["stance"],
                        "action": impact["action"],
                        "expected": impact["expected"],
                        "direction": impact["direction"],
                        "day_move": impact["day_move"],
                        "volume": impact["volume"],
                        "confidence": impact["confidence"],
                    }
                )
            claims = []
            for claim in self.store.claims_for(thesis["id"]):
                receipt = self.store.receipt(claim["receipt_id"]) if claim["receipt_id"] else None
                claims.append(
                    {
                        "text": claim["text"],
                        "kind": claim["kind"],
                        "url": None if receipt is None else receipt["url"],
                        "passage": None if receipt is None else receipt["passage"],
                        "retrieved_at": None if receipt is None else receipt["retrieved_at"],
                    }
                )
            net = portfolio_net(
                [
                    (Stance(impact["stance"]), weights.get(impact["ticker"], 0.0), Confidence(impact["confidence"]))
                    for impact in impacts
                ]
            )
            theses.append(
                {
                    "id": thesis["id"],
                    "event_id": thesis["event_id"],
                    "version": thesis["version"],
                    "previous_id": thesis["previous_id"],
                    "created_at": thesis["created_at"],
                    "disclaimer": body["disclaimer"],
                    "missing": body["missing"],
                    "changes": body["changes"],
                    "dossier_version": body.get("dossier_version"),
                    "net": net,
                    "color": portfolio_color(net),
                    "lean": portfolio_lean(net),
                    "impacts": impacts,
                    "claims": claims,
                    "receipt_count": sum(1 for claim in claims if claim["kind"] == "evidence" and claim["url"]),
                }
            )
        events = []
        for event in self.store.events():
            has_thesis = self.store.has_thesis(event["id"])
            events.append(
                {
                    "id": event["id"],
                    "status": event["status"],
                    "title": event["title"],
                    "corroboration": event["corroboration"],
                    "related_event_id": event["related_event_id"],
                    "opened_at": event["opened_at"],
                    "last_sighting_at": event["last_sighting_at"],
                    "dossier_session": event["dossier_session"],
                    "research_pending": bool(event["research_pending"]),
                    "revision_pending": bool(event["revision_pending"]),
                    "last_error": event["last_error"],
                    "awaiting_research": self._needs_research(event),
                    "awaiting_analysis": self._needs_analysis(event, has_thesis),
                    "dossiers": self.store.dossier_versions(event["id"]),
                }
            )
        sightings = []
        dismissed = []
        for row in self.store.sightings():
            item = {
                "id": row["id"],
                "url": row["url"],
                "title": row["title"],
                "source": row["source"],
                "topic": row["topic"],
                "holdings": json.loads(row["holdings"]),
                "outcome": row["outcome"],
                "reason": row["reason"],
                "event_id": row["event_id"],
                "posted_at": row["posted_at"],
            }
            sightings.append(item)
            if row["outcome"] == "dismissed":
                dismissed.append(item)
        stages = self.store.stages()
        return {
            "holdings": holdings,
            "topics": self.store.topics(),
            "sightings": sightings,
            "dismissed": dismissed,
            "events": events,
            "theses": theses,
            "track_record": self._track_record(),
            "costs": self._cost_view(),
            "recording": self.recording,
            "runtime": self.runtime,
            "blocked": self.blocked,
            "stages": stages,
            "activity": {
                "awaiting_research": sum(1 for event in events if event["awaiting_research"]),
                "awaiting_analysis": sum(1 for event in events if event["awaiting_analysis"]),
                "event_errors": [
                    {"event_id": event["id"], "title": event["title"], "error": event["last_error"]}
                    for event in events
                    if event["last_error"]
                ],
                "stage_errors": [
                    {"stage": name, **info}
                    for name, info in stages.items()
                    if info["state"] in ("failed", "degraded")
                ],
            },
            "scout_interval_seconds": SCOUT_INTERVAL_SECONDS,
            "alert_weight": float(self.store.setting("alert_weight", "0.25")),
            "harness": {
                "researcher": self.store.setting("researcher_harness", "claude"),
                "analyst": self.store.setting("analyst_harness", "claude"),
            },
            "now": self.clock.now().isoformat(),
            "demo": self.demo_mode,
            "inject_enabled": self.inject_enabled,
            "orders": [],
        }

    def _track_record(self) -> dict[str, Any]:
        rows = self.store.grade_rows()
        graded = {(row["impact_id"], row["horizon"]) for row in rows}
        pending = {horizon: 0 for horizon in HORIZONS}
        for impact in self.store.all_impacts():
            if impact["base_price"] in (None, 0):
                continue
            for horizon in HORIZONS:
                if (impact["id"], horizon) not in graded:
                    pending[horizon] += 1
        horizons = []
        for horizon in HORIZONS:
            subset = [row for row in rows if row["horizon"] == horizon]
            horizons.append(
                {
                    "horizon": horizon,
                    "graded": len(subset),
                    "correct": sum(1 for row in subset if row["correct"]),
                    "pending": pending[horizon],
                }
            )
        grades = [
            {
                "impact_id": row["impact_id"],
                "horizon": row["horizon"],
                "move": row["move"],
                "correct": bool(row["correct"]),
                "ticker": row["ticker"],
                "stance": row["stance"],
                "thesis_id": row["thesis_id"],
                "event_id": row["event_id"],
            }
            for row in rows
        ]
        return {
            "grades": grades,
            "horizons": horizons,
            "correct": sum(1 for grade in grades if grade["correct"]),
            "total": len(grades),
        }

    def _rates(self) -> tuple[bool, float, float, float]:
        search_raw = self.store.setting("you_search_usd", "")
        research_raw = self.store.setting("you_research_usd", "")
        budget_raw = self.store.setting("monthly_budget_usd", "25") or "25"
        priced = search_raw != "" and research_raw != ""
        search_rate = float(search_raw) if search_raw else 0.0
        research_rate = float(research_raw) if research_raw else 0.0
        return priced, search_rate, research_rate, float(budget_raw)

    def _month(self) -> str:
        return self.clock.now().strftime("%Y-%m")

    def _budget_allows(self, kind: str) -> bool:
        priced, search_rate, research_rate, budget = self._rates()
        if not priced:
            return True
        month = self.store.month_costs(self._month())
        spent = spend_usd(month.get("search", 0), month.get("research", 0), search_rate, research_rate)
        rate = search_rate if kind == "search" else research_rate
        return spent + rate <= budget + 1e-9

    def _cost_view(self) -> dict[str, Any]:
        priced, search_rate, research_rate, budget = self._rates()
        month = self.store.month_costs(self._month())
        lifetime = self.store.costs()
        month_search = month.get("search", 0)
        month_research = month.get("research", 0)
        return {
            "month": self._month(),
            "month_name": self.clock.now().strftime("%B %Y"),
            "search": month_search,
            "research": month_research,
            "spend_usd": spend_usd(month_search, month_research, search_rate, research_rate),
            "lifetime_search": lifetime.get("search", 0),
            "lifetime_research": lifetime.get("research", 0),
            "lifetime_spend_usd": spend_usd(
                lifetime.get("search", 0), lifetime.get("research", 0), search_rate, research_rate
            ),
            "priced": priced,
            "enforced": priced,
            "budget_usd": budget,
            "search_usd": self.store.setting("you_search_usd", ""),
            "research_usd": self.store.setting("you_research_usd", ""),
        }

    # ----- Scout -----------------------------------------------------------

    def _scout(self) -> str | None:
        items, failures, attempted = self._gather()
        for item in items:
            if self.store.sighting_by_url(item.url):
                continue
            topic = self.store.topic_named(item.topic)
            holdings = list(topic["holdings"]) if topic else []
            sighting_id = self.store.insert_sighting(
                url=item.url,
                title=item.title,
                source=item.source,
                topic=item.topic,
                holdings=holdings,
                posted_at=self.clock.now(),
            )
            self._post({"k": "sighting", "id": sighting_id})
        if failures and attempted and len(failures) == attempted:
            raise RuntimeError("every news source failed: " + "; ".join(failures[:3]))
        if failures:
            return f"{len(failures)} of {attempted} news sources failed: " + "; ".join(failures[:3])
        return None

    def _gather(self) -> tuple[list[Any], list[str], int]:
        with self._inject_lock:
            items, self.injected = list(self.injected), []
        failures: list[str] = []
        attempted = 0

        def fetch(label: str, call: Callable[[], list[Any]], topic_name: str, source: str) -> None:
            nonlocal attempted
            attempted += 1
            try:
                items.extend(self._tagged(call(), topic_name, source))
            except Exception as exc:
                failures.append(f"{label}: {type(exc).__name__}: {exc}")

        filed: set[str] = set()
        for topic in self.store.topics("active"):
            name = topic["name"]
            fetch(f"Google News '{name}'", lambda name=name: self.news.google_news(name), name, "google-news")
            for feed in topic["feeds"]:
                fetch(f"feed {feed}", lambda feed=feed: self.news.rss(feed), name, "rss")
            if not getattr(self.news, "edgar_enabled", True):
                continue
            for ticker in topic["holdings"]:
                if ticker in filed:
                    continue
                filed.add(ticker)
                fetch(f"SEC EDGAR {ticker}", lambda ticker=ticker: self.news.edgar(ticker), name, "sec-edgar")
        return items, failures, attempted

    def _tagged(self, items: list[Any], topic_name: str, source: str) -> list[Any]:
        return [NewsItem(item.title, item.url, source, topic_name) for item in items]

    def _post(self, payload: dict[str, Any]) -> None:
        text = json.dumps(payload, separators=(",", ":"))
        if len(text) > 512:
            raise ValueError("board post exceeds 512 characters")
        self.board.post(text)

    # ----- Supervisor ------------------------------------------------------

    def _consume_board(self) -> None:
        for message in self.board.fetch_all():
            if self.store.seen(message["id"]):
                continue
            self.store.mark_seen(message["id"])
            payload = json.loads(message["text"])
            if payload["k"] == "sighting":
                self._judge(payload["id"])

    def _heal_pending(self) -> None:
        for row in self.store.sightings():
            if row["outcome"] == "pending":
                self._judge(row["id"])

    def _judge(self, sighting_id: str) -> None:
        row = self.store.sighting(sighting_id)
        if row is None or row["outcome"] != "pending":
            return
        open_events = [
            (event["id"], self.store.event_text(event["id"]))
            for event in self.store.events()
            if event["status"] == "open"
        ]
        closed_events = [
            (event["id"], self.store.event_text(event["id"]))
            for event in self.store.events()
            if event["status"] == "closed"
        ]
        holdings = tuple(json.loads(row["holdings"]))
        kind, payload = classify_sighting(row["title"], row["topic"], holdings, open_events, closed_events)
        if kind == "ambiguous":
            choice = self.llm.judge(row["title"], open_events)
            if choice.startswith("attach:"):
                kind, payload = "attach", choice.split(":", 1)[1]
            elif choice.startswith("dismiss:"):
                kind, payload = "dismiss", choice.split(":", 1)[1]
            elif choice == "new":
                kind, payload = "new", None
            else:
                kind, payload = "dismiss", "ambiguous match unresolved"
        if kind == "dismiss":
            self.store.dismiss_sighting(sighting_id, payload or "dismissed")
            return
        if kind == "attach":
            self._attach(sighting_id, payload or "")
            return
        self._open_event(sighting_id, payload)

    def _attach(self, sighting_id: str, event_id: str) -> None:
        self.store.attach_sighting(sighting_id, event_id)
        self.store.touch_event(event_id, self.clock.now())
        if self.store.dossier(event_id) is not None:
            self.store.set_research_pending(event_id, True)
        if self.store.has_thesis(event_id):
            self.store.set_revision_pending(event_id, True)

    def _open_event(self, sighting_id: str, related: str | None) -> None:
        row = self.store.sighting(sighting_id)
        assert row is not None
        event_id = self.store.create_event(
            title=row["title"],
            opened_at=self.clock.now(),
            related_event_id=related,
        )
        self.store.attach_sighting(sighting_id, event_id)
        self._corroborate(event_id, row["title"])

    def _corroborate(self, event_id: str, title: str) -> None:
        try:
            hits = self.executor.search(title)
        except BudgetExceeded:
            self.store.set_corroboration(event_id, "over budget")
            return
        except Exception:
            self.store.set_corroboration(event_id, "unavailable")
            return
        self.store.set_corroboration(event_id, "corroborated" if hits else "unconfirmed")

    def _close_stale(self) -> None:
        now = self.clock.now()
        for event in self.store.events():
            if event["status"] != "open":
                continue
            last = datetime.fromisoformat(event["last_sighting_at"])
            if now >= quiet_deadline(last):
                self.store.close_event(event["id"])

    # ----- Researcher ------------------------------------------------------

    def _needs_research(self, event: Any) -> bool:
        return (
            event["status"] == "open"
            and event["corroboration"] == "corroborated"
            and (not event["dossier_session"] or bool(event["research_pending"]))
        )

    def _needs_analysis(self, event: Any, has_thesis: bool) -> bool:
        if not event["dossier_session"] or event["research_pending"]:
            return False
        return not has_thesis or bool(event["revision_pending"])

    def _run_each(self, event_ids: list[str], work: Callable[[str], None], label: str) -> str | None:
        if not event_ids:
            return None
        failed: list[str] = []

        def guarded(event_id: str) -> None:
            try:
                work(event_id)
            except Exception as exc:
                self.store.set_event_error(event_id, f"{label} failed: {type(exc).__name__}: {exc}")
                failed.append(event_id)
            else:
                self.store.set_event_error(event_id, None)

        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(event_ids))) as pool:
            list(pool.map(guarded, event_ids))
        if failed:
            return f"{len(failed)} of {len(event_ids)} Events failed {label.lower()}"
        return None

    def _research_ready(self) -> str | None:
        pending = [event["id"] for event in self.store.events() if self._needs_research(event)]
        return self._run_each(pending, self._research_one, "Research")

    def _uncovered(self, event_id: str, covered: set[str], fallback_since: str | None) -> list[Any]:
        rows = [row for row in self.store.sightings() if row["event_id"] == event_id]
        if covered:
            return [row for row in rows if row["id"] not in covered]
        if fallback_since is None:
            return rows
        return [row for row in rows if row["posted_at"] > fallback_since]

    def _research_one(self, event_id: str) -> None:
        started = self.clock.now()
        tools = Toolset(self, event_id, started)
        event = self.store.event(event_id)
        versions = self.store.dossier_versions(event_id)
        previous = versions[-1] if versions else None
        covered = {sid for version in versions for sid in version.get("sightings") or []}
        new_rows = self._uncovered(event_id, covered, previous["created_at"] if previous else None)
        prompt = json.dumps(
            {
                "role": "researcher",
                "event": event_id,
                "title": event["title"],
                "holdings": self.store.event_holdings(event_id),
                "revision": previous is not None,
                "new_sightings": [row["title"] for row in new_rows],
            }
        )
        result = self.harness.run("researcher", event_id, prompt, tools)
        answers = {question: "" for question in QUESTIONS}
        answers.update(result.payload.get("answers") or {})
        missing = list(result.payload.get("missing") or [])
        for question in QUESTIONS:
            if not str(answers.get(question) or "").strip() and question not in missing:
                missing.append(question)
        body = {
            "answers": answers,
            "missing": missing,
            "receipts": list(tools.receipts),
            "sightings": [row["id"] for row in new_rows],
        }
        session_id = result.session_id
        self.store.save_dossier(event_id, session_id, body, self.clock.now())
        self.sessions.store_session(session_id, json.dumps(body))
        self._post({"k": "dossier-ready", "event": event_id, "session": session_id})

    # ----- Analyst ---------------------------------------------------------

    def _analyze_ready(self) -> str | None:
        pending = [
            event["id"]
            for event in self.store.events()
            if self._needs_analysis(event, self.store.has_thesis(event["id"]))
        ]
        return self._run_each(pending, self._analyze_one, "Analysis")

    def _analyze_one(self, event_id: str) -> None:
        event = self.store.event(event_id)
        versions = self.store.dossier_versions(event_id)
        dossier = versions[-1] if versions else {"answers": {}, "missing": [], "receipts": [], "version": None}
        previous = self.store.latest_thesis_id(event_id)
        new_titles: list[str] = []
        if previous:
            row = next(item for item in self.store.theses() if item["id"] == previous)
            covered = set(json.loads(row["body"]).get("sightings") or [])
            new_titles = [item["title"] for item in self._uncovered(event_id, covered, row["created_at"])]
        tools = Toolset(self, event_id, self.clock.now())
        prompt = json.dumps(
            {
                "role": "analyst",
                "event": event_id,
                "title": event["title"],
                "session": event["dossier_session"],
                "revision": previous is not None,
                "new_sightings": new_titles,
                "holdings": self.store.event_holdings(event_id),
            }
        )
        result = self.harness.run("analyst", event_id, prompt, tools)
        known = {receipt for version in versions for receipt in version.get("receipts") or []}
        self._accept_thesis(event_id, dossier, known, previous, new_titles, result.payload)

    def _accept_thesis(
        self,
        event_id: str,
        dossier: dict[str, Any],
        known: set[str],
        previous: str | None,
        new_titles: list[str],
        payload: dict[str, Any],
    ) -> None:
        missing = list(dossier.get("missing") or [])
        for fact in payload.get("missing") or []:
            if fact not in missing:
                missing.append(fact)
        claims = []
        for claim in payload.get("claims") or []:
            receipt_id = claim.get("receipt_id")
            text = str(claim.get("text") or "").strip()
            if not text:
                continue
            if receipt_id and receipt_id in known:
                claims.append({"text": text, "kind": "evidence", "receipt_id": receipt_id})
            else:
                claims.append({"text": text, "kind": "inference", "receipt_id": None})
        holdings = self.store.event_holdings(event_id)
        supplied = {item["ticker"].upper(): item for item in payload.get("impacts") or []}
        impacts = []
        today = self.clock.now().date()
        for ticker in holdings:
            raw = supplied.get(ticker, {})
            stance = parse_stance(raw.get("stance", Stance.NEUTRAL.value))
            action = parse_action(raw.get("action", Action.HOLD.value))
            confidence = cap_confidence(parse_confidence(raw.get("confidence", Confidence.LOW.value)), missing)
            current = self.quotes.quote(ticker, today)
            prior = self.quotes.quote(ticker, previous_trading_day(today))
            day_move = None
            if current is not None and prior is not None and prior.price:
                day_move = (current.price - prior.price) / prior.price
            direction = raw.get("direction")
            if not direction:
                direction = {Stance.BULL: "up", Stance.BEAR: "down", Stance.NEUTRAL: "flat"}[stance]
            expected = raw.get("expected") or "No scripted finding for this Holding."
            impacts.append(
                {
                    "ticker": ticker,
                    "stance": stance.value,
                    "action": action.value,
                    "expected": expected,
                    "direction": direction,
                    "day_move": day_move,
                    "volume": None if current is None else current.volume,
                    "confidence": confidence.value,
                    "base_price": None if current is None or not current.price else current.price,
                    "thesis_day": today.isoformat(),
                }
            )
        changes = list(payload.get("changes") or [])
        if previous and not changes:
            changes = [f"New Sighting attached: {title}" for title in new_titles] or ["New Sighting attached"]
        body = {
            "disclaimer": DISCLAIMER,
            "missing": missing,
            "changes": changes,
            "dossier_version": dossier.get("version"),
            "sightings": [row["id"] for row in self.store.sightings() if row["event_id"] == event_id],
        }
        self.store.save_thesis(
            event_id=event_id,
            version=self.store.next_version(event_id),
            previous_id=previous,
            body=body,
            created_at=self.clock.now(),
            impacts=impacts,
            claims=claims,
        )
        self.store.set_revision_pending(event_id, False)

    # ----- delivery and grading -------------------------------------------

    def _notify(self) -> None:
        threshold = float(self.store.setting("alert_weight", "0.25"))
        for thesis in self.store.unnotified_theses():
            for impact in self.store.impacts_for(thesis["id"]):
                stance = Stance(impact["stance"])
                confidence = Confidence(impact["confidence"])
                weight = self.store.weight(impact["ticker"])
                if stance is Stance.NEUTRAL or confidence is not Confidence.HIGH or weight < threshold:
                    continue
                url = f"{self.dashboard_url}/thesis/{thesis['id']}"
                text = f"{impact['ticker']} {stance.value} {impact['action']}. {DISCLAIMER} {url}"
                self.discord.push(text, url)
            self.store.mark_notified(thesis["id"])

    def _grade(self) -> None:
        today = self.clock.now().date()
        for impact in self.store.all_impacts():
            thesis_day = datetime.fromisoformat(impact["thesis_day"]).date()
            base = impact["base_price"]
            if base in (None, 0):
                continue
            for horizon in HORIZONS:
                if self.store.has_grade(impact["id"], horizon):
                    continue
                due = add_trading_days(thesis_day, horizon)
                if today < due:
                    continue
                later = self.quotes.quote(impact["ticker"], due)
                if later is None:
                    continue
                move = (later.price - base) / base
                correct = grade_stance(Stance(impact["stance"]), move)
                self.store.add_grade(impact["id"], horizon, move, correct)


class SessionResult:
    def __init__(self, session_id: str, payload: dict[str, Any]):
        self.session_id = session_id
        self.payload = payload


class ScriptedHarness:
    """Runs Researcher and Analyst as in-process functions.

    mode="custom-code" is ADR-0001's fallback: the built-in rule-based scripts
    run against live adapters. mode="scripted" is for tests and demos.
    """

    def __init__(self, mode: str = "scripted") -> None:
        self.mode = mode
        self._scripts: dict[str, Any] = {}
        self.runs: list[tuple[str, str]] = []
        self.max_in_flight = 0
        self._current = 0
        self._lock = threading.Lock()

    def on(self, role: str, script: Any) -> None:
        self._scripts[role] = script

    def run(self, role: str, event_id: str, prompt: str, tools: Toolset) -> SessionResult:
        with self._lock:
            self._current += 1
            self.max_in_flight = max(self.max_in_flight, self._current)
            self.runs.append((role, event_id))
        try:
            script = self._scripts.get(role, _default_script)
            payload = script(event_id, prompt, tools)
            return SessionResult(uuid.uuid4().hex, payload)
        finally:
            with self._lock:
                self._current -= 1


def _call_research(tools: Toolset, tier: str, query: str) -> dict[str, Any]:
    try:
        return tools.research(tier, query)
    except Exception:
        return {"error": "unavailable"}


def _keep_receipts(tools: Toolset, payload: dict[str, Any]) -> None:
    for source in payload.get("sources") or []:
        if not isinstance(source, dict):
            continue
        url = source.get("url")
        passage = source.get("passage")
        if isinstance(url, str) and isinstance(passage, str) and url and passage:
            tools.check_receipt(url, passage)


def _research_event(info: dict[str, Any], tools: Toolset) -> dict[str, Any]:
    title = str(info.get("title") or "")
    if info.get("revision") and info.get("new_sightings"):
        title = "; ".join(str(item) for item in info["new_sightings"])
    tools.search_memory(title)
    lite = _call_research(tools, "lite", title)
    _keep_receipts(tools, lite)
    holdings = [str(ticker) for ticker in info.get("holdings") or []]
    affected = any(holding_effect(ticker, title)["stance"] != "Neutral" for ticker in holdings)
    standard: dict[str, Any] = {}
    if affected and not lite.get("error"):
        standard = _call_research(tools, "standard", f"counter-evidence {title}")
        _keep_receipts(tools, standard)
    answers = {
        "exposure": str(lite.get("text") or ""),
        "magnitude": str(lite.get("text") or ""),
        "timing": "",
        "counter-evidence": str(standard.get("text") or ""),
    }
    missing = [question for question in QUESTIONS if not answers[question].strip()]
    if lite.get("error"):
        missing = list(QUESTIONS)
    return {"answers": answers, "missing": missing, "claims": []}


def _analyze_event(info: dict[str, Any], tools: Toolset) -> dict[str, Any]:
    receipt_ids: list[str] = []
    session_id = str(info.get("session") or "")
    if session_id:
        try:
            body = json.loads(tools.fetch_session(session_id))
        except (KeyError, json.JSONDecodeError, TypeError):
            body = {}
        if isinstance(body, dict):
            receipt_ids = [item for item in body.get("receipts") or [] if isinstance(item, str)]
    event_title = " ".join([str(info.get("title") or ""), *[str(item) for item in info.get("new_sightings") or []]]).strip()
    impacts = []
    for ticker in info.get("holdings") or []:
        tools.quote(str(ticker))
        effect = holding_effect(str(ticker), event_title)
        impacts.append({"ticker": ticker, "confidence": "low", **effect})
    if receipt_ids:
        claims = [{"text": str(info.get("title") or "Recorded finding."), "receipt_id": receipt_ids[-1]}]
    else:
        claims = [{"text": "No passage was confirmed against a source page.", "receipt_id": None}]
    return {"impacts": impacts, "claims": claims, "missing": [], "changes": []}


def _default_script(event_id: str, prompt: str, tools: Toolset) -> dict[str, Any]:
    info = json.loads(prompt)
    if info["role"] == "researcher":
        return _research_event(info, tools)
    return _analyze_event(info, tools)
