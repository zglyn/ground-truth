from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Any


def _id() -> str:
    return uuid.uuid4().hex


class Store:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._create()

    def _create(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS holdings (
              ticker TEXT PRIMARY KEY,
              weight REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS topics (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              holdings TEXT NOT NULL,
              feeds TEXT NOT NULL,
              status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sightings (
              id TEXT PRIMARY KEY,
              url TEXT NOT NULL UNIQUE,
              title TEXT NOT NULL,
              source TEXT NOT NULL,
              topic TEXT NOT NULL,
              holdings TEXT NOT NULL,
              posted_at TEXT NOT NULL,
              outcome TEXT NOT NULL,
              reason TEXT,
              event_id TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
              id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              title TEXT NOT NULL,
              corroboration TEXT NOT NULL,
              related_event_id TEXT,
              opened_at TEXT NOT NULL,
              last_sighting_at TEXT NOT NULL,
              dossier_session TEXT,
              revision_pending INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS dossiers (
              event_id TEXT PRIMARY KEY,
              session_id TEXT NOT NULL,
              body TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS theses (
              id TEXT PRIMARY KEY,
              event_id TEXT NOT NULL,
              version INTEGER NOT NULL,
              previous_id TEXT,
              body TEXT NOT NULL,
              created_at TEXT NOT NULL,
              notified INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS impacts (
              id TEXT PRIMARY KEY,
              thesis_id TEXT NOT NULL,
              ticker TEXT NOT NULL,
              stance TEXT NOT NULL,
              action TEXT NOT NULL,
              expected TEXT NOT NULL,
              direction TEXT NOT NULL,
              day_move REAL,
              volume REAL,
              confidence TEXT NOT NULL,
              base_price REAL,
              thesis_day TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS receipts (
              id TEXT PRIMARY KEY,
              url TEXT NOT NULL,
              passage TEXT NOT NULL,
              retrieved_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS claims (
              id TEXT PRIMARY KEY,
              thesis_id TEXT NOT NULL,
              text TEXT NOT NULL,
              kind TEXT NOT NULL,
              receipt_id TEXT
            );
            CREATE TABLE IF NOT EXISTS grades (
              impact_id TEXT NOT NULL,
              horizon INTEGER NOT NULL,
              move REAL NOT NULL,
              correct INTEGER NOT NULL,
              PRIMARY KEY (impact_id, horizon)
            );
            CREATE TABLE IF NOT EXISTS query_log (
              query TEXT PRIMARY KEY,
              result TEXT NOT NULL,
              at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS seen_messages (
              id TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS costs (
              kind TEXT PRIMARY KEY,
              n INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS charges (
              id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dossier_versions (
              event_id TEXT NOT NULL,
              version INTEGER NOT NULL,
              session_id TEXT NOT NULL,
              body TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY (event_id, version)
            );
            CREATE TABLE IF NOT EXISTS pipeline_status (
              stage TEXT PRIMARY KEY,
              state TEXT NOT NULL,
              detail TEXT NOT NULL,
              last_run_at TEXT NOT NULL,
              last_ok_at TEXT
            );
            """
        )
        columns = {row["name"] for row in self._db.execute("PRAGMA table_info(events)")}
        if "research_pending" not in columns:
            self._db.execute("ALTER TABLE events ADD COLUMN research_pending INTEGER NOT NULL DEFAULT 0")
        if "last_error" not in columns:
            self._db.execute("ALTER TABLE events ADD COLUMN last_error TEXT")
        migrated = self._db.execute("SELECT COUNT(*) AS n FROM dossier_versions").fetchone()["n"]
        if not migrated:
            self._db.execute(
                """
                INSERT INTO dossier_versions (event_id, version, session_id, body, created_at)
                SELECT dossiers.event_id, 1, dossiers.session_id, dossiers.body, events.opened_at
                FROM dossiers JOIN events ON events.id = dossiers.event_id
                """
            )
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def set_holdings(self, rows: list[tuple[str, float]]) -> None:
        with self._lock:
            self._db.execute("DELETE FROM holdings")
            self._db.executemany("INSERT INTO holdings (ticker, weight) VALUES (?, ?)", rows)
            self._db.commit()

    def upsert_holding(self, ticker: str, weight: float) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO holdings (ticker, weight) VALUES (?, ?) "
                "ON CONFLICT(ticker) DO UPDATE SET weight = excluded.weight",
                (ticker, weight),
            )
            self._db.commit()

    def remove_holding(self, ticker: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM holdings WHERE ticker = ?", (ticker,))
            self._db.commit()

    def holdings(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._db.execute("SELECT ticker, weight FROM holdings ORDER BY ticker"))

    def weight(self, ticker: str) -> float:
        with self._lock:
            row = self._db.execute("SELECT weight FROM holdings WHERE ticker = ?", (ticker,)).fetchone()
            return float(row["weight"]) if row else 0.0

    def add_topic(self, name: str, holdings: list[str], feeds: list[str], status: str) -> str:
        topic_id = _id()
        with self._lock:
            self._db.execute(
                "INSERT INTO topics (id, name, holdings, feeds, status) VALUES (?, ?, ?, ?, ?)",
                (topic_id, name, json.dumps(holdings), json.dumps(feeds), status),
            )
            self._db.commit()
        return topic_id

    def update_topic(
        self,
        topic_id: str,
        *,
        name: str | None,
        holdings: list[str] | None,
        status: str | None,
        feeds: list[str] | None = None,
    ) -> None:
        with self._lock:
            row = self._db.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
            if row is None:
                raise KeyError(topic_id)
            self._db.execute(
                "UPDATE topics SET name = ?, holdings = ?, status = ?, feeds = ? WHERE id = ?",
                (
                    name if name is not None else row["name"],
                    json.dumps(holdings) if holdings is not None else row["holdings"],
                    status if status is not None else row["status"],
                    json.dumps(feeds) if feeds is not None else row["feeds"],
                    topic_id,
                ),
            )
            self._db.commit()

    def topics(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if status is None:
                rows = self._db.execute("SELECT * FROM topics ORDER BY name").fetchall()
            else:
                rows = self._db.execute("SELECT * FROM topics WHERE status = ? ORDER BY name", (status,)).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "holdings": tuple(json.loads(row["holdings"])),
                "feeds": tuple(json.loads(row["feeds"])),
                "status": row["status"],
            }
            for row in rows
        ]

    def topic_named(self, name: str) -> dict[str, Any] | None:
        for topic in self.topics():
            if topic["name"] == name and topic["status"] == "active":
                return topic
        return None

    def sighting_by_url(self, url: str) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute("SELECT * FROM sightings WHERE url = ?", (url,)).fetchone()

    def insert_sighting(self, *, url: str, title: str, source: str, topic: str, holdings: list[str], posted_at: datetime) -> str:
        sighting_id = _id()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO sightings
                  (id, url, title, source, topic, holdings, posted_at, outcome, reason, event_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL)
                """,
                (sighting_id, url, title, source, topic, json.dumps(holdings), posted_at.isoformat()),
            )
            self._db.commit()
        return sighting_id

    def sighting(self, sighting_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute("SELECT * FROM sightings WHERE id = ?", (sighting_id,)).fetchone()

    def sightings(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._db.execute("SELECT * FROM sightings ORDER BY posted_at"))

    def dismiss_sighting(self, sighting_id: str, reason: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE sightings SET outcome = 'dismissed', reason = ?, event_id = NULL WHERE id = ?",
                (reason, sighting_id),
            )
            self._db.commit()

    def attach_sighting(self, sighting_id: str, event_id: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE sightings SET outcome = 'attached', reason = NULL, event_id = ? WHERE id = ?",
                (event_id, sighting_id),
            )
            self._db.commit()

    def create_event(self, *, title: str, opened_at: datetime, related_event_id: str | None) -> str:
        event_id = _id()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO events
                  (id, status, title, corroboration, related_event_id, opened_at, last_sighting_at, dossier_session, revision_pending)
                VALUES (?, 'open', ?, 'pending', ?, ?, ?, NULL, 0)
                """,
                (event_id, title, related_event_id, opened_at.isoformat(), opened_at.isoformat()),
            )
            self._db.commit()
        return event_id

    def touch_event(self, event_id: str, at: datetime) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE events SET last_sighting_at = ? WHERE id = ?",
                (at.isoformat(), event_id),
            )
            self._db.commit()

    def set_corroboration(self, event_id: str, value: str) -> None:
        with self._lock:
            self._db.execute("UPDATE events SET corroboration = ? WHERE id = ?", (value, event_id))
            self._db.commit()

    def set_revision_pending(self, event_id: str, pending: bool) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE events SET revision_pending = ? WHERE id = ?",
                (1 if pending else 0, event_id),
            )
            self._db.commit()

    def set_research_pending(self, event_id: str, pending: bool) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE events SET research_pending = ? WHERE id = ?",
                (1 if pending else 0, event_id),
            )
            self._db.commit()

    def set_event_error(self, event_id: str, error: str | None) -> None:
        with self._lock:
            self._db.execute("UPDATE events SET last_error = ? WHERE id = ?", (error, event_id))
            self._db.commit()

    def close_event(self, event_id: str) -> None:
        with self._lock:
            self._db.execute("UPDATE events SET status = 'closed' WHERE id = ?", (event_id,))
            self._db.commit()

    def events(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._db.execute("SELECT * FROM events ORDER BY opened_at"))

    def event(self, event_id: str) -> sqlite3.Row:
        with self._lock:
            row = self._db.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            if row is None:
                raise KeyError(event_id)
            return row

    def event_text(self, event_id: str) -> str:
        titles = [row["title"] for row in self.sightings() if row["event_id"] == event_id]
        event = self.event(event_id)
        return " ".join([event["title"], *titles])

    def event_holdings(self, event_id: str) -> list[str]:
        found: list[str] = []
        for row in self.sightings():
            if row["event_id"] != event_id:
                continue
            for ticker in json.loads(row["holdings"]):
                if ticker not in found:
                    found.append(ticker)
        return found

    def has_thesis(self, event_id: str) -> bool:
        with self._lock:
            row = self._db.execute("SELECT 1 FROM theses WHERE event_id = ? LIMIT 1", (event_id,)).fetchone()
            return row is not None

    def latest_thesis_id(self, event_id: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id FROM theses WHERE event_id = ? ORDER BY version DESC LIMIT 1",
                (event_id,),
            ).fetchone()
            return None if row is None else row["id"]

    def next_version(self, event_id: str) -> int:
        with self._lock:
            row = self._db.execute("SELECT MAX(version) AS v FROM theses WHERE event_id = ?", (event_id,)).fetchone()
            return 1 if row["v"] is None else int(row["v"]) + 1

    def save_dossier(self, event_id: str, session_id: str, body: dict[str, Any], created_at: datetime) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT MAX(version) AS v FROM dossier_versions WHERE event_id = ?", (event_id,)
            ).fetchone()
            version = 1 if row["v"] is None else int(row["v"]) + 1
            self._db.execute(
                "INSERT INTO dossier_versions (event_id, version, session_id, body, created_at) VALUES (?, ?, ?, ?, ?)",
                (event_id, version, session_id, json.dumps(body), created_at.isoformat()),
            )
            self._db.execute(
                "UPDATE events SET dossier_session = ?, research_pending = 0 WHERE id = ?",
                (session_id, event_id),
            )
            self._db.commit()
        return version

    def dossier_versions(self, event_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM dossier_versions WHERE event_id = ? ORDER BY version", (event_id,)
            ).fetchall()
        return [
            {
                "version": int(row["version"]),
                "session_id": row["session_id"],
                "created_at": row["created_at"],
                **json.loads(row["body"]),
            }
            for row in rows
        ]

    def dossier(self, event_id: str) -> dict[str, Any] | None:
        versions = self.dossier_versions(event_id)
        return versions[-1] if versions else None

    def add_receipt(self, url: str, passage: str, retrieved_at: datetime) -> str:
        receipt_id = _id()
        with self._lock:
            self._db.execute(
                "INSERT INTO receipts (id, url, passage, retrieved_at) VALUES (?, ?, ?, ?)",
                (receipt_id, url, passage, retrieved_at.isoformat()),
            )
            self._db.commit()
        return receipt_id

    def receipt(self, receipt_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute("SELECT * FROM receipts WHERE id = ?", (receipt_id,)).fetchone()

    def save_thesis(
        self,
        *,
        event_id: str,
        version: int,
        previous_id: str | None,
        body: dict[str, Any],
        created_at: datetime,
        impacts: list[dict[str, Any]],
        claims: list[dict[str, Any]],
    ) -> str:
        thesis_id = _id()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO theses (id, event_id, version, previous_id, body, created_at, notified)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (thesis_id, event_id, version, previous_id, json.dumps(body), created_at.isoformat()),
            )
            for impact in impacts:
                self._db.execute(
                    """
                    INSERT INTO impacts
                      (id, thesis_id, ticker, stance, action, expected, direction, day_move, volume, confidence, base_price, thesis_day)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _id(),
                        thesis_id,
                        impact["ticker"],
                        impact["stance"],
                        impact["action"],
                        impact["expected"],
                        impact["direction"],
                        impact["day_move"],
                        impact["volume"],
                        impact["confidence"],
                        impact["base_price"],
                        impact["thesis_day"],
                    ),
                )
            for claim in claims:
                self._db.execute(
                    "INSERT INTO claims (id, thesis_id, text, kind, receipt_id) VALUES (?, ?, ?, ?, ?)",
                    (_id(), thesis_id, claim["text"], claim["kind"], claim.get("receipt_id")),
                )
            self._db.execute("UPDATE events SET revision_pending = 0 WHERE id = ?", (event_id,))
            self._db.commit()
        return thesis_id

    def theses(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._db.execute("SELECT * FROM theses ORDER BY created_at"))

    def impacts_for(self, thesis_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._db.execute("SELECT * FROM impacts WHERE thesis_id = ? ORDER BY ticker", (thesis_id,)))

    def claims_for(self, thesis_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._db.execute("SELECT * FROM claims WHERE thesis_id = ?", (thesis_id,)))

    def unnotified_theses(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._db.execute("SELECT * FROM theses WHERE notified = 0 ORDER BY created_at"))

    def mark_notified(self, thesis_id: str) -> None:
        with self._lock:
            self._db.execute("UPDATE theses SET notified = 1 WHERE id = ?", (thesis_id,))
            self._db.commit()

    def all_impacts(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._db.execute(
                    """
                    SELECT impacts.*, theses.created_at AS thesis_created
                    FROM impacts JOIN theses ON theses.id = impacts.thesis_id
                    """
                )
            )

    def has_grade(self, impact_id: str, horizon: int) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM grades WHERE impact_id = ? AND horizon = ?",
                (impact_id, horizon),
            ).fetchone()
            return row is not None

    def add_grade(self, impact_id: str, horizon: int, move: float, correct: bool) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO grades (impact_id, horizon, move, correct) VALUES (?, ?, ?, ?)",
                (impact_id, horizon, move, 1 if correct else 0),
            )
            self._db.commit()

    def grades(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._db.execute("SELECT * FROM grades"))

    def seen(self, message_id: str) -> bool:
        with self._lock:
            row = self._db.execute("SELECT 1 FROM seen_messages WHERE id = ?", (message_id,)).fetchone()
            return row is not None

    def mark_seen(self, message_id: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO seen_messages (id) VALUES (?)", (message_id,))
            self._db.commit()

    def query_get(self, query: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT result FROM query_log WHERE query = ?", (query,)).fetchone()
            return None if row is None else json.loads(row["result"])

    def query_put(self, query: str, result: dict[str, Any], at: datetime) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO query_log (query, result, at) VALUES (?, ?, ?)",
                (query, json.dumps(result), at.isoformat()),
            )
            self._db.commit()

    def add_cost(self, kind: str, at: datetime) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO charges (id, kind, at) VALUES (?, ?, ?)",
                (_id(), kind, at.isoformat()),
            )
            self._db.execute(
                """
                INSERT INTO costs (kind, n) VALUES (?, 1)
                ON CONFLICT(kind) DO UPDATE SET n = n + 1
                """,
                (kind,),
            )
            self._db.commit()

    def costs(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT kind, n FROM costs").fetchall()
            return {row["kind"]: int(row["n"]) for row in rows}

    def month_costs(self, month: str) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute(
                "SELECT kind, COUNT(*) AS n FROM charges WHERE substr(at, 1, 7) = ? GROUP BY kind",
                (month,),
            ).fetchall()
            return {row["kind"]: int(row["n"]) for row in rows}

    def stage_report(self, stage: str, state: str, detail: str, at: datetime) -> None:
        ok_at = at.isoformat() if state != "failed" else None
        with self._lock:
            self._db.execute(
                """
                INSERT INTO pipeline_status (stage, state, detail, last_run_at, last_ok_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(stage) DO UPDATE SET
                  state = excluded.state,
                  detail = excluded.detail,
                  last_run_at = excluded.last_run_at,
                  last_ok_at = COALESCE(excluded.last_ok_at, pipeline_status.last_ok_at)
                """,
                (stage, state, detail, at.isoformat(), ok_at),
            )
            self._db.commit()

    def stages(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM pipeline_status").fetchall()
        return {
            row["stage"]: {
                "state": row["state"],
                "detail": row["detail"],
                "last_run_at": row["last_run_at"],
                "last_ok_at": row["last_ok_at"],
            }
            for row in rows
        }

    def grade_rows(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._db.execute(
                    """
                    SELECT grades.*, impacts.ticker, impacts.stance, impacts.thesis_id, theses.event_id
                    FROM grades
                    JOIN impacts ON impacts.id = grades.impact_id
                    JOIN theses ON theses.id = impacts.thesis_id
                    ORDER BY theses.created_at DESC, impacts.ticker, grades.horizon
                    """
                )
            )

    def setting(self, key: str, default: str) -> str:
        with self._lock:
            row = self._db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return default if row is None else row["value"]

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._db.commit()
