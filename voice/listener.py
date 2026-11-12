"""Speech recognition loop; loaded only when voice mode is requested."""

from __future__ import annotations

from collections.abc import Callable


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
    speak("Protocolo JARVIS en línea. Sistemas operativos, Señor.")
    while True:
        try:
            with sr.Microphone() as source:
                print("\n[JARVIS escuchando...]")
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
                audio = recognizer.listen(source, timeout=1, phrase_time_limit=8)
            text = recognizer.recognize_google(audio, language="es-EC").lower()
            print(f"[Tú]: {text}")
            if any(
                phrase in text
                for phrase in ("apágate", "apag ate", "desconéctate", "adiós jarvis")
            ):
                speak("Desconectando sistemas. Que tenga un excelente día, Señor.")
                return
            response = handle_message(text)
            if response:
                speak(response)
        except sr.WaitTimeoutError:
            continue
        except sr.UnknownValueError:
            continue
        except sr.RequestError as exc:
            print(f"Servicio de reconocimiento no disponible: {exc}")
        except OSError as exc:
            raise RuntimeError(f"No se pudo acceder al micrófono: {exc}") from exc
