// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------

const queryInput = document.getElementById("query-input");
const runBtn = document.getElementById("run-btn");
const iterationsSelect = document.getElementById("iterations");
const inlineError = document.getElementById("inline-error");
const connStatus = document.getElementById("conn-status");
const exampleChips = document.getElementById("example-chips");

const stepper = document.getElementById("stepper");
const stepEls = {
  plan: stepper.querySelector('[data-stage="plan"]'),
  search: stepper.querySelector('[data-stage="search"]'),
  evaluate: stepper.querySelector('[data-stage="evaluate"]'),
  synthesize: stepper.querySelector('[data-stage="synthesize"]'),
};
const runMeta = document.getElementById("run-meta");
const roundCountEl = document.getElementById("round-count");
const sourceCountEl = document.getElementById("source-count");

const tabReport = document.getElementById("tab-report");
const tabSources = document.getElementById("tab-sources");
const tabSourceCount = document.getElementById("tab-source-count");
const panelReport = document.getElementById("panel-report");
const panelSources = document.getElementById("panel-sources");

const reportEmpty = document.getElementById("report-empty");
const reportSkeleton = document.getElementById("report-skeleton");
const reportEl = document.getElementById("report");
const reportTitle = document.getElementById("report-title");
const reportSummary = document.getElementById("report-summary");
const reportSections = document.getElementById("report-sections");
const copyBtn = document.getElementById("copy-btn");
const downloadBtn = document.getElementById("download-btn");

const sourcesEmpty = document.getElementById("sources-empty");
const sourceGrid = document.getElementById("source-grid");

// ---------------------------------------------------------------------------
// Run state
// ---------------------------------------------------------------------------

let abortController = null;
let searchRounds = 0;
let collectedSources = new Map(); // url -> {title, url, snippet, query}
let currentReportMarkdown = "";

const STAGE_ORDER = ["plan", "search", "evaluate", "synthesize"];

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

function domainOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function autosizeTextarea() {
  queryInput.style.height = "auto";
  queryInput.style.height = Math.min(queryInput.scrollHeight, 160) + "px";
}

function setConnState(state, label) {
  connStatus.dataset.state = state;
  connStatus.querySelector(".conn-label").textContent = label;
}

function showInlineError(message) {
  inlineError.textContent = message;
  inlineError.classList.remove("hidden");
}

function clearInlineError() {
  inlineError.classList.add("hidden");
  inlineError.textContent = "";
}

// ---------------------------------------------------------------------------
// Stepper control
// ---------------------------------------------------------------------------

function resetStepper() {
  STAGE_ORDER.forEach((stage) => {
    const el = stepEls[stage];
    el.dataset.status = "waiting";
    el.querySelector(".step-status").textContent = "Waiting";
    el.querySelector(".step-detail").innerHTML = "";
  });
  runMeta.hidden = true;
  searchRounds = 0;
}

function setStageStatus(stage, status, label) {
  const el = stepEls[stage];
  if (!el) return;
  el.dataset.status = status;
  el.querySelector(".step-status").textContent = label;
}

function addStageDetail(stage, text) {
  const el = stepEls[stage];
  if (!el) return;
  const detail = el.querySelector(".step-detail");
  const line = document.createElement("div");
  line.className = "detail-line";
  line.textContent = text;
  detail.appendChild(line);
  // keep the detail list from growing unbounded on stages that loop
  while (detail.children.length > 6) {
    detail.removeChild(detail.firstChild);
  }
}

function markPriorStagesDone(uptoStage) {
  const idx = STAGE_ORDER.indexOf(uptoStage);
  for (let i = 0; i < idx; i++) {
    const el = stepEls[STAGE_ORDER[i]];
    if (el.dataset.status !== "done") {
      el.dataset.status = "done";
      el.querySelector(".step-status").textContent = "Done";
    }
  }
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

function activateTab(name) {
  const isReport = name === "report";
  tabReport.classList.toggle("active", isReport);
  tabSources.classList.toggle("active", !isReport);
  tabReport.setAttribute("aria-selected", String(isReport));
  tabSources.setAttribute("aria-selected", String(!isReport));
  panelReport.classList.toggle("hidden", !isReport);
  panelSources.classList.toggle("hidden", isReport);
}

tabReport.addEventListener("click", () => activateTab("report"));
tabSources.addEventListener("click", () => activateTab("sources"));

// ---------------------------------------------------------------------------
// Sources tab rendering
// ---------------------------------------------------------------------------

function addSources(results) {
  results.forEach((r) => {
    if (!r.url || collectedSources.has(r.url)) return;
    collectedSources.set(r.url, r);
  });

  sourceCountEl.textContent = `${collectedSources.size} source${collectedSources.size === 1 ? "" : "s"}`;
  tabSourceCount.textContent = String(collectedSources.size);

  if (collectedSources.size === 0) return;
  sourcesEmpty.classList.add("hidden");
  sourceGrid.classList.remove("hidden");

  sourceGrid.innerHTML = "";
  for (const r of collectedSources.values()) {
    const li = document.createElement("li");
    li.className = "source-card";
    li.innerHTML = `
      <a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.title || r.url)}</a>
      <span class="domain">${escapeHtml(domainOf(r.url))}</span>
      <p class="snippet">${escapeHtml((r.snippet || "").slice(0, 160))}${(r.snippet || "").length > 160 ? "…" : ""}</p>
      <div class="from-query">from: “${escapeHtml(r.query || "")}”</div>
    `;
    sourceGrid.appendChild(li);
  }
}

// ---------------------------------------------------------------------------
// Report rendering
// ---------------------------------------------------------------------------

function reportToMarkdown(data) {
  let md = `# ${data.title || "Untitled report"}\n\n`;
  if (data.summary) md += `_${data.summary}_\n\n`;
  (data.sections || []).forEach((s) => {
    md += `## ${s.heading}\n\n${s.content}\n\n`;
    if (s.source_urls && s.source_urls.length) {
      s.source_urls.forEach((u) => (md += `- ${u}\n`));
      md += "\n";
    }
  });
  return md;
}

function renderReport(data) {
  reportSkeleton.classList.add("hidden");
  reportEmpty.classList.add("hidden");
  reportEl.classList.remove("hidden");

  reportTitle.textContent = data.title || "Untitled report";
  reportSummary.textContent = data.summary || "";
  reportSections.innerHTML = "";

  (data.sections || []).forEach((section) => {
    const wrap = document.createElement("div");
    wrap.className = "report-section";

    const h3 = document.createElement("h3");
    h3.textContent = section.heading;
    wrap.appendChild(h3);

    const p = document.createElement("p");
    p.textContent = section.content;
    wrap.appendChild(p);

    if (section.source_urls && section.source_urls.length) {
      const tagWrap = document.createElement("div");
      tagWrap.className = "source-tags";
      section.source_urls.forEach((url) => {
        const a = document.createElement("a");
        a.className = "source-tag";
        a.href = url;
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = domainOf(url);
        tagWrap.appendChild(a);
      });
      wrap.appendChild(tagWrap);
    }

    reportSections.appendChild(wrap);
  });

  currentReportMarkdown = reportToMarkdown(data);
}

copyBtn.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(currentReportMarkdown);
    copyBtn.textContent = "Copied";
    copyBtn.classList.add("copied");
    setTimeout(() => {
      copyBtn.textContent = "Copy";
      copyBtn.classList.remove("copied");
    }, 1500);
  } catch {
    showInlineError("Couldn't copy to clipboard — your browser may have blocked it.");
  }
});

downloadBtn.addEventListener("click", () => {
  const blob = new Blob([currentReportMarkdown], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "research-report.md";
  a.click();
  URL.revokeObjectURL(url);
});

// ---------------------------------------------------------------------------
// Event handling from the SSE stream
// ---------------------------------------------------------------------------

function handleEvent(event) {
  switch (event.type) {
    case "status": {
      const stage = event.node;
      if (!stage || !STAGE_ORDER.includes(stage)) break;
      markPriorStagesDone(stage);
      setStageStatus(stage, "active", "Working…");
      addStageDetail(stage, event.message);
      break;
    }
    case "plan": {
      setStageStatus("plan", "done", "Done");
      addStageDetail("plan", `${event.queries.length} queries planned`);
      break;
    }
    case "search": {
      searchRounds += 1;
      runMeta.hidden = false;
      roundCountEl.textContent = `Round ${searchRounds}`;
      setStageStatus("search", "active", `Round ${searchRounds}`);
      addSources(event.results || []);
      break;
    }
    case "report": {
      markPriorStagesDone("synthesize");
      setStageStatus("synthesize", "done", "Done");
      renderReport(event.report);
      break;
    }
    case "error": {
      const active = STAGE_ORDER.find((s) => stepEls[s].dataset.status === "active") || "synthesize";
      setStageStatus(active, "error", "Failed");
      setConnState("error", "Failed");
      showInlineError(event.message || "Something went wrong while researching.");
      break;
    }
    case "done": {
      STAGE_ORDER.forEach((s) => {
        if (stepEls[s].dataset.status !== "error") {
          stepEls[s].dataset.status = "done";
          stepEls[s].querySelector(".step-status").textContent = "Done";
        }
      });
      setConnState("done", "Complete");
      break;
    }
    default:
      break;
  }
}

// ---------------------------------------------------------------------------
// Run / abort
// ---------------------------------------------------------------------------

async function runResearch() {
  const query = queryInput.value.trim();
  if (!query) {
    queryInput.focus();
    return;
  }

  clearInlineError();
  resetStepper();
  collectedSources = new Map();
  sourceGrid.innerHTML = "";
  sourceGrid.classList.add("hidden");
  sourcesEmpty.classList.remove("hidden");
  sourceCountEl.textContent = "0 sources";
  tabSourceCount.textContent = "0";

  reportEl.classList.add("hidden");
  reportEmpty.classList.add("hidden");
  reportSkeleton.classList.remove("hidden");
  activateTab("report");

  runBtn.disabled = true;
  runBtn.querySelector(".btn-label").textContent = "Running…";
  setConnState("running", "Running");
  setStageStatus("plan", "active", "Working…");

  abortController = new AbortController();

  try {
    const response = await fetch("/api/research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        max_iterations: Number(iterationsSelect.value),
      }),
      signal: abortController.signal,
    });

    if (!response.ok || !response.body) {
      throw new Error(`Server returned ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const frames = buffer.split("\n\n");
      buffer = frames.pop();

      for (const frame of frames) {
        const line = frame.trim();
        if (!line.startsWith("data:")) continue;
        const jsonStr = line.slice(5).trim();
        if (!jsonStr) continue;
        try {
          handleEvent(JSON.parse(jsonStr));
        } catch {
          /* ignore malformed frame */
        }
      }
    }
  } catch (err) {
    if (err.name !== "AbortError") {
      setConnState("error", "Failed");
      showInlineError(err.message || "Something went wrong.");
      reportSkeleton.classList.add("hidden");
      reportEmpty.classList.remove("hidden");
    }
  } finally {
    runBtn.disabled = false;
    runBtn.querySelector(".btn-label").textContent = "Run research";
    abortController = null;
  }
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

runBtn.addEventListener("click", runResearch);

queryInput.addEventListener("input", autosizeTextarea);
queryInput.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
    e.preventDefault();
    runResearch();
  }
});

exampleChips.addEventListener("click", (e) => {
  const btn = e.target.closest(".chip");
  if (!btn) return;
  queryInput.value = btn.dataset.query;
  autosizeTextarea();
  queryInput.focus();
});

autosizeTextarea();
