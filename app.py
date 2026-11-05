import os
from flask import Flask, jsonify
from dotenv import load_dotenv

from db import init_db
import task_service

load_dotenv()

app = Flask(__name__)
init_db()

try:
    upserted, removed = task_service.reconcile_index()
    print(f"[index] synced {upserted} tasks, removed {removed} stale entries")
except Exception as e:
    print(f"[warning] could not sync search index at startup: {e}")


@app.route("/")
def home():
    return "AI Task Manager is running"


@app.route("/api/health")
def health():
    return jsonify({
        "success": True,
        "message": "Server is healthy",
        "timezone": os.getenv("APP_TIMEZONE", "Asia/Dhaka"),
    })


if __name__ == "__main__":
    app.run(debug=True)