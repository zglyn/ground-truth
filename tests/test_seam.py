from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from ground_truth.dashboard import bind_address, render_html, serve
from ground_truth.adapters import edgar_user_agent
from ground_truth.__main__ import _Llm
from ground_truth.domain import QUESTIONS, NewsItem
from ground_truth.opencrawl import OpenCrawlBoard, OpenCrawlSessions
from ground_truth.recording_check import allows_start, recording_status
from ground_truth.swarm import ManualClock, ScriptedHarness, Swarm
from tests.fakes import (
    FakeBoard,
    FakeDiscord,
    FakeLlm,
    FakeNews,
    FakePages,
    FakeQuotes,
    FakeRecording,
    FakeSessions,
    FakeYou,
)

START = datetime(2026, 1, 7, 15, 0, 0)
PASSAGE = "The mine halted output overnight."


def answered() -> dict[str, str]:
    return {question: "answered" for question in QUESTIONS}


class World:
    def __init__(self, path: Path):
        self.clock = ManualClock(START)
        self.news = FakeNews()
        self.you = FakeYou()
        self.pages = FakePages(f"Report. {PASSAGE} More soon.")
        self.quotes = FakeQuotes()
        self.board = FakeBoard()
        self.sessions = FakeSessions()
        self.harness = ScriptedHarness()
        self.discord = FakeDiscord()
        self.llm = FakeLlm()
        self.recording = FakeRecording()
        self.swarm = Swarm(
            str(path),
            clock=self.clock,
            news=self.news,
            you=self.you,
            pages=self.pages,
            quotes=self.quotes,
            board=self.board,
            sessions=self.sessions,
            harness=self.harness,
            discord=self.discord,
            llm=self.llm,
            recording=self.recording,
        )


def world(tmp_path: Path) -> World:
    return World(tmp_path / "swarm.sqlite")


def portfolio(env: World) -> None:
    env.swarm.set_holdings([("TSLA", 0.4), ("ALB", 0.2)])
    env.swarm.add_topic("lithium", ["TSLA", "ALB"])


def story(title: str, url: str | None = None) -> NewsItem:
    return NewsItem(title=title, url=url or f"https://example.test/{title.replace(' ', '-')}", source="google-news", topic="lithium")


def test_portfolio_topics_and_links(tmp_path: Path) -> None:
    env = world(tmp_path)
    env.swarm.set_holdings([("TSLA", 0.5), ("ALB", 0.2)])
    env.swarm.remove_holding("ALB")
    env.llm.suggestions = [{"name": "EV supply chain", "holdings": ["TSLA"]}]
    suggested = env.swarm.suggest_topics()
    env.swarm.accept_topic(suggested[0]["id"], name="EV supply", holdings=["TSLA"])
    rejected = env.swarm.suggest_topics()
    env.swarm.reject_topic(rejected[0]["id"])
    env.swarm.add_topic("lithium", ["TSLA"], feeds=["https://example.test/lithium.xml"])
    view = env.swarm.view()
    assert [row["ticker"] for row in view["holdings"]] == ["TSLA"]
    active = [topic for topic in view["topics"] if topic["status"] == "active"]
    names = {topic["name"]: topic["holdings"] for topic in active}
    assert names["EV supply"] == ("TSLA",)
    assert names["lithium"] == ("TSLA",)
    assert any(topic["status"] == "rejected" for topic in view["topics"])


def test_scout_posts_each_source_once(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    item = story("Chile lithium mine strike cuts supply")
    env.news.google = [item]
    env.news.feeds["https://example.test/extra.xml"] = [story("Lithium warehouse fire reported", "https://example.test/warehouse")]
    env.swarm.add_topic("lithium", ["TSLA", "ALB"], feeds=["https://example.test/extra.xml"])
    filing = story("ALB files an 8-K", "https://example.test/8k")
    env.news.filings = [("ALB", filing)]
    env.swarm.tick()
    env.swarm.tick()
    urls = {row["url"] for row in env.swarm.view()["sightings"]}
    assert urls == {item.url, "https://example.test/warehouse", filing.url}
    assert any(row["source"] == "sec-edgar" for row in env.swarm.view()["sightings"])
    assert ("rss", "https://example.test/extra.xml") in env.news.calls
    assert env.swarm.view()["scout_interval_seconds"] == 300


def test_groups_sightings_and_dismisses_irrelevant(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    env.swarm.inject_news(story("Chile lithium mine strike cuts supply"))
    env.swarm.tick()
    env.swarm.inject_news(story("Chile lithium mine strike spreads further", "https://example.test/second"))
    env.swarm.inject_news(story("Lakers win finals", "https://example.test/lakers"))
    env.swarm.tick()
    view = env.swarm.view()
    assert len(view["events"]) == 1
    assert len([row for row in view["sightings"] if row["outcome"] == "attached"]) == 2
    assert view["dismissed"][0]["reason"] == "unrelated to Watch Topic lithium"
    assert len([run for run in env.harness.runs if run[0] == "researcher"]) == 1


def test_ambiguous_match_uses_the_model(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    env.swarm.inject_news(story("alpha lithium red west"))
    env.swarm.tick()
    env.swarm.inject_news(story("bravo lithium blue east", "https://example.test/bravo"))
    env.swarm.tick()
    first = env.swarm.view()["events"][0]["id"]
    env.llm.answer = f"attach:{first}"
    env.swarm.inject_news(story("alpha bravo lithium red blue", "https://example.test/mix"))
    env.swarm.tick()
    attached = [row for row in env.swarm.view()["sightings"] if row["title"] == "alpha bravo lithium red blue"]
    assert attached[0]["event_id"] == first
    assert len(env.swarm.view()["events"]) == 2


def test_duplicate_board_delivery_is_one_event(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    env.swarm.inject_news(story("Chile lithium mine strike cuts supply"))
    env.swarm.tick()
    env.swarm.tick()
    assert len(env.swarm.view()["events"]) == 1
    assert len(env.swarm.view()["sightings"]) == 1


def test_unconfirmed_event_is_not_researched(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    env.you.search_hits = []
    env.swarm.inject_news(story("Chile lithium mine strike cuts supply"))
    env.swarm.tick()
    view = env.swarm.view()
    assert view["events"][0]["corroboration"] == "unconfirmed"
    assert view["theses"] == []
    assert env.swarm.view()["costs"]["search"] == 1
    assert env.swarm.view()["costs"]["research"] == 0


def test_you_com_outage_skips_research(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    env.you.fail = True
    env.swarm.inject_news(story("Chile lithium mine strike cuts supply"))
    env.swarm.tick()
    assert env.swarm.view()["events"][0]["corroboration"] == "unavailable"
    assert env.swarm.view()["theses"] == []


def test_closure_and_related_event(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    env.swarm.inject_news(story("Chile lithium mine strike cuts supply"))
    env.swarm.tick()
    original = env.swarm.view()["events"][0]["id"]
    env.clock.advance(hours=73)
    env.swarm.tick()
    assert env.swarm.view()["events"][0]["status"] == "closed"
    env.swarm.inject_news(story("Chile lithium mine strike spreads further", "https://example.test/later"))
    env.swarm.tick()
    events = env.swarm.view()["events"]
    assert events[0]["status"] == "closed"
    assert events[1]["related_event_id"] == original
    assert events[1]["status"] == "open"


def test_restart_ignores_repeated_board_messages(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    env.swarm.inject_news(story("Chile lithium mine strike cuts supply"))
    env.swarm.tick()
    env.swarm.store.close()
    again = Swarm(
        str(tmp_path / "swarm.sqlite"),
        clock=env.clock,
        news=env.news,
        you=env.you,
        pages=env.pages,
        quotes=env.quotes,
        board=FakeBoard(),
        sessions=env.sessions,
        harness=env.harness,
        discord=env.discord,
        llm=env.llm,
        recording=env.recording,
    )
    again.tick()
    assert len(again.view()["events"]) == 1
    assert len(again.view()["sightings"]) == 1


def test_parallel_research_on_two_events(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    barrier = threading.Barrier(2, timeout=5)

    def researcher(event_id: str, prompt: str, tools: Any) -> dict:
        barrier.wait()
        return {"answers": answered(), "missing": []}

    env.harness.on("researcher", researcher)
    env.swarm.inject_news(story("lithium refinery fire in nevada"))
    env.swarm.inject_news(story("lithium shipping lane closes", "https://example.test/lane"))
    env.swarm.tick()
    assert env.harness.max_in_flight >= 2
    assert len(env.swarm.view()["events"]) == 2
    assert len(env.swarm.view()["theses"]) == 2


def test_research_budget_query_log_and_receipts(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)

    def over_budget(event_id: str, prompt: str, tools: Any) -> dict:
        errors = []
        for index in range(5):
            result = tools.research("lite", f"question {index}")
            if result.get("error"):
                errors.append(result["error"])
        return {"answers": answered(), "missing": errors}

    env.harness.on("researcher", over_budget)
    env.swarm.inject_news(story("Chile lithium mine strike cuts supply"))
    env.swarm.tick()
    assert env.swarm.view()["costs"]["research"] == 4
    assert "budget" in env.swarm.view()["theses"][0]["missing"]

    def standard_first(event_id: str, prompt: str, tools: Any) -> dict:
        blocked = tools.research("standard", "deep dive")
        assert blocked["error"] == "lite required first"
        tools.research("lite", "lite first")
        tools.research("standard", "deep dive")
        return {"answers": answered(), "missing": []}

    env.harness.on("researcher", standard_first)
    env.swarm.inject_news(story("lithium xylophone quota announced", "https://example.test/graphite"))
    env.swarm.tick()
    research_calls = [call for call in env.you.calls if call[0] == "research"]
    assert ("research", "standard", "deep dive") in research_calls
    assert sum(1 for call in research_calls if call[1] == "standard" and call[2] == "deep dive") == 1

    def shared(event_id: str, prompt: str, tools: Any) -> dict:
        tools.research("lite", "shared-query")
        return {"answers": answered(), "missing": []}

    paid_before = env.swarm.view()["costs"]["research"]
    env.harness.on("researcher", shared)
    env.swarm.inject_news(story("lithium quokka habitat listed", "https://example.test/nickel"))
    env.swarm.tick()
    env.swarm.inject_news(story("lithium narwhal census published", "https://example.test/cobalt"))
    env.swarm.tick()
    assert env.swarm.view()["costs"]["research"] == paid_before + 1

    def receipts(event_id: str, prompt: str, tools: Any) -> dict:
        tools.check_receipt("https://example.test/page", "invented sentence")
        good = tools.check_receipt("https://example.test/page", PASSAGE)
        return {"answers": answered(), "missing": [], "good": good["receipt_id"]}

    def analyst(event_id: str, prompt: str, tools: Any) -> dict:
        info = json.loads(prompt)
        body = json.loads(tools.fetch_session(info["session"]))
        return {
            "impacts": [
                {"ticker": "TSLA", "stance": "Bear", "action": "Trim", "expected": "costs up", "direction": "down", "confidence": "high"},
                {"ticker": "ALB", "stance": "Bull", "action": "Add", "expected": "price up", "direction": "up", "confidence": "high"},
            ],
            "claims": [
                {"text": PASSAGE, "receipt_id": body["receipts"][0]},
                {"text": "The strike lasts all quarter.", "receipt_id": None},
                {"text": "Dropped.", "receipt_id": "missing-receipt"},
            ],
            "missing": ["China inventory is unknown"],
        }

    env.harness.on("researcher", receipts)
    env.harness.on("analyst", analyst)
    env.swarm.inject_news(story("lithium ibex migration noted", "https://example.test/spodumene"))
    env.swarm.tick()
    thesis = env.swarm.view()["theses"][-1]
    evidence = [claim for claim in thesis["claims"] if claim["kind"] == "evidence"]
    inferences = [claim for claim in thesis["claims"] if claim["kind"] == "inference"]
    assert len(evidence) == 1
    assert evidence[0]["passage"] == PASSAGE
    assert {claim["text"] for claim in inferences} == {"The strike lasts all quarter.", "Dropped."}
    assert "China inventory is unknown" in thesis["missing"]
    assert {impact["confidence"] for impact in thesis["impacts"]} == {"medium"}
    assert {impact["stance"] for impact in thesis["impacts"]} == {"Bear", "Bull"}
    assert thesis["disclaimer"] == "Not financial advice."
    assert env.swarm.view()["orders"] == []


def test_revision_links_the_previous_thesis(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)

    def researcher(event_id: str, prompt: str, tools: Any) -> dict:
        return {"answers": answered(), "missing": []}

    env.harness.on("researcher", researcher)
    env.swarm.inject_news(story("Chile lithium mine strike cuts supply"))
    env.swarm.tick()
    first = env.swarm.view()["theses"][0]
    env.swarm.inject_news(story("Chile lithium mine strike spreads further", "https://example.test/more"))
    env.swarm.tick()
    theses = env.swarm.view()["theses"]
    assert len(theses) == 2
    assert theses[1]["version"] == 2
    assert theses[1]["previous_id"] == first["id"]
    assert theses[1]["changes"] == ["New Sighting attached"]


def test_discord_threshold(tmp_path: Path) -> None:
    env = world(tmp_path)
    env.swarm.set_holdings([("TSLA", 0.5), ("ALB", 0.05)])
    env.swarm.add_topic("lithium", ["TSLA", "ALB"])
    env.swarm.set_alert_weight(0.3)

    def researcher(event_id: str, prompt: str, tools: Any) -> dict:
        return {"answers": answered(), "missing": []}

    def analyst(event_id: str, prompt: str, tools: Any) -> dict:
        return {
            "impacts": [
                {"ticker": "TSLA", "stance": "Bear", "action": "Trim", "expected": "costs up", "direction": "down", "confidence": "high"},
                {"ticker": "ALB", "stance": "Bull", "action": "Add", "expected": "price up", "direction": "up", "confidence": "high"},
            ],
            "claims": [],
            "missing": [],
        }

    env.harness.on("researcher", researcher)
    env.harness.on("analyst", analyst)
    env.swarm.inject_news(story("Chile lithium mine strike cuts supply"))
    env.swarm.tick()
    assert len(env.discord.sent) == 1
    assert "TSLA" in env.discord.sent[0]["text"]
    assert "Not financial advice." in env.discord.sent[0]["text"]
    assert env.discord.sent[0]["url"].startswith("http://127.0.0.1:8080/thesis/")

    env.harness.on(
        "analyst",
        lambda event_id, prompt, tools: {
            "impacts": [
                {"ticker": "TSLA", "stance": "Neutral", "action": "Hold", "expected": "unclear", "direction": "flat", "confidence": "high"}
            ],
            "claims": [],
            "missing": [],
        },
    )
    env.swarm.inject_news(story("lithium contract remains uncertain", "https://example.test/rare"))
    env.swarm.tick()
    assert len(env.discord.sent) == 1


def test_track_record_catches_up_after_sleep(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)

    def researcher(event_id: str, prompt: str, tools: Any) -> dict:
        return {"answers": answered(), "missing": []}

    def analyst(event_id: str, prompt: str, tools: Any) -> dict:
        return {
            "impacts": [
                {"ticker": "TSLA", "stance": "Bull", "action": "Add", "expected": "demand", "direction": "up", "confidence": "medium"},
                {"ticker": "ALB", "stance": "Bear", "action": "Trim", "expected": "oversupply", "direction": "down", "confidence": "medium"},
            ],
            "claims": [],
            "missing": [],
        }

    env.harness.on("researcher", researcher)
    env.harness.on("analyst", analyst)
    env.quotes.prices[("TSLA", env.clock.now().date())] = (100.0, 10.0)
    env.swarm.inject_news(story("Chile lithium mine strike cuts supply"))
    env.swarm.tick()
    from datetime import timedelta

    day = env.clock.now().date()
    for offset in range(1, 40):
        current = day + timedelta(days=offset)
        env.quotes.prices[("TSLA", current)] = (110.0, 12.0)
        env.quotes.prices[("ALB", current)] = (120.0, 8.0)
    env.clock.advance(trading_days=20)
    env.swarm.grade()
    grades = env.swarm.view()["track_record"]["grades"]
    assert sorted(grade["horizon"] for grade in grades) == [1, 1, 5, 5, 20, 20]
    tsla = [grade for grade in grades if grade["horizon"] == 1]
    assert any(grade["correct"] for grade in tsla)
    assert any(not grade["correct"] for grade in tsla)


def test_dashboard_shows_thesis_and_dismissal(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    env.swarm.inject_news(story("Lakers win finals", "https://example.test/lakers"))
    env.swarm.inject_news(story("Chile lithium mine strike cuts supply"))
    env.swarm.tick()
    html = render_html(env.swarm.view())
    assert "Not financial advice." in html
    assert "Lakers win finals" in html
    assert 'data-effect="' in html
    httpd = serve(env.swarm)
    host, port = httpd.server_address
    with urllib.request.urlopen(f"http://{host}:{port}/") as response:
        body = response.read().decode()
    httpd.shutdown()
    assert "Not financial advice." in body
    assert "Dismissed Sightings" in body


def test_recorded_and_canned_demo(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    env.you.fail = True
    env.swarm.run_demo("recorded")
    impacts = {impact["ticker"]: impact["stance"] for impact in env.swarm.view()["theses"][-1]["impacts"]}
    assert impacts == {"TSLA": "Bear", "ALB": "Bull"}
    assert env.you.calls == []

    canned_dir = tmp_path / "canned"
    canned_dir.mkdir()
    canned = world(canned_dir)
    canned.swarm.set_holdings([("TSLA", 0.4), ("ALB", 0.2)])
    canned.swarm.add_topic("lithium", ["TSLA", "ALB"])
    canned.swarm.run_demo("canned")
    impacts = {impact["ticker"]: impact["stance"] for impact in canned.swarm.view()["theses"][-1]["impacts"]}
    assert impacts == {"TSLA": "Bear", "ALB": "Bull"}
    assert canned.you.calls == []
    assert canned.swarm.view()["theses"][-1]["claims"][0]["kind"] == "inference"


def test_recording_check_and_cursor_rejection(tmp_path: Path) -> None:
    env = world(tmp_path)
    assert env.swarm.start()["ok"] is True
    try:
        env.swarm.configure(researcher="cursor")
        raised = False
    except ValueError:
        raised = True
    assert raised
    assert recording_status(None)["status"] == "FALLBACK"
    assert recording_status("/usr/bin/claude", recorded=True)["status"] == "VERIFIED"
    assert recording_status("/usr/bin/claude", recorded=False)["status"] == "MISSING"
    assert allows_start("FALLBACK") is True
    assert allows_start("VERIFIED") is True
    assert allows_start("MISSING") is False
    from ground_truth.recording_check import claude_argv

    assert claude_argv("investigate the event") == ["claude", "-p", "investigate the event"]
    assert "--bare" not in claude_argv("investigate the event")


class _BoardCli:
    def __init__(self) -> None:
        self.published: list[dict[str, str]] = []
        self.pending: list[dict[str, str]] = []

    def __call__(self, args: list[str]) -> dict:
        if args[:2] == ["board", "publish"]:
            text = args[args.index("--text") + 1]
            post_id = args[args.index("--id") + 1]
            message = {"id": post_id, "text": text}
            self.published.append(message)
            self.pending.append(dict(message))
            return {"id": post_id}
        if args[:2] == ["board", "inbox"]:
            if not self.pending:
                return {"batch": None}
            messages = self.pending
            self.pending = []
            return {"batch": {"receipt": "a" * 64, "messages": messages}}
        if args[:1] == ["search"]:
            return {"results": [{"id": "sessions:past-strike"}]}
        if args[:1] == ["fetch"]:
            return {"text": "earlier strike"}
        return {}


def test_opencrawl_board_creates_one_event(tmp_path: Path) -> None:
    cli = _BoardCli()
    board = OpenCrawlBoard(cli)
    first = board.post('{"k":"sighting","id":"s1"}')
    second = board.post('{"k":"sighting","id":"s1"}')
    assert first["id"] == second["id"]
    assert [item["text"] for item in board.fetch_all()] == [first["text"], second["text"]]
    assert board.fetch_all() == []

    env = world(tmp_path)
    env.swarm = Swarm(
        str(tmp_path / "board.sqlite"),
        clock=env.clock,
        news=env.news,
        you=env.you,
        pages=env.pages,
        quotes=env.quotes,
        board=OpenCrawlBoard(cli),
        sessions=OpenCrawlSessions(cli),
        harness=env.harness,
        discord=env.discord,
        llm=env.llm,
        recording=env.recording,
    )
    portfolio(env)
    env.swarm.inject_news(story("Chile lithium mine strike cuts supply"))
    env.swarm.tick()
    view = env.swarm.view()
    assert len(view["events"]) == 1
    sighting_id = view["sightings"][0]["id"]
    posted = [json.loads(item["text"]) for item in cli.published]
    assert {"k": "sighting", "id": sighting_id} in posted
    cli.pending = [item for item in cli.published if json.loads(item["text"]).get("k") == "sighting"]
    env.swarm.tick()
    assert len(env.swarm.view()["events"]) == 1
    sessions = OpenCrawlSessions(cli)
    assert sessions.search("lithium strike") == ["sessions:past-strike"]
    assert sessions.fetch_session("sessions:past-strike") == "earlier strike"


def test_default_path_prices_a_lithium_strike(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    env.swarm.store.set_setting("you_search_usd", "0.50")
    env.swarm.store.set_setting("you_research_usd", "2")
    env.swarm.store.set_setting("monthly_budget_usd", "25")
    env.swarm.llm = _Llm()
    names = {item["name"] for item in env.swarm.suggest_topics()}
    assert "lithium" in names
    assert "EV supply chain" in names
    env.swarm.inject_news(story("Chile lithium mine strike cuts supply"))
    env.swarm.tick()
    impacts = {impact["ticker"]: impact["stance"] for impact in env.swarm.view()["theses"][-1]["impacts"]}
    assert impacts == {"TSLA": "Bear", "ALB": "Bull"}
    assert env.swarm.view()["theses"][-1]["claims"][0]["kind"] == "inference"
    assert 1 <= env.swarm.view()["costs"]["research"] <= 4
    html = render_html(env.swarm.view())
    assert "Spend $4.50 of $25.00 this month." in html
    assert edgar_user_agent("Li yan <research@example.com>") == "GroundTruth/0.1 (Li yan <research@example.com>)"
    assert edgar_user_agent("") == "GroundTruth/0.1 (set EDGAR_CONTACT)"


def test_time_budget_stops_research(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)

    def slow(event_id: str, prompt: str, tools: Any) -> dict:
        env.clock.advance(minutes=4)
        result = tools.research("lite", "too late")
        return {"answers": answered(), "missing": [result["error"]]}

    env.harness.on("researcher", slow)
    env.swarm.inject_news(story("Chile lithium mine strike cuts supply"))
    env.swarm.tick()
    assert env.swarm.view()["costs"]["research"] == 0
    assert "time" in env.swarm.view()["theses"][0]["missing"]


def test_dashboard_escapes_sighting_titles(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    env.swarm.inject_news(story("<script>bad</script>", "https://example.test/xss"))
    env.swarm.tick()
    page = render_html(env.swarm.view())
    assert "<script>bad</script>" not in page
    assert "&lt;script&gt;bad&lt;/script&gt;" in page


def test_local_dashboard_saves_portfolio_and_opens_thesis(tmp_path: Path) -> None:
    assert bind_address({}) == ("0.0.0.0", 8080)
    assert bind_address({"DASHBOARD_HOST": "0.0.0.0", "DASHBOARD_PORT": "8080"}) == ("0.0.0.0", 8080)
    compose = Path("docker-compose.yml").read_text()
    assert "DASHBOARD_HOST: 0.0.0.0" in compose
    env = world(tmp_path)
    portfolio(env)
    env.swarm.llm = _Llm()
    env.swarm.inject_news(story("Chile lithium mine strike cuts supply"))
    env.swarm.tick()
    thesis_id = env.swarm.view()["theses"][0]["id"]
    httpd = serve(env.swarm, "127.0.0.1", 0)
    host, port = httpd.server_address
    base = f"http://{host}:{port}"

    def post(fields: dict[str, str]) -> str:
        data = urllib.parse.urlencode(fields).encode()
        request = urllib.request.Request(base + "/", data=data, method="POST")
        with urllib.request.urlopen(request) as response:
            return response.read().decode()

    page = post({"action": "remove_holding", "ticker": "ALB"})
    assert 'data-ticker="ALB"' not in page
    assert 'data-ticker="TSLA"' in page
    page = post({"action": "add_holding", "ticker": "F", "weight": "0.1"})
    assert 'data-ticker="F"' in page
    page = post({"action": "add_holding", "ticker": "F", "weight": "4"})
    assert "Enter a weight from 0 to 1." in page
    page = post({"action": "suggest_topics"})
    assert "EV supply chain" in page
    assert 'data-status="suggested"' in page
    page = post(
        {
            "action": "save_settings",
            "alert_weight": "0.5",
            "you_search_usd": "0.5",
            "you_research_usd": "2",
            "monthly_budget_usd": "25",
        }
    )
    assert "Spend $" in page
    assert "of $25.00 this month." in page
    with urllib.request.urlopen(f"{base}/thesis/{thesis_id}") as response:
        one = response.read().decode()
    assert f'id="thesis-{thesis_id}"' in one
    assert "Chile lithium mine strike cuts supply" in one
    httpd.shutdown()
