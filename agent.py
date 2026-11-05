import json
import os
import re
from datetime import datetime
from typing import Literal, Optional

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAI,
    OpenAIError,
    RateLimitError,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas import TIMEZONE, format_error

load_dotenv()

BASE_URL = os.getenv("LLM_BASE_URL") or "https://api.x.ai/v1"
MAX_ACTIONS = 5
MAX_HISTORY = 6  # recent chat messages sent to the model


class PlannerError(Exception):
    """A problem with a clear message that is safe to show in the chat."""


# ---------- Plan models ----------

ActionType = Literal["create_task", "update_task", "complete_task", "delete_task", "find_tasks"]
ALLOWED_FIELDS = {"title", "description", "status", "priority", "due_date"}
NOT_CLEARABLE = {"title", "status", "priority"}  # null here means "not given"


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActionType
    target: Optional[str] = Field(default=None, max_length=200)   # task ID or a phrase
    data: dict = Field(default_factory=dict)                      # fields for create / update
    query: Optional[str] = Field(default=None, max_length=200)    # find_tasks only
    status: Optional[str] = None                                  # find_tasks filter
    priority: Optional[str] = None                                # find_tasks filter
    unfinished_only: bool = False                                 # find_tasks filter

    @field_validator("target", "query", mode="before")
    @classmethod
    def blank_to_none(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    @field_validator("data", mode="before")
    @classmethod
    def clean_data(cls, v):
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise ValueError("must be an object")
        unknown = set(v) - ALLOWED_FIELDS
        if unknown:
            raise ValueError(f"unknown field(s): {', '.join(sorted(unknown))}")
        # The model sometimes sends null for fields it doesn't want to change
        return {k: val for k, val in v.items() if not (val is None and k in NOT_CLEARABLE)}

    @model_validator(mode="after")
    def check_required_parts(self):
        if self.type == "create_task":
            title = self.data.get("title")
            if not isinstance(title, str) or not title.strip():
                raise ValueError("create_task needs data.title")
        elif self.type == "update_task":
            if not self.target:
                raise ValueError("update_task needs a target")
            if not self.data:
                raise ValueError("update_task needs at least one field in data")
        elif self.type in ("complete_task", "delete_task"):
            if not self.target:
                raise ValueError(f"{self.type} needs a target")
        return self


class ActionPlan(BaseModel):
    actions: list[Action] = Field(default_factory=list, max_length=MAX_ACTIONS)
    reply: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def need_something(self):
        if not self.actions and not (self.reply and self.reply.strip()):
            raise ValueError("plan needs either actions or a reply")
        return self


# ---------- System prompt ----------

SYSTEM_PROMPT = """You are the planner for a task manager app. You do NOT run anything.
You turn the user's message into a JSON plan. The program validates and executes it.

Today is <<WEEKDAY>>, <<TODAY>> (timezone <<TZ>>).

OUTPUT FORMAT
Reply with ONE JSON object and nothing else (no markdown, no explanation):
{"actions": [ ... ], "reply": null}

ACTION TYPES
1. create_task   - data: title (required), description, status, priority, due_date
2. update_task   - target (required) + data with ONLY the fields to change
3. complete_task - target (required). Use for "complete", "finish", "mark done".
4. delete_task   - target (required)
5. find_tasks    - optional: query (what to search for), status, priority, unfinished_only (true/false)
   With no query it lists tasks. Use it for "show", "list", "find", "which".

FIELD VALUES
- status: "Todo", "In progress" or "Done"
- priority: "Low", "Medium" or "High"
- due_date: pass the user's phrase if it is one of: today, tomorrow, day after tomorrow,
  in N days, a weekday name (friday), or next + weekday (next friday).
  Otherwise convert it to YYYY-MM-DD using today's date.
- To remove a description or due date, set it to null.
- Put only the fields that change in data. Do not send null for the other fields.

TARGET RULES
- If the user gives an ID like TSK-000004, use exactly that as the target.
- Otherwise use a short phrase in the user's own words ("demo task", "weekly report").
- NEVER invent an ID. You cannot see the task list.
- If the user says "it", "that one" or "the second one", use the conversation history to find
  which task they mean. If the history has an ID, use it.

WHEN NOT TO ACT
- If the message is unrelated to tasks, or the target is missing and cannot be worked out,
  return "actions": [] and write a short helpful "reply" (one or two sentences).
- Never delete or change anything the user did not clearly ask for.
- Maximum 5 actions. For several requests in one message, return several actions in order.
- When you return actions, set "reply" to null. The program writes the result message.

EXAMPLES
User: Create a high-priority task to finish the demo tomorrow.
{"actions":[{"type":"create_task","data":{"title":"Finish the demo","priority":"High","due_date":"tomorrow"}}],"reply":null}

User: Show all unfinished tasks related to documentation.
{"actions":[{"type":"find_tasks","query":"documentation","unfinished_only":true}],"reply":null}

User: Show all my tasks
{"actions":[{"type":"find_tasks"}],"reply":null}

User: Move the demo task to In progress and mark the weekly report Done.
{"actions":[{"type":"update_task","target":"demo task","data":{"status":"In progress"}},{"type":"complete_task","target":"weekly report"}],"reply":null}

User: Change the priority of the API task to Low.
{"actions":[{"type":"update_task","target":"API task","data":{"priority":"Low"}}],"reply":null}

User: Remove the due date from TSK-000004
{"actions":[{"type":"update_task","target":"TSK-000004","data":{"due_date":null}}],"reply":null}

User: Delete TSK-000007
{"actions":[{"type":"delete_task","target":"TSK-000007"}],"reply":null}

User: Delete it   (no earlier context)
{"actions":[],"reply":"Which task should I delete? Please give its ID or title."}

User: What's the weather today?
{"actions":[],"reply":"I can only help with managing your tasks, such as creating, finding, updating or deleting them."}
"""


def build_system_prompt(now=None):
    now = now or datetime.now(TIMEZONE)
    return (
        SYSTEM_PROMPT
        .replace("<<TODAY>>", now.strftime("%Y-%m-%d"))
        .replace("<<WEEKDAY>>", now.strftime("%A"))
        .replace("<<TZ>>", str(TIMEZONE))
    )


# ---------- Calling the LLM ----------

def _get_client():
    key = os.getenv("LLM_API_KEY", "").strip()
    model = os.getenv("LLM_MODEL", "").strip()
    if not key:
        raise PlannerError("LLM_API_KEY is missing. Add your Grok key to .env and restart the server.")
    if not model:
        raise PlannerError("LLM_MODEL is missing. Add the model name from your xAI console to .env.")
    return OpenAI(api_key=key, base_url=BASE_URL, timeout=60, max_retries=1), model


def _ask(client, model, messages):
    """Send the chat to the model and return its raw text."""
    kwargs = dict(model=model, messages=messages, temperature=0)
    try:
        try:
            resp = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
        except BadRequestError:
            # Some models reject JSON mode; try once without it
            resp = client.chat.completions.create(**kwargs)
    except AuthenticationError:
        raise PlannerError("The Grok API key was rejected. Check LLM_API_KEY in .env.")
    except NotFoundError:
        raise PlannerError(f"The model '{model}' was not found. Check LLM_MODEL against your xAI console.")
    except RateLimitError:
        raise PlannerError("The AI service is rate limited or out of quota. Try again shortly.")
    except APIConnectionError:
        raise PlannerError("Could not reach the AI service. Check your internet connection.")
    except OpenAIError as e:
        raise PlannerError(f"The AI service returned an error: {e}")
    return resp.choices[0].message.content or ""


def _extract_json(text):
    """Parse the model's reply, tolerating ```json fences or extra words around it."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in the reply")
    obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("the reply must be a JSON object")
    return obj


def plan_actions(message, history=None):
    """Turn a user message into a validated ActionPlan.
    history: optional list of {"role": "user"|"assistant", "content": "..."}."""
    client, model = _get_client()

    messages = [{"role": "system", "content": build_system_prompt()}]
    for item in (history or [])[-MAX_HISTORY:]:
        if item.get("role") in ("user", "assistant") and item.get("content"):
            messages.append({"role": item["role"], "content": item["content"]})
    messages.append({"role": "user", "content": message})

    for attempt in (1, 2):
        raw = _ask(client, model, messages)
        try:
            return ActionPlan(**_extract_json(raw))
        except ValueError as e:  # covers bad JSON and Pydantic validation errors
            problem = format_error(e) if hasattr(e, "errors") else str(e)
            print(f"[planner] attempt {attempt} failed: {problem} | raw: {raw[:300]!r}")
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": f"Your last reply was invalid ({problem}). "
                           "Reply again with only the corrected JSON object.",
            })

    raise PlannerError("I couldn't turn that into a valid plan. Please rephrase your request.")