"""Transactional segment state without rewriting a whole book on every segment."""
import json
import sqlite3
from pathlib import Path

from .util import now


class State:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS segments (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        self.db.commit()

    def get(self, key: str, table: str = "segments") -> dict | None:
        if table not in {"segments", "artifacts"}:
            raise ValueError("Invalid table")
        result = self.db.execute(f"SELECT data FROM {table} WHERE id=?", (key,)).fetchone()
        return json.loads(result[0]) if result else None

    def save(self, key: str, data: dict, table: str = "segments"):
        if table not in {"segments", "artifacts"}:
            raise ValueError("Invalid table")
        data = {**data, "updated_at": now()}
        with self.db:
            self.db.execute(f"INSERT OR REPLACE INTO {table}(id,data) VALUES (?,?)",
                            (key, json.dumps(data, ensure_ascii=False, allow_nan=False)))

    def close(self):
        self.db.close()
