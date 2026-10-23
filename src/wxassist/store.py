"""SQLite 本地存储：消息、会话、建议。数据只留在本机。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .models import Analysis, Message


SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session TEXT NOT NULL,
    text TEXT NOT NULL,
    is_me INTEGER NOT NULL,
    sender TEXT NOT NULL DEFAULT '',
    ts REAL NOT NULL,
    digest TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session_ts ON messages(session, ts);

CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session TEXT NOT NULL,
    incoming TEXT NOT NULL,
    intent TEXT NOT NULL DEFAULT '',
    candidates_json TEXT NOT NULL DEFAULT '[]',
    adopted_text TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS style_profiles (
    session TEXT PRIMARY KEY,
    profile TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---------- 消息 ----------

    def add_message(self, message: Message) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages(session, text, is_me, sender, ts, digest)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    message.session,
                    message.text,
                    int(message.is_me),
                    message.sender,
                    message.ts or time.time(),
                    message.digest(),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def add_messages(self, messages: list[Message]) -> int:
        for message in messages:
            self.add_message(message)
        return len(messages)

    def recent_messages(self, session: str, limit: int = 20) -> list[Message]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT session, text, is_me, sender, ts FROM messages"
                " WHERE session = ? ORDER BY id DESC LIMIT ?",
                (session, limit),
            ).fetchall()
        return [
            Message(
                session=row["session"],
                text=row["text"],
                is_me=bool(row["is_me"]),
                sender=row["sender"],
                ts=row["ts"],
            )
            for row in reversed(rows)
        ]

    def my_recent_messages(self, session: str, limit: int = 10) -> list[str]:
        """用户自己发过的话，用于风格 few-shot。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT text FROM messages WHERE session = ? AND is_me = 1"
                " ORDER BY id DESC LIMIT ?",
                (session, limit),
            ).fetchall()
        return [row["text"] for row in rows]

    def sessions(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT session, MAX(ts) AS last_ts FROM messages GROUP BY session"
                " ORDER BY last_ts DESC"
            ).fetchall()
        return [row["session"] for row in rows]

    # ---------- 建议 ----------

    def add_analysis(self, analysis: Analysis, adopted_text: str = "") -> None:
        candidates = [
            {"tone": c.tone, "text": c.text, "score": c.score} for c in analysis.candidates
        ]
        with self._lock:
            self._conn.execute(
                "INSERT INTO suggestions(session, incoming, intent, candidates_json,"
                " adopted_text, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    analysis.session,
                    "\n".join(m.text for m in analysis.incoming),
                    analysis.intent,
                    json.dumps(candidates, ensure_ascii=False),
                    adopted_text,
                    time.time(),
                ),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- 风格画像 ----------

    def get_style_profile(self, session: str) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT profile FROM style_profiles WHERE session = ?", (session,)
            ).fetchone()
        return str(row["profile"]) if row else ""

    def set_style_profile(self, session: str, profile: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO style_profiles(session, profile, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(session) DO UPDATE SET profile=excluded.profile,"
                " updated_at=excluded.updated_at",
                (session, profile, time.time()),
            )
            self._conn.commit()
