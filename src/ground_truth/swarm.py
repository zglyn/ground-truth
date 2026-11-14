from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from ground_truth.domain import (
    DISCLAIMER,
    QUESTIONS,
    SCOUT_INTERVAL_SECONDS,
    Action,
    Confidence,
    Stance,
    add_trading_days,
    cap_confidence,
    classify_sighting,
    grade_stance,
    holding_effect,
    parse_action,
    parse_confidence,
    parse_stance,
    portfolio_color,
    portfolio_net,
    spend_usd,
    previous_trading_day,
    quiet_deadline,
)
from ground_truth.store import Store

RESEARCH_CALL_BUDGET = 4
RESEARCH_TIME_BUDGET_SECONDS = 180


class ManualClock:
    def __init__(self, start: datetime):
        self._now = start
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def advance(self, *, hours: float = 0, minutes: float = 0, trading_days: int = 0) -> None:
        with self._lock:
            from datetime import timedelta

            self._now += timedelta(hours=hours, minutes=minutes)
            left = trading_days
            while left:
                self._now += timedelta(days=1)
                if self._now.weekday() < 5:
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
        cached = self._swarm.store.query_get(query)
        if cached is not None:
            if tier == "lite":
                self.lite_done = True
            return {"cached": True, **cached}
        result = self._swarm.you.research(tier, query)
        self._swarm.store.query_put(query, result, self._swarm.clock.now())
        self._swarm.store.add_cost("research")
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

    def quote(self, ticker: str) -> dict[str, float]:
        today = self._swarm.clock.now().date()
        current = self._swarm.quotes.quote(ticker, today)
        return {"price": current.price, "volume": current.volume}


class Swarm:
    def __init__(
        self,
        path: str,
        *,
        clock: ManualClock,
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
        self._tick_lock = threading.Lock()
        self.recording = {"ok": False, "detail": "not checked"}
        if self.store.setting("alert_weight", "") == "":
            self.store.set_setting("alert_weight", "0.25")
        if self.store.setting("researcher_harness", "") == "":
            self.store.set_setting("researcher_harness", "claude")
        if self.store.setting("analyst_harness", "") == "":
            self.store.set_setting("analyst_harness", "claude")

    def start(self) -> dict[str, Any]:
        harness_name = self.store.setting("researcher_harness", "claude")
        if harness_name == "cursor":
            self.recording = {"ok": False, "detail": "Cursor is not used for the Researcher"}
            return self.recording
        self.recording = self.recording_probe.check(harness_name)
        return self.recording

    def configure(self, *, researcher: str = "claude", analyst: str = "claude") -> None:
        if researcher == "cursor":
            raise ValueError("Cursor is not used for the Researcher")
        self.store.set_setting("researcher_harness", researcher)
        self.store.set_setting("analyst_harness", analyst)

    def set_holdings(self, holdings: list[tuple[str, float]]) -> None:
        self.store.set_holdings([(ticker.upper(), float(weight)) for ticker, weight in holdings])

    def save_holding(self, ticker: str, weight: float) -> None:
        symbol = ticker.upper()
        rows = [(row["ticker"], float(row["weight"])) for row in self.store.holdings() if row["ticker"] != symbol]
        rows.append((symbol, weight))
        self.store.set_holdings(rows)

    def save_rates(self, search_usd: str, research_usd: str, budget_usd: str) -> None:
        self.store.set_setting("you_search_usd", search_usd)
        self.store.set_setting("you_research_usd", research_usd)
        self.store.set_setting("monthly_budget_usd", budget_usd)

    def remove_holding(self, ticker: str) -> None:
        self.store.remove_holding(ticker.upper())

    def suggest_topics(self) -> list[dict[str, Any]]:
        suggestions = self.llm.suggest_topics([row["ticker"] for row in self.store.holdings()])
        created = []
        for suggestion in suggestions:
            topic_id = self.store.add_topic(suggestion["name"], list(suggestion["holdings"]), [], "suggested")
            created.append({"id": topic_id, "name": suggestion["name"], "holdings": tuple(suggestion["holdings"])})
        return created

    def accept_topic(self, topic_id: str, *, name: str | None = None, holdings: list[str] | None = None) -> None:
        self.store.update_topic(topic_id, name=name, holdings=holdings, status="active")

    def reject_topic(self, topic_id: str) -> None:
        self.store.update_topic(topic_id, name=None, holdings=None, status="rejected")

    def add_topic(self, name: str, holdings: list[str], feeds: list[str] | None = None) -> str:
        return self.store.add_topic(name, holdings, list(feeds or []), "active")

    def set_alert_weight(self, weight: float) -> None:
        self.store.set_setting("alert_weight", str(weight))

    def inject_news(self, item: Any) -> None:
        self.injected.append(item)

    def run_demo(self, mode: str) -> None:
        from ground_truth.demo import run_demo

        run_demo(self, mode)

    def tick(self) -> None:
        with self._tick_lock:
            self._scout()
            self._consume_board()
            self._heal_pending()
            self._close_stale()
            self._research_ready()
            self._consume_board()
            self._analyze_ready()
            self._notify()
            self._grade()

    def grade(self) -> None:
        self._grade()

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
                    "net": net,
                    "color": portfolio_color(net),
                    "impacts": impacts,
                    "claims": claims,
                }
            )
        events = []
        for event in self.store.events():
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
            }
            sightings.append(item)
            if row["outcome"] == "dismissed":
                dismissed.append(item)
        grades = self.store.grades()
        correct = sum(1 for grade in grades if grade["correct"])
        return {
            "holdings": holdings,
            "topics": self.store.topics(),
            "sightings": sightings,
            "dismissed": dismissed,
            "events": events,
            "theses": theses,
            "track_record": {
                "grades": [
                    {
                        "impact_id": grade["impact_id"],
                        "horizon": grade["horizon"],
                        "move": grade["move"],
                        "correct": bool(grade["correct"]),
                    }
                    for grade in grades
                ],
                "correct": correct,
                "total": len(grades),
            },
            "costs": self._cost_view(),
            "recording": self.recording,
            "scout_interval_seconds": SCOUT_INTERVAL_SECONDS,
            "alert_weight": float(self.store.setting("alert_weight", "0.25")),
            "orders": [],
        }

    def _cost_view(self) -> dict[str, Any]:
        counts = self.store.costs()
        search_count = counts.get("search", 0)
        research_count = counts.get("research", 0)
        search_raw = self.store.setting("you_search_usd", "")
        research_raw = self.store.setting("you_research_usd", "")
        budget_raw = self.store.setting("monthly_budget_usd", "25")
        priced = search_raw != "" and research_raw != ""
        search_rate = float(search_raw) if search_raw else 0.0
        research_rate = float(research_raw) if research_raw else 0.0
        return {
            "search": search_count,
            "research": research_count,
            "priced": priced,
            "spend_usd": spend_usd(search_count, research_count, search_rate, research_rate),
            "budget_usd": float(budget_raw or "25"),
            "search_usd": search_raw,
            "research_usd": research_raw,
        }

    def _scout(self) -> None:
        for item in self._gather():
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

    def _gather(self) -> list[Any]:
        items = list(self.injected)
        self.injected.clear()
        for topic in self.store.topics("active"):
            items.extend(self._tagged(self.news.google_news(topic["name"]), topic["name"], "google-news"))
            for feed in topic["feeds"]:
                items.extend(self._tagged(self.news.rss(feed), topic["name"], "rss"))
            for ticker in topic["holdings"]:
                items.extend(self._tagged(self.news.edgar(ticker), topic["name"], "sec-edgar"))
        return items

    def _tagged(self, items: list[Any], topic_name: str, source: str) -> list[Any]:
        from ground_truth.domain import NewsItem

        return [NewsItem(item.title, item.url, source, topic_name) for item in items]

    def _post(self, payload: dict[str, Any]) -> None:
        text = json.dumps(payload, separators=(",", ":"))
        if len(text) > 512:
            raise ValueError("board post exceeds 512 characters")
        self.board.post(text)

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
            hits = self.you.search(title)
        except Exception:
            self.store.set_corroboration(event_id, "unavailable")
            return
        self.store.add_cost("search")
        self.store.set_corroboration(event_id, "corroborated" if hits else "unconfirmed")

    def _close_stale(self) -> None:
        now = self.clock.now()
        for event in self.store.events():
            if event["status"] != "open":
                continue
            last = datetime.fromisoformat(event["last_sighting_at"])
            if now >= quiet_deadline(last):
                self.store.close_event(event["id"])

    def _research_ready(self) -> None:
        pending = []
        for event in self.store.events():
            if event["status"] == "open" and event["corroboration"] == "corroborated" and not event["dossier_session"]:
                pending.append(event["id"])
        if not pending:
            return
        with ThreadPoolExecutor(max_workers=max(1, len(pending))) as pool:
            list(pool.map(self._research_one, pending))

    def _research_one(self, event_id: str) -> None:
        started = self.clock.now()
        tools = Toolset(self, event_id, started)
        holdings = self.store.event_holdings(event_id)
        prompt = json.dumps({"role": "researcher", "event": event_id, "title": self.store.event(event_id)["title"], "holdings": holdings})
        result = self.harness.run("researcher", event_id, prompt, tools)
        answers = {question: "" for question in QUESTIONS}
        answers.update(result.payload.get("answers") or {})
        missing = list(result.payload.get("missing") or [])
        for question in QUESTIONS:
            if not str(answers.get(question) or "").strip() and question not in missing:
                missing.append(question)
        body = {"answers": answers, "missing": missing, "receipts": list(tools.receipts)}
        session_id = result.session_id
        self.store.save_dossier(event_id, session_id, body)
        self.sessions.store_session(session_id, json.dumps(body))
        self._post({"k": "dossier-ready", "event": event_id, "session": session_id})

    def _analyze_ready(self) -> None:
        pending = []
        for event in self.store.events():
            if not event["dossier_session"]:
                continue
            if not self.store.has_thesis(event["id"]) or event["revision_pending"]:
                pending.append(event["id"])
        if not pending:
            return
        with ThreadPoolExecutor(max_workers=max(1, len(pending))) as pool:
            list(pool.map(self._analyze_one, pending))

    def _analyze_one(self, event_id: str) -> None:
        event = self.store.event(event_id)
        dossier = self.store.dossier(event_id) or {"answers": {}, "missing": [], "receipts": []}
        previous = self.store.latest_thesis_id(event_id)
        tools = Toolset(self, event_id, self.clock.now())
        prompt = json.dumps(
            {
                "role": "analyst",
                "event": event_id,
                "title": event["title"],
                "session": event["dossier_session"],
                "revision": previous is not None,
                "holdings": self.store.event_holdings(event_id),
            }
        )
        result = self.harness.run("analyst", event_id, prompt, tools)
        self._accept_thesis(event_id, dossier, previous, result.payload)

    def _accept_thesis(self, event_id: str, dossier: dict[str, Any], previous: str | None, payload: dict[str, Any]) -> None:
        known = set(dossier.get("receipts") or [])
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
            day_move = None if prior.price == 0 else (current.price - prior.price) / prior.price
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
                    "volume": current.volume,
                    "confidence": confidence.value,
                    "base_price": current.price,
                    "thesis_day": today.isoformat(),
                }
            )
        if previous and not payload.get("changes"):
            changes = ["New Sighting attached"]
        else:
            changes = list(payload.get("changes") or [])
        body = {"disclaimer": DISCLAIMER, "missing": missing, "changes": changes}
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

    def _notify(self) -> None:
        threshold = float(self.store.setting("alert_weight", "0.25"))
        for thesis in self.store.unnotified_theses():
            pushed = False
            for impact in self.store.impacts_for(thesis["id"]):
                stance = Stance(impact["stance"])
                confidence = Confidence(impact["confidence"])
                weight = self.store.weight(impact["ticker"])
                if stance is Stance.NEUTRAL or confidence is not Confidence.HIGH or weight < threshold:
                    continue
                url = f"{self.dashboard_url}/thesis/{thesis['id']}"
                text = f"{impact['ticker']} {stance.value} {impact['action']}. {DISCLAIMER} {url}"
                self.discord.push(text, url)
                pushed = True
            if not pushed:
                self.store.mark_notified(thesis["id"])
            else:
                self.store.mark_notified(thesis["id"])

    def _grade(self) -> None:
        today = self.clock.now().date()
        for impact in self.store.all_impacts():
            thesis_day = datetime.fromisoformat(impact["thesis_day"]).date() if "T" in impact["thesis_day"] else datetime.fromisoformat(impact["thesis_day"]).date()
            base = impact["base_price"]
            if base in (None, 0):
                continue
            for horizon in (1, 5, 20):
                if self.store.has_grade(impact["id"], horizon):
                    continue
                due = add_trading_days(thesis_day, horizon)
                if today < due:
                    continue
                later = self.quotes.quote(impact["ticker"], due)
                move = (later.price - base) / base
                correct = grade_stance(Stance(impact["stance"]), move)
                self.store.add_grade(impact["id"], horizon, move, correct)


class SessionResult:
    def __init__(self, session_id: str, payload: dict[str, Any]):
        self.session_id = session_id
        self.payload = payload


class ScriptedHarness:
    def __init__(self) -> None:
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
    event_title = str(info.get("title") or "")
    impacts = []
    for ticker in info.get("holdings") or []:
        tools.quote(str(ticker))
        effect = holding_effect(str(ticker), event_title)
        impacts.append({"ticker": ticker, "confidence": "low", **effect})
    if receipt_ids:
        claims = [{"text": event_title or "Recorded finding.", "receipt_id": receipt_ids[0]}]
    else:
        claims = [{"text": "No passage was confirmed against a source page.", "receipt_id": None}]
    changes = ["New Sighting attached"] if info.get("revision") else []
    return {"impacts": impacts, "claims": claims, "missing": [], "changes": changes}


def _default_script(event_id: str, prompt: str, tools: Toolset) -> dict[str, Any]:
    info = json.loads(prompt)
    if info["role"] == "researcher":
        return _research_event(info, tools)
    return _analyze_event(info, tools)
