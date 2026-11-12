"""Speech synthesis and playback kept independent of the conversation brain."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path


def speak(text: str) -> None:
    print(f"\n[JARVIS]: {text}")
    try:
        import edge_tts
        import pygame
    except ImportError as exc:
        print(f"Voz no disponible: falta una dependencia ({exc.name}).")
        return

    async def generate(target: str) -> None:
        await edge_tts.Communicate(text, "es-ES-AlvaroNeural").save(target)

    audio_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as audio_file:
            audio_path = Path(audio_file.name)
        asyncio.run(generate(str(audio_path)))
        if pygame.mixer.get_init():
            pygame.mixer.quit()
        pygame.mixer.init()
        pygame.mixer.music.load(str(audio_path))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)
        pygame.mixer.music.stop()
        pygame.mixer.music.unload()
    except Exception as exc:
        print(f"No se pudo reproducir la voz: {type(exc).__name__}: {exc}")
    finally:
        if audio_path is not None:
            audio_path.unlink(missing_ok=True)


def stop_speaking() -> None:
    try:
        import pygame
    except ImportError:
        return
    if pygame.mixer.get_init():
        pygame.mixer.music.stop()
