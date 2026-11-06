# TaskFlow: AI Agent Task Manager

A single-page task manager controlled mainly through an AI chat. You type
instructions such as "Move the demo task to In progress and mark the weekly report
Done", and the agent understands and executes them. The task board and the chat
are visible together on one page.


## Features

- Create, list, edit, move, change status, complete, and delete tasks from chat
- Task fields: title, description (optional), status, priority, due date (optional)
- Semantic search with ChromaDB ("documentation" finds "Update API documentation")
- Several actions in one message, applied all-or-nothing in one SQLite transaction
- Asks a clarification question when a task reference is ambiguous
- Tasks and chat persist after refresh and restart
- Stats row (Total / Todo / In progress / Done) and expandable task details
- Responsive layout (chat stacks under the board on phones)

## Tech stack

| Part | Choice |
|---|---|
| Backend | Python, Flask |
| Source of truth | SQLite |
| Vector store | ChromaDB (local, persistent, built-in embedding model) |
| Validation | Pydantic |
| LLM | Groq API, model `openai/gpt-oss-120b`, called with the `openai` Python package |
| Frontend | Plain HTML, CSS, JavaScript (no framework) |

## Setup

Requires Python 3.12 (3.10+ should work), pip, and internet access for installing
packages, the LLM API, and a one-time ChromaDB embedding model download (about 80 MB).

```bash
git clone https://github.com/SakibShehan/AI-task-manager
cd Ai-task-manager
python3 -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
cp .env.example .env               # Windows: Copy-Item .env.example .env
```

Edit `.env` and set `LLM_API_KEY` to a key from https://console.groq.com/keys.
Set `SECRET_KEY` to any long random text. Then run:

```bash
python app.py
```

Open http://127.0.0.1:5000. The first start may take a minute while ChromaDB
downloads its embedding model.

If `LLM_MODEL` gives "model not found", your key may not have access to it. List the
models your key can use and copy one chat model ID into `.env`.

Shell environment variables override `.env`. If a setting seems ignored, run
`echo $LLM_MODEL` and `unset` any stale variable.

## Why Groq and `openai/gpt-oss-120b`

- Free tier, no payment details needed to start.
- OpenAI-compatible API, so the standard `openai` package works with only a changed
  `base_url`, and the code can switch provider by editing `.env`.
- Very fast responses, which matters for a chat interface.
- Supports JSON mode, and the model follows the structured-output prompt reliably at
  temperature 0.

Trade-off: the free tier allows about 8,000 tokens per minute, so rapid-fire prompts
can hit a rate limit. The app retries once after a short wait, then shows a clear
message.

## How it works

```
Browser chat -> POST /api/chat -> planner (LLM) -> validation -> resolver -> executor -> SQLite
                                                                                  |
                                                                          ChromaDB (search index)
```

1. **Planner (`agent.py`).** The LLM receives today's date, the allowed actions,
   examples, and the last few chat messages. It returns a JSON plan such as
   `{"actions":[{"type":"update_task","target":"demo task","data":{"status":"In progress"}}]}`.
   It never touches the database and never sees the task list.
2. **Validation (Pydantic).** The plan is checked: known action types only, required
   fields, allowed statuses and priorities, valid dates, at most 5 actions. If the
   reply is invalid, the error is sent back to the model once to repair it. Python also
   rejects any task ID the user never mentioned, so the model cannot invent IDs.
3. **Target resolution (`executor.py`).** "The demo task" is resolved in this order:
   exact ID, exact title, then ChromaDB semantic search with a distance cutoff. If two
   or more tasks are almost equally close, the bot asks "Which one?" and stores the
   pending plan in the Flask session. The next reply (`2`, `the second one`,
   `TSK-000001`, `cancel`) resumes it without another LLM call.
4. **Execution.** All actions are validated first. Changes then run in one SQLite
   transaction, so a multi-action request is all-or-nothing. ChromaDB is updated only
   after the commit succeeds.
5. **Reply.** The program, not the LLM, writes the result message
   (for example `Updated TSK-000002: Prepare demo slides (Medium, In progress, no due date)`).

### Source of truth and IDs

- SQLite is the only source of truth. ChromaDB is a derived search index. Search
  returns IDs, and the real records are always loaded from SQLite.
- The index is rebuilt from SQLite at every server start (`reconcile_index()`), so it
  repairs itself if it drifts.
- Task IDs are deterministic (`TSK-000001`, `TSK-000002`, ...) from a counter that only
  increases. A deleted ID is never reused.

### Semantic search

Chroma embeds `title + description` for each task, using the task ID as the vector
ID. Distances are cosine (lower means closer). Matches above a cutoff
(`MATCH_MAX_DISTANCE`, default 0.65) count as "not found", so unrelated text never
changes a task by accident.

### Chat persistence

Tasks persist in SQLite. The chat transcript is stored in the browser's
`localStorage` (up to 100 messages) and survives refresh. The **Clear chat** button is the
only thing that clears it.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/tasks` | List tasks (`?status=`, `?priority=`) |
| GET | `/api/tasks/<id>` | One task |
| GET | `/api/search?q=...` | Semantic search (useful for checking retrieval) |
| POST | `/api/tasks` | Create a task directly (testing helper) |
| POST | `/api/chat` | Main entry: message in, reply out |

Every response is JSON with `success` and `message`.

## Test prompts and results

Run on a fresh database. Real output from my runs: <<replace each with what you saw>>

| # | Prompt | Result |
|---|---|---|
| 1 | `Create a high-priority task to finish the demo tomorrow.` | <<e.g. Created TSK-000001: Finish the demo (High, Todo, 2026-11-07)>> |
| 2 | `Show all unfinished tasks related to documentation.` | <<e.g. Listed only TSK-000003: Update API documentation>> |
| 3 | `Move the demo task to In progress and mark the weekly report Done.` | <<e.g. Asked which demo task, then both actions ran after I answered "2">> |
| 4 | `Change the priority of the API task to Low.` | <<e.g. Resolved to TSK-000003 by meaning, updated to Low>> |
| 5 | `Move TSK-000001 to Done and delete TSK-999999` | <<e.g. Rejected as a whole, TSK-000001 unchanged>> |

## Failures encountered and fixes

See FAILUE.md file for the full table.

## Project structure

```
AI-task-manager/
├── app.py              Flask routes, error handlers, chat endpoint
├── agent.py            LLM planner: prompt, plan models, Groq call, ID safety check
├── executor.py         Target resolution, clarification, batch execution
├── task_service.py     Validation + SQLite + Chroma sync in one layer
├── db.py               SQLite storage (tasks, ID counter, atomic batch)
├── vector_store.py     ChromaDB index and semantic search
├── schemas.py          Pydantic models, enums, due-date parser
├── requirements.txt    Python dependencies
├── .env.example        Safe configuration template
├── FAILURES.md         Failures encountered and how they were fixed
├── README.md           This file
├── templates/
│   └── index.html      Single-page UI (task board + AI chat)
└── static/
    ├── style.css       Layout, responsive design
    └── app.js          Task board, chat, persistence
```

Created at runtime and not committed: `.env` (your API key), `data/` (SQLite
database and ChromaDB index), `.venv/`.

## Index recovery

If search results look stale, stop the server and start it again. The index is
rebuilt from SQLite on startup. Never delete `data/tasks.sqlite3` to fix search. To reset only the
index, delete `data/chroma/`.

## Limitations

- Single-user local prototype: no login, no encryption, one server process.
- Chat history lives in the browser, so another browser starts with an empty chat.
- The clarification state lives in a session cookie, so it is tied to one browser.
- Groq's free tier has token-per-minute limits.
- The LLM can misread vague instructions. Python validation prevents invalid or
  unrequested changes from being applied, but a wrong-but-valid plan is still possible.
- Due dates support `YYYY-MM-DD`, today, tomorrow, day after tomorrow, in N days, and
  weekday names (`next friday` means the following week). A time of day is kept in the
  description, not the due date.

## Possible improvements

- Save chat history on the server, and add user accounts
- Streaming replies, and a confirmation step for deletes
- Drag-and-drop on the board, and status filters