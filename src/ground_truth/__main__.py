from __future__ import annotations

import json
import os
import shutil
import sys
import time

from ground_truth.adapters import LiveNews, LivePages, LiveQuotes, LiveYou
from ground_truth.domain import topics_for_holdings
from ground_truth.dashboard import bind_address, serve
from ground_truth.opencrawl import LocalBoard, LocalSessions, OpenCrawlBoard, OpenCrawlSessions, cli_run, opencrawl_path
from ground_truth.recording_check import allows_start, recording_status
from ground_truth.swarm import ScriptedHarness, Swarm, SystemClock


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
    def __init__(self, mode: str = "recorded", board: str = "OpenCrawl") -> None:
        self.mode = mode
        self.board = board

    def check(self, harness_name: str) -> dict:
        if self.mode == "custom-code":
            return {
                "ok": True,
                "detail": (
                    "Custom-code mode: the Researcher and Analyst run inside this app and never start a harness, "
                    f"so there is no session to record. The board runs on {self.board}."
                ),
            }
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


def build_swarm(path: str | None = None) -> Swarm:
    if opencrawl_path():
        board, sessions, board_name = OpenCrawlBoard(cli_run), OpenCrawlSessions(cli_run), "OpenCrawl"
    else:
        board, sessions, board_name = LocalBoard(), LocalSessions(), "this computer (the opencrawl command was not found)"
    return Swarm(
        path or os.environ.get("SWARM_DB", "ground-truth.sqlite"),
        clock=SystemClock(),
        news=LiveNews(),
        you=LiveYou(),
        pages=LivePages(),
        quotes=LiveQuotes(),
        board=board,
        sessions=sessions,
        harness=ScriptedHarness(mode="custom-code"),
        discord=_Discord(),
        llm=_Llm(),
        recording=_Recording("custom-code", board_name),
        dashboard_url=os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8080"),
    )


def main() -> None:
    demo = "--demo" in sys.argv[1:]
    if demo:
        from ground_truth.demo import prepare

        swarm = build_swarm(os.environ.get("SWARM_DEMO_DB", "ground-truth-demo.sqlite"))
        prepare(swarm)
    else:
        swarm = build_swarm()
    host, port = bind_address()
    try:
        httpd = serve(swarm, host, port)
    except OSError as exc:
        print(f"dashboard failed to listen on {host}:{port}: {exc}", flush=True)
        raise SystemExit(1) from exc
    shown = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    print(f"dashboard http://{shown}:{httpd.server_address[1]}/", flush=True)
    print(f"mode {swarm.runtime['label']}", flush=True)
    try:
        status = swarm.start()
    except Exception as exc:
        swarm.blocked = True
        swarm.recording = {"ok": False, "detail": f"recording check could not run: {exc}"}
        status = swarm.recording
    print(f"recording {status['detail']}", flush=True)
    if swarm.blocked:
        print("research paused: the recording check failed; the dashboard stays up", flush=True)
    if demo:
        print("demo mode: staged inputs only. Use the button on the Overview to inject the story.", flush=True)
    while True:
        try:
            swarm.tick()
        except Exception as exc:
            print(f"tick failed: {exc}", flush=True)
        time.sleep(swarm.view()["scout_interval_seconds"])


if __name__ == "__main__":
    main()
