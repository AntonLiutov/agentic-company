let lastRunEventCount = null;

function byId(id) {
  return document.getElementById(id);
}

async function pollRunStatus() {
  const head = document.querySelector("[data-run-id]");
  const target = byId("live-run-status");
  if (!head || !target) return;
  const runId = head.getAttribute("data-run-id");
  try {
    const response = await fetch(`/api/runs/${runId}/status`);
    if (!response.ok) return;
    const payload = await response.json();
    target.textContent = payload.running ? `${payload.stage} - ${payload.status}` : payload.status;
    if (
      lastRunEventCount !== null &&
      payload.events !== lastRunEventCount &&
      shouldRefreshWorkspaceFragments()
    ) {
      refreshWorkspaceFragments();
    }
    lastRunEventCount = payload.events;
    if (payload.running) {
      setTimeout(pollRunStatus, 3000);
    }
  } catch {
    setTimeout(pollRunStatus, 5000);
  }
}

function shouldRefreshWorkspaceFragments() {
  return Boolean(document.querySelector("[data-live-fragment]"));
}

async function refreshWorkspaceFragments() {
  try {
    const response = await fetch(window.location.href, { headers: { "X-Requested-With": "fetch" } });
    if (!response.ok) return;
    const html = await response.text();
    const nextDocument = new DOMParser().parseFromString(html, "text/html");
    document.querySelectorAll("[data-live-fragment]").forEach((current) => {
      const name = current.getAttribute("data-live-fragment");
      const next = Array.from(nextDocument.querySelectorAll("[data-live-fragment]"))
        .find((node) => node.getAttribute("data-live-fragment") === name);
      if (next) {
        current.replaceWith(next);
      }
    });
    setupPersistentDetails();
    updateDurationPills();
  } catch {
    // Keep the current workspace visible if a lightweight fragment refresh fails.
  }
}

async function pollRunLogs() {
  const target = document.querySelector("[data-live-logs-run]");
  if (!target) return;
  const runId = target.getAttribute("data-live-logs-run");
  try {
    const response = await fetch(`/api/runs/${runId}/logs`);
    if (!response.ok) return;
    const payload = await response.json();
    if (payload.groups?.length) {
      rememberScrollContainers();
      target.innerHTML = payload.groups.map(renderActivityGroup).join("");
      setupPersistentDetails();
      restoreScrollContainers();
    } else if (payload.logs?.length) {
      rememberScrollContainers();
      target.innerHTML = payload.logs
        .map((entry) => `<article class="log-entry">${entry}</article>`)
        .join("");
      restoreScrollContainers();
    }
  } catch {
    // Keep the existing log list visible if the lightweight refresh fails.
  } finally {
    setTimeout(pollRunLogs, 4000);
  }
}

function scrollStorageKey(node, index) {
  const owner = node.closest("[data-detail-key]")?.getAttribute("data-detail-key");
  return `agenticScroll:${window.location.pathname}:${owner || "log-list"}:${index}`;
}

function setupActivityScrollMemory() {
  document.querySelectorAll(".log-list").forEach((node, index) => {
    if (node.dataset.scrollBound === "true") return;
    node.dataset.scrollBound = "true";
    const key = scrollStorageKey(node, index);
    const saved = sessionStorage.getItem(key);
    requestAnimationFrame(() => {
      if (saved !== null) {
        node.scrollTop = Number(saved);
      } else {
        node.scrollTop = node.scrollHeight;
      }
    });
    node.addEventListener("scroll", () => {
      sessionStorage.setItem(key, String(node.scrollTop));
    });
  });
}

function rememberScrollContainers() {
  document.querySelectorAll(".log-list").forEach((node, index) => {
    sessionStorage.setItem(scrollStorageKey(node, index), String(node.scrollTop));
  });
}

function restoreScrollContainers() {
  setupActivityScrollMemory();
}

function formatDuration(seconds) {
  const safe = Math.max(0, Math.floor(seconds));
  if (safe < 60) return `${safe}s`;
  const minutes = Math.floor(safe / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder ? `${hours}h ${remainder}m` : `${hours}h`;
}

function updateDurationPills() {
  const now = Date.now();
  document.querySelectorAll("[data-duration-start]").forEach((node) => {
    const start = Date.parse(node.getAttribute("data-duration-start"));
    if (Number.isNaN(start)) return;
    const endValue = node.getAttribute("data-duration-end");
    const end = endValue ? Date.parse(endValue) : now;
    node.textContent = formatDuration((end - start) / 1000);
  });
}

function setupSpeechChecks() {
  const speech = window.SpeechRecognition || window.webkitSpeechRecognition;
  document.querySelectorAll("[data-speech-check]").forEach((node) => {
    const dot = node.querySelector(".status-dot");
    if (dot) dot.classList.add(speech ? "ok" : "bad");
  });
}

const voiceSessions = new Map();

function setupVoiceButtons() {
  const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
  document.querySelectorAll("[data-voice-target]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = byId(button.getAttribute("data-voice-target"));
      const preview = document.querySelector("[data-voice-preview]");
      if (!Speech || !target) {
        alert("Voice input is not supported by this browser. Please type the request.");
        return;
      }
      const active = voiceSessions.get(button);
      if (active) {
        active.manualStop = true;
        active.recognition.stop();
        voiceSessions.delete(button);
        button.textContent = "Start dictation";
        if (preview) preview.classList.add("hidden");
        return;
      }

      const recognition = new Speech();
      recognition.lang = "en-US";
      recognition.continuous = true;
      recognition.interimResults = true;
      const session = { recognition, manualStop: false };
      voiceSessions.set(button, session);
      button.textContent = "Stop dictation";
      if (preview) {
        preview.classList.remove("hidden");
        preview.textContent = "Listening...";
      }
      recognition.onresult = (event) => {
        const finalParts = [];
        const interimParts = [];
        for (let index = event.resultIndex; index < event.results.length; index += 1) {
          const transcript = event.results[index][0].transcript.trim();
          if (!transcript) continue;
          if (event.results[index].isFinal) {
            finalParts.push(transcript);
          } else {
            interimParts.push(transcript);
          }
        }
        if (finalParts.length) {
          target.value = `${target.value} ${finalParts.join(" ")}`.replace(/\s+/g, " ").trim();
        }
        if (preview) {
          preview.textContent = interimParts.length
            ? `Listening: ${interimParts.join(" ")}`
            : "Listening...";
        }
      };
      recognition.onend = () => {
        if (!session.manualStop) {
          try {
            recognition.start();
            return;
          } catch {
            // Browser refused auto-restart; fall through to stopped state.
          }
        }
        voiceSessions.delete(button);
        button.textContent = "Start dictation";
        if (preview) preview.classList.add("hidden");
      };
      recognition.start();
    });
  });
}

function setupFormatButtons() {
  document.querySelectorAll("[data-format-target]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = byId(button.getAttribute("data-format-target"));
      const preview = byId("format-preview");
      const formattedView = byId("formatted-request-view");
      if (!target || !preview) return;
      const formData = new FormData();
      formData.append("text", target.value);
      button.textContent = "Formatting...";
      const response = await fetch("/api/format-request", { method: "POST", body: formData });
      const payload = await response.json();
      preview.classList.remove("hidden");
      preview.innerHTML = `
        <h3>Preview</h3>
        <div class="markdown-preview">${renderMarkdown(payload.formatted)}</div>
        <button class="primary" type="button" id="apply-format">Use this text</button>
        <button class="secondary" type="button" id="keep-editing">Keep editing</button>
      `;
      byId("apply-format").addEventListener("click", () => {
        target.value = payload.formatted;
        target.classList.add("hidden");
        if (formattedView) {
          formattedView.classList.remove("hidden");
          formattedView.innerHTML = renderMarkdown(payload.formatted);
        }
        document.querySelectorAll("[data-edit-target]").forEach((editButton) => {
          editButton.classList.remove("hidden");
        });
        preview.classList.add("hidden");
      });
      byId("keep-editing").addEventListener("click", () => {
        preview.classList.add("hidden");
      });
      button.textContent = "Format with AI";
    });
  });
}

function setupSidebarToggle() {
  const button = document.querySelector("[data-sidebar-toggle]");
  const collapsed = localStorage.getItem("agenticSidebarCollapsed") === "true";
  document.body.classList.toggle("sidebar-collapsed", collapsed);
  if (button) {
    button.textContent = collapsed ? "Open" : "Menu";
    button.addEventListener("click", () => {
      const next = !document.body.classList.contains("sidebar-collapsed");
      document.body.classList.toggle("sidebar-collapsed", next);
      localStorage.setItem("agenticSidebarCollapsed", String(next));
      button.textContent = next ? "Open" : "Menu";
    });
  }
}

function setupScrollTop() {
  document.querySelectorAll("[data-scroll-top]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      const options = { top: 0, left: 0, behavior: "smooth" };
      window.scrollTo(options);
      document.scrollingElement?.scrollTo(options);
      document.documentElement.scrollTop = 0;
      document.body.scrollTop = 0;
      document.querySelectorAll(".app-main, .board-wrap, .log-list, .table-scroll, .html-preview, .mermaid-frame")
        .forEach((node) => {
          if (typeof node.scrollTo === "function") {
            node.scrollTo(options);
          } else {
            node.scrollTop = 0;
            node.scrollLeft = 0;
          }
        });
    });
  });
}

function setupEditButtons() {
  document.querySelectorAll("[data-edit-target]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = byId(button.getAttribute("data-edit-target"));
      const formattedView = byId("formatted-request-view");
      if (target) target.classList.remove("hidden");
      if (formattedView) formattedView.classList.add("hidden");
      button.classList.add("hidden");
      target?.focus();
    });
  });
}

function setupConfirmForms() {
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    if (form.dataset.confirmBound === "true") return;
    form.dataset.confirmBound = "true";
    form.addEventListener("submit", (event) => {
      const message = form.getAttribute("data-confirm") || "Are you sure?";
      if (!window.confirm(message)) {
        event.preventDefault();
      }
    });
  });
}

function setupPersistentDetails() {
  document.querySelectorAll("details").forEach((node, index) => {
    if (node.dataset.detailsBound === "true") return;
    node.dataset.detailsBound = "true";
    const summaryNode = node.querySelector("summary");
    const summary =
      node.getAttribute("data-detail-key") ||
      summaryNode?.childNodes?.[0]?.textContent?.trim() ||
      summaryNode?.textContent?.trim() ||
      `details-${index}`;
    const key = `agenticDetails:${window.location.pathname}:${summary}`;
    const saved = localStorage.getItem(key);
    if (saved !== null) {
      node.open = saved === "true";
    }
    node.addEventListener("toggle", () => {
      localStorage.setItem(key, String(node.open));
    });
  });
}

function renderActivityGroup(group) {
  return `
    <details class="activity-owner-group" data-detail-key="activity-${escapeHtml(group.owner)}" open>
      <summary>${escapeHtml(group.owner)} <span>${group.count}</span></summary>
      <div class="log-list">
        ${group.logs.map((entry) => `<article class="log-entry">${entry}</article>`).join("")}
      </div>
    </details>
  `;
}

function renderMarkdown(markdown) {
  const escaped = escapeHtml(markdown);
  const withInlineCode = escaped.replace(/`([^`]+)`/g, "<code>$1</code>");
  const lines = withInlineCode.split(/\r?\n/);
  let html = "";
  let inList = false;
  lines.forEach((line) => {
    if (line.startsWith("# ")) {
      if (inList) { html += "</ul>"; inList = false; }
      html += `<h1>${line.slice(2)}</h1>`;
    } else if (line.startsWith("## ")) {
      if (inList) { html += "</ul>"; inList = false; }
      html += `<h2>${line.slice(3)}</h2>`;
    } else if (line.startsWith("- ")) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${line.slice(2)}</li>`;
    } else if (line.startsWith("&gt; ")) {
      if (inList) { html += "</ul>"; inList = false; }
      html += `<blockquote>${line.slice(5)}</blockquote>`;
    } else if (line.trim()) {
      if (inList) { html += "</ul>"; inList = false; }
      html += `<p>${line}</p>`;
    }
  });
  if (inList) html += "</ul>";
  return html;
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (match) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[match]));
}

setupSpeechChecks();
setupSidebarToggle();
setupScrollTop();
setupVoiceButtons();
setupFormatButtons();
setupEditButtons();
setupConfirmForms();
setupPersistentDetails();
setupActivityScrollMemory();
updateDurationPills();
setInterval(updateDurationPills, 1000);
pollRunStatus();
pollRunLogs();
