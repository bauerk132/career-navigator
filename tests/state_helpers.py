"""Test helpers: read the saved events straight from career-navigator.db."""
import json
import sqlite3
from pathlib import Path

DB_FILENAME = "career-navigator.db"


def db_exists(state_dir) -> bool:
    return (Path(state_dir) / DB_FILENAME).exists()


def read_events(state_dir, name: str) -> list[dict]:
    """Every record of one log in seq order; [] when there is no database."""
    path = Path(state_dir) / DB_FILENAME
    if not path.exists():
        return []
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute("SELECT record FROM events WHERE log = ? ORDER BY seq", (name,)).fetchall()
    finally:
        conn.close()
    return [json.loads(text) for (text,) in rows]
