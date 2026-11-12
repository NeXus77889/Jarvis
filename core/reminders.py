"""Persistent, user-scheduled reminders."""

from __future__ import annotations

from datetime import datetime, timezone

from database.database import Database


class ReminderManager:
    def __init__(self, database: Database):
        self.database = database

    def add(self, content: str, due_at: datetime) -> int:
        if due_at.tzinfo is None:
            raise ValueError("La fecha del recordatorio debe incluir zona horaria.")
        due_utc = due_at.astimezone(timezone.utc)
        if due_utc <= datetime.now(timezone.utc):
            raise ValueError("La fecha del recordatorio debe estar en el futuro.")
        return self.database.create_reminder(
            content,
            due_utc.isoformat(timespec="seconds"),
        )

    def due(self) -> list[dict[str, str | int | None]]:
        return self.database.get_due_reminders(datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def notify_once(self, reminder_id: int) -> bool:
        return self.database.mark_reminder_notified(reminder_id)

    def list(self) -> list[dict[str, str | int | None]]:
        return self.database.list_reminders()

    def complete(self, reminder_id: int) -> bool:
        return self.database.complete_reminder(reminder_id)
