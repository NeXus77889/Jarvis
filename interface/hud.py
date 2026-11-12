"""Functional PyQt desktop HUD for chat, tools, reminders, and optional voice."""

from __future__ import annotations

import threading
from pathlib import Path

from PyQt5.QtCore import QDateTime, QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen, QTextCursor
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.brain import JarvisBrain
from core.config import settings
from core.reminders import ReminderManager
from tools.registry import ToolRegistry
from voice.speech import speak, stop_speaking
from voice.wake_word import VoskWakeWordListener

MAX_DISPLAY_FILE_CHARS = 16_000


class StatusOrb(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.state = "READY"
        self._phase = 0
        self._audio_level = 0.0
        self.setMinimumSize(120, 120)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(40)

    def set_state(self, state: str) -> None:
        self.state = state
        if state == "READY":
            self._audio_level = 0.0
        self.update()

    def set_audio_level(self, level: float) -> None:
        self._audio_level = max(0.0, min(1.0, level))
        self.update()

    def _animate(self) -> None:
        self._phase = (self._phase + 1) % 120
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = {
            "READY": QColor("#39c6ff"),
            "LISTENING": QColor("#61e294"),
            "THINKING": QColor("#f2c14e"),
            "SPEAKING": QColor("#cf8cff"),
        }.get(self.state, QColor("#ff6868"))
        size = min(self.width(), self.height()) * 0.32
        center = self.rect().center()
        pulse = self._phase % 30
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 70), 2))
        audio_expansion = self._audio_level * size * 0.25
        painter.drawEllipse(
            center,
            int(size + pulse / 3 + audio_expansion),
            int(size + pulse / 3 + audio_expansion),
        )
        painter.setPen(QPen(color, 3))
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 36))
        painter.drawEllipse(center, int(size), int(size))
        painter.setPen(QColor("#eaf6ff"))
        painter.drawText(self.rect(), Qt.AlignCenter, "J")


class ChatWorker(QThread):
    chunk = pyqtSignal(str)
    completed = pyqtSignal()

    def __init__(self, brain: JarvisBrain, message: str, session_id: str):
        super().__init__()
        self.brain = brain
        self.message = message
        self.session_id = session_id

    def run(self) -> None:
        try:
            for part in self.brain.ask_stream(self.message, self.session_id):
                self.chunk.emit(part)
        except Exception as exc:
            self.chunk.emit(f"Error interno de conversación: {type(exc).__name__}: {exc}")
        finally:
            self.completed.emit()


class VoiceInputWorker(QThread):
    transcript = pyqtSignal(str)
    status = pyqtSignal(str)

    def run(self) -> None:
        try:
            import speech_recognition as sr

            recognizer = sr.Recognizer()
            with sr.Microphone() as source:
                self.status.emit("Calibrando micrófono…")
                recognizer.adjust_for_ambient_noise(source, duration=0.4)
                self.status.emit("Escuchando una instrucción…")
                audio = recognizer.listen(source, timeout=5, phrase_time_limit=12)
            self.transcript.emit(recognizer.recognize_google(audio, language="es-EC"))
        except Exception as exc:
            self.status.emit(f"Entrada de voz no disponible: {type(exc).__name__}: {exc}")


class SpeechWorker(QThread):
    completed = pyqtSignal()

    def __init__(self, text: str):
        super().__init__()
        self.text = text

    def run(self) -> None:
        speak(self.text)
        self.completed.emit()


class ApprovalRequest:
    def __init__(self, prompt: str):
        self.prompt = prompt
        self.event = threading.Event()
        self.approved = False


class ReminderDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Nuevo recordatorio")
        self.content = QLineEdit()
        self.due_at = QDateTimeEdit(QDateTime.currentDateTime().addSecs(900))
        self.due_at.setCalendarPopup(True)
        self.due_at.setDisplayFormat("yyyy-MM-dd HH:mm")
        layout = QFormLayout(self)
        layout.addRow("Recordatorio:", self.content)
        layout.addRow("Fecha y hora:", self.due_at)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)


class JarvisHud(QMainWindow):
    approval_requested = pyqtSignal(object)
    wake_state_changed = pyqtSignal(str)
    wake_command_received = pyqtSignal(str)
    audio_level_changed = pyqtSignal(float)

    def __init__(self, brain: JarvisBrain | None = None):
        super().__init__()
        self.brain = brain or JarvisBrain()
        self.brain.tool_approval = self._request_approval
        self.reminders = ReminderManager(self.brain.database)
        self.tools = ToolRegistry(settings.workspace_path)
        self.session_id = self.brain.session_id
        self._chat_worker: ChatWorker | None = None
        self._voice_worker: VoiceInputWorker | None = None
        self._speech_worker: SpeechWorker | None = None
        self._wake_listener: VoskWakeWordListener | None = None
        self._bot_response = ""
        self._speak_response = False
        self._stream_started = False
        self._closing = False
        self._pending_approvals: list[ApprovalRequest] = []
        self.setWindowTitle("J.A.R.V.I.S. — Personal Assistant")
        self.resize(900, 720)
        self._build_ui()
        self.approval_requested.connect(self._handle_approval_request)
        self.wake_state_changed.connect(self._set_status)
        self.wake_command_received.connect(self._handle_wake_command)
        self.audio_level_changed.connect(self.orb.set_audio_level)
        self._load_conversations()
        self._load_history()
        self._refresh_system_status()
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._check_reminders)
        self._status_timer.start(30_000)

    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        header = QHBoxLayout()
        self.orb = StatusOrb()
        header.addWidget(self.orb)
        title = QVBoxLayout()
        title.addWidget(QLabel("<h1>J.A.R.V.I.S.</h1>"))
        self.status_label = QLabel("En línea · Inteligencia híbrida")
        title.addWidget(self.status_label)
        self.system_label = QLabel("Sistema: consultando…")
        title.addWidget(self.system_label)
        header.addLayout(title, 1)
        self.sessions = QComboBox()
        self.sessions.currentIndexChanged.connect(self._select_conversation)
        header.addWidget(self.sessions)
        new_session = QPushButton("Nueva sesión")
        new_session.clicked.connect(self._new_conversation)
        header.addWidget(new_session)
        layout.addLayout(header)

        self.chat = QPlainTextEdit()
        self.chat.setReadOnly(True)
        self.chat.setPlaceholderText("Las conversaciones y respuestas aparecen aquí.")
        layout.addWidget(self.chat, 1)
        composer = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Escriba una instrucción…")
        self.input.returnPressed.connect(self._send_message)
        composer.addWidget(self.input, 1)
        self.send_button = QPushButton("Enviar")
        self.send_button.clicked.connect(self._send_message)
        composer.addWidget(self.send_button)
        self.voice_button = QPushButton("Hablar")
        self.voice_button.clicked.connect(self._start_voice_input)
        composer.addWidget(self.voice_button)
        self.stop_button = QPushButton("Interrumpir voz")
        self.stop_button.clicked.connect(stop_speaking)
        composer.addWidget(self.stop_button)
        self.read_aloud = QCheckBox("Leer respuesta")
        composer.addWidget(self.read_aloud)
        layout.addLayout(composer)

        tools_layout = QHBoxLayout()
        for label, callback in (
            ("Estado del sistema", self._show_system_status),
            ("Archivos del proyecto", self._browse_files),
            ("Buscar en archivos", self._search_files),
            ("Abrir web", self._open_website),
            ("Aplicaciones", self._launch_app),
            ("Recordatorios", self._manage_reminders),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            tools_layout.addWidget(button)
        self.wake_button = QPushButton("Activar palabra Jarvis")
        self.wake_button.setEnabled(settings.vosk_model_path is not None)
        self.wake_button.clicked.connect(self._toggle_wake_word)
        tools_layout.addWidget(self.wake_button)
        layout.addLayout(tools_layout)
        central.setStyleSheet(
            "QWidget { background: #101923; color: #eaf6ff; }"
            "QPlainTextEdit, QLineEdit, QComboBox { background: #172635; border: 1px solid #31516b; "
            "border-radius: 6px; padding: 7px; }"
            "QPushButton { background: #1c3448; border: 1px solid #39c6ff; border-radius: 6px; "
            "padding: 7px; } QPushButton:hover { background: #28506c; }"
        )
        self.setCentralWidget(central)

    def _append(self, speaker: str, text: str) -> None:
        if speaker == "JARVIS":
            self.chat.appendPlainText("JARVIS: " + text)
        else:
            self.chat.appendPlainText(f"{speaker}: {text}")
        self.chat.verticalScrollBar().setValue(self.chat.verticalScrollBar().maximum())

    def _load_conversations(self) -> None:
        self.sessions.blockSignals(True)
        self.sessions.clear()
        for item in self.brain.database.list_conversations(limit=100):
            self.sessions.addItem(
                item["title"] or str(item["created_at"])[:19],
                item["id"],
            )
        selected = self.sessions.findData(self.session_id)
        if selected >= 0:
            self.sessions.setCurrentIndex(selected)
        self.sessions.blockSignals(False)

    def _load_history(self) -> None:
        self.chat.clear()
        for message in self.brain.database.get_messages(self.session_id):
            self._append("Tú" if message["role"] == "user" else "JARVIS", message["content"])

    def _select_conversation(self, index: int) -> None:
        session_id = self.sessions.itemData(index)
        if session_id:
            self.session_id = str(session_id)
            self._load_history()

    def _new_conversation(self) -> None:
        if self._chat_worker and self._chat_worker.isRunning():
            return
        self.session_id = self.brain.conversations.start()
        self._load_conversations()
        self._load_history()

    def _send_message(self, message: str | None = None) -> None:
        content = (message if message is not None else self.input.text()).strip()
        if not content or (self._chat_worker and self._chat_worker.isRunning()):
            return
        if message is None:
            self.input.clear()
        self._append("Tú", content)
        self._bot_response = ""
        self._stream_started = False
        self.send_button.setEnabled(False)
        self.orb.set_state("THINKING")
        self._set_status("Razonando…")
        self._chat_worker = ChatWorker(self.brain, content, self.session_id)
        self._chat_worker.chunk.connect(self._on_response_chunk)
        self._chat_worker.completed.connect(self._on_response_complete)
        self._chat_worker.finished.connect(self._finish_requested_close)
        self._chat_worker.start()

    def _on_response_chunk(self, chunk: str) -> None:
        self._bot_response += chunk
        cursor = self.chat.textCursor()
        cursor.movePosition(QTextCursor.End)
        if not self._stream_started:
            cursor.insertText("JARVIS: ")
            self._stream_started = True
        cursor.insertText(chunk)
        self.chat.setTextCursor(cursor)
        self.chat.ensureCursorVisible()
        self.orb.set_state("THINKING")

    def _on_response_complete(self) -> None:
        if not self._stream_started:
            self._append("JARVIS", self._bot_response or "No se recibió una respuesta.")
        self.send_button.setEnabled(True)
        self.orb.set_state("READY")
        self._set_status("En línea")
        self._load_conversations()
        if (self.read_aloud.isChecked() or self._speak_response) and not self._closing:
            self._speak_response = False
            self.orb.set_state("SPEAKING")
            self._speech_worker = SpeechWorker(self._bot_response)
            self._speech_worker.completed.connect(
                lambda: (self.orb.set_state("READY"), self._set_status("En línea"))
            )
            self._speech_worker.finished.connect(self._finish_requested_close)
            self._speech_worker.start()

    def _start_voice_input(self) -> None:
        if self._voice_worker and self._voice_worker.isRunning():
            return
        self._speak_response = True
        self.orb.set_state("LISTENING")
        self._voice_worker = VoiceInputWorker()
        self._voice_worker.status.connect(self._set_status)
        self._voice_worker.status.connect(
            lambda status: self._on_voice_error(status)
        )
        self._voice_worker.transcript.connect(self._on_voice_transcript)
        self._voice_worker.finished.connect(self._finish_requested_close)
        self._voice_worker.start()

    def _on_voice_error(self, status: str) -> None:
        if status.startswith("Entrada de voz no disponible"):
            self.orb.set_state("READY")
            self._speak_response = False

    def _on_voice_transcript(self, text: str) -> None:
        self.orb.set_state("THINKING")
        self._set_status("Instrucción reconocida")
        self._send_message(text)

    def _toggle_wake_word(self) -> None:
        if self._wake_listener is not None:
            self._wake_listener.stop()
            self._wake_listener = None
            self.wake_button.setText("Activar palabra Jarvis")
            self.orb.set_state("READY")
            self._set_status("Wake word detenido")
            return
        if settings.vosk_model_path is None:
            QMessageBox.warning(self, "Modelo no configurado", "Defina JARVIS_VOSK_MODEL_PATH.")
            return
        self._wake_listener = VoskWakeWordListener(
            settings.vosk_model_path,
            self._wake_status,
            self._on_wake_command,
            self._on_audio_level,
        )
        try:
            self._wake_listener.start()
        except (FileNotFoundError, OSError) as exc:
            self._wake_listener = None
            QMessageBox.warning(self, "Wake word no disponible", str(exc))
            return
        self.wake_button.setText("Detener palabra Jarvis")

    def _wake_status(self, status: str) -> None:
        self.wake_state_changed.emit(status)

    def _on_wake_command(self, command: str) -> None:
        self.wake_command_received.emit(command)

    def _on_audio_level(self, level: float) -> None:
        self.audio_level_changed.emit(level)

    def _handle_wake_command(self, command: str) -> None:
        self._speak_response = True
        self._send_message(command)

    def _request_approval(self, prompt: str) -> bool:
        request = ApprovalRequest(prompt)
        self._pending_approvals.append(request)
        self.approval_requested.emit(request)
        try:
            if not request.event.wait(timeout=120):
                return False
            return request.approved
        finally:
            if request in self._pending_approvals:
                self._pending_approvals.remove(request)

    def _handle_approval_request(self, request: ApprovalRequest) -> None:
        request.approved = (
            QMessageBox.question(
                self,
                "Confirmar acción",
                request.prompt,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            == QMessageBox.Yes
        )
        request.event.set()

    def _finish_requested_close(self) -> None:
        if self._closing and not any(
            worker and worker.isRunning()
            for worker in (self._chat_worker, self._voice_worker, self._speech_worker)
        ):
            self.close()

    def _refresh_system_status(self) -> None:
        try:
            overview = self.tools.system_overview()
            total_gb = int(overview["memory_total_bytes"]) / 1024**3
            used = overview["memory_percent_used"]
            cpu_count = overview["logical_cpus"]
            self.system_label.setText(
                f"CPU: {cpu_count} hilos · RAM: {used:.0f}% de {total_gb:.1f} GB"
            )
        except (ImportError, OSError, ValueError) as exc:
            self.system_label.setText(f"Monitorización no disponible: {exc}")

    def _show_system_status(self) -> None:
        try:
            overview = self.tools.system_overview()
            details = "\n".join(f"{key}: {value}" for key, value in overview.items())
            QMessageBox.information(self, "Estado del sistema", details)
        except (ImportError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Monitorización", str(exc))

    def _browse_files(self) -> None:
        try:
            files = self.tools.list_files()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Archivos", str(exc))
            return
        choices = [
            str(item["name"]) + ("/" if item["is_directory"] else "")
            for item in files
        ]
        selection, accepted = QInputDialog.getItem(
            self,
            "Workspace del proyecto",
            f"Solo lectura · {self.tools.workspace}",
            choices,
            0,
            False,
        )
        if not accepted or not selection:
            return
        relative = selection.rstrip("/")
        if selection.endswith("/"):
            self._browse_directory(relative)
            return
        try:
            content = self.tools.read_file(relative)
        except (OSError, UnicodeError, ValueError) as exc:
            QMessageBox.warning(self, "No se pudo leer", str(exc))
            return
        self._show_file_contents(relative, content)

    def _browse_directory(self, relative: str) -> None:
        try:
            files = self.tools.list_files(relative)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Archivos", str(exc))
            return
        choices = [
            f"{relative}\\{item['name']}" + ("\\" if item["is_directory"] else "")
            for item in files
        ]
        selection, accepted = QInputDialog.getItem(
            self,
            "Archivos del proyecto",
            "Solo lectura",
            choices,
            0,
            False,
        )
        if not accepted or not selection:
            return
        if selection.endswith("\\"):
            self._browse_directory(selection.rstrip("\\"))
            return
        try:
            content = self.tools.read_file(selection)
        except (OSError, UnicodeError, ValueError) as exc:
            QMessageBox.warning(self, "No se pudo leer", str(exc))
            return
        self._show_file_contents(Path(selection).name, content)

    def _show_file_contents(self, title: str, content: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(800, 600)
        layout = QVBoxLayout(dialog)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(content[:MAX_DISPLAY_FILE_CHARS])
        layout.addWidget(editor)
        dialog.exec_()

    def _search_files(self) -> None:
        query, accepted = QInputDialog.getText(self, "Buscar en proyecto", "Texto:")
        if not accepted or not query:
            return
        try:
            matches = self.tools.search_files(query)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Búsqueda", str(exc))
            return
        QMessageBox.information(self, "Coincidencias", "\n".join(matches) or "Sin resultados.")

    def _open_website(self) -> None:
        url, accepted = QInputDialog.getText(
            self,
            "Abrir sitio web",
            "Dirección HTTPS:",
        )
        if not accepted or not url:
            return
        try:
            self.tools.open_website(
                url,
                lambda prompt: QMessageBox.question(
                    self,
                    "Confirmar acción",
                    prompt,
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                == QMessageBox.Yes,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Navegación web", str(exc))

    def _launch_app(self) -> None:
        app, accepted = QInputDialog.getItem(
            self,
            "Abrir aplicación",
            "Aplicación permitida:",
            ["notepad", "calculator"],
            0,
            False,
        )
        if not accepted:
            return
        try:
            self.tools.launch_application(
                app,
                lambda prompt: QMessageBox.question(
                    self,
                    "Confirmar acción",
                    prompt,
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                == QMessageBox.Yes,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, "Aplicaciones", str(exc))

    def _manage_reminders(self) -> None:
        dialog = ReminderDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            try:
                due_at = dialog.due_at.dateTime().toPyDateTime().astimezone()
                reminder_id = self.reminders.add(dialog.content.text(), due_at)
                self._append("JARVIS", f"Recordatorio {reminder_id} guardado.")
            except ValueError as exc:
                QMessageBox.warning(self, "Recordatorio no guardado", str(exc))
        reminders = self.reminders.list()
        if reminders:
            lines = [
                f"{item['id']}: {item['content']} — {item['due_at']}"
                for item in reminders
            ]
            QMessageBox.information(self, "Recordatorios pendientes", "\n".join(lines))

    def _check_reminders(self) -> None:
        for reminder in self.reminders.due():
            reminder_id = int(reminder["id"])
            if self.reminders.notify_once(reminder_id):
                QMessageBox.information(
                    self,
                    "Recordatorio JARVIS",
                    str(reminder["content"]),
                )

    def _set_status(self, status: str) -> None:
        self.status_label.setText(status)
        if status.startswith("Escuchando"):
            self.orb.set_state("LISTENING")
        elif status.startswith("Razonando"):
            self.orb.set_state("THINKING")

    def closeEvent(self, event) -> None:
        for request in self._pending_approvals:
            request.approved = False
            request.event.set()
        if self._wake_listener is not None:
            self._wake_listener.stop()
            self._wake_listener = None
        stop_speaking()
        if any(
            worker and worker.isRunning()
            for worker in (self._chat_worker, self._voice_worker, self._speech_worker)
        ):
            self._closing = True
            event.ignore()
            return
        event.accept()


def run_hud(brain: JarvisBrain | None = None) -> int:
    application = QApplication.instance() or QApplication([])
    window = JarvisHud(brain)
    window.show()
    return application.exec_()
