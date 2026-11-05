import os
import re

from pydantic import ValidationError

import task_service
from agent import ActionPlan
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

# Cosine distance: lower = more similar. Tune these using /api/search (see the test step).
MATCH_MAX_DISTANCE = float(os.getenv("MATCH_MAX_DISTANCE") or 0.65)  # to change/delete a task
FIND_MAX_DISTANCE = float(os.getenv("FIND_MAX_DISTANCE") or 0.70)    # for "show tasks about ..."
AMBIGUITY_GAP = 0.05  # matches this close to the best one count as equally good


class Ambiguous(Exception):
    """Several tasks fit a target equally well, so the user must choose."""

    def __init__(self, target, candidates):
        super().__init__(target)
        self.target = target
        self.candidates = candidates
        self.index = 0  # which action in the plan; filled in by prepare()


# ---------- Helpers ----------

def describe(task):
    due = task["due_date"] or "no due date"
    return f"{task['id']}: {task['title']} ({task['priority']}, {task['status']}, {due})"


def resolve_target(target, prefer_unfinished=False):
    """Return exactly one real task, raise Ambiguous, or raise TaskError."""
    text = target.strip()

    # 1) Exact ID
    if ID_PATTERN.match(text):
        return task_service.get_task(text.upper())  # raises TaskError if missing

    # 2) Exact title (case-insensitive)
    exact = [t for t in task_service.list_tasks() if t["title"].strip().lower() == text.lower()]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise Ambiguous(target, exact)

    # 3) Semantic search with a relevance cutoff
    hits = task_service.search_tasks(
        text, n=5, unfinished_only=prefer_unfinished, max_distance=MATCH_MAX_DISTANCE
    )
    if not hits and prefer_unfinished:
        hits = task_service.search_tasks(text, n=5, max_distance=MATCH_MAX_DISTANCE)
    if not hits:
        raise TaskError(
            f"I couldn't find a task matching '{target}'. "
            "Try its ID (like TSK-000001) or different words."
        )

    best = hits[0]["distance"]
    close = [t for t in hits if t["distance"] - best <= AMBIGUITY_GAP]
    if len(close) == 1:
        return close[0]
    raise Ambiguous(target, close)


def _find_filters(action):
    status = _normalize_status(action.status) if action.status else None
    priority = _normalize_priority(action.priority) if action.priority else None
    task_service.list_tasks(status=status, priority=priority)  # raises TaskError if invalid
    return status, priority


def _target(action, index, chosen):
    """Use the user's earlier choice if there is one, otherwise resolve the phrase."""
    if index in chosen:
        return task_service.get_task(chosen[index])
    return resolve_target(action.target, prefer_unfinished=(action.type == "complete_task"))


# ---------- Step 1: validate everything, change nothing ----------

def prepare(plan, chosen):
    prepared = []
    for index, action in enumerate(plan.actions):
        label = f"Action {index + 1} ({action.type})"
        try:
            if action.type == "create_task":
                TaskCreate(**action.data)
                prepared.append((action, None))
            elif action.type == "update_task":
                task = _target(action, index, chosen)
                TaskUpdate(**action.data)
                prepared.append((action, task))
            elif action.type in ("complete_task", "delete_task"):
                prepared.append((action, _target(action, index, chosen)))
            else:  # find_tasks
                _find_filters(action)
                prepared.append((action, None))
        except Ambiguous as e:
            e.index = index
            raise
        except ValidationError as e:
            raise TaskError(f"{label}: {format_error(e)}. Nothing was changed.")
        except TaskError as e:
            raise TaskError(f"{label}: {e} Nothing was changed.")
    return prepared


# ---------- Step 2: run the validated actions ----------

def _run_find(action):
    status, priority = _find_filters(action)

    if action.query:
        found = task_service.search_tasks(
            action.query, n=10,
            unfinished_only=action.unfinished_only,
            max_distance=FIND_MAX_DISTANCE,
        )
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




# ---------- Clarification ----------

def _question(e):
    lines = [f"'{e.target}' could mean {len(e.candidates)} different tasks. Which one do you mean?"]
    for number, task in enumerate(e.candidates, start=1):
        lines.append(f"{number}. {describe(task)}")
    lines.append("Reply with the number or the ID, or say 'cancel'.")
    return "\n".join(lines)


ORDINALS = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4, "fifth": 5, "5th": 5,
}


def parse_choice(text, options):
    """Return a task ID from options, 'cancel', or None if the reply isn't an answer."""
    t = text.strip().lower()
    if re.fullmatch(r"(cancel|never ?mind|stop|none|no|neither)\W*", t):
        return "cancel"

    match = re.search(r"tsk-(\d{1,9})", t)
    if match:
        task_id = f"TSK-{int(match.group(1)):06d}"
        return task_id if task_id in options else None

    if re.search(r"\blast\b", t):
        return options[-1]
    for word in re.findall(r"[a-z0-9]+", t):
        if word in ORDINALS:
            number = ORDINALS[word]
            return options[number - 1] if number <= len(options) else None

    match = re.fullmatch(r"(?:number |no\.? |#)?(\d{1,2})[.)]?", t)
    if match:
        number = int(match.group(1))
        return options[number - 1] if 1 <= number <= len(options) else None
    return None


def resume(pending, reply):
    """Continue a plan after the user answered a clarification.
    Returns a result dict, or None if the reply wasn't an answer (treat it as a new message)."""
    choice = parse_choice(reply, pending["options"])
    if choice is None:
        return None
    if choice == "cancel":
        return {"message": "Okay, cancelled. Nothing was changed.", "tasks_changed": False}

    plan = ActionPlan(**pending["plan"])  # validated again, never trusted blindly
    chosen = {int(k): v for k, v in pending["chosen"].items()}
    chosen[pending["index"]] = choice
    return execute_plan(plan, chosen)


# ---------- Entry point ----------
def _build_operations(prepared):
    """Turn validated actions into batch operations.
    Returns (operations, slots) where slots[i] is the operation index for action i, or None."""
    operations, slots = [], []
    for action, task in prepared:
        if action.type == "create_task":
            operations.append({"op": "create", "data": action.data})
        elif action.type == "update_task":
            operations.append({"op": "update", "id": task["id"], "changes": action.data})
        elif action.type == "complete_task" and task["status"] != "Done":
            operations.append({"op": "update", "id": task["id"], "changes": {"status": "Done"}})
        elif action.type == "delete_task":
            operations.append({"op": "delete", "id": task["id"]})
        else:
            slots.append(None)  # find_tasks, or a task that is already Done
            continue
        slots.append(len(operations) - 1)
    return operations, slots


def execute_plan(plan: ActionPlan, chosen=None):
    """Returns {"message", "tasks_changed"} and, if a question was asked, "pending"."""
    chosen = chosen or {}
    if not plan.actions:
        return {"message": plan.reply or "I'm not sure what to do. Could you rephrase?",
                "tasks_changed": False}

    try:
        prepared = prepare(plan, chosen)  # raises TaskError before anything is changed
    except Ambiguous as e:
        return {
            "message": _question(e),
            "tasks_changed": False,
            "pending": {
                "plan": plan.model_dump(mode="json"),
                "chosen": {str(k): v for k, v in chosen.items()},
                "index": e.index,
                "options": [t["id"] for t in e.candidates],
            },
        }

    # Run all changes in ONE transaction: all succeed or none do
    operations, slots = _build_operations(prepared)
    results = []
    if operations:
        try:
            results = task_service.apply_batch(operations)
        except TaskError as e:
            return {"message": f"{e}", "tasks_changed": False}

    # Build the reply in the same order as the user's actions
    lines = []
    for (action, task), slot in zip(prepared, slots):
        if action.type == "find_tasks":
            lines.append(_run_find(action))
        elif slot is None:  # complete_task on a task that was already Done
            lines.append(f"{task['id']}: {task['title']} is already Done.")
        elif action.type == "create_task":
            lines.append(f"Created {describe(results[slot])}")
        elif action.type == "update_task":
            lines.append(f"Updated {describe(results[slot])}")
        elif action.type == "complete_task":
            lines.append(f"Marked done: {describe(results[slot])}")
        elif action.type == "delete_task":
            lines.append(f"Deleted {results[slot]['id']}: {results[slot]['title']}")

    return {"message": "\n".join(lines), "tasks_changed": bool(operations)}
    """Returns {"message", "tasks_changed"} and, if a question was asked, "pending"."""
    chosen = chosen or {}
    if not plan.actions:
        return {"message": plan.reply or "I'm not sure what to do. Could you rephrase?",
                "tasks_changed": False}

    try:
        prepared = prepare(plan, chosen)  # raises TaskError before anything is changed
    except Ambiguous as e:
        return {
            "message": _question(e),
            "tasks_changed": False,
            "pending": {
                "plan": plan.model_dump(mode="json"),
                "chosen": {str(k): v for k, v in chosen.items()},
                "index": e.index,
                "options": [t["id"] for t in e.candidates],
            },
        }

    lines, changed = [], False
    for action, task in prepared:
        try:
            text, did_change = _run_one(action, task)
        except TaskError as e:
            text, did_change = f"Could not run {action.type}: {e}", False
        lines.append(text)
        changed = changed or did_change

    return {"message": "\n".join(lines), "tasks_changed": changed}