from __future__ import annotations

import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from ground_truth.__main__ import build_swarm
from ground_truth.adapters import LiveQuotes
from ground_truth.dashboard import render_page, serve
from ground_truth.domain import Quote, add_trading_days, is_trading_day, previous_trading_day
from ground_truth.research import ResearchExecutor
from ground_truth.store import Store
from ground_truth.swarm import ManualClock, SystemClock
from ground_truth.views import build
from tests.test_seam import PASSAGE, answered, portfolio, story, world


def _lithium_thesis(env: Any, missing: tuple[str, ...] = ("timing",)) -> None:
    def researcher(event_id: str, prompt: str, tools: Any) -> dict:
        tools.research("lite", "lithium exposure")
        tools.check_receipt("https://example.test/report", PASSAGE)
        return {"answers": answered(), "missing": list(missing)}

    def analyst(event_id: str, prompt: str, tools: Any) -> dict:
        import json

        body = json.loads(tools.fetch_session(json.loads(prompt)["session"]))
        return {
            "impacts": [
                {"ticker": "TSLA", "stance": "Bear", "action": "Trim", "expected": "costs up", "direction": "down", "confidence": "high"},
                {"ticker": "ALB", "stance": "Bull", "action": "Add", "expected": "price up", "direction": "up", "confidence": "medium"},
            ],
            "claims": [
                {"text": "The mine stopped producing.", "receipt_id": body["receipts"][0]},
                {"text": "Tesla buys lithium from this mine.", "receipt_id": None},
            ],
            "missing": [],
        }

    env.harness.on("researcher", researcher)
    env.harness.on("analyst", analyst)
    env.swarm.inject_news(story("Chile lithium mine strike cuts supply"))
    env.swarm.tick()


# ----- production time --------------------------------------------------------


def test_production_uses_a_real_clock(tmp_path: Path) -> None:
    swarm = build_swarm(str(tmp_path / "prod.sqlite"))
    assert isinstance(swarm.clock, SystemClock)
    first = swarm.clock.now()
    time.sleep(0.01)
    assert swarm.clock.now() > first
    assert swarm.runtime["mode"] == "custom-code"
    assert "not recorded in OpenCrawl" in swarm.runtime["label"]


def test_custom_code_mode_is_not_paused_when_claude_is_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import ground_truth.opencrawl as opencrawl

    monkeypatch.setattr(opencrawl.shutil, "which", lambda name: "C:/tools/claude.cmd" if name == "claude" else None)
    swarm = build_swarm(str(tmp_path / "win.sqlite"))
    status = swarm.start()
    assert status["ok"] is True
    assert swarm.blocked is False
    assert "never start a harness" in status["detail"]
    assert "opencrawl" not in status["detail"].lower()
    assert type(swarm.board).__name__ == "LocalBoard"


def test_cli_run_uses_the_resolved_opencrawl_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    import ground_truth.opencrawl as opencrawl

    seen: list[list[str]] = []

    class Done:
        returncode = 0
        stdout = '{"schemaVersion": 1, "data": {"ok": true}}'
        stderr = ""

    monkeypatch.setattr(opencrawl.shutil, "which", lambda name: r"C:\\npm\\opencrawl.cmd")
    monkeypatch.setattr(opencrawl.subprocess, "run", lambda argv, **kwargs: seen.append(argv) or Done())
    assert opencrawl.cli_run(["whoami", "--json"]) == {"ok": True}
    assert seen[0][0].endswith("opencrawl.cmd")
    monkeypatch.setattr(opencrawl.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="not on PATH"):
        opencrawl.cli_run(["whoami"])


# ----- prices by date and the trading calendar --------------------------------


def test_live_quotes_return_the_requested_day_or_nothing() -> None:
    history = (
        "Date,Open,High,Low,Close,Volume\n"
        "2026-01-05,1,1,1,100.0,1000\n"
        "2026-01-06,1,1,1,104.0,1100\n"
        "2026-01-08,1,1,1,99.0,900\n"
    )
    latest = "Symbol,Date,Time,Open,High,Low,Close,Volume\nTSLA.US,2026-01-09,15:00:00,1,1,1,101.5,700\n"
    seen: list[str] = []

    def get(url: str) -> bytes:
        seen.append(url)
        return (latest if "/q/l/" in url else history).encode()

    quotes = LiveQuotes(get=get, today=lambda: date(2026, 1, 9))
    assert quotes.quote("TSLA", date(2026, 1, 5)) == Quote(100.0, 1000.0)
    assert quotes.quote("TSLA", date(2026, 1, 6)) == Quote(104.0, 1100.0)
    assert quotes.quote("TSLA", date(2026, 1, 7)) is None
    assert quotes.quote("TSLA", date(2026, 1, 9)) == Quote(101.5, 700.0)
    assert any("d2=20260106" in url for url in seen)
    stale = LiveQuotes(get=get, today=lambda: date(2026, 1, 12))
    assert stale.quote("TSLA", date(2026, 1, 12)) is None


def test_trading_calendar_skips_us_market_holidays() -> None:
    assert not is_trading_day(date(2026, 1, 19))
    assert not is_trading_day(date(2026, 4, 3))
    assert not is_trading_day(date(2026, 7, 3))
    assert is_trading_day(date(2026, 1, 20))
    assert previous_trading_day(date(2026, 1, 20)) == date(2026, 1, 16)
    assert add_trading_days(date(2026, 1, 16), 1) == date(2026, 1, 20)


def test_missing_price_leaves_grade_pending(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)

    class GappyQuotes:
        def __init__(self) -> None:
            self.missing: set[date] = set()

        def quote(self, ticker: str, on: date) -> Quote | None:
            if on in self.missing:
                return None
            return Quote(100.0 if on <= date(2026, 1, 7) else 90.0, 1.0)

    gappy = GappyQuotes()
    env.swarm.quotes = gappy
    _lithium_thesis(env)
    due = add_trading_days(date(2026, 1, 7), 1)
    gappy.missing.add(due)
    env.clock.advance(trading_days=1)
    env.swarm.grade()
    assert env.swarm.view()["track_record"]["grades"] == []
    gappy.missing.clear()
    env.swarm.grade()
    grades = env.swarm.view()["track_record"]["grades"]
    assert {grade["ticker"]: grade["correct"] for grade in grades} == {"TSLA": True, "ALB": False}
    one_day = next(item for item in env.swarm.view()["track_record"]["horizons"] if item["horizon"] == 1)
    assert one_day["graded"] == 2 and one_day["pending"] == 0


def test_thesis_without_prices_shows_no_day_move(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)

    class NoQuotes:
        def quote(self, ticker: str, on: date) -> None:
            return None

    env.swarm.quotes = NoQuotes()
    _lithium_thesis(env)
    impact = env.swarm.view()["theses"][0]["impacts"][0]
    assert impact["day_move"] is None
    page = render_page(build(env.swarm.view()), f"/thesis/{env.swarm.view()['theses'][0]['id']}", {})[1]
    assert "Day move No price" in page


# ----- revisions use new evidence ----------------------------------------------


def test_revision_researches_the_new_sighting(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    _lithium_thesis(env)
    env.clock.advance(minutes=30)
    queries: list[str] = []

    def researcher(event_id: str, prompt: str, tools: Any) -> dict:
        import json

        info = json.loads(prompt)
        queries.append("; ".join(info["new_sightings"]))
        tools.research("lite", "; ".join(info["new_sightings"]))
        tools.check_receipt("https://example.test/second", PASSAGE)
        return {"answers": answered(), "missing": []}

    env.harness.on("researcher", researcher)
    env.swarm.inject_news(story("Chile lithium mine strike spreads to second site", "https://example.test/second"))
    env.swarm.tick()
    assert queries == ["Chile lithium mine strike spreads to second site"]
    event = env.swarm.view()["events"][0]
    assert [dossier["version"] for dossier in event["dossiers"]] == [1, 2]
    theses = env.swarm.view()["theses"]
    assert theses[-1]["version"] == 2
    assert theses[-1]["dossier_version"] == 2
    assert theses[-1]["claims"][0]["kind"] == "evidence"


# ----- dated costs and the monthly budget --------------------------------------


def test_costs_roll_over_by_month_and_budget_stops_paid_calls(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    env.swarm.save_rates("1", "2", "4")
    env.swarm.inject_news(story("Chile lithium mine strike cuts supply"))
    env.swarm.tick()
    costs = env.swarm.view()["costs"]
    assert costs["month"] == "2026-01"
    assert costs["search"] == 1
    assert costs["spend_usd"] <= 4
    env.swarm.inject_news(story("Copper smelter fire in Peru lithium plant", "https://example.test/peru"))
    env.swarm.inject_news(story("Argentina lithium export ban announced", "https://example.test/ban"))
    env.swarm.tick()
    statuses = {event["corroboration"] for event in env.swarm.view()["events"]}
    assert "over budget" in statuses
    assert env.swarm.view()["costs"]["spend_usd"] <= 4
    env.clock.advance(hours=24 * 31)
    rolled = env.swarm.view()["costs"]
    assert rolled["month"] == "2026-02"
    assert rolled["search"] == 0 and rolled["research"] == 0
    assert rolled["lifetime_search"] >= 1
    page = render_page(build(env.swarm.view()), "/settings", {})[1]
    assert "Lifetime, all months" in page
    assert "February 2026" in page


# ----- finite values -----------------------------------------------------------


@pytest.mark.parametrize("bad", ["nan", "inf", "-1", "1.5"])
def test_domain_rejects_non_finite_or_out_of_range_weights(tmp_path: Path, bad: str) -> None:
    env = world(tmp_path)
    with pytest.raises(ValueError):
        env.swarm.save_holding("TSLA", float(bad))
    with pytest.raises(ValueError):
        env.swarm.set_alert_weight(float(bad))


def test_dashboard_rejects_nan_and_infinity(tmp_path: Path) -> None:
    env = world(tmp_path)
    httpd = serve(env.swarm, "127.0.0.1", 0)
    host, port = httpd.server_address

    def post(fields: dict[str, str]) -> tuple[int, str]:
        data = urllib.parse.urlencode(fields).encode()
        request = urllib.request.Request(f"http://{host}:{port}/", data=data, method="POST")
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    status, page = post({"action": "add_holding", "ticker": "TSLA", "weight": "nan", "return": "/portfolio", "form": "add-holding"})
    assert status == 400 and "Enter a weight from 0 to 100%." in page
    status, page = post(
        {
            "action": "save_settings",
            "return": "/settings",
            "form": "settings",
            "alert_weight": "25",
            "you_search_usd": "inf",
            "you_research_usd": "",
            "monthly_budget_usd": "Infinity",
        }
    )
    assert status == 400
    assert "Enter a dollar amount of zero or more, or leave it blank." in page
    assert "Enter a monthly budget of zero dollars or more." in page
    assert env.swarm.view()["holdings"] == []
    httpd.shutdown()


# ----- concurrency ---------------------------------------------------------------


def test_identical_concurrent_queries_pay_once(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "q.sqlite"))
    gate = threading.Event()

    class SlowYou:
        def __init__(self) -> None:
            self.calls = 0

        def research(self, tier: str, query: str) -> dict:
            self.calls += 1
            gate.wait(2)
            return {"text": "finding"}

    you = SlowYou()
    clock = ManualClock(datetime_start())
    executor = ResearchExecutor(store, lambda: you, clock, allows=lambda kind: True)
    results: list[tuple[dict, bool]] = []

    def run() -> None:
        results.append(executor.research("lite", "same question"))

    threads = [threading.Thread(target=run) for _ in range(4)]
    for thread in threads:
        thread.start()
    time.sleep(0.1)
    gate.set()
    for thread in threads:
        thread.join()
    assert you.calls == 1
    assert sum(1 for _, paid in results if paid) == 1
    assert store.costs() == {"research": 1}


def datetime_start():
    from datetime import datetime

    return datetime(2026, 1, 7, 15, 0, 0)


def test_concurrent_holding_saves_keep_every_holding(tmp_path: Path) -> None:
    env = world(tmp_path)
    tickers = [f"T{index}" for index in range(20)]
    threads = [threading.Thread(target=env.swarm.save_holding, args=(ticker, 0.01)) for ticker in tickers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(row["ticker"] for row in env.swarm.view()["holdings"]) == sorted(tickers)


def test_research_workers_are_bounded(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    env.llm.answer = "new"

    def slow(event_id: str, prompt: str, tools: Any) -> dict:
        time.sleep(0.05)
        return {"answers": answered(), "missing": []}

    env.harness.on("researcher", slow)
    headlines = [
        "Chile lithium mine strike",
        "Argentina lithium export ban",
        "Australia lithium port flood",
        "Bolivia lithium brine permit revoked",
        "Nevada lithium clay lawsuit",
        "Zimbabwe lithium truck shortage",
        "Quebec refinery fire halts lithium",
    ]
    for index, headline in enumerate(headlines):
        env.swarm.inject_news(story(headline, f"https://example.test/{index}"))
    env.swarm.tick()
    assert len(env.swarm.view()["events"]) == 7
    assert env.harness.max_in_flight <= 4


# ----- failure isolation and recorded status ------------------------------------


def test_one_feed_failing_does_not_stop_the_tick(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    env.swarm.add_topic("lithium", ["TSLA", "ALB"], feeds=["https://example.test/broken.xml"])
    original = env.news.rss

    def broken(url: str) -> list:
        if "broken" in url:
            raise OSError("feed offline")
        return original(url)

    env.news.rss = broken
    env.news.google = [story("Chile lithium mine strike cuts supply")]
    env.swarm.tick()
    view = env.swarm.view()
    assert len(view["theses"]) == 1
    assert view["stages"]["scout"]["state"] == "degraded"
    assert "feed offline" in view["stages"]["scout"]["detail"]
    assert view["stages"]["grade"]["state"] == "ok"
    status = build(view).status
    assert status.label == "Running with problems"


def test_a_failing_stage_is_reported_and_later_stages_still_run(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    env.swarm.set_holdings([("TSLA", 0.5), ("ALB", 0.4)])

    class BrokenDiscord:
        def push(self, text: str, url: str) -> None:
            raise RuntimeError("webhook rejected")

    env.swarm.discord = BrokenDiscord()
    _lithium_thesis(env, missing=())
    view = env.swarm.view()
    assert view["stages"]["notify"]["state"] == "failed"
    assert view["stages"]["grade"]["state"] == "ok"
    page = render_page(build(view), "/", {})[1]
    assert "A stage failed" in page
    assert "webhook rejected" in page
    assert "Run checks now" in page


def test_research_error_is_kept_on_the_event(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)

    def crash(event_id: str, prompt: str, tools: Any) -> dict:
        raise RuntimeError("harness crashed")

    env.harness.on("researcher", crash)
    env.swarm.inject_news(story("Chile lithium mine strike cuts supply"))
    env.swarm.tick()
    view = env.swarm.view()
    assert "harness crashed" in view["events"][0]["last_error"]
    assert view["activity"]["awaiting_research"] == 1
    assert view["activity"]["event_errors"][0]["error"].startswith("Research failed")


def test_failed_recording_check_pauses_research(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    env.recording.result = {"ok": False, "detail": "no recorded tool call"}
    env.swarm.start()
    env.news.google = [story("Chile lithium mine strike cuts supply")]
    env.swarm.tick()
    view = env.swarm.view()
    assert view["blocked"] is True
    assert view["sightings"] == []
    page = render_page(build(view), "/", {})[1]
    assert "Research is paused" in page
    assert "no recorded tool call" in page


# ----- pages ----------------------------------------------------------------------


def test_overview_answers_what_changed_and_what_supports_it(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    _lithium_thesis(env)
    view = env.swarm.view()
    page = render_page(build(view), "/", {})[1]
    assert '<meta name="viewport"' in page
    assert "Latest Theses" in page
    assert "Portfolio effect:" in page
    assert "not an expected return" in page
    assert "Net 0." not in page
    assert '<span class="badge badge-bear">Bear</span>' in page
    assert "1 Receipt" in page
    assert "Missing evidence: timing" in page
    assert "Events awaiting research" in page
    filtered = render_page(build(view), "/", {"stance": ["Neutral"]})[1]
    assert "No Theses match these filters." in filtered


def test_thesis_detail_pairs_claims_with_receipts(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    _lithium_thesis(env)
    thesis = env.swarm.view()["theses"][0]
    page = render_page(build(env.swarm.view()), f"/thesis/{thesis['id']}", {})[1]
    assert "The mine stopped producing." in page
    assert PASSAGE in page
    assert "example.test" in page
    assert "Inference" in page
    assert "Tesla buys lithium from this mine." in page
    assert "does not by itself prove" in page
    assert "Event timeline" in page
    assert "Dossier" in page
    assert "Not financial advice." in page


def test_track_record_keeps_horizons_apart(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    _lithium_thesis(env)
    env.clock.advance(trading_days=5)
    env.swarm.grade()
    page = render_page(build(env.swarm.view()), "/track-record", {})[1]
    assert 'data-horizon="1"' in page and 'data-horizon="5"' in page and 'data-horizon="20"' in page
    assert "2 pending" in page
    assert "correct of 2 graded" in page


def test_empty_states_distinguish_quiet_from_failed(tmp_path: Path) -> None:
    env = world(tmp_path)
    page = render_page(build(env.swarm.view()), "/", {})[1]
    assert "Set up your Portfolio" in page
    portfolio(env)
    env.swarm.tick()
    page = render_page(build(env.swarm.view()), "/", {})[1]
    assert "No relevant Events yet" in page

    def down(topic: str) -> list:
        raise OSError("dns failure")

    env.news.google_news = down
    env.news.edgar = down
    env.swarm.tick()
    page = render_page(build(env.swarm.view()), "/", {})[1]
    assert "The Scout could not check the news" in page
    assert "dns failure" in page


def test_portfolio_page_uses_checkboxes_and_hides_rejected(tmp_path: Path) -> None:
    env = world(tmp_path)
    portfolio(env)
    env.llm.suggestions = [{"name": "EV supply chain", "holdings": ["TSLA"]}, {"name": "Batteries", "holdings": ["TSLA"]}]
    created = env.swarm.suggest_topics()
    env.swarm.reject_topic(created[1]["id"])
    page = render_page(build(env.swarm.view()), "/portfolio", {})[1]
    assert 'type="checkbox"' in page
    assert "Rejected suggestions (1)" in page
    assert 'data-status="suggested"' in page
    assert "Total allocated: 60%" in page


def test_demo_mode_injects_the_staged_story(tmp_path: Path) -> None:
    from ground_truth.demo import inject, prepare

    env = world(tmp_path)
    prepare(env.swarm)
    page = render_page(build(env.swarm.view()), "/", {})[1]
    assert "Inject breaking news: Chile lithium mine strike" in page
    assert inject(env.swarm) == "recorded"
    view = env.swarm.view()
    thesis = view["theses"][-1]
    assert {impact["ticker"]: impact["stance"] for impact in thesis["impacts"]} == {"TSLA": "Bear", "ALB": "Bull"}
    tsla = next(impact for impact in thesis["impacts"] if impact["ticker"] == "TSLA")
    assert round(tsla["day_move"], 3) == -0.036
    assert thesis["claims"][0]["kind"] == "evidence"
    assert len(env.discord.sent) == 1 and "TSLA" in env.discord.sent[0]["text"]
    assert inject(env.swarm) == "already"
    assert len(env.swarm.view()["theses"]) == 1
