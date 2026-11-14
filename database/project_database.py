"""Read-only, allowlisted SQLite access to an explicitly configured project database."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from urllib.parse import quote

from database.database import SECRET_PATTERN

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
MAX_ROWS = 50
MAX_RESULT_CHARS = 20_000
SECRET_COLUMN_PATTERN = re.compile(
    r"(?i)(?:password|passwd|secret|token|api[_-]?key|credential|private[_-]?key)"
)


class ReadOnlyProjectDatabase:
    def __init__(self, path: str | Path | None, allowed_tables: tuple[str, ...] = ()):
        configured = str(path) if path else ""
        self._sqlalchemy_url = (
            configured
            if configured.startswith(("mysql://", "mysql+", "postgresql://", "postgresql+"))
            else None
        )
        self.path = (
            Path(path).expanduser().resolve()
            if path and self._sqlalchemy_url is None
            else None
        )
        self.allowed_tables = frozenset(allowed_tables)
        self._engine = None
        self._engine_lock = threading.Lock()
        if any(not IDENTIFIER_PATTERN.fullmatch(table) for table in self.allowed_tables):
            raise ValueError("La lista de tablas contiene un identificador no válido.")

    def _get_engine(self):
        if self._sqlalchemy_url is None:
            raise RuntimeError("No hay una URL MySQL/PostgreSQL configurada.")
        if self._engine is None:
            with self._engine_lock:
                if self._engine is None:
                    try:
                        from sqlalchemy import create_engine
                    except ImportError as exc:
                        raise RuntimeError(
                            "Instale SQLAlchemy y el driver del motor configurado."
                        ) from exc
                    self._engine = create_engine(
                        self._sqlalchemy_url,
                        pool_pre_ping=True,
                        pool_size=1,
                        max_overflow=1,
                        pool_recycle=300,
                    )
        return self._engine

    def _connect(self) -> sqlite3.Connection:
        if self.path is None:
            raise RuntimeError("Configure JARVIS_PROJECT_DB_PATH para habilitar esta integración.")
        if not self.path.is_file():
            raise FileNotFoundError("No se encontró la base de datos de proyecto configurada.")
        uri_path = quote(self.path.as_posix(), safe="/:")
        connection = sqlite3.connect(
            f"file:{uri_path}?mode=ro",
            uri=True,
            timeout=5,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def list_tables(self) -> list[str]:
        if self._sqlalchemy_url:
            from sqlalchemy import inspect

            names = inspect(self._get_engine()).get_table_names()
            return sorted(set(names).intersection(self.allowed_tables))
        with self._connection() as connection:
            names = {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            }
        return sorted(names.intersection(self.allowed_tables))

    def describe_table(self, table: str) -> list[dict[str, object]]:
        self._validate_table(table)
        if self._sqlalchemy_url:
            from sqlalchemy import inspect

            return [
                {
                    "name": str(column["name"]),
                    "type": str(column["type"]),
                    "not_null": not bool(column.get("nullable", True)),
                }
                for column in inspect(self._get_engine()).get_columns(table)
            ]
        escaped = self._quote_identifier(table)
        with self._connection() as connection:
            rows = connection.execute(f"PRAGMA table_info({escaped})").fetchall()
        return [
            {
                "name": str(row["name"]),
                "type": str(row["type"]),
                "not_null": bool(row["notnull"]),
            }
            for row in rows
        ]

    def select_rows(
        self,
        table: str,
        columns: list[str] | None = None,
        where_column: str | None = None,
        equals: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        self._validate_table(table)
        available_columns = self.describe_table(table)
        available = {str(column["name"]) for column in available_columns}
        if columns is not None and (
            not isinstance(columns, list)
            or any(not isinstance(column, str) for column in columns)
        ):
            raise ValueError("La selección contiene una columna no válida.")
        selected = columns or sorted(available)
        if not selected or any(column not in available for column in selected):
            raise ValueError("La selección contiene una columna no permitida.")
        secret_columns = {
            str(column["name"])
            for column in available_columns
            if SECRET_COLUMN_PATTERN.search(str(column["name"]))
        }
        if any(column in secret_columns for column in selected):
            raise ValueError("La consulta contiene columnas sensibles no permitidas.")
        if where_column is not None and where_column not in available:
            raise ValueError("El filtro contiene una columna no permitida.")
        bounded_limit = max(1, min(int(limit), MAX_ROWS))
        if self._sqlalchemy_url:
            from sqlalchemy import text

            connection = self._get_engine().connect()
            transaction = connection.begin()
            try:
                preparer = connection.dialect.identifier_preparer
                projection = ", ".join(preparer.quote(column) for column in selected)
                query = (
                    f"SELECT {projection} FROM {preparer.quote(table)}"
                )
                parameters: dict[str, object] = {"limit": bounded_limit}
                if where_column is not None:
                    query += f" WHERE {preparer.quote(where_column)} = :equals"
                    parameters["equals"] = equals or ""
                query += " LIMIT :limit"
                result = connection.execute(text(query), parameters).mappings().all()
                rows = [dict(row) for row in result]
            finally:
                transaction.rollback()
                connection.close()
            return self._validate_result(rows)

        projection = ", ".join(self._quote_identifier(column) for column in selected)
        query = f"SELECT {projection} FROM {self._quote_identifier(table)}"
        parameters: tuple[object, ...] = ()
        if where_column is not None:
            query += f" WHERE {self._quote_identifier(where_column)} = ?"
            parameters = (equals or "",)
        query += " LIMIT ?"
        parameters += (bounded_limit,)
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        result = [dict(row) for row in rows]
        return self._validate_result(result)

    @staticmethod
    def _validate_result(result: list[dict[str, object]]) -> list[dict[str, object]]:
        encoded = json.dumps(result, ensure_ascii=False, default=str)
        if len(encoded) > MAX_RESULT_CHARS:
            raise ValueError("El resultado excede el límite seguro de tamaño.")
        if SECRET_PATTERN.search(encoded):
            raise ValueError("El resultado contiene campos posiblemente sensibles.")
        return result

    def _validate_table(self, table: str) -> None:
        if table not in self.allowed_tables:
            raise ValueError("La tabla no está incluida en JARVIS_PROJECT_DB_TABLES.")
        if table not in self.list_tables():
            raise ValueError("La tabla permitida no existe en la base de datos configurada.")

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        if not IDENTIFIER_PATTERN.fullmatch(identifier):
            raise ValueError("Identificador SQL no válido.")
        return f'"{identifier}"'
