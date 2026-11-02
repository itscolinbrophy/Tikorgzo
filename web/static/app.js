const form = document.getElementById("download-form");
const input = document.getElementById("url");
const submitBtn = document.getElementById("submit-btn");
const statusEl = document.getElementById("status");
const statusText = statusEl.querySelector(".status-text");
const statusLogBtn = document.getElementById("status-log-btn");
const jobLogEl = document.getElementById("job-log");
const gallery = document.getElementById("gallery");
const empty = document.getElementById("empty");
const countEl = document.getElementById("count");
const refreshBtn = document.getElementById("refresh-btn");
const cardTemplate = document.getElementById("card-template");

let currentJobId = null;
let busy = false;

function setBusy(value) {
  busy = value;
  submitBtn.disabled = value;
  submitBtn.classList.toggle("is-busy", value);
}

function setStatus(text, state) {
  statusEl.hidden = false;
  statusEl.dataset.state = state;
  statusText.textContent = text;
}

function hideStatus() {
  statusEl.hidden = true;
  statusEl.removeAttribute("data-state");
  statusLogBtn.hidden = true;
}

function showLog(text) {
  jobLogEl.textContent = text || "No log output.";
  jobLogEl.hidden = false;
}

function hideLog() {
  jobLogEl.hidden = true;
  jobLogEl.textContent = "";
}

async function api(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers });
  let body = null;
  try {
    body = await response.json();
  } catch (_) {
    body = null;
  }
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    if (body) {
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map((item) => item.msg).join("; ");
    }
    throw new Error(message);
  }
  return body;
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatDate(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const now = Date.now();
  const diff = now - date.getTime();
  if (diff >= 0 && diff < 60_000) return "just now";
  if (diff >= 0 && diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff >= 0 && diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function renderGallery(items, totalBytes) {
  gallery.textContent = "";
  const fragment = document.createDocumentFragment();

  for (const item of items) {
    const node = cardTemplate.content.firstElementChild.cloneNode(true);
    const thumb = node.querySelector(".thumb");
    const img = node.querySelector("img");
    const username = node.querySelector(".username");
    const badge = node.querySelector(".badge");
    const meta = node.querySelector(".meta");
    const download = node.querySelector(".download-btn");
    const preview = node.querySelector(".preview-btn");

    thumb.href = item.stream_url;
    img.src = item.thumb_url;
    img.alt = item.username ? `@${item.username} TikTok video` : "TikTok video";
    img.addEventListener(
      "error",
      () => {
        thumb.classList.add("no-image");
        img.removeAttribute("src");
      },
      { once: true }
    );

    username.textContent = item.username ? `@${item.username}` : "TikTok video";
    if (!item.username) username.classList.add("empty");

    const age = Date.now() - new Date(item.modified).getTime();
    if (Number.isFinite(age) && age >= 0 && age < 3_600_000) {
      badge.textContent = "New";
      badge.hidden = false;
    }

    meta.textContent = `${formatBytes(item.size)} · ${formatDate(item.modified)}`;
    meta.title = item.name;

    download.href = item.download_url;
    download.setAttribute("download", item.name);
    preview.href = item.stream_url;

    fragment.appendChild(node);
  }

  gallery.appendChild(fragment);
  const count = items.length;
  const size = formatBytes(totalBytes || 0);
  countEl.textContent = count === 0 ? "No downloads yet" : `${count} video${count === 1 ? "" : "s"} · ${size}`;
  empty.hidden = count !== 0;
  gallery.hidden = count === 0;
}

async function loadVideos({ silent = false } = {}) {
  if (!silent) refreshBtn.classList.add("is-spinning");
  try {
    const data = await api("/api/videos");
    renderGallery(data.items || [], data.total_bytes || 0);
  } catch (error) {
    if (!silent) setStatus(error.message, "error");
  } finally {
    refreshBtn.classList.remove("is-spinning");
  }
}

async function pollJob(jobId) {
  while (true) {
    let job;
    try {
      job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    } catch (error) {
      setStatus(error.message, "error");
      setBusy(false);
      return;
    }

    if (job.status === "queued" || job.status === "running") {
      const started = job.started_at ? new Date(job.started_at) : new Date(job.created_at);
      const elapsed = Math.max(0, Math.floor((Date.now() - started.getTime()) / 1000));
      const stage = job.stage || "Working";
      setStatus(`${stage} · ${elapsed}s`, "working");
      await new Promise((resolve) => setTimeout(resolve, 1200));
      continue;
    }

    if (job.status === "done") {
      const message = job.message || "Download complete";
      setStatus(message, "success");
      statusLogBtn.hidden = false;
      await loadVideos({ silent: true });
      setBusy(false);
      return;
    }

    setStatus(job.message || "Download failed", "error");
    statusLogBtn.hidden = false;
    if (job.log) showLog(job.log);
    setBusy(false);
    return;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;

  const url = input.value.trim();
  if (!url) {
    setStatus("Paste a TikTok link first.", "error");
    input.focus();
    return;
  }

  setBusy(true);
  hideLog();
  statusLogBtn.hidden = true;
  setStatus("Submitting…", "working");

  try {
    const job = await api("/api/download", {
      method: "POST",
      body: JSON.stringify({ url }),
    });
    currentJobId = job.id;
    input.value = "";
    setStatus("Queued…", "working");
    await pollJob(job.id);
  } catch (error) {
    setStatus(error.message, "error");
    setBusy(false);
  }
});

statusLogBtn.addEventListener("click", async () => {
  if (!currentJobId) return;
  if (!jobLogEl.hidden) {
    hideLog();
    return;
  }
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(currentJobId)}`);
    showLog(job.log || "No log output.");
  } catch (error) {
    showLog(error.message);
  }
});

refreshBtn.addEventListener("click", () => loadVideos());

loadVideos();
