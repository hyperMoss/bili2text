"use strict";
const messages = JSON.parse(document.getElementById("ui-messages").textContent);
const terminalStatuses = new Set(["completed", "failed", "cancelled"]);

document.querySelectorAll("[data-submit]").forEach(form => {
  form.addEventListener("submit", () => {
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    button.textContent = messages.submitting;
  });
});
window.addEventListener("pageshow", event => { if (event.persisted) location.reload(); });

const openWorkspace = document.getElementById("open-workspace");
openWorkspace?.addEventListener("click", async () => {
  openWorkspace.disabled = true;
  try {
    const response = await fetch("/api/workspace/open", {
      method: "POST", headers: { "X-B2T-Token": document.body.dataset.actionToken }
    });
    if (!response.ok) throw new Error("action failed");
    document.getElementById("action-message").textContent = messages.opened;
  } catch {
    document.getElementById("action-message").textContent = messages.actionError;
  } finally {
    openWorkspace.disabled = false;
  }
});

async function readJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error("request failed");
  return response.json();
}

const rows = Array.from(document.querySelectorAll("[data-task-id]"));
async function pollRow(row) {
  if (row.dataset.finished) return;
  try {
    const data = await readJSON("/api/tasks/" + row.dataset.taskId + "/progress");
    const status = row.querySelector(".task-status");
    status.textContent = messages[data.status] || data.status;
    row.querySelector(".task-progress").value = Math.max(0, Math.min(100, Math.round((data.progress_percent || 0) * 100)));
    row.querySelector(".task-message").textContent = data.error_message || data.current_message || "";
    if (row.hasAttribute("data-single-task")) {
      const events = await readJSON("/api/tasks/" + row.dataset.taskId + "/events");
      document.getElementById("task-events").textContent = events.items.map(
        event => [event.created_at, event.stage, event.message].filter(Boolean).join(" · ")
      ).join("\n");
    }
    if (data.status === "completed" && data.video_id) {
      const result = document.createElement("a");
      result.href = "/videos/" + Number(data.video_id);
      result.textContent = messages.completed + " →";
      status.replaceChildren(result);
    }
    if (terminalStatuses.has(data.status)) row.dataset.finished = "true";
  } catch {
    row.querySelector(".task-status").textContent = messages.connectionError;
  }
}
async function pollTasks() {
  await Promise.all(rows.map(pollRow));
  const done = rows.filter(row => row.dataset.finished).length;
  const summary = document.getElementById("batch-summary");
  if (summary) summary.textContent = done + "/" + rows.length;
  if (done < rows.length) setTimeout(pollTasks, 1000);
}
if (rows.length) pollTasks();
