from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from ground_truth.swarm import Swarm


def _cost_line(costs: dict[str, Any]) -> str:
    counts = f"You.com calls: search {costs['search']}, research {costs['research']}."
    if not costs.get("priced"):
        return counts + " Set you_search_usd and you_research_usd to price them."
    return counts + f" Spend ${costs['spend_usd']:.2f} of ${costs['budget_usd']:.2f} this month."


def render_html(view: dict[str, Any]) -> str:
    cards = []
    for thesis in view["theses"]:
        impacts = []
        for impact in thesis["impacts"]:
            move = "unknown" if impact["day_move"] is None else f"{impact['day_move']:+.2%}"
            impacts.append(
                "<li>"
                f"<strong>{impact['ticker']}</strong> {impact['stance']} {impact['action']}. "
                f"{impact['expected']} Direction {impact['direction']}. "
                f"Day move {move}, volume {impact['volume']}. Confidence {impact['confidence']}."
                "</li>"
            )
        claims = []
        for claim in thesis["claims"]:
            if claim["kind"] == "evidence" and claim["url"]:
                claims.append(
                    f'<li>Receipt <a href="{claim["url"]}">{claim["url"]}</a> "{claim["passage"]}" retrieved {claim["retrieved_at"]}</li>'
                )
            else:
                claims.append(f"<li>Inference: {claim['text']}</li>")
        changes = ""
        if thesis["changes"]:
            items = "".join(f"<li>{change}</li>" for change in thesis["changes"])
            changes = f"<p>Changed since the previous Thesis</p><ul>{items}</ul>"
        missing = ""
        if thesis["missing"]:
            missing = "<p>Missing: " + ", ".join(thesis["missing"]) + "</p>"
        cards.append(
            f'<article data-effect="{thesis["color"]}" data-thesis="{thesis["id"]}">'
            f"<h2>{thesis['event_id']}</h2>"
            f"<p>Version {thesis['version']}. Net {thesis['net']:.3f}.</p>"
            f"{changes}{missing}"
            "<details><summary>Holding Impacts</summary>"
            f"<ul>{''.join(impacts)}</ul>"
            f"<ul>{''.join(claims)}</ul>"
            f"<p>{thesis['disclaimer']}</p>"
            "</details></article>"
        )
    dismissed = "".join(f"<li>{item['title']}: {item['reason']}</li>" for item in view["dismissed"])
    record = view["track_record"]
    costs = view["costs"]
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
</style>
</head>
<body>
<header>
  <h1>Market Impact Swarm</h1>
  <p>Track Record {record['correct']} correct of {record['total']}.</p>
  <p>{_cost_line(costs)}</p>
</header>
<section id="theses">{''.join(cards) or '<p>No Thesis yet.</p>'}</section>
<section id="dismissed"><h2>Dismissed Sightings</h2><ul>{dismissed}</ul></section>
</body>
</html>
"""


def serve(swarm: Swarm) -> ThreadingHTTPServer:
    dashboard = swarm

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/view":
                body = json.dumps(dashboard.view()).encode()
                content_type = "application/json"
            else:
                body = render_html(dashboard.view()).encode()
                content_type = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd
