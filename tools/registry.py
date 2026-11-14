"""Allowlisted, bounded local tools. No shell or model-provided commands."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import webbrowser
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from database.database import SECRET_PATTERN
from database.project_database import ReadOnlyProjectDatabase

READABLE_SUFFIXES = {".py", ".md", ".txt", ".json", ".toml", ".ini", ".yaml", ".yml"}
MAX_READ_BYTES = 128_000
APPLICATIONS = {
    "notepad": ("notepad.exe",),
    "calculator": ("calc.exe",),
}
TOOL_DEFINITIONS: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": "system_overview",
            "description": "Get CPU, memory, operating system, and project disk status.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List non-hidden files in a directory under the configured workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search allowed text files under the configured workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 3},
                    "path": {"type": "string", "default": "."},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a small, non-secret text file under the configured workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_website",
            "description": "Open a user-requested HTTPS website after asking for confirmation.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch_application",
            "description": "Open Notepad or Calculator after asking for confirmation.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "enum": ["notepad", "calculator"]}},
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "project_database",
            "description": (
                "Read data from a configured SQLite project database and explicitly allowlisted tables. "
                "This reads data only and requires user approval."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list_tables", "describe_table", "select_rows"],
                    },
                    "table": {"type": "string"},
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "where_column": {"type": "string"},
                    "equals": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    },
]


class ToolRegistry:
    def __init__(
        self,
        workspace: str | Path,
        project_database_path: str | Path | None = None,
        project_database_tables: tuple[str, ...] = (),
    ):
        self.workspace = Path(workspace).resolve()
        self.project_database = ReadOnlyProjectDatabase(
            project_database_path,
            project_database_tables,
        )

    @staticmethod
    def definitions() -> list[dict[str, object]]:
        return TOOL_DEFINITIONS

    def system_overview(self) -> dict[str, object]:
        import psutil

        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(str(self.workspace))
        return {
            "platform": platform.platform(),
            "processor": platform.processor() or platform.machine(),
            "logical_cpus": os.cpu_count(),
            "memory_total_bytes": memory.total,
            "memory_available_bytes": memory.available,
            "memory_percent_used": memory.percent,
            "workspace_disk_free_bytes": disk.free,
        }

    def list_files(self, relative_path: str = ".") -> list[dict[str, object]]:
        directory = self._resolve(relative_path)
        if not directory.is_dir():
            raise ValueError("La ruta no es una carpeta del workspace.")
        result: list[dict[str, object]] = []
        for path in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if path.name.startswith(".") or path.name in {"__pycache__", ".venv"}:
                continue
            result.append(
                {
                    "name": path.name,
                    "is_directory": path.is_dir(),
                    "size_bytes": path.stat().st_size if path.is_file() else None,
                }
            )
        return result

    def read_file(self, relative_path: str) -> str:
        path = self._resolve(relative_path)
        if path.suffix.lower() not in READABLE_SUFFIXES or path.name.lower() in {
            ".env",
            "secrets.json",
        }:
            raise ValueError("Solo se pueden leer archivos de texto permitidos, nunca secretos.")
        if not path.is_file() or path.stat().st_size > MAX_READ_BYTES:
            raise ValueError("El archivo no existe o supera el límite de lectura.")
        content = path.read_text(encoding="utf-8")
        if SECRET_PATTERN.search(content):
            raise ValueError("El archivo contiene un posible secreto y no se mostrará.")
        return content

    def search_files(self, query: str, relative_path: str = ".") -> list[str]:
        query = query.strip().lower()
        if len(query) < 3:
            raise ValueError("La búsqueda debe contener al menos tres caracteres.")
        directory = self._resolve(relative_path)
        matches: list[str] = []
        for path in directory.rglob("*"):
            if len(matches) >= 50:
                break
            if (
                not path.is_file()
                or path.suffix.lower() not in READABLE_SUFFIXES
                or path.name.startswith(".")
                or ".venv" in path.parts
                or "__pycache__" in path.parts
                or path.stat().st_size > MAX_READ_BYTES
            ):
                continue
            try:
                if not self._resolve(str(path.relative_to(self.workspace))).is_file():
                    continue
                if query in path.read_text(encoding="utf-8").lower():
                    matches.append(str(path.relative_to(self.workspace)))
            except (UnicodeDecodeError, ValueError):
                continue
        return matches

    @staticmethod
    def open_website(url: str, approve: Callable[[str], bool]) -> bool:
        parsed = urlparse(url.strip())
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Solo se permiten direcciones HTTPS válidas.")
        if not approve(f"¿Abrir esta página HTTPS en el navegador?\n{url}"):
            return False
        webbrowser.open(url, new=2)
        return True

    @staticmethod
    def launch_application(name: str, approve: Callable[[str], bool]) -> bool:
        normalized = name.strip().lower()
        normalized = {"calculadora": "calculator", "bloc de notas": "notepad"}.get(
            normalized,
            normalized,
        )
        command = APPLICATIONS.get(normalized)
        if command is None:
            raise ValueError("Aplicación no permitida. Opciones: Notepad o Calculator.")
        if platform.system().lower() != "windows":
            raise RuntimeError("El lanzamiento de estas aplicaciones está disponible en Windows.")
        if not approve(f"¿Abrir {normalized}?"):
            return False
        subprocess.Popen(command, shell=False)
        return True

    def _resolve(self, relative_path: str) -> Path:
        candidate = (self.workspace / relative_path).resolve()
        if candidate != self.workspace and self.workspace not in candidate.parents:
            raise ValueError("La ruta solicitada queda fuera del workspace.")
        return candidate

    def execute(
        self,
        name: str,
        arguments: dict[str, object],
        approve: Callable[[str], bool] | None = None,
    ) -> str:
        if name == "system_overview":
            return "\n".join(f"{key}: {value}" for key, value in self.system_overview().items())
        if name == "list_files":
            path = str(arguments.get("path", "."))
            return "\n".join(
                item["name"] + ("\\" if item["is_directory"] else "")
                for item in self.list_files(path)
            ) or "La carpeta está vacía."
        if name == "search_files":
            query = str(arguments.get("query", ""))
            path = str(arguments.get("path", "."))
            return "\n".join(self.search_files(query, path)) or "Sin coincidencias."
        if name == "read_file":
            return self.read_file(str(arguments.get("path", "")))
        if name == "project_database":
            if approve is None:
                return "La consulta de datos requiere aprobación explícita en el HUD."
            action = str(arguments.get("action", ""))
            if action == "list_tables":
                prompt = "¿Autoriza consultar los nombres de las tablas permitidas del proyecto?"
            elif action == "describe_table":
                table = str(arguments.get("table", ""))
                prompt = f"¿Autoriza leer el esquema de la tabla permitida «{table}»?"
            elif action == "select_rows":
                table = str(arguments.get("table", ""))
                prompt = f"¿Autoriza leer filas de la tabla permitida «{table}»?"
            else:
                raise ValueError("Acción de base de datos no permitida.")
            if not approve(prompt):
                return "Consulta cancelada por el usuario."
            if action == "list_tables":
                result: object = self.project_database.list_tables()
            elif action == "describe_table":
                result = self.project_database.describe_table(str(arguments.get("table", "")))
            else:
                columns = arguments.get("columns")
                if columns is not None and (
                    not isinstance(columns, list)
                    or any(not isinstance(column, str) for column in columns)
                ):
                    raise ValueError("La selección contiene una columna no válida.")
                result = self.project_database.select_rows(
                    str(arguments.get("table", "")),
                    columns=columns,
                    where_column=str(arguments["where_column"])
                    if arguments.get("where_column") is not None
                    else None,
                    equals=str(arguments["equals"])
                    if arguments.get("equals") is not None
                    else None,
                    limit=int(arguments.get("limit", 20)),
                )
            return json.dumps(result, ensure_ascii=False, default=str)
        if name in {"open_website", "launch_application"}:
            if approve is None:
                return "Esta acción requiere aprobación explícita en el HUD."
            if name == "open_website":
                approved = self.open_website(str(arguments.get("url", "")), approve)
            else:
                approved = self.launch_application(str(arguments.get("name", "")), approve)
            return "Acción ejecutada." if approved else "Acción cancelada por el usuario."
        raise ValueError(f"Herramienta no permitida: {name}")
