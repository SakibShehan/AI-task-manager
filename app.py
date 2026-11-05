import os
from flask import Flask, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)


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