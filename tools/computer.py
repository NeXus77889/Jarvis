"""Conservative local desktop commands retained from the original assistant."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def execute_local_command(
    command: str,
    speak: Callable[[str], None],
    monitor_detector: Callable[[], list[object]] | None = None,
    hotkey: Callable[..., None] | None = None,
) -> bool:
    text = _normalize(command).strip()
    words = set(re.findall(r"[a-z0-9]+", text))
    if re.search(
        r"(?:^|\b)(?:que hora(?: es)?|hora(?: actual)?|dime la hora|"
        r"que tiempo es|tiempo actual)(?:$|\b)",
        text,
    ):
        import datetime

        speak(f"Son las {datetime.datetime.now().strftime('%H:%M')}, Señor.")
        return True

    if words.intersection({"proyector", "pantalla", "monitor"}):
        if words.intersection({"detecta", "detectar", "cuantas", "cuenta"}):
            if monitor_detector is None:
                from screeninfo import get_monitors

                monitor_detector = get_monitors
            speak(f"Detecto {len(monitor_detector())} pantallas activas en el sistema, Señor.")
            return True
        if words.intersection({"duplicar", "proyectar", "proyecta"}):
            if hotkey is None:
                import pyautogui

                hotkey = pyautogui.hotkey
            hotkey("win", "p")
            speak("Abriendo menú de proyección.")
            return True

    if re.fullmatch(r"(?:jarvis\s+)?(?:agranda|acerca|zoom in)(?:\s+la vista)?", text):
        if hotkey is None:
            import pyautogui

            hotkey = pyautogui.hotkey
        hotkey("ctrl", "+")
        speak("Agrandando vista.")
        return True
    if re.fullmatch(r"(?:jarvis\s+)?(?:achica|aleja|zoom out)(?:\s+la vista)?", text):
        if hotkey is None:
            import pyautogui

            hotkey = pyautogui.hotkey
        hotkey("ctrl", "-")
        speak("Reduciendo vista.")
        return True
    if re.fullmatch(r"(?:jarvis\s+)?(?:normal|restablecer)(?:\s+vista)?", text):
        if hotkey is None:
            import pyautogui

            hotkey = pyautogui.hotkey
        hotkey("ctrl", "0")
        speak("Tamaño restablecido.")
        return True
    return False
