from __future__ import annotations

import os
import shutil
import time
from datetime import datetime, timezone

from ground_truth.adapters import LiveNews, LivePages, LiveQuotes, LiveYou
from ground_truth.dashboard import serve
from ground_truth.recording_check import recording_status
from ground_truth.swarm import ManualClock, ScriptedHarness, Swarm


class _Board:
    def __init__(self) -> None:
        self._messages: list[dict[str, str]] = []

    def post(self, text: str) -> dict[str, str]:
        message = {"id": str(len(self._messages) + 1), "text": text}
        self._messages.append(message)
        return message

    def fetch_all(self) -> list[dict[str, str]]:
        return list(self._messages)


class _Sessions:
    def __init__(self) -> None:
        self._rows: dict[str, str] = {}

    def store_session(self, session_id: str, text: str) -> None:
        self._rows[session_id] = text

    def fetch_session(self, session_id: str) -> str:
        return self._rows[session_id]

    def search(self, query: str) -> list[str]:
        return [key for key, value in self._rows.items() if query.lower() in value.lower()]


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
        return []

    def judge(self, title: str, events: list) -> str:
        return "new"


class _Recording:
    def check(self, harness_name: str) -> dict:
        if harness_name == "cursor":
            return {"ok": False, "detail": "Cursor is not used for the Researcher"}
        status = recording_status(shutil.which("claude" if harness_name == "claude" else harness_name))
        return {"ok": status["status"] == "VERIFIED", "detail": status["detail"]}


def main() -> None:
    path = os.environ.get("SWARM_DB", "/tmp/ground-truth.sqlite")
    swarm = Swarm(
        path,
        clock=ManualClock(datetime.now(timezone.utc).replace(tzinfo=None)),
        news=LiveNews(),
        you=LiveYou(),
        pages=LivePages(),
        quotes=LiveQuotes(),
        board=_Board(),
        sessions=_Sessions(),
        harness=ScriptedHarness(),
        discord=_Discord(),
        llm=_Llm(),
        recording=_Recording(),
        dashboard_url=os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8080"),
    )
    swarm.start()
    httpd = serve(swarm)
    print(f"dashboard http://127.0.0.1:{httpd.server_address[1]}/", flush=True)
    while True:
        try:
            swarm.tick()
        except Exception as exc:
            print(f"tick failed: {exc}", flush=True)
        time.sleep(swarm.view()["scout_interval_seconds"])


if __name__ == "__main__":
    main()
