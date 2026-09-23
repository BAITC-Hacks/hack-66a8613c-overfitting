"""SQLite persistence models defined in schema.sql, using the standard library."""
import sqlite3
from contextlib import closing
from pathlib import Path


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute('PRAGMA foreign_keys = ON')
        connection.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
