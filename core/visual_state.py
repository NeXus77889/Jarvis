"""Estado compartido entre Jarvis (voz, cámara) y el núcleo visual del navegador."""

from __future__ import annotations

import threading
import time

MODES = {"idle", "listening", "heard", "thinking", "speaking"}

_lock = threading.Lock()
_state: dict[str, object] = {
    "mode": "idle",
    "level": 0.0,
    "face": False,
    "face_x": 0.0,
    "face_y": 0.0,
    "hand_zoom": None,
    "hand_rot": 0.0,
}


def set_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"Modo visual desconocido: {mode}")
    with _lock:
        _state["mode"] = mode
        if mode == "idle":
            _state["level"] = 0.0


def get_mode() -> str:
    with _lock:
        return str(_state["mode"])


def set_level(level: float) -> None:
    with _lock:
        _state["level"] = max(0.0, min(1.0, float(level)))


def set_face(x: float, y: float, present: bool = True) -> None:
    with _lock:
        _state["face"] = bool(present)
        _state["face_x"] = max(-1.0, min(1.0, float(x)))
        _state["face_y"] = max(-1.0, min(1.0, float(y)))


def set_hand(zoom: float | None, rotation: float = 0.0) -> None:
    with _lock:
        _state["hand_zoom"] = None if zoom is None else max(0.0, min(1.0, float(zoom)))
        _state["hand_rot"] = float(rotation)


def snapshot() -> dict[str, object]:
    with _lock:
        data = dict(_state)
    data["t"] = time.time()
    return data
