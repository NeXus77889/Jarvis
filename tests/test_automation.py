from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine

from core.reminders import ReminderManager
from database.database import Database
from database.project_database import ReadOnlyProjectDatabase
from tools.registry import ToolRegistry
from core.brain import JarvisBrain


class AutomationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database = Database(self.root / "jarvis.db")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_reminders_are_persistent_due_once_and_completable(self) -> None:
        manager = ReminderManager(self.database)
        due = datetime.now(timezone.utc) + timedelta(seconds=1)
        reminder_id = manager.add("Revisar el proyecto", due)
        reopened = ReminderManager(Database(self.root / "jarvis.db"))
        self.assertEqual(len(reopened.list()), 1)
        self.assertEqual(reopened.due(), [])
        self.assertTrue(
            self.database.get_due_reminders((due + timedelta(minutes=1)).isoformat())[0]["id"]
        )
        self.assertTrue(manager.notify_once(reminder_id))
        self.assertFalse(manager.notify_once(reminder_id))
        self.assertTrue(manager.complete(reminder_id))
        self.assertEqual(manager.list(), [])

    def test_reminder_requires_future_aware_datetime_and_rejects_secret(self) -> None:
        manager = ReminderManager(self.database)
        with self.assertRaises(ValueError):
            manager.add("past", datetime.now(timezone.utc) - timedelta(days=1))
        with self.assertRaises(ValueError):
            self.database.create_reminder("api_key=private", "2030-01-01T00:00:00+00:00")

    def test_brain_can_manage_reminders_without_provider(self) -> None:
        brain = JarvisBrain(database=self.database)
        future = (datetime.now().astimezone() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        response = brain.ask(f"recuérdame revisar el hotel el {future}")
        self.assertIn("Recordatorio", response)
        self.assertIn("hotel", brain.ask("mis recordatorios"))

    def test_file_tools_are_workspace_scoped_and_read_only(self) -> None:
        (self.root / "notes.md").write_text("Project architecture", encoding="utf-8")
        (self.root / ".env").write_text("GROQ_API_KEY=private", encoding="utf-8")
        tools = ToolRegistry(self.root)
        self.assertEqual(tools.read_file("notes.md"), "Project architecture")
        self.assertEqual(tools.search_files("architecture"), ["notes.md"])
        with self.assertRaises(ValueError):
            tools.read_file(".env")
        with self.assertRaises(ValueError):
            tools.read_file("..\\outside.txt")
        with self.assertRaises(ValueError):
            tools.execute("run_shell", {"command": "whoami"})
        with self.assertRaises(ValueError):
            tools.open_website("javascript:alert(1)", lambda _: True)

    def test_website_open_requires_approval_and_https(self) -> None:
        with patch("tools.registry.webbrowser.open") as browser_open:
            approved = ToolRegistry.open_website("https://example.com", lambda _: False)
            self.assertFalse(approved)
            browser_open.assert_not_called()
            approved = ToolRegistry.open_website("https://example.com", lambda _: True)
            self.assertTrue(approved)
            browser_open.assert_called_once_with("https://example.com", new=2)

    def test_external_project_database_is_read_only_allowlisted_and_parameterized(self) -> None:
        path = self.root / "project-data.sqlite3"
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE projects (id INTEGER, name TEXT)")
            connection.execute("CREATE TABLE private_data (secret TEXT)")
            connection.execute("INSERT INTO projects VALUES (1, 'hotel')")
            connection.execute("INSERT INTO private_data VALUES ('hidden')")
            connection.commit()
        project_db = ReadOnlyProjectDatabase(path, ("projects",))
        self.assertEqual(project_db.list_tables(), ["projects"])
        self.assertEqual(project_db.describe_table("projects")[0]["name"], "id")
        self.assertEqual(
            project_db.select_rows("projects", where_column="name", equals="hotel"),
            [{"id": 1, "name": "hotel"}],
        )
        self.assertEqual(
            project_db.select_rows(
                "projects",
                where_column="name",
                equals="hotel' OR 1=1",
            ),
            [],
        )
        with self.assertRaises(ValueError):
            project_db.describe_table("private_data")
        connection = project_db._connect()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("CREATE TABLE unexpected (id INTEGER)")
        finally:
            connection.close()

    def test_sqlalchemy_backend_selects_only_allowlisted_bound_columns(self) -> None:
        path = self.root / "orm-project.sqlite3"
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE projects (id INTEGER, name TEXT, api_token TEXT)")
            connection.execute("INSERT INTO projects VALUES (1, 'hotel', 'hidden')")
            connection.commit()
        project_db = ReadOnlyProjectDatabase(
            "postgresql+psycopg://user:password@localhost/project",
            ("projects",),
        )
        project_db._engine = create_engine(f"sqlite:///{path.as_posix()}")
        try:
            self.assertEqual(
                project_db.select_rows(
                    "projects",
                    columns=["id", "name"],
                    where_column="name",
                    equals="hotel' OR 1=1",
                ),
                [],
            )
            with self.assertRaises(ValueError):
                project_db.select_rows("projects", columns=["api_token"])
        finally:
            project_db._engine.dispose()

    def test_database_urls_create_mysql_and_postgresql_dialects_without_connecting(self) -> None:
        mysql = ReadOnlyProjectDatabase(
            "mysql+pymysql://user:password@localhost/project",
            ("projects",),
        )
        postgres = ReadOnlyProjectDatabase(
            "postgresql+psycopg://user:password@localhost/project",
            ("projects",),
        )
        mysql_engine = mysql._get_engine()
        postgres_engine = postgres._get_engine()
        try:
            self.assertEqual(mysql_engine.dialect.name, "mysql")
            self.assertEqual(postgres_engine.dialect.name, "postgresql")
        finally:
            mysql_engine.dispose()
            postgres_engine.dispose()

    def test_database_tool_does_not_read_before_user_approval(self) -> None:
        path = self.root / "project-data.sqlite3"
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE projects (id INTEGER)")
            connection.commit()
        tools = ToolRegistry(self.root, path, ("projects",))
        with patch.object(tools.project_database, "list_tables") as list_tables:
            result = tools.execute(
                "project_database",
                {"action": "list_tables"},
                lambda _: False,
            )
            self.assertIn("cancelada", result)
            list_tables.assert_not_called()
        self.assertEqual(
            tools.execute(
                "project_database",
                {"action": "list_tables"},
                lambda _: True,
            ),
            '["projects"]',
        )

    def test_brain_queries_external_project_data_offline_with_approval(self) -> None:
        path = self.root / "project-data.sqlite3"
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE projects (id INTEGER, name TEXT)")
            connection.execute("INSERT INTO projects VALUES (1, 'hotel')")
            connection.commit()
        brain = JarvisBrain(database=self.database, tool_approval=lambda _: False)
        brain.tools = ToolRegistry(self.root, path, ("projects",))
        denied = brain.ask("consulta la tabla projects")
        self.assertIn("cancelada", denied)
        brain.tool_approval = lambda _: True
        allowed = brain.ask("consulta la tabla projects donde name es hotel")
        self.assertIn("hotel", allowed)

    def test_brain_requires_approval_for_external_actions(self) -> None:
        brain = JarvisBrain(database=self.database)
        denied = brain.ask("Jarvis abre sitio https://example.com")
        self.assertIn("HUD", denied)
        with patch("tools.registry.webbrowser.open") as browser_open:
            brain.tool_approval = lambda _: False
            self.assertIn("cancelada", brain.ask("Jarvis abre sitio https://example.com"))
            browser_open.assert_not_called()
            brain.tool_approval = lambda _: True
            self.assertIn("ejecutada", brain.ask("Jarvis abre sitio https://example.com"))
            browser_open.assert_called_once()

    def test_brain_executes_only_registered_model_tool_calls(self) -> None:
        class ToolCallingProvider:
            def __init__(self) -> None:
                self.requests: list[list[dict[str, object]]] = []

            def chat(self, messages: list[dict[str, str]]) -> str:
                del messages
                return "unused"

            def chat_with_tools(
                self,
                messages: list[dict[str, object]],
                tools: list[dict[str, object]],
            ) -> tuple[str | None, list[dict[str, str]]]:
                self.requests.append(messages)
                if len(self.requests) == 1:
                    self.assert_tool_registered(tools, "list_files")
                    return None, [
                        {
                            "id": "call-1",
                            "name": "list_files",
                            "arguments": '{"path":"."}',
                        }
                    ]
                return "He consultado el workspace.", []

            @staticmethod
            def assert_tool_registered(
                tools: list[dict[str, object]],
                name: str,
            ) -> None:
                names = [
                    item["function"]["name"]  # type: ignore[index]
                    for item in tools
                ]
                if name not in names:
                    raise AssertionError(f"Missing function: {name}")

        provider = ToolCallingProvider()
        brain = JarvisBrain(provider=provider, database=self.database)
        brain.tools = ToolRegistry(self.root)
        (self.root / "project.py").write_text("print('hello')", encoding="utf-8")
        response = brain.ask("¿Puedes explorar los archivos del workspace?")
        self.assertEqual(response, "He consultado el workspace.")
        self.assertTrue(
            any(message.get("role") == "tool" for message in provider.requests[-1])
        )

    def test_hud_sends_and_persists_a_conversation_turn(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication

        from interface.hud import JarvisHud

        class OfflineProvider:
            def chat(self, messages: list[dict[str, str]]) -> str:
                del messages
                return "HUD operativo."

        app = QApplication.instance() or QApplication([])
        brain = JarvisBrain(provider=OfflineProvider(), database=self.database)
        window = JarvisHud(brain)
        window._send_message("¿Qué opinas del HUD?")
        worker = window._chat_worker
        self.assertIsNotNone(worker)
        worker.wait(5000)
        app.processEvents()
        self.assertEqual(len(self.database.get_messages(window.session_id)), 2)
        self.assertIn("HUD operativo.", window.chat.toPlainText())
        with patch("interface.hud.stop_speaking"):
            window.close()


if __name__ == "__main__":
    unittest.main()
