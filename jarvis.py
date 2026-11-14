"""Compatibility entry points for the original JARVIS script."""

from core.brain import JarvisBrain
from tools.computer import execute_local_command
from voice.listener import listen_loop
from voice.speech import speak

_brain: JarvisBrain | None = None


def _get_brain() -> JarvisBrain:
    global _brain
    if _brain is None:
        _brain = JarvisBrain()
    return _brain


def hablar(texto: str) -> None:
    speak(texto)


def ejecutar_comando_local(comando: str) -> bool:
    return execute_local_command(comando, hablar)


def consultar_ia(prompt_usuario: str) -> str:
    respuesta = _get_brain().ask(prompt_usuario)
    hablar(respuesta)
    return respuesta


def iniciar_jarvis() -> None:
    def handle(texto: str) -> str:
        if ejecutar_comando_local(texto):
            return ""
        return _get_brain().ask(texto)

    listen_loop(handle, hablar)


if __name__ == "__main__":
    iniciar_jarvis()
