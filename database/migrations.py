"""Database schema initialization entry point."""

from database.database import Database


def migrate(database: Database) -> None:
    database.initialize()
