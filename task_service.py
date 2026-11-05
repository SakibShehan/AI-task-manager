from pydantic import ValidationError

import db
import vector_store
from schemas import TaskCreate, TaskUpdate, Status, Priority, format_error


class TaskError(Exception):
    """A problem with a clear message that is safe to show in the chat."""


# ---------- Index sync (never breaks a successful SQLite write) ----------

def _sync(task):
    try:
        vector_store.upsert_task(task)
    except Exception as e:
        print(f"[warning] index sync failed for {task['id']}: {e}")


def _unsync(task_id):
    try:
        vector_store.delete_task(task_id)
    except Exception as e:
        print(f"[warning] index delete failed for {task_id}: {e}")


# ---------- CRUD ----------

def create_task(data):
    try:
        clean = TaskCreate(**data).to_db()
    except ValidationError as e:
        raise TaskError(format_error(e))
    task = db.create_task(**clean)
    _sync(task)
    return task


def get_task(task_id):
    task = db.get_task(task_id)
    if not task:
        raise TaskError(f"Task {task_id} was not found")
    return task


def list_tasks(status=None, priority=None):
    if status:
        try:
            status = Status(status).value
        except ValueError:
            raise TaskError("status must be one of Todo, In progress, Done")
    if priority:
        try:
            priority = Priority(priority).value
        except ValueError:
            raise TaskError("priority must be one of Low, Medium, High")
    return db.list_tasks(status=status, priority=priority)


def update_task(task_id, changes):
    get_task(task_id)  # raises TaskError if missing
    try:
        clean = TaskUpdate(**changes).to_db()
    except ValidationError as e:
        raise TaskError(format_error(e))
    if "title" in clean and clean["title"] is None:
        raise TaskError("title: cannot be empty")
    task = db.update_task(task_id, **clean)
    _sync(task)
    return task


def delete_task(task_id):
    task = get_task(task_id)  # raises TaskError if missing
    db.delete_task(task_id)
    _unsync(task_id)
    return task  # returned so the reply can say what was deleted

# ---------- Batch (all-or-nothing) ----------

def apply_batch(operations):
    """Validate every operation, run them in one SQLite transaction, then sync the index.
    Raises TaskError (and changes nothing) if any operation is invalid or fails."""
    clean_ops = []
    for number, op in enumerate(operations, start=1):
        try:
            if op["op"] == "create":
                clean_ops.append({"op": "create", "data": TaskCreate(**op["data"]).to_db()})
            elif op["op"] == "update":
                clean = TaskUpdate(**op["changes"]).to_db()
                if "title" in clean and clean["title"] is None:
                    raise TaskError("title: cannot be empty")
                clean_ops.append({"op": "update", "id": op["id"], "changes": clean})
            elif op["op"] == "delete":
                clean_ops.append({"op": "delete", "id": op["id"]})
            else:
                raise TaskError(f"unknown operation '{op['op']}'")
        except ValidationError as e:
            raise TaskError(f"Operation {number}: {format_error(e)}. Nothing was changed.")

    try:
        results = db.apply_changes(clean_ops)
    except LookupError as e:
        raise TaskError(f"{e} Nothing was changed.")

    # Only after the commit succeeded: update the search index
    for op, task in zip(clean_ops, results):
        if op["op"] == "delete":
            _unsync(task["id"])
        else:
            _sync(task)
    return results
# ---------- Semantic search ----------

def search_tasks(query, n=5, unfinished_only=False, max_distance=None):
    """Return tasks most similar to the query, best first.
    Each task is a real SQLite record plus a 'distance' (lower = closer)."""
    if not query or not query.strip():
        raise TaskError("search text is empty")

    # Ask for extra candidates, because filtering may remove some
    hits = vector_store.search(query, n=n * 3 if unfinished_only else n)

    results = []
    for hit in hits:
        task = db.get_task(hit["id"])
        if not task:
            continue  # stale index entry, skip it
        if unfinished_only and task["status"] == "Done":
            continue
        if max_distance is not None and hit["distance"] > max_distance:
            continue
        task["distance"] = round(hit["distance"], 3)
        results.append(task)
        if len(results) == n:
            break
    return results


# ---------- Rebuild the index from SQLite ----------

def reconcile_index():
    """Make Chroma match SQLite. Returns (upserted, deleted)."""
    tasks = db.list_tasks()
    sqlite_ids = {t["id"] for t in tasks}

    for task in tasks:
        vector_store.upsert_task(task)

    stale = [i for i in vector_store.all_ids() if i not in sqlite_ids]
    for task_id in stale:
        vector_store.delete_task(task_id)

    return len(tasks), len(stale)