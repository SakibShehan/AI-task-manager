import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
if not DATA_DIR.is_absolute():
    DATA_DIR = BASE_DIR / DATA_DIR
DB_PATH = DATA_DIR / "tasks.sqlite3"

# Only these columns can be changed through update_task
EDITABLE_FIELDS = {"title", "description", "status", "priority", "due_date"}


@contextmanager
def get_conn():
    """Open a connection, commit on success, rollback on error, always close."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def now():
    return datetime.now().isoformat(timespec="seconds")


def init_db():
    """Create tables if they don't exist yet."""
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                description TEXT,
                status      TEXT NOT NULL DEFAULT 'Todo',
                priority    TEXT NOT NULL DEFAULT 'Medium',
                due_date    TEXT,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS counters (
                name  TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO counters (name, value) VALUES ('task', 0)"
        )


def _next_task_id(conn):
    """Increase the counter and return the next ID like TSK-000001."""
    conn.execute("UPDATE counters SET value = value + 1 WHERE name = 'task'")
    value = conn.execute(
        "SELECT value FROM counters WHERE name = 'task'"
    ).fetchone()["value"]
    return f"TSK-{value:06d}"


def create_task(title, description=None, status="Todo", priority="Medium", due_date=None):
    with get_conn() as conn:
        task_id = _next_task_id(conn)
        timestamp = now()
        conn.execute(
            """
            INSERT INTO tasks
                (id, title, description, status, priority, due_date, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, title, description, status, priority, due_date, timestamp, timestamp),
        )
    return get_task(task_id)


def get_task(task_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def list_tasks(status=None, priority=None):
    query = "SELECT * FROM tasks WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if priority:
        query += " AND priority = ?"
        params.append(priority)
    query += " ORDER BY id"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def update_task(task_id, **changes):
    """Update only allowed fields. Returns the updated task, or None if not found."""
    fields = {k: v for k, v in changes.items() if k in EDITABLE_FIELDS}
    if not fields:
        return get_task(task_id)

    set_clause = ", ".join(f"{col} = ?" for col in fields)
    params = list(fields.values()) + [now(), task_id]
    with get_conn() as conn:
        cursor = conn.execute(
            f"UPDATE tasks SET {set_clause}, updated_at = ? WHERE id = ?", params
        )
        if cursor.rowcount == 0:
            return None
    return get_task(task_id)


def delete_task(task_id):
    """Returns True if a task was deleted, False if it didn't exist."""
    with get_conn() as conn:
        cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cursor.rowcount > 0