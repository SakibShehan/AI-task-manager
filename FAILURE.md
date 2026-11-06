# Failures and fixes

| # | Prompt / situation | What went wrong | How I fixed it |
|---|---|---|---|
| 1 | First Groq call, any prompt | 404 "model does not exist" | Listed the models my key could access (`client.models.list()`) and used the full ID `openai/gpt-oss-120b` |
| 2 | Running 9 prompts quickly | Groq free tier 429 (8,000 tokens/min) | Retry once after a short wait, then a clear message |
| 3 | "delete the demo task" | Exact-title matching found nothing | Added semantic search as the third resolution step |
| 4 | Unrelated search words | Search always returns the closest tasks, even irrelevant ones | Added a distance cutoff (`MATCH_MAX_DISTANCE`) |
| 5 | "demo task" with two demo tasks | Silently picking the best match could change the wrong task | If matches are within a small gap, ask the user and store the pending plan in the session |
| 6 | Chat returned 500 after pasting new code | A leftover `@app.route` made Flask call `clean_history` as the chat route | Removed the duplicate decorator and moved the helper out of the routes |
| 7 | "Why is TSK-000002 missing?" | The LLM invented an explanation, and a follow-up complaint was read as a create request | Prompt rules: complaints are not actions, never guess task history, IDs are never reused |
| 8 | LLM proposing an ID the user never typed | A prompt rule alone can't guarantee the model won't invent IDs | Python check after validation rejects any ID not in the message or recent history |
| 9 | "Delete X and mark X done" | Each action ran in its own transaction, so a late failure left earlier changes applied | Added `db.apply_changes()` to run the whole plan in one transaction, with the index synced after commit |
| 10 | Model returns text instead of JSON | The plan can't be parsed | Retry once with the error sent back to the model, then a clear message |
| 11 | Pressing Send twice quickly | The same instruction could run twice | `busy` flag disables the input, Send and Clear chat while a request runs |
| 12 | Page refresh | Chat disappeared | Saved the transcript in `localStorage`, with a Clear chat button |
| 13 | `python - <<PY` diagnostic script | `load_dotenv()` crashed without a script file | Saved the diagnostic as a real file |