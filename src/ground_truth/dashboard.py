from __future__ import annotations

import html
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from ground_truth.swarm import Swarm

_TICKER = re.compile(r"[A-Z][A-Z0-9.\-]{0,7}")
_ERRORS = {
    "ticker": "Enter a ticker of up to 8 letters or digits.",
    "weight": "Enter a weight from 0 to 1.",
    "name": "Enter a Watch Topic name.",
    "topic": "That Watch Topic is not on this dashboard.",
    "money": "Enter a zero or positive dollar amount.",
    "alert": "Enter an alert weight from 0 to 1.",
    "action": "That action is not available.",
}


def bind_address(environ: dict[str, str] | None = None) -> tuple[str, int]:
    source = os.environ if environ is None else environ
    host = source.get("DASHBOARD_HOST", "127.0.0.1") or "127.0.0.1"
    port = int(source.get("DASHBOARD_PORT", "8080") or "8080")
    return host, port


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _tickers(raw: str) -> list[str] | None:
    symbols = []
    for part in raw.split(","):
        symbol = part.strip().upper()
        if not symbol:
            continue
        if _TICKER.fullmatch(symbol) is None:
            return None
        if symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _unit(raw: str) -> float | None:
    try:
        value = float(raw.strip())
    except ValueError:
        return None
    if value < 0 or value > 1:
        return None
    return value


def _money(raw: str) -> str | None:
    text = raw.strip()
    if text == "":
        return ""
    try:
        value = float(text)
    except ValueError:
        return None
    if value < 0:
        return None
    return text


def _add_holding(swarm: Swarm, fields: dict[str, str]) -> str | None:
    ticker = fields.get("ticker", "").strip().upper()
    if _TICKER.fullmatch(ticker) is None:
        return "ticker"
    weight = _unit(fields.get("weight", ""))
    if weight is None:
        return "weight"
    swarm.save_holding(ticker, weight)
    return None


def _remove_holding(swarm: Swarm, fields: dict[str, str]) -> str | None:
    ticker = fields.get("ticker", "").strip().upper()
    if _TICKER.fullmatch(ticker) is None:
        return "ticker"
    swarm.remove_holding(ticker)
    return None


def _suggest_topics(swarm: Swarm, fields: dict[str, str]) -> str | None:
    swarm.suggest_topics()
    return None


def _topic_edit(swarm: Swarm, fields: dict[str, str], *, accept: bool) -> str | None:
    topic_id = fields.get("id", "").strip()
    name = fields.get("name", "").strip()
    holdings = _tickers(fields.get("holdings", ""))
    if holdings is None:
        return "ticker"
    try:
        if accept:
            swarm.accept_topic(topic_id, name=name or None, holdings=holdings or None)
        else:
            swarm.reject_topic(topic_id)
    except KeyError:
        return "topic"
    return None


def _add_topic(swarm: Swarm, fields: dict[str, str]) -> str | None:
    name = fields.get("name", "").strip()
    if not name:
        return "name"
    holdings = _tickers(fields.get("holdings", ""))
    if not holdings:
        return "ticker"
    feeds = [item.strip() for item in fields.get("feeds", "").split(",") if item.strip()]
    swarm.add_topic(name, holdings, feeds)
    return None


def _save_settings(swarm: Swarm, fields: dict[str, str]) -> str | None:
    weight = _unit(fields.get("alert_weight", ""))
    if weight is None:
        return "alert"
    search = _money(fields.get("you_search_usd", ""))
    research = _money(fields.get("you_research_usd", ""))
    budget = _money(fields.get("monthly_budget_usd", ""))
    if search is None or research is None or budget is None:
        return "money"
    swarm.set_alert_weight(weight)
    swarm.save_rates(search, research, budget or "25")
    return None


_COMMANDS: dict[str, Callable[[Swarm, dict[str, str]], str | None]] = {
    "add_holding": _add_holding,
    "remove_holding": _remove_holding,
    "suggest_topics": _suggest_topics,
    "accept_topic": lambda swarm, fields: _topic_edit(swarm, fields, accept=True),
    "reject_topic": lambda swarm, fields: _topic_edit(swarm, fields, accept=False),
    "add_topic": _add_topic,
    "save_settings": _save_settings,
}


def apply_command(swarm: Swarm, fields: dict[str, str]) -> str | None:
    command = _COMMANDS.get(fields.get("action", ""))
    if command is None:
        return "action"
    return command(swarm, fields)


def _cost_line(costs: dict[str, Any]) -> str:
    counts = f"You.com calls: search {costs['search']}, research {costs['research']}."
    if not costs.get("priced"):
        return counts + " Set the dollar rates below to price them."
    return counts + f" Spend ${costs['spend_usd']:.2f} of ${costs['budget_usd']:.2f} this month."


def render_html(view: dict[str, Any], focus: str | None = None, error: str | None = None) -> str:
    titles = {event["id"]: event["title"] for event in view["events"]}
    theses = list(view["theses"])
    if focus:
        theses.sort(key=lambda thesis: thesis["id"] != focus)
    cards = []
    for thesis in theses:
        impacts = []
        for impact in thesis["impacts"]:
            move = "unknown" if impact["day_move"] is None else f"{impact['day_move']:+.2%}"
            impacts.append(
                "<li>"
                f"<strong>{_esc(impact['ticker'])}</strong> {_esc(impact['stance'])} {_esc(impact['action'])}. "
                f"{_esc(impact['expected'])} Direction {_esc(impact['direction'])}. "
                f"Day move {_esc(move)}, volume {_esc(impact['volume'])}. Confidence {_esc(impact['confidence'])}."
                "</li>"
            )
        claims = []
        for claim in thesis["claims"]:
            if claim["kind"] == "evidence" and claim["url"]:
                claims.append(
                    f'<li>Receipt <a href="{_esc(claim["url"])}">{_esc(claim["url"])}</a> '
                    f'"{_esc(claim["passage"])}" retrieved {_esc(claim["retrieved_at"])}</li>'
                )
            else:
                claims.append(f"<li>Inference: {_esc(claim['text'])}</li>")
        changes = ""
        if thesis["changes"]:
            items = "".join(f"<li>{_esc(change)}</li>" for change in thesis["changes"])
            changes = f"<p>Changed since the previous Thesis</p><ul>{items}</ul>"
        missing = ""
        if thesis["missing"]:
            missing = "<p>Missing: " + _esc(", ".join(thesis["missing"])) + "</p>"
        headline = titles.get(thesis["event_id"], thesis["event_id"])
        cards.append(
            f'<article id="thesis-{_esc(thesis["id"])}" data-effect="{_esc(thesis["color"])}" data-thesis="{_esc(thesis["id"])}">'
            f"<h2>{_esc(headline)}</h2>"
            f"<p>Version {_esc(thesis['version'])}. Net {thesis['net']:.3f}.</p>"
            f"{changes}{missing}"
            "<details><summary>Holding Impacts</summary>"
            f"<ul>{''.join(impacts)}</ul>"
            f"<ul>{''.join(claims)}</ul>"
            f"<p>{_esc(thesis['disclaimer'])}</p>"
            "</details></article>"
        )
    holdings = []
    for holding in view["holdings"]:
        ticker = _esc(holding["ticker"])
        holdings.append(
            "<li>"
            f'<span data-ticker="{ticker}">{ticker} {holding["weight"]:.2f}</span> '
            '<form method="post" action="/">'
            '<input type="hidden" name="action" value="remove_holding">'
            f'<input type="hidden" name="ticker" value="{ticker}">'
            "<button>Remove</button></form></li>"
        )
    topics = []
    for topic in view["topics"]:
        joined = _esc(", ".join(topic["holdings"]))
        feeds = _esc(", ".join(topic["feeds"]) or "none")
        name = _esc(topic["name"])
        topic_id = _esc(topic["id"])
        controls = ""
        if topic["status"] == "suggested":
            controls = (
                '<form method="post" action="/">'
                '<input type="hidden" name="action" value="accept_topic">'
                f'<input type="hidden" name="id" value="{topic_id}">'
                f'<input name="name" value="{name}">'
                f'<input name="holdings" value="{joined}">'
                "<button>Accept</button></form>"
                '<form method="post" action="/">'
                '<input type="hidden" name="action" value="reject_topic">'
                f'<input type="hidden" name="id" value="{topic_id}">'
                "<button>Reject</button></form>"
            )
        topics.append(
            f'<li data-topic="{topic_id}" data-status="{_esc(topic["status"])}">'
            f"{name}. Holdings {joined}. Feeds {feeds}. {_esc(topic['status'])}. {controls}</li>"
        )
    events = []
    for event in view["events"]:
        events.append(
            "<li>"
            f"{_esc(event['title'])}. {_esc(event['status'])}. {_esc(event['corroboration'])}."
            "</li>"
        )
    grades = []
    for grade in view["track_record"]["grades"]:
        mark = "correct" if grade["correct"] else "incorrect"
        grades.append(f"<li>{_esc(grade['horizon'])} trading days, move {_esc(grade['move'])}, {mark}.</li>")
    dismissed = "".join(
        f"<li>{_esc(item['title'])}: {_esc(item['reason'])}</li>" for item in view["dismissed"]
    )
    record = view["track_record"]
    recording = view.get("recording") or {}
    detail = recording.get("detail", "") if isinstance(recording, dict) else ""
    notice = ""
    if error in _ERRORS:
        notice = f'<p id="error">{_esc(_ERRORS[error])}</p>'
    missing_focus = ""
    if focus and all(thesis["id"] != focus for thesis in view["theses"]):
        missing_focus = "<p>That Thesis is not on this dashboard.</p>"
    costs = view["costs"]
    alert = _esc(view["alert_weight"])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Market Impact Swarm</title>
<style>
  body {{ font-family: Georgia, serif; margin: 2rem; color: #1c1915; background: #f6f3ee; }}
  article[data-effect="bull"] {{ border-left: 8px solid #1b7f4a; }}
  article[data-effect="bear"] {{ border-left: 8px solid #9c2f2f; }}
  article[data-effect="neutral"] {{ border-left: 8px solid #8a8175; }}
  article {{ background: white; padding: 1rem 1.25rem; margin: 0 0 1rem; }}
  section > form {{ display: block; margin: 0.75rem 0; }}
  li form {{ display: inline; margin-right: 0.5rem; }}
  label {{ display: inline-block; margin: 0.25rem 0.75rem 0.25rem 0; }}
  section {{ margin: 2rem 0; }}
  #error {{ background: #9c2f2f; color: white; padding: 0.5rem 0.75rem; }}
</style>
</head>
<body>
<header>
  <h1>Market Impact Swarm</h1>
  <p>This dashboard is on this computer.</p>
  <p>Track Record {record['correct']} correct of {record['total']}.</p>
  <ul id="grades">{''.join(grades)}</ul>
  <p>{_esc(_cost_line(costs))}</p>
  <p>Recording {_esc(detail)}</p>
</header>
{notice}{missing_focus}
<section id="theses">{''.join(cards) or '<p>No Thesis yet.</p>'}</section>
<section id="portfolio">
  <h2>Holdings</h2>
  <ul id="holdings">{''.join(holdings)}</ul>
  <form method="post" action="/">
    <input type="hidden" name="action" value="add_holding">
    <label>Ticker <input name="ticker" required></label>
    <label>Weight <input name="weight" value="0.25" required></label>
    <button>Save Holding</button>
  </form>
</section>
<section id="topics">
  <h2>Watch Topics</h2>
  <ul>{''.join(topics)}</ul>
  <form method="post" action="/">
    <input type="hidden" name="action" value="suggest_topics">
    <button>Suggest from Holdings</button>
  </form>
  <form method="post" action="/">
    <input type="hidden" name="action" value="add_topic">
    <label>Name <input name="name" required></label>
    <label>Holdings <input name="holdings" placeholder="TSLA, ALB" required></label>
    <label>Feeds <input name="feeds" placeholder="https://example.test/feed.xml"></label>
    <button>Add Watch Topic</button>
  </form>
</section>
<section id="events">
  <h2>Events</h2>
  <ul>{''.join(events)}</ul>
</section>
<section id="dismissed"><h2>Dismissed Sightings</h2><ul>{dismissed}</ul></section>
<section id="settings">
  <h2>Settings</h2>
  <form method="post" action="/">
    <input type="hidden" name="action" value="save_settings">
    <label>Alert weight <input name="alert_weight" value="{alert}"></label>
    <label>Search dollars <input name="you_search_usd" value="{_esc(costs.get('search_usd', ''))}"></label>
    <label>Research dollars <input name="you_research_usd" value="{_esc(costs.get('research_usd', ''))}"></label>
    <label>Monthly budget <input name="monthly_budget_usd" value="{_esc(costs.get('budget_usd', ''))}"></label>
    <button>Save settings</button>
  </form>
</section>
</body>
</html>
"""


def _fields(raw: bytes) -> dict[str, str]:
    parsed = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
    return {key: values[0] if values else "" for key, values in parsed.items()}


def serve(swarm: Swarm, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    dashboard = swarm

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/view":
                body = json.dumps(dashboard.view()).encode()
                self._send(200, body, "application/json")
                return
            focus = None
            if parsed.path.startswith("/thesis/"):
                focus = parsed.path.removeprefix("/thesis/").strip("/")
            error = parse_qs(parsed.query).get("error", [""])[0]
            body = render_html(dashboard.view(), focus or None, error or None).encode()
            self._send(200, body, "text/html; charset=utf-8")

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length > 20_000:
                self._send(413, b"too large", "text/plain")
                return
            error = apply_command(dashboard, _fields(self.rfile.read(length)))
            location = "/" if not error else "/?error=" + error
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    class _Server(ThreadingHTTPServer):
        allow_reuse_address = True

    httpd = _Server((host, port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd
