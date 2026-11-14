from __future__ import annotations

import hashlib
import json
import subprocess
from typing import Any, Callable

Runner = Callable[[list[str]], dict[str, Any]]

SESSION_ID = "ground-truth-supervisor"
TOPIC = "swarm"


def cli_run(args: list[str]) -> dict[str, Any]:
    completed = subprocess.run(["opencrawl", *args], check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"opencrawl exited {completed.returncode}")
    payload = json.loads(completed.stdout or "{}")
    if payload.get("schemaVersion") != 1 or not isinstance(payload.get("data"), dict):
        raise RuntimeError("opencrawl returned an unexpected payload")
    return payload["data"]


def _post_id(text: str) -> str:
    return "gt-" + hashlib.sha256(text.encode()).hexdigest()[:20]


class OpenCrawlBoard:
    def __init__(self, run: Runner, session_id: str = SESSION_ID, topic: str = TOPIC) -> None:
        self._run = run
        self.session_id = session_id
        self.topic = topic
        self._registered = False

    def post(self, text: str) -> dict[str, str]:
        self._ensure()
        post_id = _post_id(text)
        self._run(
            [
                "board",
                "publish",
                "--session",
                self.session_id,
                "--id",
                post_id,
                "--topic",
                self.topic,
                "--text",
                text,
                "--json",
            ]
        )
        return {"id": post_id, "text": text}

    def fetch_all(self) -> list[dict[str, str]]:
        self._ensure()
        found: list[dict[str, str]] = []
        for _ in range(20):
            data = self._run(["board", "inbox", "--session", self.session_id, "--json"])
            batch = data.get("batch")
            if not isinstance(batch, dict):
                break
            messages = batch.get("messages")
            if not isinstance(messages, list) or not messages:
                break
            for message in messages:
                if not isinstance(message, dict):
                    continue
                text = message.get("text")
                if not isinstance(text, str):
                    continue
                found.append({"id": str(message.get("id") or ""), "text": text})
        return found

    def _ensure(self) -> None:
        if self._registered:
            return
        self._run(
            [
                "board",
                "register",
                "--session",
                self.session_id,
                "--name",
                "ground-truth:supervisor",
                "--task",
                "Group sightings and write theses",
                "--harness",
                "other",
                "--json",
            ]
        )
        self._run(
            [
                "board",
                "subscribe",
                "--session",
                self.session_id,
                "--topic",
                self.topic,
                "--json",
            ]
        )
        self._registered = True


class OpenCrawlSessions:
    def __init__(self, run: Runner) -> None:
        self._run = run
        self._local: dict[str, str] = {}

    def store_session(self, session_id: str, text: str) -> None:
        self._local[session_id] = text

    def fetch_session(self, session_id: str) -> str:
        if session_id in self._local:
            return self._local[session_id]
        data = self._run(["fetch", session_id, "--json"])
        text = data.get("text")
        return text if isinstance(text, str) else ""

    def search(self, query: str) -> list[str]:
        data = self._run(["search", query, "--json"])
        results = data.get("results")
        if not isinstance(results, list):
            return []
        return [str(row["id"]) for row in results if isinstance(row, dict) and row.get("id")]
