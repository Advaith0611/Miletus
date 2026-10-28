const API_URL = (window.MILETUS_API_URL || "http://localhost:8000").replace(/\/$/, "");
const $ = (id) => document.getElementById(id);
const panels = [$("upload-panel"), $("progress-panel"), $("result-panel")];
const progressSteps = [
  ["queued", "Preparing your documents"],
  ["extracting", "Reading your documents"],
  ["writing", "Writing the conversation"],
  ["generating_audio", "Giving each voice a sound"],
  ["assembling", "Finishing your episode"],
  ["complete", "Ready to listen"],
];

let files = [];
let pollTimer;

function show(panel) {
  panels.forEach((item) => item.classList.toggle("hidden", item !== panel));
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

function renderFiles() {
  const list = $("file-list");
  list.replaceChildren();
  if (!files.length) {
    $("create-button").disabled = true;
    return;
  }
  const summary = document.createElement("p");
  summary.className = "file-summary";
  summary.textContent = `${plural(files.length, "document")} selected`;
  list.append(summary);
  files.forEach((file, index) => {
    const item = document.createElement("div");
    item.className = "file-item";
    const name = document.createElement("span");
    name.textContent = file.name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.title = `Remove ${file.name}`;
    remove.setAttribute("aria-label", `Remove ${file.name}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      files.splice(index, 1);
      renderFiles();
    });
    item.append(name, remove);
    list.append(item);
  });
  $("create-button").disabled = false;
}

function setFiles(nextFiles) {
  const allowed = ["pdf", "docx", "md"];
  files = nextFiles.filter((file) => allowed.includes(file.name.split(".").pop().toLowerCase()));
  renderFiles();
  if (files.length !== nextFiles.length) window.alert("Miletus accepts PDF, DOCX, and Markdown files.");
}

$("file-input").addEventListener("change", (event) => setFiles([...event.target.files]));
$("dropzone").addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") $("file-input").click();
});
["dragenter", "dragover"].forEach((eventName) => $("dropzone").addEventListener(eventName, (event) => {
  event.preventDefault();
  $("dropzone").classList.add("is-dragging");
}));
["dragleave", "drop"].forEach((eventName) => $("dropzone").addEventListener(eventName, (event) => {
  event.preventDefault();
  $("dropzone").classList.remove("is-dragging");
}));
$("dropzone").addEventListener("drop", (event) => setFiles([...event.dataTransfer.files]));

$("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!files.length) return;
  const formData = new FormData();
  files.forEach((file) => formData.append("files", file));
  show($("progress-panel"));
  $("progress-heading").textContent = "Preparing your material";
  $("progress-message").textContent = "Your documents are on their way to Miletus.";
  try {
    const response = await fetch(`${API_URL}/api/generate`, { method: "POST", body: formData });
    const job = await jsonOrError(response);
    poll(job.job_id);
  } catch (error) {
    fail(error.message);
  }
});

async function jsonOrError(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "The server could not process that request.");
  return data;
}

function poll(jobId) {
  window.clearInterval(pollTimer);
  const check = async () => {
    try {
      const job = await jsonOrError(await fetch(`${API_URL}/api/jobs/${jobId}`));
      updateProgress(job);
      if (job.status === "complete") {
        window.clearInterval(pollTimer);
        displayResult(job);
      } else if (job.status === "failed") {
        window.clearInterval(pollTimer);
        fail(job.error || "Please try again with a different document.");
      }
    } catch (error) {
      window.clearInterval(pollTimer);
      fail(error.message);
    }
  };
  check();
  pollTimer = window.setInterval(check, 1000);
}

function updateProgress(job) {
  $("progress-heading").textContent = job.status === "failed" ? "Something went wrong" : job.message;
  $("progress-message").textContent = job.status === "failed" ? job.error : "This may take a few minutes for longer notes.";
  $("progress-bar").style.width = `${job.progress || 0}%`;
  const current = progressSteps.findIndex(([status]) => status === job.status);
  $("progress-steps").replaceChildren(...progressSteps.slice(1).map(([status, label], index) => {
    const item = document.createElement("li");
    const stepIndex = index + 1;
    item.className = stepIndex < current ? "complete" : stepIndex === current ? "active" : "";
    item.innerHTML = `<span>${stepIndex < current ? "✓" : stepIndex === current ? "•" : "○"}</span>${label}`;
    return item;
  }));
}

function displayResult(job) {
  $("podcast-title").textContent = job.title || "Your Miletus episode";
  $("podcast-overview").textContent = job.overview || "";
  const audioUrl = `${API_URL}${job.audio_url}`;
  $("audio-player").src = audioUrl;
  $("download-link").href = audioUrl;
  const script = $("script-content");
  script.replaceChildren();
  (job.script?.host_texts || []).forEach((host, index) => {
    const hostLine = document.createElement("p");
    const hostLabel = document.createElement("strong");
    hostLabel.textContent = "HOST";
    hostLine.append(hostLabel, document.createTextNode(host));
    const expertLine = document.createElement("p");
    const expertLabel = document.createElement("strong");
    expertLabel.textContent = "EXPERT";
    expertLine.append(expertLabel, document.createTextNode(job.script.expert_texts[index] || ""));
    script.append(hostLine, expertLine);
  });
  show($("result-panel"));
}

function fail(message) {
  $("progress-heading").textContent = "We couldn’t make that episode.";
  $("progress-message").textContent = message || "Please try again in a moment.";
  $("progress-bar").style.width = "0%";
  $("progress-steps").replaceChildren();
}

function reset() {
  window.clearInterval(pollTimer);
  files = [];
  $("file-input").value = "";
  $("audio-player").removeAttribute("src");
  $("audio-player").load();
  renderFiles();
  show($("upload-panel"));
}

$("restart-button").addEventListener("click", reset);
$("make-another-button").addEventListener("click", reset);
