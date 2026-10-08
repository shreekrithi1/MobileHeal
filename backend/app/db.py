"""SQLite persistence. Standard attributes are columns; everything else lives in a JSON
`extra` column, so new requirements need no migrations (FR-1.3).
All writes go through a single lock + transaction so mobile saves and background
reconciliation never interleave (NFR data consistency)."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

STANDARD = ("name", "phone_number", "email")


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT, phone_number TEXT, email TEXT,
                extra TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )

    # ---- helpers -------------------------------------------------------
    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = {k: row[k] for k in ("id", "name", "phone_number", "email", "updated_at")}
        d.update(json.loads(row["extra"] or "{}"))
        return d

    @staticmethod
    def _split(data: dict) -> tuple[dict, dict]:
        std = {k: data[k] for k in STANDARD if k in data}
        extra = {k: v for k, v in data.items() if k not in STANDARD and k not in ("id", "updated_at")}
        return std, extra

    # ---- CRUD ----------------------------------------------------------
    def list_profiles(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM profiles ORDER BY id").fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_profile(self, pid: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM profiles WHERE id=?", (pid,)).fetchone()
        return self._row_to_dict(row) if row else None

    def create_profile(self, data: dict) -> dict:
        std, extra = self._split(data)
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO profiles(name, phone_number, email, extra) VALUES (?,?,?,?)",
                (std.get("name"), std.get("phone_number"), std.get("email"), json.dumps(extra)),
            )
            return self.get_profile(cur.lastrowid)

    def update_profile(self, pid: int, data: dict) -> dict | None:
        """Atomic partial update (read-modify-write inside one transaction)."""
        std, extra = self._split(data)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute("SELECT extra FROM profiles WHERE id=?", (pid,)).fetchone()
                if row is None:
                    self._conn.execute("ROLLBACK")
                    return None
                merged = json.loads(row["extra"] or "{}")
                merged.update(extra)
                sets = [f"{k}=?" for k in std] + ["extra=?", "updated_at=datetime('now')"]
                self._conn.execute(
                    f"UPDATE profiles SET {', '.join(sets)} WHERE id=?",
                    (*std.values(), json.dumps(merged), pid),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            return self.get_profile(pid)

    def delete_profile(self, pid: int) -> bool:
        with self._lock:
            return self._conn.execute("DELETE FROM profiles WHERE id=?", (pid,)).rowcount > 0

    # ---- settings (persistent agent toggle) ----------------------------
    def get_setting(self, key: str, default: str) -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
