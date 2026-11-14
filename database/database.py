"""SQLite persistence for conversations, preferences, and explicit memories."""

from __future__ import annotations

import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator


SECRET_PATTERN = re.compile(
    r"(?i)(?:gsk[_-][A-Za-z0-9_-]{12,}|"
    r"(?:api[_ -]?key|password|contrase(?:ñ|n)a|token|secret)\s*[:=]\s*\S+)"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path):
        self._is_memory_database = str(path) == ":memory:"
        self.path = Path(path)
        self._database_uri = (
            f"file:jarvis-{uuid.uuid4().hex}?mode=memory&cache=shared"
            if self._is_memory_database
            else str(self.path)
        )
        self._memory_anchor = (
            sqlite3.connect(self._database_uri, uri=True) if self._is_memory_database else None
        )
        if not self._is_memory_database:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        connection = sqlite3.connect(
            self._database_uri,
            timeout=10,
            uri=self._is_memory_database,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
                    ON messages(conversation_id, created_at, id);
                CREATE TABLE IF NOT EXISTS memory_categories (
                    name TEXT PRIMARY KEY,
                    description TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    category TEXT NOT NULL REFERENCES memory_categories(name),
                    confidence REAL NOT NULL DEFAULT 0.8 CHECK (confidence BETWEEN 0 AND 1),
                    relevance INTEGER NOT NULL DEFAULT 5 CHECK (relevance BETWEEN 1 AND 10),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_memories_category
                    ON memories(category, updated_at);
                CREATE TABLE IF NOT EXISTS conversation_summaries (
                    conversation_id TEXT PRIMARY KEY
                        REFERENCES conversations(id) ON DELETE CASCADE,
                    summary TEXT NOT NULL,
                    last_message_id INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS user_preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS system_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    notified_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_reminders_due
                    ON reminders(due_at, completed_at, notified_at);
                """
            )
            summary_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(conversation_summaries)")
            }
            if "last_message_id" not in summary_columns:
                connection.execute(
                    "ALTER TABLE conversation_summaries "
                    "ADD COLUMN last_message_id INTEGER NOT NULL DEFAULT 0"
                )
            connection.executemany(
                "INSERT OR IGNORE INTO memory_categories(name, description) VALUES (?, ?)",
                [
                    ("profile", "Persistent user facts and preferences"),
                    ("project", "Projects, technologies, and decisions"),
                    ("episodic", "Notable conversation events"),
                    ("preference", "Communication and interaction preferences"),
                ],
            )

    def create_conversation(self, title: str = "") -> str:
        conversation_id = str(uuid.uuid4())
        now = _now()
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (conversation_id, title, now, now),
            )
        return conversation_id

    def add_message(self, conversation_id: str, role: str, content: str) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("role must be 'user' or 'assistant'.")
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (conversation_id, role, content, _now()),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (_now(), conversation_id),
            )

    def get_messages(self, conversation_id: str, limit: int | None = None) -> list[dict[str, str]]:
        query = (
            "SELECT role, content, created_at FROM messages "
            "WHERE conversation_id = ? ORDER BY id DESC"
        )
        parameters: tuple[object, ...] = (conversation_id,)
        if limit is not None:
            query += " LIMIT ?"
            parameters += (limit,)
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in reversed(rows)]

    def list_conversations(self, limit: int = 20) -> list[dict[str, str]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, title, created_at, updated_at FROM conversations "
                "ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_memory(
        self,
        content: str,
        category: str = "episodic",
        confidence: float = 0.8,
        relevance: int = 5,
    ) -> int:
        content = content.strip()
        if not content:
            raise ValueError("Memory content cannot be empty.")
        if SECRET_PATTERN.search(content):
            raise ValueError("Potential credentials or secrets cannot be stored as memories.")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1.")
        if not 1 <= relevance <= 10:
            raise ValueError("relevance must be between 1 and 10.")
        now = _now()
        with self._connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO memory_categories(name) VALUES (?)", (category,)
            )
            cursor = connection.execute(
                "INSERT INTO memories(content, category, confidence, relevance, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (content, category, confidence, relevance, now, now),
            )
            return int(cursor.lastrowid)

    def search_memories(self, query: str = "", limit: int = 5) -> list[dict[str, object]]:
        terms = {term.lower() for term in re.findall(r"[\wáéíóúñ]+", query) if len(term) > 2}
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, content, category, confidence, relevance, created_at, updated_at "
                "FROM memories ORDER BY relevance DESC, confidence DESC, updated_at DESC"
            ).fetchall()
        memories = [dict(row) for row in rows]
        if terms:
            memories.sort(
                key=lambda memory: (
                    sum(term in str(memory["content"]).lower() for term in terms),
                    int(memory["relevance"]),
                    float(memory["confidence"]),
                    str(memory["updated_at"]),
                ),
                reverse=True,
            )
            memories = [
                memory
                for memory in memories
                if any(term in str(memory["content"]).lower() for term in terms)
            ]
        return memories[:limit]

    def update_memory(self, memory_id: int, content: str) -> bool:
        content = content.strip()
        if not content:
            raise ValueError("Memory content cannot be empty.")
        if SECRET_PATTERN.search(content):
            raise ValueError("Potential credentials or secrets cannot be stored as memories.")
        with self._connection() as connection:
            result = connection.execute(
                "UPDATE memories SET content = ?, updated_at = ? WHERE id = ?",
                (content, _now(), memory_id),
            )
        return result.rowcount > 0

    def delete_memory(self, memory_id: int) -> bool:
        with self._connection() as connection:
            result = connection.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        return result.rowcount > 0

    def get_latest_memory_id(self) -> int | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT id FROM memories ORDER BY updated_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return int(row["id"]) if row else None

    def delete_matching_memories(self, query: str) -> int:
        terms = [term.lower() for term in re.findall(r"[\wáéíóúñ]+", query) if len(term) > 2]
        if not terms:
            return 0
        memories = self.search_memories("", limit=10000)
        ids = [
            int(item["id"])
            for item in memories
            if all(term in str(item["content"]).lower() for term in terms)
        ]
        if not ids:
            return 0
        with self._connection() as connection:
            cursor = connection.executemany(
                "DELETE FROM memories WHERE id = ?", [(memory_id,) for memory_id in ids]
            )
        return cursor.rowcount

    def save_summary(
        self,
        conversation_id: str,
        summary: str,
        last_message_id: int = 0,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO conversation_summaries"
                "(conversation_id, summary, last_message_id, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(conversation_id) DO UPDATE SET "
                "summary = excluded.summary, last_message_id = excluded.last_message_id, "
                "updated_at = excluded.updated_at",
                (conversation_id, summary, last_message_id, _now()),
            )

    def get_summary(self, conversation_id: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT summary FROM conversation_summaries WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        return str(row["summary"]) if row else None

    def get_summary_state(self, conversation_id: str) -> tuple[str | None, int]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT summary, last_message_id FROM conversation_summaries "
                "WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        return (
            (str(row["summary"]), int(row["last_message_id"]))
            if row
            else (None, 0)
        )

    def get_messages_with_ids(
        self,
        conversation_id: str,
        after_message_id: int = 0,
        before_message_id: int | None = None,
    ) -> list[dict[str, object]]:
        query = (
            "SELECT id, role, content, created_at FROM messages "
            "WHERE conversation_id = ? AND id > ?"
        )
        parameters: tuple[object, ...] = (conversation_id, after_message_id)
        if before_message_id is not None:
            query += " AND id < ?"
            parameters += (before_message_id,)
        query += " ORDER BY id"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def create_reminder(self, content: str, due_at: str) -> int:
        content = content.strip()
        if not content or SECRET_PATTERN.search(content):
            raise ValueError("El recordatorio está vacío o contiene un posible secreto.")
        now = _now()
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO reminders(content, due_at, created_at) VALUES (?, ?, ?)",
                (content, due_at, now),
            )
        return int(cursor.lastrowid)

    def list_reminders(self, include_completed: bool = False) -> list[dict[str, str | int | None]]:
        query = (
            "SELECT id, content, due_at, created_at, completed_at, notified_at "
            "FROM reminders"
        )
        if not include_completed:
            query += " WHERE completed_at IS NULL"
        query += " ORDER BY due_at"
        with self._connection() as connection:
            rows = connection.execute(query).fetchall()
        return [dict(row) for row in rows]

    def get_due_reminders(self, now: str) -> list[dict[str, str | int | None]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, content, due_at FROM reminders "
                "WHERE completed_at IS NULL AND notified_at IS NULL AND due_at <= ? "
                "ORDER BY due_at",
                (now,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_reminder_notified(self, reminder_id: int) -> bool:
        with self._connection() as connection:
            result = connection.execute(
                "UPDATE reminders SET notified_at = ? "
                "WHERE id = ? AND completed_at IS NULL AND notified_at IS NULL",
                (_now(), reminder_id),
            )
        return result.rowcount > 0

    def complete_reminder(self, reminder_id: int) -> bool:
        with self._connection() as connection:
            result = connection.execute(
                "UPDATE reminders SET completed_at = ? "
                "WHERE id = ? AND completed_at IS NULL",
                (_now(), reminder_id),
            )
        return result.rowcount > 0
