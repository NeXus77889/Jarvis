"""Speech recognition loop; loaded only when voice mode is requested."""

from __future__ import annotations

import math
import os
import threading
import time
import unicodedata
from array import array
from collections.abc import Callable

EXIT_PHRASES = ("apagate", "desconectate", "adios jarvis")
LANGUAGE = "es-EC"


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip()


def _rms(data: bytes) -> float:
    samples = array("h")
    samples.frombytes(data[: len(data) - len(data) % 2])
    if not samples:
        return 0.0
    return math.sqrt(sum(value * value for value in samples) / len(samples))


def _speak_interruptible(text: str, speak: Callable[[str], None], source, recognizer) -> None:
    """Habla en un hilo y vigila el micrófono: si el usuario habla encima, calla.

    Desactívalo con JARVIS_BARGE_IN=off si con altavoces se interrumpe solo
    (con audífonos funciona mucho mejor).
    """
    from voice.speech import stop_speaking

    thread = threading.Thread(target=speak, args=(text,), daemon=True)
    thread.start()
    if os.getenv("JARVIS_BARGE_IN", "on").lower() in {"off", "0", "no"}:
        thread.join()
        return
    echo_peak = 0.0
    loud = 0
    started = time.monotonic()
    while thread.is_alive():
        try:
            level = _rms(source.stream.read(source.CHUNK))
        except Exception:
            thread.join()
            return
        # Los primeros 0.8 s miden cuánto "eco" de su propia voz entra al micrófono.
        if time.monotonic() - started < 0.8:
            echo_peak = max(echo_peak, level)
            continue
        threshold = max(recognizer.energy_threshold * 2.5, echo_peak * 2.0)
        loud = loud + 1 if level > threshold else max(0, loud - 1)
        if loud >= 4:  # ~0.25 s de voz sostenida
            stop_speaking()
            break
    thread.join(timeout=3)


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
                        _speak_interruptible(response, speak, source, recognizer)
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
