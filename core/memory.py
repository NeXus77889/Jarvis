"""Higher-level memory operations and cautious exchange capture."""

from __future__ import annotations

import re

from database.database import Database, SECRET_PATTERN


class MemoryManager:
    def __init__(self, database: Database, max_results: int = 5):
        self.database = database
        self.max_results = max_results

    def remember(self, content: str, category: str = "episodic") -> int:
        return self.database.save_memory(content, category=category, confidence=1.0, relevance=9)

    def recall(self, query: str = "") -> list[dict[str, object]]:
        return self.database.search_memories(query, self.max_results)

    def forget(self, query: str) -> int:
        return self.database.delete_matching_memories(query)

    def capture_notable_exchange(self, user_message: str, assistant_message: str) -> None:
        if not re.search(
            r"\b(proyecto|desarroll|hotel|decid|plan|inteligencia artificial|"
            r"añadir|agregar|implementar|mysql|python)\b",
            user_message,
            flags=re.IGNORECASE,
        ):
            return
        content = f"Se conversó sobre: {user_message.strip()} Respuesta: {assistant_message.strip()}"
        if SECRET_PATTERN.search(content):
            return
        self.database.save_memory(
            content,
            category="episodic",
            confidence=0.6,
            relevance=6,
        )
