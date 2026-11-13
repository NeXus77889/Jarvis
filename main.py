"""JARVIS command-line and optional voice entry point."""

from __future__ import annotations

import argparse
import logging

from core.brain import JarvisBrain
from core.config import settings
from tools.computer import execute_local_command
from voice.listener import listen_loop
from voice.speech import speak


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="JARVIS personal assistant")
    parser.add_argument("--voice", action="store_true", help="Use microphone and speech output")
    parser.add_argument("--gui", action="store_true", help="Open the desktop HUD")
    parser.add_argument(
        "--core",
        action="store_true",
        help="Voz + núcleo visual 3D en el navegador (implica --voice)",
    )
    parser.add_argument("--no-camera", action="store_true", help="Sin rastreo facial en --core")
    args = parser.parse_args()
    brain = JarvisBrain()
    tracker = None
    if args.core:
        args.voice = True
        from visual.server import start as start_core

        _server, url = start_core()
        print(f"Núcleo visual: {url}  (pulsa F11 en el navegador para pantalla completa)")
        if not args.no_camera:
            from visual.face_tracker import FaceTracker

            tracker = FaceTracker()
            tracker.start()
    if args.gui:
        from interface.hud import run_hud

        raise SystemExit(run_hud(brain))

    if args.voice:
        def approve_local_action(prompt: str) -> bool:
            print(f"\n[Permiso requerido por JARVIS]\n{prompt}")
            answer = input("¿Autorizar? [s/N]: ").strip().lower()
            return answer in {"s", "si", "sí", "y", "yes"}

        brain.tool_approval = approve_local_action

    def handle(message: str, speech_output: bool = False) -> str:
        local_output = speak if speech_output else lambda text: print(f"JARVIS: {text}")
        if execute_local_command(message, local_output):
            return ""
        return brain.ask(message)

    if args.voice:
        try:
            listen_loop(lambda message: handle(message, True), speak)
        finally:
            if tracker is not None:
                tracker.stop()
        return
    print("JARVIS listo. Escriba 'salir' para cerrar.")
    while True:
        try:
            message = input("Tú: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if message.lower() in {"salir", "exit", "quit"}:
            break
        if not message:
            continue
        response = handle(message)
        if response:
            print(f"JARVIS: {response}")


if __name__ == "__main__":
    main()
