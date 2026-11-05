import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(env_path)

print("Model from environment:", repr(os.getenv("LLM_MODEL")))
print("Base URL:", repr(os.getenv("LLM_BASE_URL")))

client = OpenAI(
    api_key=os.getenv("LLM_API_KEY", "").strip(),
    base_url=os.getenv("LLM_BASE_URL"),
)
print("\nModels your key can use:")
for m in sorted(client.models.list().data, key=lambda m: m.id):
    print(" ", m.id)