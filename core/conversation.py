"""Conversation/session lifecycle helpers."""

from database.database import Database


class ConversationManager:
    def __init__(self, database: Database):
        self.database = database

    def start(self, title: str = "") -> str:
        return self.database.create_conversation(title)

    def history(self, conversation_id: str, limit: int | None = None) -> list[dict[str, str]]:
        return self.database.get_messages(conversation_id, limit)
