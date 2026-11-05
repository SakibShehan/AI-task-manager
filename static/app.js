const taskList = document.getElementById("task-list");
const taskCount = document.getElementById("task-count");
const messages = document.getElementById("messages");
const form = document.getElementById("chat-form");
const input = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");

/* Build an element safely: text goes in with textContent, never innerHTML */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/* ---------- Tasks ---------- */

function describeDue(dueDate, isDone) {
  if (!dueDate) return { text: "No due date", overdue: false };

  const [y, m, d] = dueDate.split("-").map(Number);
  const due = new Date(y, m - 1, d);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const diff = Math.round((due - today) / 86400000);
  const pretty = due.toLocaleDateString("en-US", { month: "short", day: "numeric" });

  if (diff === 0) return { text: "Today", overdue: false };
  if (diff === 1) return { text: "Tomorrow", overdue: false };
  if (diff < 0 && !isDone) return { text: `Overdue (${pretty})`, overdue: true };
  return { text: pretty, overdue: false };
}

function renderTask(task) {
  const isDone = task.status === "Done";
  const li = el("li", "task" + (isDone ? " done" : ""));

  li.appendChild(el("div", "check", isDone ? "✓" : ""));

  const main = el("div", "task-main");
  const title = el("div", "task-title", task.title);
  title.appendChild(el("span", "task-id", task.id));
  main.appendChild(title);

  const meta = el("div", "task-meta");
  if (isDone) {
    meta.textContent = "Completed • Done";
  } else {
    const due = describeDue(task.due_date, isDone);
    meta.appendChild(el("span", due.overdue ? "overdue" : "", due.text));
    meta.appendChild(document.createTextNode(` • ${task.status}`));
  }
  main.appendChild(meta);
  li.appendChild(main);

  li.appendChild(el("span", `badge ${task.priority}`, task.priority));
  return li;
}

async function loadTasks() {
  try {
    const res = await fetch("/api/tasks");
    const data = await res.json();
    if (!data.success) throw new Error(data.message);

    taskList.replaceChildren();
    taskCount.textContent = data.tasks.length;

    if (data.tasks.length === 0) {
      taskList.appendChild(el("li", "empty", "No tasks yet. Ask the assistant to create one."));
      return;
    }

    // Unfinished first, completed at the bottom (like the reference image)
    const sorted = [...data.tasks].sort(
      (a, b) => (a.status === "Done") - (b.status === "Done")
    );
    sorted.forEach((t) => taskList.appendChild(renderTask(t)));
  } catch (err) {
    taskList.replaceChildren(el("li", "empty", "Could not load tasks: " + err.message));
  }
}

/* ---------- Chat ---------- */

function addMessage(kind, text) {
  messages.appendChild(el("div", `msg ${kind}`, text));
  messages.scrollTop = messages.scrollHeight;
}

async function sendMessage(text) {
  addMessage("user", text);
  sendBtn.disabled = true;
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });
    const data = await res.json();
    addMessage(data.success ? "bot" : "error", data.message);
  } catch (err) {
    addMessage("error", "Could not reach the server. Is it running?");
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  sendMessage(text);
});

/* Enter sends, Shift+Enter makes a new line */
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

/* ---------- Start ---------- */
addMessage("bot", "Hi! Tell me what to do with your tasks, for example: \"Create a high-priority task to finish the demo tomorrow.\"");
loadTasks();