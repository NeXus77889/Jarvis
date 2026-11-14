"""Groq provider boundary with bounded transient retries."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from typing import Protocol

from core.config import settings

logger = logging.getLogger(__name__)


class AIProviderError(RuntimeError):
    """A safe, user-presentable provider error."""


class AIProvider(Protocol):
    def chat(self, messages: list[dict[str, str]]) -> str: ...


class GroqAIProvider:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        client: object | None = None,
    ):
        groq_api_key = os.getenv("GROQ_API_KEY") if api_key is None else api_key
        self.api_key = (groq_api_key or "").strip()
        self.model = model or settings.groq_model
        self.max_tokens = max_tokens or settings.max_response_tokens
        self._client = client

    def _get_client(self) -> object:
        if not self.api_key:
            error = (
                "Groq no está configurado. Añada una clave nueva a la variable GROQ_API_KEY "
                "o al archivo .env para habilitar las respuestas de IA."
            )
            logger.error(error)
            raise AIProviderError(error)
        if self._client is None:
            try:
                from groq import Groq
            except ImportError as exc:
                raise AIProviderError("Falta el paquete groq; instale requirements.txt.") from exc
            groq_api_key = self.api_key
            self._client = Groq(api_key=groq_api_key, timeout=30.0, max_retries=0)
        return self._client

    def chat(self, messages: list[dict[str, str]]) -> str:
        client = self._get_client()
        for attempt in range(3):
            try:
                response = client.chat.completions.create(  # type: ignore[attr-defined]
                    model=self.model,
                    messages=messages,
                    max_tokens=self.max_tokens,
                    temperature=0.6,
                )
                content = response.choices[0].message.content
                if not content or not content.strip():
                    raise AIProviderError("Groq devolvió una respuesta vacía.")
                return content.strip()
            except AIProviderError:
                raise
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                transient_connection_error = type(exc).__name__ in {
                    "APIConnectionError",
                    "APITimeoutError",
                    "TimeoutError",
                }
                if status_code in {401, 403}:
                    logger.error("Groq rechazó las credenciales o los permisos (HTTP %s).", status_code)
                    raise AIProviderError(
                        "Groq rechazó la clave o sus permisos. Compruebe GROQ_API_KEY."
                    ) from exc
                if status_code == 404:
                    logger.error("El modelo de Groq configurado no está disponible.")
                    raise AIProviderError(
                        "El modelo configurado no está disponible en Groq. Revise GROQ_MODEL."
                    ) from exc
                if (
                    status_code == 429
                    or (isinstance(status_code, int) and status_code >= 500)
                    or transient_connection_error
                ):
                    if attempt < 2:
                        time.sleep(0.5 * (2**attempt))
                        continue
                logger.error("Falló la solicitud a Groq (%s).", type(exc).__name__)
                raise AIProviderError(
                    "No se pudo completar la solicitud a Groq. Revise la conexión e inténtelo de nuevo."
                ) from exc
        raise AIProviderError("Groq no está disponible tras varios intentos.")

    def stream(self, messages: list[dict[str, str]]) -> Iterator[str]:
        """Yield generated text chunks for future non-blocking voice/UI clients."""
        client = self._get_client()
        try:
            response = client.chat.completions.create(  # type: ignore[attr-defined]
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=0.6,
                stream=True,
            )
            for chunk in response:
                content = chunk.choices[0].delta.content
                if content:
                    yield content
        except Exception as exc:
            logger.error("Falló el streaming de Groq (%s).", type(exc).__name__)
            raise AIProviderError(
                "No se pudo completar la respuesta en streaming de Groq."
            ) from exc

    def chat_with_tools(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> tuple[str | None, list[dict[str, str]]]:
        client = self._get_client()
        response = None
        for attempt in range(3):
            try:
                response = client.chat.completions.create(  # type: ignore[attr-defined]
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    max_tokens=self.max_tokens,
                    temperature=0.2,
                )
                break
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                transient_connection_error = type(exc).__name__ in {
                    "APIConnectionError",
                    "APITimeoutError",
                    "TimeoutError",
                }
                if status_code == 404:
                    logger.error("El modelo de Groq no está disponible para herramientas.")
                    raise AIProviderError(
                        "El modelo configurado no está disponible en Groq. Revise GROQ_MODEL."
                    ) from exc
                if status_code in {401, 403}:
                    logger.error("Groq rechazó las credenciales para la llamada con herramientas.")
                    raise AIProviderError(
                        "Groq rechazó la clave o sus permisos. Compruebe GROQ_API_KEY."
                    ) from exc
                retryable = (
                    status_code == 429
                    or (isinstance(status_code, int) and status_code >= 500)
                    or transient_connection_error
                )
                if retryable and attempt < 2:
                    time.sleep(0.5 * (2**attempt))
                    continue
                logger.error("Falló la solicitud de herramientas a Groq (%s).", type(exc).__name__)
                raise AIProviderError(
                    "No se pudo completar la solicitud con herramientas de Groq."
                ) from exc
        if response is None:
            raise AIProviderError("Groq no está disponible tras varios intentos.")
        message = response.choices[0].message
        calls: list[dict[str, str]] = []
        for item in message.tool_calls or []:
            calls.append(
                {
                    "id": item.id,
                    "name": item.function.name,
                    "arguments": item.function.arguments,
                }
            )
        return message.content, calls
