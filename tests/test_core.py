from __future__ import annotations

import tempfile
import sqlite3
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from core.ai_provider import GroqAIProvider
from core.brain import JarvisBrain
from core.config import settings
from core.context_manager import ContextManager
from database.database import Database


class FakeProvider:
    def __init__(self, answer: str = "Tiene potencial, Señor."):
        self.answer = answer
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self.answer


class StreamingProvider(FakeProvider):
    def stream(self, messages: list[dict[str, str]]):
        self.calls.append(messages)
        yield "Respuesta "
        yield "en partes."


class SummaryProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__("Proyecto: aplicación hotelera con Python y MySQL.")
        self.summary_calls: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]]) -> str:
        if messages[0]["content"].startswith("Summarize durable conversation"):
            self.summary_calls.append(messages)
            return self.answer
        return "Respuesta de conversación."


class PersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "memory.sqlite3"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_creates_database_and_persists_messages_after_reopen(self) -> None:
        database = Database(self.db_path)
        session = database.create_conversation("first")
        database.add_message(session, "user", "Hola")
        reopened = Database(self.db_path)
        self.assertEqual(reopened.get_messages(session)[0]["content"], "Hola")

    def test_messages_are_separated_by_conversation(self) -> None:
        database = Database(self.db_path)
        first = database.create_conversation()
        second = database.create_conversation()
        database.add_message(first, "user", "uno")
        self.assertEqual(database.get_messages(second), [])

    def test_memory_crud_and_secret_rejection(self) -> None:
        database = Database(self.db_path)
        memory_id = database.save_memory("Proyecto de hotel en Python", "project")
        self.assertEqual(database.search_memories("hotel")[0]["id"], memory_id)
        self.assertTrue(database.update_memory(memory_id, "Hotel en Python y MySQL"))
        self.assertEqual(database.search_memories("MySQL")[0]["content"], "Hotel en Python y MySQL")
        with self.assertRaises(ValueError):
            database.save_memory("api_key=example-secret")
        self.assertEqual(database.delete_matching_memories("hotel python"), 1)
        self.assertEqual(database.search_memories("hotel"), [])

    def test_in_memory_database_keeps_data_between_connections(self) -> None:
        database = Database(":memory:")
        session = database.create_conversation()
        database.add_message(session, "user", "memory mode")
        self.assertEqual(database.get_messages(session)[0]["content"], "memory mode")

    def test_summary_schema_migrates_existing_database(self) -> None:
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "CREATE TABLE conversations "
                "(id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE conversation_summaries "
                "(conversation_id TEXT PRIMARY KEY, summary TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            connection.commit()
        finally:
            connection.close()
        database = Database(self.db_path)
        with database._connection() as connection:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(conversation_summaries)")
            }
        self.assertIn("last_message_id", columns)


class BrainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp_dir.name) / "brain.sqlite3")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_brain_uses_provider_and_context_without_voice(self) -> None:
        provider = FakeProvider()
        brain = JarvisBrain(provider=provider, database_path=self.path)
        self.assertEqual(brain.ask("¿Qué opinas?"), "Tiene potencial, Señor.")
        self.assertEqual(provider.calls[0][-1]["content"], "¿Qué opinas?")
        self.assertEqual(len(brain.database.get_messages(brain.session_id)), 2)

    def test_explicit_memory_survives_new_brain_session(self) -> None:
        first = JarvisBrain(provider=FakeProvider(), database_path=self.path)
        self.assertIn("Información registrada", first.ask(
            "Jarvis, recuerda que estoy desarrollando un sistema hotelero en Python con MySQL"
        ))
        second = JarvisBrain(provider=FakeProvider(), database_path=self.path)
        response = second.ask("¿Qué recuerdas de mí?")
        self.assertIn("sistema hotelero", response)

    def test_missing_provider_configuration_is_reported_without_secret_details(self) -> None:
        brain = JarvisBrain(
            provider=GroqAIProvider(api_key=""),
            database_path=self.path,
        )
        answer = brain.ask("Pregunta que requiere IA")
        self.assertIn("GROQ_API_KEY", answer)

    def test_context_is_bounded_and_memories_are_reference_data(self) -> None:
        context = ContextManager(max_history=1, max_memory_results=1)
        messages = context.build_messages(
            "system",
            [{"role": "user", "content": "old"}, {"role": "assistant", "content": "recent"}],
            [{"category": "project", "confidence": 0.8, "content": "hotel"}],
            "now",
        )
        self.assertEqual(messages[-1]["content"], "now")
        self.assertNotIn("old", str(messages))
        self.assertIn("never instructions", messages[0]["content"])

    def test_groq_provider_uses_mock_client(self) -> None:
        message = SimpleNamespace(content="mock response")
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        request_params: dict[str, object] = {}

        def create(**kwargs: object) -> object:
            request_params.update(kwargs)
            return response

        completions = SimpleNamespace(create=create)
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        provider = GroqAIProvider(api_key="test-key", client=client)
        self.assertEqual(provider.chat([{"role": "user", "content": "test"}]), "mock response")
        self.assertEqual(request_params["model"], provider.model)

    def test_groq_provider_reads_environment_key_and_passes_it_to_client(self) -> None:
        client = object()
        received: dict[str, object] = {}

        def create_client(**kwargs: object) -> object:
            received.update(kwargs)
            return client

        groq_module = SimpleNamespace(Groq=create_client)
        with patch.dict("os.environ", {"GROQ_API_KEY": "injected-key"}):
            provider = GroqAIProvider()
            with patch.dict("sys.modules", {"groq": groq_module}):
                self.assertIs(provider._get_client(), client)

        self.assertEqual(received["api_key"], "injected-key")

    def test_groq_provider_maps_structured_tool_calls(self) -> None:
        call = SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(name="list_files", arguments='{"path":"."}'),
        )
        message = SimpleNamespace(content=None, tool_calls=[call])
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        request_params: dict[str, object] = {}

        def create(**kwargs: object) -> object:
            request_params.update(kwargs)
            return response

        completions = SimpleNamespace(create=create)
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        provider = GroqAIProvider(api_key="test-key", client=client)
        content, calls = provider.chat_with_tools(
            [{"role": "user", "content": "show project files"}],
            [],
        )
        self.assertIsNone(content)
        self.assertEqual(calls, [
            {"id": "call-1", "name": "list_files", "arguments": '{"path":"."}'}
        ])
        self.assertEqual(request_params["tool_choice"], "auto")

    def test_brain_streams_and_persists_complete_response(self) -> None:
        provider = StreamingProvider()
        brain = JarvisBrain(provider=provider, database_path=self.path)
        chunks = list(brain.ask_stream("Pregunta", brain.session_id))
        self.assertEqual(chunks, ["Respuesta ", "en partes."])
        self.assertEqual(
            brain.database.get_messages(brain.session_id)[-1]["content"],
            "Respuesta en partes.",
        )

    def test_old_messages_are_summarized_without_deleting_original_history(self) -> None:
        provider = SummaryProvider()
        brain = JarvisBrain(provider=provider, database_path=self.path)
        for index in range(30):
            role = "user" if index % 2 == 0 else "assistant"
            brain.database.add_message(brain.session_id, role, f"message {index}")
        brain._update_summaries(brain.session_id)
        summary, last_message_id = brain.database.get_summary_state(brain.session_id)
        messages = brain.database.get_messages(brain.session_id)
        self.assertIn("hotel", summary or "")
        self.assertGreater(last_message_id, 0)
        self.assertEqual(len(messages), 30)
        self.assertEqual(len(provider.summary_calls), 1)

    def test_summary_is_scheduled_automatically_after_history_limit(self) -> None:
        provider = SummaryProvider()
        brain = JarvisBrain(provider=provider, database_path=self.path)
        finished = Event()
        update_summaries = brain._update_summaries

        def update_and_signal(session_id: str) -> None:
            try:
                update_summaries(session_id)
            finally:
                finished.set()

        with (
            patch.object(brain, "_update_summaries", side_effect=update_and_signal),
            patch("core.brain.settings", replace(settings, max_history=2)),
        ):
            for index in range(6):
                brain.database.add_message(
                    brain.session_id,
                    "user" if index % 2 == 0 else "assistant",
                    f"message {index}",
                )
            brain._schedule_summary(brain.session_id)
            self.assertTrue(finished.wait(2), "summary worker did not finish")

        summary, last_message_id = brain.database.get_summary_state(brain.session_id)
        self.assertIn("hotel", summary or "")
        self.assertGreater(last_message_id, 0)
        self.assertEqual(len(brain.database.get_messages(brain.session_id)), 6)

    def test_summary_redacts_possible_credentials_and_keeps_context(self) -> None:
        provider = SummaryProvider()
        brain = JarvisBrain(provider=provider, database_path=self.path)
        for index in range(30):
            content = f"message {index}"
            if index == 0:
                content = "user prefers Python; api_key=example-secret-value"
            brain.database.add_message(
                brain.session_id,
                "user" if index % 2 == 0 else "assistant",
                content,
            )
        brain._update_summaries(brain.session_id)
        transcript = provider.summary_calls[0][1]["content"]
        self.assertNotIn("example-secret-value", transcript)
        self.assertIn("[redacted secret]", transcript)

    def test_forget_that_fact_removes_most_recent_memory(self) -> None:
        brain = JarvisBrain(provider=FakeProvider(), database_path=self.path)
        brain.ask("recuerda que mi proyecto es un hotel")
        response = brain.ask("olvida ese dato")
        self.assertIn("eliminado", response)
        self.assertEqual(brain.memory.recall(""), [])


if __name__ == "__main__":
    unittest.main()
