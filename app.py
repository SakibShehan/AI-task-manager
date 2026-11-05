import os
from flask import Flask, jsonify
from dotenv import load_dotenv
from db import init_db
load_dotenv()

app = Flask(__name__)
init_db()


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