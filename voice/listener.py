"""Speech recognition loop; loaded only when voice mode is requested."""

from __future__ import annotations

import math
import os
import threading
import time
import unicodedata
from array import array
from collections.abc import Callable

from core import visual_state

EXIT_PHRASES = ("apagate", "desconectate", "adios jarvis")
SILENCE_PHRASES = ("silencio", "haz silencio", "callate", "deja de hablar", "no hables", "para de hablar")
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


def _install_level_tap(source, recognizer) -> None:
    """Pasa el volumen del micrófono al núcleo visual mientras Jarvis espera."""
    from core import visual_state

    stream = source.stream
    original = stream.read

    def read(size, *args, **kwargs):
        data = original(size, *args, **kwargs)
        try:
            if visual_state.get_mode() in {"idle", "listening"}:
                base = max(recognizer.energy_threshold, 1.0)
                level = _rms(data)
                visual_state.set_level(min(1.0, level / (base * 4)))
                if level > base * 1.6:
                    visual_state.set_mode("listening")
        except Exception:
            pass
        return data

    try:
        stream.read = read
    except Exception:
        pass


def _speak_interruptible(text: str, speak: Callable[[str], None], source, recognizer) -> str | None:
    """Habla mientras vigila el micro; devuelve la frase que interrumpió a Jarvis.

    Desactívalo con JARVIS_BARGE_IN=off si con altavoces se interrumpe solo
    (con audífonos funciona mucho mejor).
    """
    from voice.speech import stop_speaking
    import speech_recognition as sr

    thread = threading.Thread(target=speak, args=(text,), daemon=True)
    thread.start()
    if os.getenv("JARVIS_BARGE_IN", "on").lower() in {"off", "0", "no"}:
        thread.join()
        return None
    echo_peak = 0.0
    loud = 0
    started = time.monotonic()
    phrase: list[bytes] = []
    while thread.is_alive():
        try:
            frame = source.stream.read(source.CHUNK)
            level = _rms(frame)
        except Exception:
            thread.join()
            return None
        # Estima el eco de los audífonos brevemente; después puede detectar voz encima.
        if time.monotonic() - started < 0.3:
            echo_peak = max(echo_peak, level)
            continue
        threshold = max(recognizer.energy_threshold * 1.8, echo_peak * 1.35)
        loud = loud + 1 if level > threshold else max(0, loud - 1)
        if level > threshold:
            phrase.append(frame)
        if loud >= 3:  # voz sostenida durante varios bloques seguidos
            stop_speaking()
            quiet = 0
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and quiet < 8:
                try:
                    frame = source.stream.read(source.CHUNK)
                except Exception:
                    break
                phrase.append(frame)
                if _rms(frame) < threshold:
                    quiet += 1
                else:
                    quiet = 0
            break
    thread.join(timeout=3)
    if phrase:
        try:
            audio = sr.AudioData(b"".join(phrase), source.SAMPLE_RATE, source.SAMPLE_WIDTH)
            return recognizer.recognize_google(audio, language=LANGUAGE)
        except Exception:
            return None
    return None


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

    speak("Sistemas en línea. Dime qué necesitas, Samuel.")
    failures = 0
    muted = False
    try:
        # El micrófono se abre UNA vez y se calibra UNA vez: no se pierden palabras.
        device = os.getenv("JARVIS_MIC_INDEX", "").strip()
        names = sr.Microphone.list_microphone_names()
        index = int(device) if device.isdigit() else None
        shown = names[index] if index is not None and index < len(names) else "predeterminado de Windows"
        print(f"[Micrófono: {shown}]  (para cambiarlo: JARVIS_MIC_INDEX en .env)")
        with sr.Microphone(device_index=index) as source:
            print("\n[Calibrando ruido ambiente... guarda silencio 1 segundo]")
            recognizer.adjust_for_ambient_noise(source, duration=1.0)
            override = os.getenv("JARVIS_ENERGY", "").strip()
            if override.isdigit():
                recognizer.energy_threshold = float(override)
                recognizer.dynamic_energy_threshold = False
            print(f"[Umbral de voz: {recognizer.energy_threshold:.0f}]")
            _install_level_tap(source, recognizer)
            while True:
                try:
                    visual_state.set_mode("idle")
                    print("\n[JARVIS escuchando...]")
                    audio = recognizer.listen(source, timeout=None, phrase_time_limit=10)
                    visual_state.set_mode("thinking")
                    text = _normalize(recognizer.recognize_google(audio, language=LANGUAGE))
                    failures = 0
                    if not text:
                        continue
                    if muted:
                        if "jarvis" not in text.split():
                            continue
                        text = text.split("jarvis", 1)[1].strip(" ,.!¿?¡")
                        muted = False
                        if not text:
                            continue
                    if any(phrase in text for phrase in SILENCE_PHRASES):
                        from voice.speech import stop_speaking

                        stop_speaking()
                        muted = True
                        print("[JARVIS en silencio; di «Jarvis» para reactivarlo]")
                        continue
                    print(f"[Tú]: {text}")
                    if any(phrase in text for phrase in EXIT_PHRASES):
                        speak("Desconectando. Que te vaya brillante, Samuel.")
                        return
                    response = handle_message(text)
                    while response and not muted:
                        interrupted = _speak_interruptible(response, speak, source, recognizer)
                        if not interrupted:
                            break
                        interrupted = _normalize(interrupted)
                        if any(phrase in interrupted for phrase in SILENCE_PHRASES):
                            from voice.speech import stop_speaking

                            stop_speaking()
                            muted = True
                            print("[JARVIS en silencio; di «Jarvis» para reactivarlo]")
                            break
                        print(f"[Tú]: {interrupted}")
                        response = handle_message(interrupted)
                    # Descarta lo que el micrófono captó mientras Jarvis hablaba.
                    time.sleep(0.25)
                except sr.WaitTimeoutError:
                    continue
                except sr.UnknownValueError:
                    print("[Captó sonido pero no logró transcribirlo. Habla más cerca o más fuerte.]")
                    continue
                except sr.RequestError as exc:
                    failures += 1
                    print(f"Servicio de reconocimiento no disponible: {exc}")
                    if failures == 1 or failures % 5 == 0:
                        speak("Perdí la conexión, Samuel. Sigo intentándolo.")
                    time.sleep(min(2 * failures, 10))
    except OSError as exc:
        raise RuntimeError(f"No se pudo acceder al micrófono: {exc}") from exc
