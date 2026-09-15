"""Base de datos SQLite para las clases procesadas."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "data" / "clases.sqlite3"

FIELDS = [
    "title",
    "txt_name",
    "txt_path",
    "txt_hash",
    "profile_id",
    "notebook_id",
    "notebook_source_id",
    "classroom_course_id",
    "classroom_material_id",
    "drive_folder_id",
    "topic_id",
    "status",
    "report_status",
    "infographic_status",
    "video_status",
    "classroom_status",
    "report_path",
    "report_drive_id",
    "infographic_path",
    "infographic_drive_id",
    "video_path",
    "video_drive_id",
    "transcript_path",
    "transcript_drive_id",
    "options",
    "error",
    "updated_at",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS classes(
    class_id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    txt_name TEXT NOT NULL DEFAULT '',
    txt_path TEXT NOT NULL DEFAULT '',
    txt_hash TEXT NOT NULL DEFAULT '',
    profile_id TEXT NOT NULL DEFAULT '',
    notebook_id TEXT NOT NULL DEFAULT '',
    notebook_source_id TEXT NOT NULL DEFAULT '',
    classroom_course_id TEXT NOT NULL DEFAULT '',
    classroom_material_id TEXT NOT NULL DEFAULT '',
    drive_folder_id TEXT NOT NULL DEFAULT '',
    topic_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    report_status TEXT NOT NULL DEFAULT 'pending',
    infographic_status TEXT NOT NULL DEFAULT 'pending',
    video_status TEXT NOT NULL DEFAULT 'pending',
    classroom_status TEXT NOT NULL DEFAULT 'pending',
    research_status TEXT NOT NULL DEFAULT 'pending',
    research_text_url TEXT NOT NULL DEFAULT '',
    research_text_title TEXT NOT NULL DEFAULT '',
    research_video_url TEXT NOT NULL DEFAULT '',
    research_video_title TEXT NOT NULL DEFAULT '',
    pending_status TEXT NOT NULL DEFAULT 'pending',
    pending_text TEXT NOT NULL DEFAULT '',
    report_path TEXT NOT NULL DEFAULT '',
    report_drive_id TEXT NOT NULL DEFAULT '',
    infographic_path TEXT NOT NULL DEFAULT '',
    infographic_drive_id TEXT NOT NULL DEFAULT '',
    video_path TEXT NOT NULL DEFAULT '',
    video_drive_id TEXT NOT NULL DEFAULT '',
    transcript_path TEXT NOT NULL DEFAULT '',
    transcript_drive_id TEXT NOT NULL DEFAULT '',
    options TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);
"""


class Database:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(classes)")}
            for name in (
                "research_status",
                "research_text_url",
                "research_text_title",
                "research_video_url",
                "research_video_title",
                "pending_status",
                "pending_text",
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE classes ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def upsert_class(self, class_id: str, **values) -> None:
        now = time.time()
        with self.connect() as db:
            exists = db.execute(
                "SELECT 1 FROM classes WHERE class_id=?", (class_id,)
            ).fetchone()
            if not exists:
                db.execute(
                    "INSERT INTO classes(class_id, created_at, updated_at) VALUES(?,?,?)",
                    (class_id, now, now),
                )
            values["updated_at"] = now
            assignments = ", ".join(f"{field}=?" for field in values)
            db.execute(
                f"UPDATE classes SET {assignments} WHERE class_id=?",
                (*values.values(), class_id),
            )

    def update_class(self, class_id: str, **values) -> None:
        if not values:
            return
        values["updated_at"] = time.time()
        assignments = ", ".join(f"{field}=?" for field in values)
        with self.connect() as db:
            db.execute(
                f"UPDATE classes SET {assignments} WHERE class_id=?",
                (*values.values(), class_id),
            )

    def get_class(self, class_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM classes WHERE class_id=?", (class_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_classes(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM classes ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def reset_incomplete(self) -> list[str]:
        """Marca como pendientes las clases interrumpidas y devuelve sus ids."""
        pending_states = ("queued", "running", "uploading", "generating", "researching", "analyzing")
        with self.connect() as db:
            rows = db.execute(
                "SELECT class_id FROM classes WHERE status IN (?,?,?,?,?,?)",
                pending_states,
            ).fetchall()
            db.execute(
                "UPDATE classes SET status='pending', error='Se reanuda tras reinicio' "
                "WHERE status IN (?,?,?,?,?,?)",
                pending_states,
            )
        return [row["class_id"] for row in rows]
