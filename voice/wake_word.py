"""Optional CPU-only offline wake-word and utterance recognizer powered by Vosk."""

from __future__ import annotations

import json
import math
import struct
import threading
from collections.abc import Callable
from pathlib import Path


class VoskWakeWordListener:
    def __init__(
        self,
        model_path: str | Path,
        on_state: Callable[[str], None],
        on_command: Callable[[str], None],
        on_audio_level: Callable[[float], None] | None = None,
    ):
        self.model_path = Path(model_path).expanduser()
        self.on_state = on_state
        self.on_command = on_command
        self.on_audio_level = on_audio_level
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.model_path.is_dir():
            raise FileNotFoundError(f"No se encontró el modelo Vosk: {self.model_path}")
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="jarvis-wake-word", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        try:
            import pyaudio
            from vosk import KaldiRecognizer, Model, SetLogLevel
        except ImportError as exc:
            self.on_state("Wake word requiere los paquetes PyAudio y vosk.")
            return

        audio = pyaudio.PyAudio()
        stream = None
        try:
            SetLogLevel(-1)
            model = Model(str(self.model_path))
            wake_recognizer = KaldiRecognizer(model, 16000, json.dumps(["jarvis", "[unk]"]))
            command_recognizer = None
            stream = audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                frames_per_buffer=4000,
            )
            stream.start_stream()
            armed = False
            self.on_state("Esperando «Jarvis» (reconocimiento local en CPU)")
            while not self._stop_event.is_set():
                frame = stream.read(2000, exception_on_overflow=False)
                if self.on_audio_level is not None:
                    samples = struct.unpack(f"<{len(frame) // 2}h", frame)
                    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
                    self.on_audio_level(min(1.0, rms / 12_000))
                if armed and command_recognizer is not None:
                    if command_recognizer.AcceptWaveform(frame):
                        result = json.loads(command_recognizer.Result()).get("text", "").strip()
                        if result:
                            self.on_command(result)
                            armed = False
                            command_recognizer = None
                            self.on_state("Esperando «Jarvis» (reconocimiento local en CPU)")
                else:
                    if wake_recognizer.AcceptWaveform(frame):
                        result = json.loads(wake_recognizer.Result()).get("text", "").strip()
                        if "jarvis" in result.split():
                            armed = True
                            command_recognizer = KaldiRecognizer(model, 16000)
                            self.on_state("Activo; diga su instrucción")
                    elif "jarvis" in json.loads(wake_recognizer.PartialResult()).get(
                        "partial", ""
                    ).split():
                        armed = True
                        command_recognizer = KaldiRecognizer(model, 16000)
                        self.on_state("Activo; diga su instrucción")
        except Exception as exc:
            self.on_state(f"Wake word detenido: {type(exc).__name__}: {exc}")
        finally:
            if stream is not None:
                stream.stop_stream()
                stream.close()
            audio.terminate()
