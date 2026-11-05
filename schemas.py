import os
import re
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

load_dotenv()

TIMEZONE = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Dhaka"))


# ---------- Enums ----------

class Status(str, Enum):
    TODO = "Todo"
    IN_PROGRESS = "In progress"
    DONE = "Done"


class Priority(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


# Extra words we accept and map to the exact values above
STATUS_ALIASES = {
    "todo": "Todo", "to do": "Todo", "to_do": "Todo", "pending": "Todo", "open": "Todo",
    "in progress": "In progress", "in_progress": "In progress", "inprogress": "In progress",
    "doing": "In progress", "started": "In progress",
    "done": "Done", "complete": "Done", "completed": "Done", "finished": "Done",
}


def _normalize_status(value):
    if isinstance(value, str):
        key = value.strip().lower()
        return STATUS_ALIASES.get(key, value.strip())
    return value


def _normalize_priority(value):
    if isinstance(value, str):
        return value.strip().capitalize()   # "HIGH" -> "High"
    return value


# ---------- Due date parser ----------

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def today_local():
    return datetime.now(TIMEZONE).date()


def parse_due_date(value, today=None):
    """Convert text like 'tomorrow' or '2026-11-10' into 'YYYY-MM-DD'.
    Returns None for empty input. Raises ValueError for anything unclear."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("", "none", "null"):
        return None

    today = today or today_local()

    # 1) Exact date: YYYY-MM-DD
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            raise ValueError(f"'{value}' is not a real calendar date")

    # 2) Simple words
    if text == "today":
        return today.isoformat()
    if text == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if text == "day after tomorrow":
        return (today + timedelta(days=2)).isoformat()

    # 3) "in N days"
    match = re.fullmatch(r"in (\d{1,3}) days?", text)
    if match:
        return (today + timedelta(days=int(match.group(1)))).isoformat()

    # 4) Weekday: "friday" = the coming Friday, "next friday" = one week after that
    match = re.fullmatch(r"(next )?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", text)
    if match:
        target = WEEKDAYS[match.group(2)]
        days_ahead = (target - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7          # same weekday as today -> next week
        if match.group(1):
            days_ahead += 7
        return (today + timedelta(days=days_ahead)).isoformat()

    raise ValueError(
        f"could not understand date '{value}'. Use YYYY-MM-DD, today, tomorrow, "
        "day after tomorrow, in N days, or a weekday name"
    )


# ---------- Models ----------

class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1000)
    status: Status = Status.TODO
    priority: Priority = Priority.MEDIUM
    due_date: Optional[str] = None

    @field_validator("status", mode="before")
    @classmethod
    def clean_status(cls, v):
        return _normalize_status(v)

    @field_validator("priority", mode="before")
    @classmethod
    def clean_priority(cls, v):
        return _normalize_priority(v)

    @field_validator("due_date", mode="before")
    @classmethod
    def clean_due_date(cls, v):
        return parse_due_date(v)

    def to_db(self):
        """Plain dict ready for db.create_task(**data)."""
        data = self.model_dump()
        data["status"] = self.status.value
        data["priority"] = self.priority.value
        return data


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1000)
    status: Optional[Status] = None
    priority: Optional[Priority] = None
    due_date: Optional[str] = None

    @field_validator("status", mode="before")
    @classmethod
    def clean_status(cls, v):
        return _normalize_status(v)

    @field_validator("priority", mode="before")
    @classmethod
    def clean_priority(cls, v):
        return _normalize_priority(v)

    @field_validator("due_date", mode="before")
    @classmethod
    def clean_due_date(cls, v):
        return parse_due_date(v)

    @model_validator(mode="after")
    def need_at_least_one_change(self):
        if not self.model_fields_set:
            raise ValueError("no changes were given")
        return self

    def to_db(self):
        """Only the fields that were actually sent. An explicit null clears a field."""
        data = self.model_dump(exclude_unset=True)
        if "status" in data and data["status"] is not None:
            data["status"] = self.status.value
        if "priority" in data and data["priority"] is not None:
            data["priority"] = self.priority.value
        return data


# ---------- Error formatting ----------

def format_error(error: ValidationError):
    """Turn a Pydantic error into one short, readable sentence."""
    parts = []
    for item in error.errors():
        field = ".".join(str(p) for p in item["loc"]) or "input"
        message = item["msg"].removeprefix("Value error, ")
        if item["type"] == "enum":
            if field == "status":
                message = "must be one of Todo, In progress, Done"
            elif field == "priority":
                message = "must be one of Low, Medium, High"
        elif item["type"] == "extra_forbidden":
            message = "is not a valid task field"
        parts.append(f"{field}: {message}")
    return "; ".join(parts)