from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone

from ground_truth.adapters import LiveNews, LivePages, LiveQuotes, LiveYou
from ground_truth.domain import topics_for_holdings
from ground_truth.dashboard import bind_address, serve
from ground_truth.opencrawl import OpenCrawlBoard, OpenCrawlSessions, cli_run
from ground_truth.recording_check import allows_start, recording_status
from ground_truth.swarm import ManualClock, ScriptedHarness, Swarm


class _Discord:
    def push(self, text: str, url: str) -> None:
        webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
        if not webhook:
            return
        import json
        import urllib.request

        body = json.dumps({"content": text[:1800]}).encode()
        request = urllib.request.Request(webhook, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(request, timeout=20).read()


class _Llm:
    def suggest_topics(self, tickers: list[str]) -> list[dict]:
        return topics_for_holdings(tickers)

    def judge(self, title: str, events: list) -> str:
        return "new"


class _Recording:
    def check(self, harness_name: str) -> dict:
        if harness_name == "cursor":
            return {"ok": False, "detail": "Cursor is not used for the Researcher"}
        binary = shutil.which("claude" if harness_name == "claude" else harness_name)
        recorded = None
        if binary:
            try:
                data = cli_run(["search", "claude -p", "--source", "sessions", "--json"])
            except (OSError, RuntimeError, json.JSONDecodeError):
                recorded = False
            else:
                results = data.get("results")
                recorded = isinstance(results, list) and len(results) > 0
        status = recording_status(binary, recorded=recorded)
        return {"ok": allows_start(status["status"]), "detail": status["detail"]}


def main() -> None:
    path = os.environ.get("SWARM_DB", "/tmp/ground-truth.sqlite")
    swarm = Swarm(
        path,
        clock=ManualClock(datetime.now(timezone.utc).replace(tzinfo=None)),
        news=LiveNews(),
        you=LiveYou(),
        pages=LivePages(),
        quotes=LiveQuotes(),
        board=OpenCrawlBoard(cli_run),
        sessions=OpenCrawlSessions(cli_run),
        harness=ScriptedHarness(),
        discord=_Discord(),
        llm=_Llm(),
        recording=_Recording(),
        dashboard_url=os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8080"),
    )
    host, port = bind_address()
    try:
        httpd = serve(swarm, host, port)
    except OSError as exc:
        print(f"dashboard failed to listen on {host}:{port}: {exc}", flush=True)
        raise SystemExit(1) from exc
    shown = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    print(f"dashboard http://{shown}:{httpd.server_address[1]}/", flush=True)
    try:
        status = swarm.start()
    except Exception as exc:
        status = {"detail": f"recording check failed: {exc}"}
    print(f"recording {status['detail']}", flush=True)
    while True:
        try:
            swarm.tick()
        except Exception as exc:
            print(f"tick failed: {exc}", flush=True)
        time.sleep(swarm.view()["scout_interval_seconds"])


if __name__ == "__main__":
    main()
