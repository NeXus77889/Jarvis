"""Speech synthesis and playback kept independent of the conversation brain.

Mejoras de latencia: el mixer de pygame se inicia una sola vez, el texto se divide en
frases y mientras se reproduce una frase ya se está generando la siguiente.
"""

from __future__ import annotations

import asyncio
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import os

# Se pueden cambiar desde el .env sin tocar el código:
#   JARVIS_VOICE=es-EC-LuisNeural   JARVIS_VOICE_RATE=+4%   JARVIS_VOICE_PITCH=-3Hz
DEFAULT_VOICE = "es-EC-LuisNeural"  # acento ecuatoriano, más natural para tu público
FALLBACK_VOICES = ("es-MX-JorgeNeural", "es-ES-AlvaroNeural")

_stop_event = threading.Event()
_mixer_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="jarvis-tts")
_SENTENCE = re.compile(r"(?<=[.!?…])\s+|\n+")


def _clean(text: str) -> str:
    text = re.sub(r"https?://\S+", "enlace", text)
    text = re.sub(r"[*_`#>~|]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str, max_chars: int = 220) -> list[str]:
    parts = [p.strip() for p in _SENTENCE.split(_clean(text)) if p.strip()]
    chunks: list[str] = []
    for part in parts:
        # Une frases muy cortas para no generar demasiadas peticiones.
        if chunks and len(chunks[-1]) < 40 and len(chunks[-1]) + len(part) < max_chars:
            chunks[-1] = f"{chunks[-1]} {part}"
        else:
            chunks.append(part)
    return chunks


def _synthesize(sentence: str) -> Path | None:
    """Genera un MP3 temporal. Devuelve None si no hay red o falla."""
    import edge_tts

    rate = os.getenv("JARVIS_VOICE_RATE", "+3%")
    pitch = os.getenv("JARVIS_VOICE_PITCH", "-2Hz")
    voices = (os.getenv("JARVIS_VOICE", DEFAULT_VOICE), *FALLBACK_VOICES)

    async def generate(voice: str, target: str) -> None:
        await edge_tts.Communicate(sentence, voice, rate=rate, pitch=pitch).save(target)

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
        path = Path(handle.name)
    last_error: Exception | None = None
    for voice in voices:
        try:
            asyncio.run(generate(voice, str(path)))
            if path.stat().st_size > 0:
                return path
        except Exception as exc:  # voz inexistente o sin red: prueba la siguiente
            last_error = exc
    path.unlink(missing_ok=True)
    print(f"No se pudo generar la voz: {type(last_error).__name__}: {last_error}")
    return None


def _ensure_mixer() -> bool:
    import pygame

    with _mixer_lock:
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        return bool(pygame.mixer.get_init())


def _play(path: Path) -> None:
    import pygame

    pygame.mixer.music.load(str(path))
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy() and not _stop_event.is_set():
        pygame.time.wait(40)
    pygame.mixer.music.stop()
    pygame.mixer.music.unload()


def speak(text: str) -> None:
    print(f"\n[JARVIS]: {text}")
    try:
        import edge_tts  # noqa: F401
        import pygame  # noqa: F401
    except ImportError as exc:
        print(f"Voz no disponible: falta una dependencia ({exc.name}).")
        return

    sentences = split_sentences(text)
    if not sentences:
        return
    _stop_event.clear()
    try:
        if not _ensure_mixer():
            print("No se pudo iniciar el audio.")
            return
        future = _executor.submit(_synthesize, sentences[0])
        for index in range(len(sentences)):
            if _stop_event.is_set():
                break
            audio_path = future.result()
            # Empieza a generar la siguiente frase mientras suena esta.
            if index + 1 < len(sentences):
                future = _executor.submit(_synthesize, sentences[index + 1])
            if audio_path is None:
                continue
            try:
                _play(audio_path)
            finally:
                audio_path.unlink(missing_ok=True)
        else:
            return
        # Si se interrumpió, descarta la generación pendiente.
        if not future.cancel():
            leftover = future.result()
            if leftover is not None:
                leftover.unlink(missing_ok=True)
    except Exception as exc:
        print(f"No se pudo reproducir la voz: {type(exc).__name__}: {exc}")


def stop_speaking() -> None:
    _stop_event.set()
    try:
        import pygame
    except ImportError:
        return
    if pygame.mixer.get_init():
        pygame.mixer.music.stop()
