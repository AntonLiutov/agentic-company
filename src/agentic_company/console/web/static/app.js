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
    target.textContent = formatRunStatus(payload);
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

function formatRunStatus(payload) {
  const status = payload.status || "";
  const stage = payload.stage || "";
  const owner = payload.active_owner || "";
  if (!payload.running) return status;
  if (owner) return `${owner} - ${status}`;
  if (stage && stage !== status) return `${stage} - ${status}`;
  return status;
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
  const taskId = target.getAttribute("data-live-task-id");
  const query = taskId ? `?task_id=${encodeURIComponent(taskId)}` : "";
  try {
    const response = await fetch(`/api/runs/${runId}/logs${query}`);
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

function setupProviderModelSelectors() {
  document.querySelectorAll("[data-provider-select]").forEach((providerSelect) => {
    const modelSelect = byId(providerSelect.getAttribute("data-model-target"));
    if (!modelSelect) return;
    const syncModels = () => {
      const provider = providerSelect.value || "openai";
      const options = Array.from(modelSelect.querySelectorAll("option"));
      options.forEach((option) => {
        option.disabled = option.dataset.provider !== provider;
      });
      const selected = modelSelect.options[modelSelect.selectedIndex];
      if (!selected || selected.disabled) {
        const defaultModel = modelSelect.dataset[provider.replace(/_([a-z])/g, (_, letter) => letter.toUpperCase()) + "Default"];
        const next = options.find((option) => option.dataset.provider === provider && option.value === defaultModel)
          || options.find((option) => option.dataset.provider === provider);
        if (next) modelSelect.value = next.value;
      }
      document.querySelectorAll("[data-provider-note]").forEach((note) => {
        note.classList.toggle("hidden", note.dataset.providerNote !== provider);
      });
      updateNewProjectSummaries();
    };
    providerSelect.addEventListener("change", syncModels);
    modelSelect.addEventListener("change", updateNewProjectSummaries);
    syncModels();
  });
}

function selectedOptionText(selector) {
  const select = document.querySelector(selector);
  if (!select) return "";
  return select.options[select.selectedIndex]?.textContent?.trim() || select.value || "";
}

function updateNewProjectSummaries() {
  const planning = document.querySelector("[data-planning-summary]");
  if (planning) {
    const provider = selectedOptionText("[name='agent_provider']");
    const model = selectedOptionText("[name='agent_model']");
    planning.textContent = [provider, model].filter(Boolean).join(" · ");
  }

  const build = document.querySelector("[data-build-summary]");
  if (build) {
    const model = selectedOptionText("[name='codex_model']");
    const reasoning = selectedOptionText("[name='codex_reasoning']").replace(" (default)", "");
    const speed = selectedOptionText("[name='service_tier']").replace(" (default)", "");
    build.textContent = [model, reasoning, speed].filter(Boolean).join(" · ");
  }

}

function setupNewProjectSummaries() {
  ["[name='codex_model']", "[name='codex_reasoning']", "[name='service_tier']"].forEach((selector) => {
    document.querySelectorAll(selector).forEach((node) => {
      node.addEventListener("change", updateNewProjectSummaries);
      node.addEventListener("input", updateNewProjectSummaries);
    });
  });
  updateNewProjectSummaries();
}

function setupClickableCards() {
  document.querySelectorAll("[data-card-href]").forEach((card) => {
    const open = () => {
      const href = card.getAttribute("data-card-href");
      if (href) window.location.href = href;
    };
    const isInteractiveTarget = (target) => Boolean(target.closest("a, button, input, select, textarea, label, form"));
    card.addEventListener("click", (event) => {
      if (isInteractiveTarget(event.target)) return;
      open();
    });
    card.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      if (isInteractiveTarget(event.target)) return;
      event.preventDefault();
      open();
    });
  });
}

function setupLandingCarousels() {
  document.querySelectorAll("[data-carousel]").forEach((carousel) => {
    const slides = Array.from(carousel.querySelectorAll(".carousel-slide"));
    const dots = Array.from(carousel.querySelectorAll("[data-carousel-dot]"));
    if (slides.length <= 1) return;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const interval = Number(carousel.getAttribute("data-carousel-interval") || "3000");
    let index = Math.max(0, slides.findIndex((slide) => slide.classList.contains("is-active")));
    let timer = null;
    const show = (nextIndex) => {
      index = (nextIndex + slides.length) % slides.length;
      slides.forEach((slide, slideIndex) => {
        slide.classList.toggle("is-active", slideIndex === index);
      });
      dots.forEach((dot, dotIndex) => {
        dot.classList.toggle("is-active", dotIndex === index);
        dot.setAttribute("aria-current", dotIndex === index ? "true" : "false");
      });
    };
    const start = () => {
      if (reduceMotion || timer) return;
      timer = window.setInterval(() => show(index + 1), interval);
    };
    const stop = () => {
      if (!timer) return;
      window.clearInterval(timer);
      timer = null;
    };
    dots.forEach((dot) => {
      dot.addEventListener("click", () => {
        stop();
        show(Number(dot.getAttribute("data-carousel-dot") || "0"));
        start();
      });
    });
    carousel.addEventListener("mouseenter", stop);
    carousel.addEventListener("mouseleave", start);
    carousel.addEventListener("focusin", stop);
    carousel.addEventListener("focusout", start);
    show(index);
    start();
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
      const providerSelect = document.querySelector("[name='agent_provider']");
      const modelSelect = document.querySelector("[name='agent_model']");
      if (providerSelect) formData.append("agent_provider", providerSelect.value);
      if (modelSelect) formData.append("agent_model", modelSelect.value);
      button.textContent = "Formatting...";
      try {
        const response = await fetch("/api/format-request", { method: "POST", body: formData });
        const payload = await response.json();
        preview.classList.remove("hidden");
        if (!payload.ok) {
          preview.innerHTML = `
            <h3>Format with AI</h3>
            <p class="alert">${escapeHtml(payload.message || "AI formatting is unavailable right now. Your text was not changed.")}</p>
            <button class="secondary" type="button" id="keep-editing">Keep editing</button>
          `;
          byId("keep-editing").addEventListener("click", () => {
            preview.classList.add("hidden");
          });
          return;
        }
        preview.innerHTML = `
          <h3>Preview</h3>
          <div class="markdown-preview">${renderMarkdown(payload.formatted)}</div>
          <button class="primary" type="button" id="apply-format">Use this text</button>
          <button class="secondary" type="button" id="keep-editing">Keep editing</button>
        `;
        byId("apply-format").addEventListener("click", () => {
          target.value = payload.formatted;
          target.dispatchEvent(new Event("input", { bubbles: true })); // re-run Start-button gate
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
      } catch (_) {
        preview.classList.remove("hidden");
        preview.innerHTML = `
          <h3>Format with AI</h3>
          <p class="alert">Sorry, AI formatting is not reachable right now. Your text was not changed.</p>
          <button class="secondary" type="button" id="keep-editing">Keep editing</button>
        `;
        byId("keep-editing").addEventListener("click", () => {
          preview.classList.add("hidden");
        });
      } finally {
        button.textContent = "Format with AI";
      }
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
      <summary>${agentLabel(group.owner)} <span class="activity-owner-count">${group.count}</span></summary>
      <div class="log-list">
        ${group.logs.map((entry) => `<article class="log-entry">${entry}</article>`).join("")}
      </div>
    </details>
  `;
}

function agentLabel(owner) {
  const safeOwner = escapeHtml(owner);
  return `<span class="agent-label"><img src="${agentIcon(owner)}" alt="" loading="lazy">${safeOwner}</span>`;
}

function agentIcon(owner) {
  const icons = {
    "Coordinator": "coordinator.png",
    "Business Analyst": "business-analyst.png",
    "Solution Architect": "solution-architect.png",
    "Delivery Planner": "delivery-planner.png",
    "Delivery Lead": "delivery-lead.png",
    "Builder": "builder.png",
    "Quality Reviewer": "quality-reviewer.png",
    "Publisher": "publisher.png",
    "Release Reporter": "release-reporter.png",
  };
  return `/static/agents/${icons[owner] || "coordinator.png"}`;
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

function setupRepoDelivery() {
  const delivSegs = document.querySelectorAll("[data-deliver-mode]");
  const delivInput = document.querySelector("[data-deliver-input]");
  if (!delivSegs.length || !delivInput) return;
  const githubPanel = document.querySelector('[data-deliver-panel="github"]');
  const summary = document.querySelector("[data-deliver-summary]");
  const loggedIn = Boolean(githubPanel && githubPanel.dataset.loggedIn === "true");
  const repoSegs = document.querySelectorAll("[data-repo-mode]");
  const repoInput = document.querySelector("[data-repo-input]");
  const panelExisting = document.querySelector('[data-repo-panel="existing"]');
  const panelNew = document.querySelector('[data-repo-panel="new"]');
  const existingSel = document.querySelector("[data-repo-existing]");
  const newInput = document.querySelector("[data-repo-new]");
  const startBtn = document.querySelector("[data-start-btn]");
  const nameField = document.querySelector("[name='name']");
  const requestField = document.getElementById("request-text");
  const MIN_REQUEST_CHARS = 30; // minimum requirements length before a project can start

  function validate() {
    if (!startBtn) return;
    const keyOk = startBtn.dataset.keyOk === "true";
    let repoOk = true;
    if (delivInput.value === "github" && loggedIn && repoInput) {
      repoOk = repoInput.value === "new"
        ? Boolean(newInput && newInput.value.trim())
        : Boolean(existingSel && existingSel.value);
    }
    const nameOk = Boolean(nameField && nameField.value.trim());
    const reqLen = requestField ? requestField.value.trim().length : 0;
    const reqOk = reqLen >= MIN_REQUEST_CHARS;
    startBtn.disabled = !(keyOk && repoOk && nameOk && reqOk);
    if (startBtn.disabled) {
      startBtn.title = !keyOk
        ? (startBtn.dataset.blocker || "Connect a provider and Codex in Settings first.")
        : !nameOk
        ? "Give the project a name first."
        : !reqOk
        ? `Describe the requirements — at least ${MIN_REQUEST_CHARS} characters (${reqLen}/${MIN_REQUEST_CHARS}).`
        : !repoOk
        ? "Pick or name a GitHub repository first."
        : "";
    } else {
      startBtn.title = "";
    }
  }
  function setRepoMode(mode) {
    if (repoInput) repoInput.value = mode;
    repoSegs.forEach((b) => b.classList.toggle("active", b.dataset.repoMode === mode));
    if (panelExisting) panelExisting.hidden = mode !== "existing";
    if (panelNew) panelNew.hidden = mode !== "new";
    if (mode !== "existing" && existingSel) existingSel.value = "";
    if (mode !== "new" && newInput) newInput.value = "";
    validate();
  }
  function setDeliverMode(mode) {
    delivInput.value = mode;
    delivSegs.forEach((b) => b.classList.toggle("active", b.dataset.deliverMode === mode));
    if (githubPanel) githubPanel.hidden = mode !== "github";
    if (summary) summary.textContent = mode === "github" ? "GitHub" : "Local";
    if (mode !== "github") {
      if (existingSel) existingSel.value = "";
      if (newInput) newInput.value = "";
    }
    validate();
  }
  const visSegs = document.querySelectorAll("[data-vis-mode]");
  const visInput = document.querySelector("[data-vis-input]");
  visSegs.forEach((b) =>
    b.addEventListener("click", () => {
      if (visInput) visInput.value = b.dataset.visMode === "private" ? "1" : "";
      visSegs.forEach((x) => x.classList.toggle("active", x === b));
    })
  );
  delivSegs.forEach((b) => b.addEventListener("click", () => setDeliverMode(b.dataset.deliverMode)));
  repoSegs.forEach((b) => b.addEventListener("click", () => setRepoMode(b.dataset.repoMode)));
  if (existingSel) existingSel.addEventListener("change", validate);
  if (newInput) newInput.addEventListener("input", validate);
  if (nameField) nameField.addEventListener("input", validate);
  if (requestField) requestField.addEventListener("input", validate);
  if (repoSegs.length) setRepoMode("existing");
  setDeliverMode("local");
}

setupSidebarToggle();
setupRepoDelivery();
setupScrollTop();
setupProviderModelSelectors();
setupNewProjectSummaries();
setupClickableCards();
setupLandingCarousels();
setupFormatButtons();
setupEditButtons();
setupConfirmForms();
setupPersistentDetails();
setupActivityScrollMemory();
updateDurationPills();
setInterval(updateDurationPills, 1000);
pollRunStatus();
pollRunLogs();
