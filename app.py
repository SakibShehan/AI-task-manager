import os

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

import task_service
from db import init_db
from task_service import TaskError

load_dotenv()

app = Flask(__name__)
init_db()

try:
    upserted, removed = task_service.reconcile_index()
    print(f"[index] synced {upserted} tasks, removed {removed} stale entries")
except Exception as e:
    print(f"[warning] could not sync search index at startup: {e}")


# ---------- Response helpers ----------

def ok(message, status_code=200, **data):
    return jsonify({"success": True, "message": message, **data}), status_code


def fail(message, status_code=400):
    return jsonify({"success": False, "message": message}), status_code


def json_body():
    """Return the JSON body as a dict, or raise a clear error."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise TaskError("request body must be a JSON object")
    return data


# ---------- Error handlers ----------

@app.errorhandler(TaskError)
def handle_task_error(e):
    return fail(str(e), 400)


@app.errorhandler(HTTPException)
def handle_http_error(e):
    return fail(e.description, e.code)


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    app.logger.exception(e)
    return fail("Something went wrong on the server", 500)


# ---------- Routes ----------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return ok("Server is healthy", timezone=os.getenv("APP_TIMEZONE", "Asia/Dhaka"))


@app.route("/api/tasks", methods=["GET"])
def api_list_tasks():
    tasks = task_service.list_tasks(
        status=request.args.get("status"),
        priority=request.args.get("priority"),
    )
    return ok(f"Found {len(tasks)} task(s)", tasks=tasks, count=len(tasks))


@app.route("/api/tasks/<task_id>", methods=["GET"])
def api_get_task(task_id):
    try:
        task = task_service.get_task(task_id.upper())
    except TaskError as e:
        return fail(str(e), 404)
    return ok("Task found", task=task)


@app.route("/api/tasks", methods=["POST"])
def api_create_task():
    task = task_service.create_task(json_body())
    return ok(f"Created {task['id']}", 201, task=task)


@app.route("/api/search", methods=["GET"])
def api_search():
    query = request.args.get("q", "")
    try:
        n = max(1, min(int(request.args.get("n", 5)), 20))
    except ValueError:
        raise TaskError("n must be a number")
    unfinished = request.args.get("unfinished", "").lower() in ("1", "true", "yes")

    tasks = task_service.search_tasks(query, n=n, unfinished_only=unfinished)
    return ok(f"Found {len(tasks)} match(es)", tasks=tasks, count=len(tasks))


@app.route("/api/chat", methods=["POST"])
def api_chat():
    message = json_body().get("message")
    if not isinstance(message, str) or not message.strip():
        raise TaskError("message is required")
    message = message.strip()
    if len(message) > 1000:
        raise TaskError("message is too long (maximum 1000 characters)")

    return ok(
        f"Chat agent is not connected yet. You said: {message}",
        tasks_changed=False,
    )


if __name__ == "__main__":
    app.run(debug=True)