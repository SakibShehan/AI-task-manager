import re

import task_service
from agent import ActionPlan
from pydantic import ValidationError
from schemas import (
    TaskCreate,
    TaskUpdate,
    _normalize_priority,
    _normalize_status,
    format_error,
)
from task_service import TaskError

ID_PATTERN = re.compile(r"^TSK-\d{1,9}$", re.IGNORECASE)
MAX_LISTED = 15


# ---------- Helpers ----------

def describe(task):
    due = task["due_date"] or "no due date"
    return f"{task['id']}: {task['title']} ({task['priority']}, {task['status']}, {due})"


def resolve_target(target):
    """Find exactly one real task. Commit 10 replaces the fallback with semantic search."""
    text = target.strip()
    if ID_PATTERN.match(text):
        return task_service.get_task(text.upper())  # raises TaskError if missing

    matches = [t for t in task_service.list_tasks() if t["title"].strip().lower() == text.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        ids = ", ".join(t["id"] for t in matches)
        raise TaskError(f"More than one task is titled '{target}' ({ids}). Please use an ID.")
    raise TaskError(
        f"I couldn't find a task called '{target}'. "
        "Use its ID (like TSK-000001) or its exact title."
    )


def _find_filters(action):
    """Clean the status/priority filters of a find_tasks action."""
    status = _normalize_status(action.status) if action.status else None
    priority = _normalize_priority(action.priority) if action.priority else None
    # list_tasks validates the values and raises TaskError if they are wrong
    task_service.list_tasks(status=status, priority=priority)
    return status, priority


# ---------- Step 1: validate everything, change nothing ----------

def prepare(plan):
    prepared = []
    for number, action in enumerate(plan.actions, start=1):
        label = f"Action {number} ({action.type})"
        try:
            if action.type == "create_task":
                TaskCreate(**action.data)
                prepared.append((action, None))
            elif action.type == "update_task":
                task = resolve_target(action.target)
                TaskUpdate(**action.data)
                prepared.append((action, task))
            elif action.type in ("complete_task", "delete_task"):
                prepared.append((action, resolve_target(action.target)))
            else:  # find_tasks
                _find_filters(action)
                prepared.append((action, None))
        except ValidationError as e:
            raise TaskError(f"{label}: {format_error(e)}. Nothing was changed.")
        except TaskError as e:
            raise TaskError(f"{label}: {e} Nothing was changed.")
    return prepared


# ---------- Step 2: run the validated actions ----------

def _run_find(action):
    status, priority = _find_filters(action)

    if action.query:
        found = task_service.search_tasks(action.query, n=10, unfinished_only=action.unfinished_only)
        if status:
            found = [t for t in found if t["status"] == status]
        if priority:
            found = [t for t in found if t["priority"] == priority]
    else:
        found = task_service.list_tasks(status=status, priority=priority)
        if action.unfinished_only:
            found = [t for t in found if t["status"] != "Done"]

    if not found:
        return "No matching tasks found."
    shown = found[:MAX_LISTED]
    text = f"Found {len(found)} task(s):\n" + "\n".join("- " + describe(t) for t in shown)
    if len(found) > MAX_LISTED:
        text += f"\n...and {len(found) - MAX_LISTED} more."
    return text


def _run_one(action, task):
    if action.type == "create_task":
        created = task_service.create_task(action.data)
        return f"Created {describe(created)}", True

    if action.type == "update_task":
        updated = task_service.update_task(task["id"], action.data)
        return f"Updated {describe(updated)}", True

    if action.type == "complete_task":
        if task["status"] == "Done":
            return f"{task['id']}: {task['title']} is already Done.", False
        done = task_service.update_task(task["id"], {"status": "Done"})
        return f"Marked done: {describe(done)}", True

    if action.type == "delete_task":
        deleted = task_service.delete_task(task["id"])
        return f"Deleted {deleted['id']}: {deleted['title']}", True

    return _run_find(action), False


def execute_plan(plan: ActionPlan):
    """Returns {"message": str, "tasks_changed": bool}."""
    if not plan.actions:
        return {"message": plan.reply or "I'm not sure what to do. Could you rephrase?",
                "tasks_changed": False}

    prepared = prepare(plan)  # raises TaskError before anything is changed

    lines, changed = [], False
    for action, task in prepared:
        try:
            text, did_change = _run_one(action, task)
        except TaskError as e:
            text, did_change = f"Could not run {action.type}: {e}", False
        lines.append(text)
        changed = changed or did_change

    return {"message": "\n".join(lines), "tasks_changed": changed}