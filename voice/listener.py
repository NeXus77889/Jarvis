"""Speech recognition loop; loaded only when voice mode is requested."""

from __future__ import annotations

import time
import unicodedata
from collections.abc import Callable

EXIT_PHRASES = ("apagate", "desconectate", "adios jarvis")
LANGUAGE = "es-EC"


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip()


def listen_loop(
    handle_message: Callable[[str], str],
    speak: Callable[[str], None],
) -> None:
    try:
        import speech_recognition as sr
    except ImportError as exc:
        raise RuntimeError("Instale SpeechRecognition y PyAudio para usar el micrófono.") from exc

    recognizer = sr.Recognizer()
    recognizer.dynamic_energy_threshold = True
    recognizer.pause_threshold = 0.7  # termina la frase antes
    recognizer.non_speaking_duration = 0.4

    speak("Protocolo JARVIS en línea. Sistemas operativos, Señor.")
    failures = 0
    try:
        # El micrófono se abre UNA vez y se calibra UNA vez: no se pierden palabras.
        with sr.Microphone() as source:
            print("\n[Calibrando ruido ambiente...]")
            recognizer.adjust_for_ambient_noise(source, duration=1.0)
            while True:
                try:
                    print("\n[JARVIS escuchando...]")
                    audio = recognizer.listen(source, timeout=None, phrase_time_limit=10)
                    text = _normalize(recognizer.recognize_google(audio, language=LANGUAGE))
                    failures = 0
                    if not text:
                        continue
                    print(f"[Tú]: {text}")
                    if any(phrase in text for phrase in EXIT_PHRASES):
                        speak("Desconectando sistemas. Que tenga un excelente día, Señor.")
                        return
                    response = handle_message(text)
                    if response:
                        speak(response)
                    # Descarta lo que el micrófono captó mientras Jarvis hablaba.
                    time.sleep(0.25)
                except sr.WaitTimeoutError:
                    continue
                except sr.UnknownValueError:
                    continue
                except sr.RequestError as exc:
                    failures += 1
                    print(f"Servicio de reconocimiento no disponible: {exc}")
                    if failures == 1 or failures % 5 == 0:
                        speak("Parece que perdí la conexión, Señor. Sigo intentándolo.")
                    time.sleep(min(2 * failures, 10))
    except OSError as exc:
        raise RuntimeError(f"No se pudo acceder al micrófono: {exc}") from exc
