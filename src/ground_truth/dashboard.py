from __future__ import annotations

import json
import math
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

from ground_truth import render
from ground_truth.render import SAVED, FormState
from ground_truth.swarm import Swarm
from ground_truth.views import Dashboard, build

_TICKER = re.compile(r"[A-Z][A-Z0-9.\-]{0,7}")
ERRORS = {
    "ticker": "Enter a ticker of 1 to 8 letters, digits, dots or hyphens, starting with a letter.",
    "weight": "Enter a weight from 0 to 100%.",
    "name": "Enter a Watch Topic name.",
    "holdings": "Choose at least one Holding.",
    "topic": "That Watch Topic is no longer on this dashboard.",
    "feeds": "Enter one feed address per line, each starting with http:// or https://.",
    "alert_weight": "Enter an alert threshold from 0 to 100%.",
    "you_search_usd": "Enter a dollar amount of zero or more, or leave it blank.",
    "you_research_usd": "Enter a dollar amount of zero or more, or leave it blank.",
    "monthly_budget_usd": "Enter a monthly budget of zero dollars or more.",
    "action": "That action is not available.",
}
PAGES = ("/", "/portfolio", "/track-record", "/settings")

Fields = dict[str, list[str]]


class Outcome:
    def __init__(self, errors: dict[str, str] | None = None, saved: str = "") -> None:
        self.errors = errors or {}
        self.saved = saved


def bind_address(environ: dict[str, str] | None = None) -> tuple[str, int]:
    source = os.environ if environ is None else environ
    host = source.get("DASHBOARD_HOST", "0.0.0.0") or "0.0.0.0"
    port = int(source.get("DASHBOARD_PORT", "8080") or "8080")
    return host, port


def _one(fields: Fields, name: str) -> str:
    values = fields.get(name)
    return values[0].strip() if values else ""


def _percent(raw: str) -> float | None:
    try:
        value = float(raw.replace("%", "").strip())
    except ValueError:
        return None
    if not math.isfinite(value) or value < 0 or value > 100:
        return None
    return value / 100


def _money(raw: str, *, blank: bool) -> str | None:
    if raw == "":
        return "" if blank else None
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return raw


def _holdings(fields: Fields) -> list[str] | None:
    symbols: list[str] = []
    for raw in fields.get("holdings", []):
        for part in raw.split(","):
            symbol = part.strip().upper()
            if not symbol:
                continue
            if _TICKER.fullmatch(symbol) is None:
                return None
            if symbol not in symbols:
                symbols.append(symbol)
    return symbols


def _feeds(raw: str) -> list[str] | None:
    feeds = [line.strip() for line in re.split(r"[\r\n,]+", raw) if line.strip()]
    if any(urlparse(feed).scheme not in ("http", "https") or not urlparse(feed).netloc for feed in feeds):
        return None
    return feeds


def _err(*names: str) -> Outcome:
    return Outcome({name: ERRORS[name] for name in names})


def _add_holding(swarm: Swarm, fields: Fields) -> Outcome:
    ticker = _one(fields, "ticker").upper()
    weight = _percent(_one(fields, "weight"))
    bad = []
    if _TICKER.fullmatch(ticker) is None:
        bad.append("ticker")
    if weight is None:
        bad.append("weight")
    if bad:
        return _err(*bad)
    assert weight is not None
    swarm.save_holding(ticker, weight)
    return Outcome(saved="holding")


def _remove_holding(swarm: Swarm, fields: Fields) -> Outcome:
    ticker = _one(fields, "ticker").upper()
    if _TICKER.fullmatch(ticker) is None:
        return _err("ticker")
    swarm.remove_holding(ticker)
    return Outcome(saved="holding-removed")


def _suggest(swarm: Swarm, fields: Fields) -> Outcome:
    swarm.suggest_topics()
    return Outcome(saved="suggested")


def _accept(swarm: Swarm, fields: Fields) -> Outcome:
    holdings = _holdings(fields)
    if not holdings:
        return _err("holdings")
    try:
        swarm.accept_topic(_one(fields, "id"), name=_one(fields, "name") or None, holdings=holdings)
    except KeyError:
        return Outcome({"_form": ERRORS["topic"]})
    return Outcome(saved="topic-accepted")


def _reject(swarm: Swarm, fields: Fields) -> Outcome:
    try:
        swarm.reject_topic(_one(fields, "id"))
    except KeyError:
        return Outcome({"_form": ERRORS["topic"]})
    return Outcome(saved="topic-rejected")


def _update_topic(swarm: Swarm, fields: Fields) -> Outcome:
    holdings = _holdings(fields)
    feeds = _feeds(_one(fields, "feeds"))
    bad = []
    if not holdings:
        bad.append("holdings")
    if feeds is None:
        bad.append("feeds")
    if bad:
        return _err(*bad)
    assert holdings is not None and feeds is not None
    try:
        swarm.update_topic(_one(fields, "id"), holdings=holdings, feeds=feeds)
    except KeyError:
        return Outcome({"_form": ERRORS["topic"]})
    return Outcome(saved="topic")


def _add_topic(swarm: Swarm, fields: Fields) -> Outcome:
    name = _one(fields, "name")
    holdings = _holdings(fields)
    feeds = _feeds(_one(fields, "feeds"))
    bad = []
    if not name:
        bad.append("name")
    if not holdings:
        bad.append("holdings")
    if feeds is None:
        bad.append("feeds")
    if bad:
        return _err(*bad)
    assert holdings is not None and feeds is not None
    swarm.add_topic(name, holdings, feeds)
    return Outcome(saved="topic")


def _save_settings(swarm: Swarm, fields: Fields) -> Outcome:
    weight = _percent(_one(fields, "alert_weight"))
    search = _money(_one(fields, "you_search_usd"), blank=True)
    research = _money(_one(fields, "you_research_usd"), blank=True)
    budget = _money(_one(fields, "monthly_budget_usd"), blank=False)
    bad = [
        name
        for name, value in (
            ("alert_weight", weight),
            ("you_search_usd", search),
            ("you_research_usd", research),
            ("monthly_budget_usd", budget),
        )
        if value is None
    ]
    if bad:
        return _err(*bad)
    assert weight is not None and search is not None and research is not None and budget is not None
    swarm.set_alert_weight(weight)
    swarm.save_rates(search, research, budget)
    return Outcome(saved="settings")


def _run_now(swarm: Swarm, fields: Fields) -> Outcome:
    return Outcome(saved="run" if swarm.run_now() else "running")


COMMANDS: dict[str, Callable[[Swarm, Fields], Outcome]] = {
    "add_holding": _add_holding,
    "remove_holding": _remove_holding,
    "suggest_topics": _suggest,
    "accept_topic": _accept,
    "reject_topic": _reject,
    "update_topic": _update_topic,
    "add_topic": _add_topic,
    "save_settings": _save_settings,
    "run_now": _run_now,
}


def apply_command(swarm: Swarm, fields: Fields) -> Outcome:
    command = COMMANDS.get(_one(fields, "action"))
    if command is None:
        return Outcome({"_form": ERRORS["action"]})
    try:
        return command(swarm, fields)
    except ValueError as exc:
        name = str(exc)
        return Outcome({name: ERRORS.get(name, "That value is not valid.")})


def render_page(
    dash: Dashboard,
    path: str,
    query: dict[str, list[str]],
    state: FormState | None = None,
) -> tuple[int, str]:
    notice = SAVED.get((query.get("saved") or [""])[0], "")
    if path == "/":
        holding = (query.get("holding") or [""])[0]
        stance = (query.get("stance") or [""])[0]
        if stance not in ("", "Bull", "Bear", "Neutral"):
            stance = ""
        return 200, render.overview(dash, holding=holding, stance=stance, notice=notice)
    if path == "/portfolio":
        return 200, render.portfolio(dash, state, notice=notice)
    if path == "/track-record":
        return 200, render.track_record(dash, notice=notice)
    if path == "/settings":
        return 200, render.settings(dash, state, notice=notice)
    if path.startswith("/thesis/"):
        thesis_id = path.removeprefix("/thesis/").strip("/")
        body = render.thesis_detail(dash, thesis_id, notice=notice)
        if body is not None:
            return 200, body
        return 404, render.not_found(dash, "That Thesis is not on this dashboard.")
    return 404, render.not_found(dash, "There is no page at this address.")


def render_html(view: dict, focus: str | None = None) -> str:
    """Render one page from a raw Swarm view: the Overview, or a Thesis when focus is given."""
    dash = build(view)
    if focus:
        body = render.thesis_detail(dash, focus)
        return body if body is not None else render.not_found(dash, "That Thesis is not on this dashboard.")
    return render.overview(dash)


def _fields(raw: bytes) -> Fields:
    return parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)


def serve(swarm: Swarm, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    dashboard = swarm

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/view":
                self._send(200, json.dumps(dashboard.view()).encode(), "application/json")
                return
            status, body = render_page(build(dashboard.view()), parsed.path, parse_qs(parsed.query))
            self._send(status, body.encode(), "text/html; charset=utf-8")

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length > 20_000:
                self._send(413, b"too large", "text/plain")
                return
            fields = _fields(self.rfile.read(length))
            back = _one(fields, "return")
            if back not in PAGES:
                back = "/"
            outcome = apply_command(dashboard, fields)
            if outcome.errors:
                state = FormState(form=_one(fields, "form"), values=fields, errors=outcome.errors)
                if "_form" in outcome.errors and len(outcome.errors) == 1 and back == "/":
                    back = "/portfolio"
                _, body = render_page(build(dashboard.view()), back, {}, state)
                self._send(400, body.encode(), "text/html; charset=utf-8")
                return
            self.send_response(303)
            self.send_header("Location", f"{back}?saved={outcome.saved}")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    class _Server(ThreadingHTTPServer):
        allow_reuse_address = True
        daemon_threads = True

    httpd = _Server((host, port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd
