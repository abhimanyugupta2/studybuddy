// StudyBuddy terminal: talks to the FastAPI backend in backend/server.py.
//
// Every chat (a "session") runs on its own: you can ask in one, switch to another or start a
// new one while the first keeps answering in the background. Each session has its own
// messages and its own running request; the page only draws the session you're looking at.
const $ = (id) => document.getElementById(id);
const SUPPORTED = /\.(pdf|png|jpe?g|tiff?|bmp)$/i;   // same list as ingest.SUPPORTED
const SHOWN_SOURCES = 4;                              // the rest fold away behind "+N more"
const SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏";
const messagesEl = $("messages");
const questionEl = $("question");

const state = {
  docs: [],              // [{name, pages, chunks}]
  selected: new Set(),   // ticked document names
  sessions: new Map(),   // id -> {id, messages, controller}; controller != null while answering
  active: null,          // the session on screen
  unseen: new Set(),     // sessions that finished in the background
  health: null,
};

// ---------- Small helpers ----------
const store = {          // per-browser preferences; the page works fine if storage is blocked
  get(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ } },
};
const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
const fileUrl = (name) => `/api/documents/${encodeURIComponent(name)}/file`;
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};
const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const isRunning = (s) => !!s?.controller;
const anyRunning = () => [...state.sessions.values()].some(isRunning);

let toastTimer;
function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 3200);
}

// A tiny, safe Markdown renderer: paragraphs, lists, headings, **bold**, `code`, Evidence lines.
function markdown(text) {
  const inline = (s) => escapeHtml(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
  let html = "", list = null, para = [];
  const flushPara = () => { if (para.length) html += `<p>${para.map(inline).join("<br>")}</p>`; para = []; };
  const closeList = () => { if (list) html += `</${list}>`; list = null; };
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    const bullet = line.match(/^[-*•]\s+(.*)/), numbered = line.match(/^\d+[.)]\s+(.*)/), heading = line.match(/^#{1,4}\s+(.*)/);
    if (bullet || numbered) {
      flushPara();
      const type = bullet ? "ul" : "ol";
      if (list !== type) { closeList(); html += `<${type}>`; list = type; }
      html += `<li>${inline((bullet || numbered)[1])}</li>`;
    } else if (heading) {
      flushPara(); closeList(); html += `<h4>${inline(heading[1])}</h4>`;
    } else if (/^evidence:/i.test(line)) {        // the quote an answer is based on
      flushPara(); closeList();
      html += `<p class="evidence">${inline(line.replace(/^evidence:\s*/i, ""))}</p>`;
    } else if (!line) {
      flushPara(); closeList();
    } else {
      closeList(); para.push(line);
    }
  }
  flushPara(); closeList();
  return html;
}

// Reads a streamed NDJSON response and calls onEvent for each event.
async function streamEvents(url, body, onEvent, signal) {
  const res = await fetch(url, {
    method: "POST", signal,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines.filter(Boolean)) {
      const event = JSON.parse(line);
      if (event.type === "error") throw new Error(event.message);
      onEvent(event);
    }
  }
}

// ---------- Matrix rain (background) ----------
const matrix = { speed: 1 };
(function startMatrix() {
  const canvas = $("matrix"), ctx = canvas.getContext("2d");
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const glyphs = "アイウエオカキクケコサシスセソタチツテトナニヌネノ0123456789ABCDEF<>/{}[]$#";
  const size = 16;
  let drops = [];
  const resize = () => {
    canvas.width = innerWidth; canvas.height = innerHeight;
    drops = Array.from({ length: Math.ceil(innerWidth / size) }, () => Math.random() * -50);
  };
  resize();
  addEventListener("resize", resize);
  let last = 0;
  const frame = (t) => {
    requestAnimationFrame(frame);
    if (document.hidden || t - last < 55 / matrix.speed) return;
    last = t;
    ctx.fillStyle = "rgba(4, 7, 10, 0.12)";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.font = `${size}px monospace`;
    drops.forEach((y, i) => {
      ctx.fillStyle = Math.random() > 0.975 ? "#d9ffe9" : "#33ff99";
      ctx.fillText(glyphs[(Math.random() * glyphs.length) | 0], i * size, y * size);
      drops[i] = y * size > canvas.height && Math.random() > 0.975 ? 0 : y + 1;
    });
  };
  requestAnimationFrame(frame);
})();
// the rain speeds up while any session is working
setInterval(() => { matrix.speed = anyRunning() ? 2.6 : 1; }, 400);

// ---------- Health line ----------
async function checkHealth() {
  try { state.health = await (await fetch("/api/health")).json(); } catch { state.health = { ollama: false, models: [] }; }
  const line = $("sysline");
  if (state.health.ollama) {
    const model = state.health.models.find((m) => m.startsWith("llama")) || state.health.models[0] || "?";
    line.textContent = `● llm link: online · ${model}`;
    line.className = "sysline online";
  } else {
    line.textContent = "✕ llm link: OFFLINE · start Ollama";
    line.className = "sysline offline";
  }
}
setInterval(checkHealth, 30000);

// ---------- Boot sequence (once per browser session) ----------
async function boot() {
  let shown = false;
  try { shown = sessionStorage.getItem("booted"); sessionStorage.setItem("booted", "1"); } catch { /* ignore */ }
  if (shown || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const screen = $("boot"), log = $("boot-log");
  screen.hidden = false;
  let skip = false;
  const stop = () => { skip = true; };
  addEventListener("keydown", stop, { once: true });
  screen.addEventListener("click", stop, { once: true });

  const chunks = state.docs.reduce((n, d) => n + d.chunks, 0);
  const model = state.health?.models.find((m) => m.startsWith("llama")) || "llama3.1:8b";
  const sessions = (await fetch("/api/chats").then((r) => r.json()).catch(() => [])).length;
  const lines = [
    ["big", "STUDYBUDDY OS v2.0"],
    ["dim", "local study intelligence · nothing leaves this machine"],
    ["", ""],
    [state.docs.length ? "ok" : "fail", `[ ${state.docs.length ? " OK " : "WARN"} ] mounting /data ................ ${state.docs.length} document(s)`],
    ["ok", `[  OK  ] loading vector index ........ ${chunks} chunks · 768-dim`],
    ["ok", "[  OK  ] search engine ............... BM25 + embeddings + HyDE"],
    [state.health?.ollama ? "ok" : "fail",
     state.health?.ollama ? `[  OK  ] llm link .................... ${model} @ localhost:11434`
                          : "[ FAIL ] llm link .................... ollama offline"],
    ["ok", `[  OK  ] sessions restored ........... ${sessions}`],
    ["", ""],
    ["big", "> access granted. welcome back, operator."],
  ];
  for (const [cls, text] of lines) {
    const span = el("span", cls);
    log.appendChild(span);
    for (let i = 0; i < text.length && !skip; i += 3) {   // 3 characters per frame: ~2.5s in all
      span.textContent = text.slice(0, i + 3);
      await sleep(8);
    }
    span.textContent = text;
    log.appendChild(document.createTextNode("\n"));
    if (!skip) await sleep(70);
  }
  if (!skip) await sleep(400);
  screen.classList.add("fade");
  await sleep(350);
  screen.hidden = true;
  removeEventListener("keydown", stop);
  questionEl.focus();
}

// ---------- Library (with tick boxes) ----------
async function loadDocuments() {
  state.docs = await (await fetch("/api/documents")).json();
  const names = state.docs.map((d) => d.name);
  const saved = store.get("selection", null);           // {selected: [...], known: [...]}
  if (!saved) {
    state.selected = new Set(names);                    // first visit: everything ticked
  } else {
    const known = new Set(saved.known);
    // keep previous choices, and tick documents added since last time
    state.selected = new Set(names.filter((n) => saved.selected.includes(n) || !known.has(n)));
  }
  saveSelection();
  renderDocuments();
}

function saveSelection() {
  store.set("selection", { selected: [...state.selected], known: state.docs.map((d) => d.name) });
}

function renderDocuments() {
  const list = $("documents");
  list.innerHTML = "";
  if (!state.docs.length) list.innerHTML = '<li class="empty">no documents yet. upload one above</li>';
  for (const d of state.docs) {
    const li = el("li", state.selected.has(d.name) ? "" : "off");
    const box = el("input");
    box.type = "checkbox";
    box.checked = state.selected.has(d.name);
    box.title = "Use this document when answering";
    box.setAttribute("aria-label", `Use ${d.name}`);
    box.onchange = () => {
      box.checked ? state.selected.add(d.name) : state.selected.delete(d.name);
      saveSelection();
      renderDocuments();
    };
    const name = el("span", "name", d.name);
    name.title = d.name;
    name.onclick = () => { box.checked = !box.checked; box.onchange(); };
    const actions = el("span", "actions");
    actions.append(
      iconButton("sum", "One-click summary", () => summarize(d.name)),
      iconButton("view", "Open document", () => openViewer(d.name, 1)),
      iconButton("x", "Remove from library", () => deleteDocument(d.name), "delete"),
    );
    li.append(box, name, el("span", "meta", `${d.pages}p`), actions);
    list.appendChild(li);
  }
  const all = state.docs.length && state.selected.size === state.docs.length;
  $("toggle-all").textContent = all ? "[none]" : "[all]";
  updateScope();
}

function iconButton(text, title, onClick, extra = "") {
  const b = el("button", `icon ${extra}`, text);
  b.type = "button";
  b.title = title;
  b.onclick = (e) => { e.stopPropagation(); onClick(); };
  return b;
}

function updateScope() {
  const n = state.selected.size, total = state.docs.length;
  const scope = $("scope");
  scope.classList.toggle("warn", total > 0 && n === 0);
  if (!total) scope.textContent = "> no documents mounted. upload one to begin";
  else if (n === 0) scope.textContent = "> no documents ticked. tick at least one in // LIBRARY";
  else scope.innerHTML = `> search scope: <b>${n}/${total}</b> document${total > 1 ? "s" : ""}` +
    (n < total ? ` · ${escapeHtml([...state.selected].join(", "))}` : "");
}

$("toggle-all").onclick = () => {
  state.selected = state.selected.size === state.docs.length ? new Set() : new Set(state.docs.map((d) => d.name));
  saveSelection();
  renderDocuments();
};

async function uploadFiles(files) {
  const label = $("upload");
  for (const file of files) {
    label.classList.add("busy");
    $("upload-text").textContent = `[ ingesting ${file.name}… ]`;
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/api/documents", { method: "POST", body: form });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      toast(`couldn't add ${file.name}: ${err.detail || res.statusText}`);
    } else {
      const d = await res.json();
      toast(`mounted ${d.name} · ${d.pages} page(s) · ${d.chunks} chunks`);
    }
  }
  label.classList.remove("busy");
  $("upload-text").textContent = "[ + UPLOAD PDF / IMAGE ]";
  $("file-input").value = "";
  loadDocuments();
}

async function deleteDocument(name) {
  if (!confirm(`Remove "${name}" from your library?`)) return;
  await fetch(`/api/documents/${encodeURIComponent(name)}`, { method: "DELETE" });
  if ($("viewer").dataset.name === name) closeViewer();
  loadDocuments();
}

$("file-input").onchange = (e) => uploadFiles(e.target.files);
const dropZone = $("upload");
dropZone.ondragover = (e) => { e.preventDefault(); dropZone.classList.add("dragging"); };
dropZone.ondragleave = () => dropZone.classList.remove("dragging");
dropZone.ondrop = (e) => {
  e.preventDefault();
  dropZone.classList.remove("dragging");
  uploadFiles([...e.dataTransfer.files].filter((f) => SUPPORTED.test(f.name)));
};

// ---------- Document viewer (jump to page, passage highlighted) ----------
// The server draws each page as an image and highlights the cited passage, so this works in
// every browser, with or without a built-in PDF reader.
const viewerState = { name: null, page: 1, pages: 1, highlight: "", highlightPage: 0 };

function openViewer(name, page = 1, highlight = "") {
  Object.assign(viewerState, {
    name, page,
    pages: state.docs.find((d) => d.name === name)?.pages || 1,
    // the chunk text minus its "Section: ..." label line, which isn't on the page itself
    highlight: highlight.replace(/^Section: .*\n/, ""),
    highlightPage: page,
  });
  $("viewer").hidden = false;
  $("viewer").dataset.name = name;
  $("viewer-title").textContent = name;
  $("viewer-title").title = name;
  $("viewer-newtab").href = fileUrl(name);
  showViewerPage();
  document.body.classList.remove("menu-open");
}

function showViewerPage() {
  const { name, page, pages, highlight, highlightPage } = viewerState;
  $("viewer-page").textContent = `${page}/${pages}`;
  $("viewer-prev").disabled = page <= 1;
  $("viewer-next").disabled = page >= pages;
  const hl = page === highlightPage && highlight ? `?hl=${encodeURIComponent(highlight.slice(0, 1500))}` : "";
  const img = el("img");
  img.alt = `${name}, page ${page}`;
  img.src = `/api/documents/${encodeURIComponent(name)}/pages/${page}.png${hl}`;
  $("viewer-body").replaceChildren(img);
}

$("viewer-prev").onclick = () => { viewerState.page--; showViewerPage(); };
$("viewer-next").onclick = () => { viewerState.page++; showViewerPage(); };

function closeViewer() {
  $("viewer").hidden = true;
  $("viewer-body").innerHTML = "";
  delete $("viewer").dataset.name;
}
$("viewer-close").onclick = closeViewer;

// ---------- Rendering messages ----------
// A message: {role: "user"|"assistant", content, kind?: "answer"|"summary"|"quiz"|"command"|"quiz-cmd",
//             title?, sources?, query?, log?, seconds?, stopped?, error?, quiz?: {questions, picks}}
// While a task runs it also has a transient `status` (never saved).
const elements = new Map();              // message object -> its element, for the session on screen

function renderMessage(msg) {
  const box = el("div", `msg ${msg.role}`);
  if (msg.role === "user") {
    if (msg.kind === "command" || msg.kind === "quiz-cmd") box.classList.add("command");
    box.appendChild(el("span", "ps", msg.kind === "command" || msg.kind === "quiz-cmd" ? "$" : "root@studybuddy:~$"));
    box.appendChild(document.createTextNode(msg.content));
    return box;
  }
  if (msg.error) box.classList.add("error");
  if (msg.status && msg.content) box.classList.add("writing");
  box.appendChild(el("div", "who", msg.kind === "summary" ? "[studybuddy::summary]"
    : msg.kind === "quiz" ? "[studybuddy::quiz]" : "[studybuddy]"));
  if (msg.title) box.appendChild(el("div", "title", msg.title));
  if (msg.log?.length) box.appendChild(renderLog(msg));

  const body = el("div", "body");
  if (msg.content) body.innerHTML = markdown(msg.content);
  if (msg.status && !msg.content) {
    const s = el("div", "status");
    s.append(el("span", "spin", SPINNER[0]), document.createTextNode(msg.status));
    body.appendChild(s);
  }
  box.appendChild(body);

  if (msg.kind === "quiz" && msg.quiz?.questions.length) box.appendChild(renderQuiz(msg));
  if (msg.query && msg.query.trim().toLowerCase() !== (msg.question || "").trim().toLowerCase()) {
    box.appendChild(el("div", "meta-row", `searched for: “${msg.query}”`));
  }
  if (msg.sources?.length) box.appendChild(renderSources(msg.sources));

  const meta = el("div", "meta-row");
  if (msg.seconds !== undefined) meta.appendChild(el("span", "", `t=${msg.seconds}s`));
  if (msg.cached) meta.appendChild(el("span", "tag-chip", "cached summary"));
  if (msg.stopped) meta.appendChild(el("span", "tag-chip stopped", "stopped"));
  if (meta.childElementCount) box.appendChild(meta);
  return box;
}

function renderLog(msg) {
  const d = el("details", "log");
  d.open = !!msg.status;                         // open while working, folded when done
  d.appendChild(el("summary", "", `▸ activity log · ${msg.log.length} lines`));
  for (const entry of msg.log) {
    const line = el("div", `line ${entry.kind}`);
    const tag = { info: "[*]", ok: "[+]", warn: "[!]", err: "[x]" }[entry.kind] || "[*]";
    line.append(el("span", "t", `${entry.t.toFixed(1).padStart(5, " ")}s `), el("span", "tag", tag + " "));
    if (entry.quote) {
      line.append(document.createTextNode(entry.text + " "), el("span", "quote", `“${entry.quote}”`));
    } else {
      line.append(document.createTextNode(entry.text));
    }
    d.appendChild(line);
  }
  return d;
}

function renderSources(sources) {
  const wrap = el("div", "sources");
  sources.forEach((src, n) => {
    if (n === SHOWN_SOURCES) {
      const more = el("button", "open-page", `+${sources.length - SHOWN_SOURCES} more`);
      more.type = "button";
      more.onclick = () => { wrap.classList.add("all"); more.remove(); };
      wrap.appendChild(more);
    }
    const d = el("details");
    d.appendChild(el("summary", "", `src ${src.label} · ${src.score.toFixed(2)}`));
    if (src.name) {
      const open = el("button", "open-page", `» open page ${src.page}`);
      open.type = "button";
      open.onclick = () => openViewer(src.name, src.page, src.text);
      d.appendChild(open);
    }
    d.appendChild(el("pre", "", src.text));
    if (n >= SHOWN_SOURCES) d.classList.add("extra");
    wrap.appendChild(d);
  });
  return wrap;
}

function renderQuiz(msg) {
  const { questions, picks } = msg.quiz;
  const wrap = el("div", "quiz");
  const answered = picks.filter((p) => p !== null && p !== undefined).length;
  const right = questions.filter((q, i) => picks[i] === q.answer).length;
  const done = answered === questions.length;
  const bar = "█".repeat(right) + "▓".repeat(answered - right) + "░".repeat(questions.length - answered);
  const score = el("div", "scorebar");
  score.append(el("span", "bar", bar),
    el("span", "", done ? `FINAL ${right}/${questions.length}${right === questions.length ? " · flawless" : ""}`
                        : `score ${right}/${answered} · ${questions.length - answered} left`));
  wrap.appendChild(score);

  questions.forEach((q, i) => {
    const card = el("div", "q");
    const qt = el("div", "qtext");
    qt.append(el("span", "n", `Q${i + 1} › `), document.createTextNode(q.question));
    card.appendChild(qt);
    const opts = el("div", "options");
    const pick = picks[i];
    q.options.forEach((option, j) => {
      const b = el("button", "option");
      b.type = "button";
      b.append(el("span", "k", `[${"ABCD"[j]}]`), document.createTextNode(option));
      if (pick !== null && pick !== undefined) {
        b.disabled = true;
        if (j === q.answer) b.classList.add("correct");
        else if (j === pick) b.classList.add("wrong");
      } else {
        b.onclick = () => {
          picks[i] = j;
          refresh(msg);
          saveSession(sessionOf(msg));
        };
      }
      opts.appendChild(b);
    });
    card.appendChild(opts);
    if (pick !== null && pick !== undefined) {
      const ex = el("div", "explain");
      ex.append(el("span", `verdict ${pick === q.answer ? "good" : "bad"}`, pick === q.answer ? "ACCESS GRANTED · " : "ACCESS DENIED · "),
        document.createTextNode(`${q.explanation} `));
      const open = el("button", "open-page", `» ${q.source.label}`);
      open.type = "button";
      open.onclick = () => openViewer(q.source.name, q.source.page, q.source.text || "");
      ex.appendChild(open);
      card.appendChild(ex);
    }
    wrap.appendChild(card);
  });
  if (done) {
    const again = el("button", "btn", "[ NEW QUIZ ]");
    again.type = "button";
    again.onclick = startQuiz;
    wrap.appendChild(again);
  }
  return wrap;
}

function sessionOf(msg) {
  for (const s of state.sessions.values()) if (s.messages.includes(msg)) return s;
  return state.active;
}

function addMessage(session, msg) {
  session.messages.push(msg);
  if (session === state.active) {
    $("welcome")?.remove();
    const node = renderMessage(msg);
    node.classList.add("fresh");                   // fade in once
    elements.set(msg, node);
    messagesEl.appendChild(node);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
  return msg;
}

// Redraw one message - only if it belongs to the session on screen (background work just
// updates the message object; it's drawn when you switch back).
function refresh(msg) {
  const old = elements.get(msg);
  if (!old) return;
  const nearBottom = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 90;
  const logWasOpen = old.querySelector(".log")?.open;
  const node = renderMessage(msg);
  const log = node.querySelector(".log");
  if (log && logWasOpen !== undefined && msg.status) log.open = logWasOpen;   // respect a manual fold
  old.replaceWith(node);
  elements.set(msg, node);
  if (nearBottom) messagesEl.scrollTop = messagesEl.scrollHeight;
}

function renderAll() {
  messagesEl.innerHTML = "";
  elements.clear();
  const session = state.active;
  if (!session.messages.length) return showWelcome();
  for (const msg of session.messages) {
    const node = renderMessage(msg);
    elements.set(msg, node);
    messagesEl.appendChild(node);
  }
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

const ASCII = [
  " ____  _             _       ____            _     _",
  "/ ___|| |_ _   _  __| |_   _| __ ) _   _  __| | __| |_   _",
  "\\___ \\| __| | | |/ _` | | | |  _ \\| | | |/ _` |/ _` | | | |",
  " ___) | |_| |_| | (_| | |_| | |_) | |_| | (_| | (_| | |_| |",
  "|____/ \\__|\\__,_|\\__,_|\\__, |____/ \\__,_|\\__,_|\\__,_|\\__, |",
  "                       |___/                         |___/",
].join("\n");

function showWelcome() {
  const w = el("div", "welcome");
  w.id = "welcome";
  w.appendChild(el("pre", "ascii", ASCII));
  w.appendChild(el("p", "", "> answers come only from the documents you tick, with the file and page they came from."));
  const cmds = el("div", "cmds");
  for (const [k, v] of [
    ["[x] tick", "choose which documents get searched in // LIBRARY"],
    ["sum / view", "one-click summary, or open a document"],
    ["[ QUIZ ME ]", "get tested on the ticked documents"],
    ["[+ new]", "start another session, even while one is still answering"],
  ]) {
    const c = el("div", "cmd");
    c.append(el("b", "", k), el("span", "", v));
    cmds.appendChild(c);
  }
  w.appendChild(cmds);
  if (state.docs.length) {
    const tryBox = el("div", "try");
    for (const q of ["what are the main topics?", "summarise the key dates", "what should I remember most?"]) {
      const b = el("button", "", `$ ${q}`);
      b.type = "button";
      b.onclick = () => ask(state.active, q);
      tryBox.appendChild(b);
    }
    w.appendChild(tryBox);
  }
  messagesEl.appendChild(w);
}

// ---------- Running a streamed task, per session ----------
async function runTask(session, msg, url, body, onEvent) {
  const controller = new AbortController();
  session.controller = controller;
  state.unseen.delete(session.id);
  updateControls();
  renderSessions();
  const started = performance.now();
  const secs = () => (performance.now() - started) / 1000;
  msg.log = msg.log || [];
  const log = (kind, text, quote) => { msg.log.push({ t: secs(), kind, text, ...(quote ? { quote } : {}) }); refresh(msg); };
  let frame = 0;
  const tick = setInterval(() => {          // spinner + seconds, drawn only if on screen
    const s = elements.get(msg)?.querySelector(".status");
    if (!s) return;
    s.firstChild.textContent = SPINNER[frame++ % SPINNER.length];
    s.lastChild.textContent = ` ${msg.status} ${secs().toFixed(0)}s`;
  }, 90);
  try {
    await streamEvents(url, body, (event) => onEvent(event, log), controller.signal);
    log("ok", `done in ${secs().toFixed(1)}s`);
  } catch (err) {
    if (err.name === "AbortError") {
      msg.stopped = true;
      log("warn", "stopped by operator");
      if (!msg.content && msg.kind !== "quiz") msg.content = "Stopped before an answer was written.";
    } else {
      msg.error = true;
      log("err", `error: ${err.message}`);
      msg.content = `[x] ${err.message}\n\nIs Ollama running?`;
    }
  } finally {
    clearInterval(tick);
    delete msg.status;
    msg.seconds = Math.round(secs());
    session.controller = null;
    if (session !== state.active) {
      state.unseen.add(session.id);
      toast(`session "${titleOf(session)}" finished`);
    }
    refresh(msg);
    updateControls();
    await saveSession(session);            // finish saving before the list re-reads it
    renderSessions();
  }
}

function updateControls() {
  const busy = isRunning(state.active);
  const b = $("send");
  b.textContent = busy ? "[ ■ STOP ]" : "[ RUN ]";
  b.classList.toggle("stop", busy);
  b.title = busy ? "Stop this answer (other sessions keep running)" : "";
  $("quiz-btn").disabled = busy;
}

function canStart(session) {
  if (isRunning(session)) {
    toast("this session is busy. press [+ new] to work in parallel");
    return false;
  }
  return true;
}

function needsDocuments(session) {
  if (state.selected.size) return false;
  addMessage(session, { role: "assistant", kind: "answer", content: "[x] no documents ticked. tick at least one in // LIBRARY first." });
  return true;
}

// ---------- Asking ----------
function apiHistory(session) {
  // what the model sees as the conversation: questions, answers and summaries (not quizzes)
  return session.messages
    .filter((m) => !["quiz", "quiz-cmd"].includes(m.kind) && !m.error && m.content)
    .map((m) => ({ role: m.role, content: m.content }))
    .slice(-12);
}

async function ask(session, question) {
  if (!canStart(session) || needsDocuments(session)) return;
  const history = apiHistory(session);
  addMessage(session, { role: "user", content: question });
  saveSession(session);
  const msg = addMessage(session, { role: "assistant", kind: "answer", question, content: "", log: [], status: "planning search" });
  let wrote = false;
  await runTask(session, msg, "/api/ask", { question, history, documents: [...state.selected] }, (event, log) => {
    if (event.type === "log") {
      log("info", event.text);
    } else if (event.type === "plan") {
      for (const q of event.queries.slice(1, 4)) log("ok", "rephrased:", q);
      if (event.hypothetical) log("ok", "imagined answer:", event.hypothetical.slice(0, 140));
      log(event.about_documents ? "ok" : "warn", event.about_documents
        ? (event.overview ? "judged: overview of your documents, reading across all of them"
                          : "judged: about your documents")
        : "judged: not about your documents (score check decides)");
      msg.status = "ranking chunks";
      refresh(msg);
    } else if (event.type === "sources") {
      msg.sources = event.sources;
      msg.query = event.query;
      if (event.sources.length) {
        const best = event.sources.reduce((a, b) => (b.score > a.score ? b : a));
        log("ok", `retrieved ${event.sources.length} chunks · best ${best.score.toFixed(2)} (${best.label})`);
      } else {
        log("warn", "no chunk above threshold: off-topic for these documents");
      }
      msg.status = "generating answer";
      refresh(msg);
    } else if (event.type === "token") {
      // with no sources the server sends its fixed "couldn't find" reply; the model isn't asked
      if (!wrote && msg.sources?.length) log("info", "streaming answer from llama3.1:8b");
      wrote = true;
      msg.content += event.text;
      refresh(msg);
    }
  });
}

// ---------- One-click summary ----------
async function summarize(name) {
  document.body.classList.remove("menu-open");
  const session = state.active;
  if (!canStart(session)) return;
  addMessage(session, { role: "user", kind: "command", content: `summarize "${name}"` });
  saveSession(session);
  const msg = addMessage(session, { role: "assistant", kind: "summary", title: name, content: "", log: [], status: "reading the document" });
  await runTask(session, msg, `/api/documents/${encodeURIComponent(name)}/summary`, {}, (event, log) => {
    if (event.type === "progress") {
      log("info", event.text.replace(/…$/, ""));
      msg.status = event.text.replace(/…$/, "").toLowerCase();
      refresh(msg);
    } else if (event.type === "token") {
      if (!msg.content) log("info", "writing summary");
      msg.content += event.text;
      refresh(msg);
    } else if (event.type === "done" && event.cached) {
      msg.cached = true;
      log("ok", "loaded saved summary from cache");
    }
  });
}

// ---------- Quiz mode ----------
async function startQuiz() {
  const session = state.active;
  if (!canStart(session) || needsDocuments(session)) return;
  const count = Number($("quiz-count").value);
  const scope = state.selected.size === state.docs.length ? "all documents" : [...state.selected].join(", ");
  addMessage(session, { role: "user", kind: "quiz-cmd", content: `quiz --questions ${count} --from "${scope}"` });
  saveSession(session);
  const msg = addMessage(session, { role: "assistant", kind: "quiz", content: "",
                                    quiz: { questions: [], picks: [] }, log: [], status: "picking passages" });
  await runTask(session, msg, "/api/quiz", { documents: [...state.selected], count }, (event, log) => {
    if (event.type === "progress") {
      log("info", event.text.replace(/…$/, ""));
      msg.status = event.text.replace(/…$/, "").toLowerCase();
      refresh(msg);
    } else if (event.type === "quiz") {
      msg.quiz = { questions: event.questions, picks: event.questions.map(() => null) };
      log("ok", `${event.questions.length} questions verified` + (event.dropped ? ` · ${event.dropped} unclear ones replaced or dropped` : ""));
      msg.content = event.questions.length
        ? "Pick an answer for each question. Each one was double-checked against its source."
        : "[x] couldn't write questions I was sure about from these documents. Try again or tick different ones.";
    }
  });
}
$("quiz-btn").onclick = startQuiz;

// ---------- Sessions (saved chats) ----------
function titleOf(session) {
  const first = session.messages.find((m) => m.role === "user");
  return (first?.content || "new session").slice(0, 60);
}

let sessionList = [];
async function renderSessions() {
  try { sessionList = await (await fetch("/api/chats")).json(); } catch { /* keep the last list */ }
  const list = $("chats");
  list.innerHTML = "";
  const ids = new Set(sessionList.map((c) => c.id));
  // a brand-new session that's still saving its first message, or running, shows too
  const local = [...state.sessions.values()].filter((s) => !ids.has(s.id) && (s.messages.length || isRunning(s)))
    .map((s) => ({ id: s.id, title: titleOf(s), updated: Date.now() / 1000 }));
  const all = [...local, ...sessionList];
  if (!all.length) list.innerHTML = '<li class="empty">no saved sessions</li>';
  for (const c of all) {
    const running = isRunning(state.sessions.get(c.id));
    const li = el("li", [c.id === state.active?.id ? "active" : "", running ? "running" : "",
                         state.unseen.has(c.id) ? "done-unseen" : ""].join(" "));
    const name = el("span", "name", c.title);
    name.title = `${c.title}\n${new Date(c.updated * 1000).toLocaleString()}${running ? "\n(working…)" : ""}`;
    const actions = el("span", "actions");
    actions.appendChild(iconButton("x", "Delete session", () => deleteSession(c.id), "delete"));
    li.append(name, actions);
    li.onclick = () => openSession(c.id);
    list.appendChild(li);
  }
}

async function saveSession(session) {
  if (!session?.messages.length || session.deleted) return;   // never resurrect a deleted session
  // transient UI fields (status) are never saved
  const messages = session.messages.map(({ status, ...m }) => m);
  const body = JSON.stringify({ title: titleOf(session), messages });
  // saves of one session run one after another, so an older save never overwrites a newer one
  session.saving = (session.saving || Promise.resolve()).then(() => fetch(`/api/chats/${session.id}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body,
  })).catch(() => {});
  return session.saving;
}

async function openSession(id) {
  let session = state.sessions.get(id);
  if (!session) {                          // not in memory yet: load it from disk
    const res = await fetch(`/api/chats/${id}`);
    if (!res.ok) return newSession();
    session = { id, messages: (await res.json()).messages, controller: null };
    state.sessions.set(id, session);
  }
  state.active = session;
  state.unseen.delete(id);
  store.set("chatId", id);
  renderAll();
  updateControls();
  renderSessions();
  document.body.classList.remove("menu-open");
  questionEl.focus();
}

function newSession() {
  const session = { id: newId(), messages: [], controller: null };
  state.sessions.set(session.id, session);
  state.active = session;
  store.set("chatId", session.id);
  renderAll();
  updateControls();
  renderSessions();
  document.body.classList.remove("menu-open");
  questionEl.focus();
}

async function deleteSession(id) {
  const session = state.sessions.get(id);
  if (!confirm(isRunning(session) ? "This session is still working. Stop it and delete?" : "Delete this session?")) return;
  if (session) session.deleted = true;
  session?.controller?.abort();
  state.sessions.delete(id);
  await fetch(`/api/chats/${id}`, { method: "DELETE" });
  if (id === state.active?.id) newSession();
  else renderSessions();
}
$("new-chat").onclick = newSession;

// ---------- Prompt ----------
$("ask-form").onsubmit = (e) => {
  e.preventDefault();
  const session = state.active;
  if (isRunning(session)) {                // the button is STOP while this session works
    session.controller.abort();
    return;
  }
  const question = questionEl.value.trim();
  if (!question) return;
  questionEl.value = "";
  questionEl.style.height = "auto";
  ask(session, question);
};
questionEl.onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    if (isRunning(state.active)) toast("this session is busy. press [+ new] to work in parallel");
    else $("ask-form").requestSubmit();
  }
};
questionEl.oninput = () => {             // grow the box with the text
  questionEl.style.height = "auto";
  questionEl.style.height = questionEl.scrollHeight + "px";
};
addEventListener("beforeunload", (e) => {
  if (anyRunning()) e.preventDefault();  // leaving would cut off answers still being written
});

// ---------- Mobile menu ----------
$("menu-open").onclick = () => document.body.classList.add("menu-open");
$("menu-close").onclick = () => document.body.classList.remove("menu-open");
$("backdrop").onclick = () => document.body.classList.remove("menu-open");

// ---------- Start ----------
(async () => {
  await Promise.all([loadDocuments(), checkHealth()]);
  const last = store.get("chatId", null);
  if (last) await openSession(last);
  else newSession();
  boot();
})();
