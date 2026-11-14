"""Speech synthesis and playback kept independent of the conversation brain.

Mejoras de latencia: el mixer de pygame se inicia una sola vez, el texto se divide en
frases y mientras se reproduce una frase ya se está generando la siguiente.
"""

from __future__ import annotations

import math
import os
import random
import re
import tempfile
import threading
import wave
from array import array
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core import visual_state

RUTA_MODELO = Path("voice") / "es_ES-davefx-medium.onnx"

_stop_event = threading.Event()
_mixer_lock = threading.Lock()
_voice_lock = threading.Lock()
_piper_voice = None
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="jarvis-tts")
_SENTENCE = re.compile(r"(?<=[.!?…])\s+|\n+")


def _get_piper_voice():
    """Carga el modelo .onnx una sola vez en memoria para evitar latencia."""
    global _piper_voice
    if _piper_voice is None:
        with _voice_lock:
            if _piper_voice is None:
                from piper.voice import PiperVoice

                model_path = Path(os.getenv("JARVIS_VOICE", str(RUTA_MODELO)))
                if not model_path.exists():
                    raise FileNotFoundError(f"No se encontró el modelo en: {model_path.resolve()}")
                _piper_voice = PiperVoice.load(str(model_path))
    return _piper_voice


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
    """Genera un WAV temporal usando Piper TTS local."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        path = Path(handle.name)

    try:
        voice = _get_piper_voice()
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(voice.config.sample_rate)
            
            if hasattr(voice, "synthesize_wav"):
                voice.synthesize_wav(sentence, wav_file)
            else:
                voice.synthesize(sentence, wav_file)

        if path.stat().st_size > 44:  # Más que solo la cabecera WAV vacía
            return path
    except Exception as exc:
        print(f"No se pudo generar la voz con Piper: {type(exc).__name__}: {exc}")

    path.unlink(missing_ok=True)
    return None


def _envelope(path: Path) -> list[float] | None:
    """Volumen de la voz cada 50 ms (0..1), para que el núcleo visual lata con ella."""
    try:
        import pygame

        sound = pygame.mixer.Sound(str(path))
        init = pygame.mixer.get_init()
        if not init or abs(init[1]) != 16:
            return None
        freq, _fmt, channels = init
        raw = sound.get_raw()
        samples = array("h")
        samples.frombytes(raw[: len(raw) - len(raw) % 2])
        window = max(1, int(freq * channels * 0.05))
        values: list[float] = []
        for start in range(0, len(samples), window):
            chunk = samples[start : start + window : 4]
            if chunk:
                values.append(math.sqrt(sum(v * v for v in chunk) / len(chunk)))
        peak = max(values, default=0.0)
        if peak <= 0:
            return None
        return [min(1.0, (v / (peak * 0.85)) ** 0.8) for v in values]
    except Exception:
        return None


def _prepare(sentence: str) -> tuple[Path | None, list[float] | None]:
    path = _synthesize(sentence)
    return (path, _envelope(path) if path is not None else None)


def _ensure_mixer() -> bool:
    import pygame

    with _mixer_lock:
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        return bool(pygame.mixer.get_init())


def _play(path: Path, envelope: list[float] | None = None) -> None:
    import pygame

    pygame.mixer.music.load(str(path))
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy() and not _stop_event.is_set():
        position = pygame.mixer.music.get_pos()
        if envelope and position >= 0:
            level = envelope[min(len(envelope) - 1, position // 50)]
        else:  # sin análisis de audio: movimiento sintético parecido al habla
            level = 0.25 + 0.5 * random.random()
        visual_state.set_level(level)
        pygame.time.wait(25)
    visual_state.set_level(0.0)
    pygame.mixer.music.stop()
    pygame.mixer.music.unload()


def speak(text: str) -> None:
    print(f"\n[JARVIS]: {text}")
    try:
        import piper  # noqa: F401
        import pygame  # noqa: F401
    except ImportError as exc:
        print(f"Voz no disponible: falta una dependencia ({exc.name}).")
        return

    sentences = split_sentences(text)
    if not sentences:
        return
    _stop_event.clear()
    visual_state.set_mode("speaking")
    try:
        if not _ensure_mixer():
            print("No se pudo iniciar el audio.")
            return
        future = _executor.submit(_prepare, sentences[0])
        for index in range(len(sentences)):
            if _stop_event.is_set():
                break
            audio_path, envelope = future.result()
            # Empieza a generar la siguiente frase mientras suena esta.
            if index + 1 < len(sentences):
                future = _executor.submit(_prepare, sentences[index + 1])
            if audio_path is None:
                continue
            try:
                _play(audio_path, envelope)
            finally:
                audio_path.unlink(missing_ok=True)
        else:
            return
        # Si se interrumpió, descarta la generación pendiente.
        if not future.cancel():
            leftover, _ = future.result()
            if leftover is not None:
                leftover.unlink(missing_ok=True)
    except Exception as exc:
        print(f"No se pudo reproducir la voz: {type(exc).__name__}: {exc}")
    finally:
        visual_state.set_level(0.0)
        visual_state.set_mode("idle")


def stop_speaking() -> None:
    _stop_event.set()
    try:
        import pygame
    except ImportError:
        return
    if pygame.mixer.get_init():
        pygame.mixer.music.stop()