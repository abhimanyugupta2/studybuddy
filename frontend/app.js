// StudyBuddy chat page: talks to the FastAPI backend in backend/server.py.
const $ = (id) => document.getElementById(id);
const SUPPORTED = /\.(pdf|png|jpe?g|tiff?|bmp)$/i;   // same list as ingest.SUPPORTED
const messagesEl = $("messages");
const questionEl = $("question");

const state = {
  docs: [],              // [{name, pages, chunks}]
  selected: new Set(),   // ticked document names
  chatId: null,
  messages: [],          // the open chat; see renderMessage() for the shape
  controller: null,      // AbortController of the running request (null = idle)
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

// A tiny, safe Markdown renderer: paragraphs, bullet/numbered lists, headings, **bold**, `code`.
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
  if (!state.docs.length) list.innerHTML = '<li class="empty">No documents yet. Add one above.</li>';
  for (const d of state.docs) {
    const li = el("li", state.selected.has(d.name) ? "" : "off");
    const box = el("input");
    box.type = "checkbox";
    box.checked = state.selected.has(d.name);
    box.title = "Use this document when answering";
    box.onchange = () => {
      box.checked ? state.selected.add(d.name) : state.selected.delete(d.name);
      saveSelection();
      renderDocuments();
    };
    const name = el("span", "name", `${/\.pdf$/i.test(d.name) ? "📄" : "🖼️"} ${d.name}`);
    name.title = d.name;
    name.style.cursor = "pointer";
    name.onclick = () => { box.checked = !box.checked; box.onchange(); };
    const actions = el("span", "actions");
    actions.append(
      iconButton("📝", "One-click summary", () => summarize(d.name)),
      iconButton("📖", "Open document", () => openViewer(d.name, 1)),
      iconButton("×", "Remove from library", () => deleteDocument(d.name), "delete"),
    );
    li.append(box, name, el("span", "meta", `${d.pages}p`), actions);
    list.appendChild(li);
  }
  const all = state.docs.length && state.selected.size === state.docs.length;
  $("toggle-all").textContent = all ? "Select none" : "Select all";
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
  scope.textContent = !total ? "Add a document to get started"
    : n === 0 ? "No documents ticked. Tick at least one in the library"
    : n === total ? (total === 1 ? "Searching 1 document" : `Searching all ${total} documents`)
    : `Searching ${n} of ${total}: ${[...state.selected].join(", ")}`;
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
    $("upload-text").textContent = `Reading ${file.name}… (scans take longer)`;
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/api/documents", { method: "POST", body: form });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(`Couldn't add ${file.name}: ${err.detail || res.statusText}`);
    }
  }
  label.classList.remove("busy");
  $("upload-text").textContent = "＋ Add PDF or photo";
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
  $("viewer-page").textContent = `${page} / ${pages}`;
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
//             title?, sources?, query?, seconds?, stopped?, error?, quiz?: {questions, picks}}
function renderMessage(msg) {
  const box = el("div", `msg ${msg.role}`);
  if (msg.role === "user") {
    if (msg.kind === "command" || msg.kind === "quiz-cmd") box.classList.add("command");
    box.textContent = msg.content;
    return box;
  }
  if (msg.error) box.classList.add("error");
  if (msg.kind === "summary" || msg.kind === "quiz") box.classList.add("wide");
  if (msg.title) box.appendChild(el("div", "title", msg.title));

  const body = el("div", "body");
  if (msg.status) body.appendChild(el("span", "status", msg.status));
  else if (msg.content) body.innerHTML = markdown(msg.content);
  box.appendChild(body);

  if (msg.kind === "quiz" && msg.quiz?.questions.length) box.appendChild(renderQuiz(msg));

  if (msg.query && msg.query.trim().toLowerCase() !== (msg.question || "").trim().toLowerCase()) {
    box.appendChild(el("div", "meta-row", `Searched for: “${msg.query}”`));
  }
  if (msg.sources?.length) box.appendChild(renderSources(msg.sources));

  const meta = el("div", "meta-row");
  if (msg.seconds !== undefined) meta.appendChild(el("span", "", `⏱ ${msg.seconds}s`));
  if (msg.cached) meta.appendChild(el("span", "tag", "saved summary"));
  if (msg.stopped) meta.appendChild(el("span", "tag stopped", "stopped"));
  if (meta.childElementCount) box.appendChild(meta);
  return box;
}

function renderSources(sources) {
  const wrap = el("div", "sources");
  for (const src of sources) {
    const d = el("details");
    d.appendChild(el("summary", "", `${src.label} · ${src.score.toFixed(2)}`));
    if (src.name) {
      const open = el("button", "open-page", `📖 Open page ${src.page}`);
      open.type = "button";
      open.onclick = () => openViewer(src.name, src.page, src.text);
      d.appendChild(open);
    }
    d.appendChild(el("pre", "", src.text));
    wrap.appendChild(d);
  }
  return wrap;
}

function renderQuiz(msg) {
  const { questions, picks } = msg.quiz;
  const wrap = el("div", "quiz");
  const answered = picks.filter((p) => p !== null && p !== undefined).length;
  const right = questions.filter((q, i) => picks[i] === q.answer).length;
  const done = answered === questions.length;
  wrap.appendChild(el("div", "score",
    done ? `Final score: ${right} / ${questions.length} ${right === questions.length ? "🎉" : ""}`
         : `Score: ${right} / ${answered} answered (${questions.length} questions)`));

  questions.forEach((q, i) => {
    const card = el("div", "q");
    card.appendChild(el("div", "qtext", `${i + 1}. ${q.question}`));
    const opts = el("div", "options");
    const pick = picks[i];
    q.options.forEach((option, j) => {
      const b = el("button", "option", `${"ABCD"[j]}. ${option}`);
      b.type = "button";
      if (pick !== null && pick !== undefined) {
        b.disabled = true;
        if (j === q.answer) b.classList.add("correct");
        else if (j === pick) b.classList.add("wrong");
      } else {
        b.onclick = () => {
          picks[i] = j;
          refresh(msg);
          saveChat();
        };
      }
      opts.appendChild(b);
    });
    card.appendChild(opts);
    if (pick !== null && pick !== undefined) {
      const ex = el("div", "explain", `${pick === q.answer ? "✅ Correct." : "❌ Not quite."} ${q.explanation} `);
      const open = el("button", "open-page", `📖 ${q.source.label}`);
      open.type = "button";
      open.onclick = () => openViewer(q.source.name, q.source.page, q.source.text || "");
      ex.appendChild(open);
      card.appendChild(ex);
    }
    wrap.appendChild(card);
  });
  if (done) {
    const again = el("button", "secondary", "🧠 New quiz");
    again.type = "button";
    again.onclick = startQuiz;
    wrap.appendChild(again);
  }
  return wrap;
}

const elements = new Map();                 // message object -> its element on the page

function addMessage(msg, save = true) {
  $("welcome")?.remove();
  state.messages.push(msg);
  const node = renderMessage(msg);
  elements.set(msg, node);
  messagesEl.appendChild(node);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  if (save) saveChat();
  return msg;
}

function refresh(msg) {
  const old = elements.get(msg);
  const nearBottom = messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 80;
  const node = renderMessage(msg);
  old?.replaceWith(node);
  elements.set(msg, node);
  if (nearBottom) messagesEl.scrollTop = messagesEl.scrollHeight;
}

function renderAll() {
  messagesEl.innerHTML = "";
  elements.clear();
  if (!state.messages.length) return showWelcome();
  for (const msg of state.messages) {
    const node = renderMessage(msg);
    elements.set(msg, node);
    messagesEl.appendChild(node);
  }
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function showWelcome() {
  const w = el("div", "welcome");
  w.id = "welcome";
  w.innerHTML = `
    <h2>Ask anything about your notes</h2>
    <p>Answers come only from the documents you tick, with the file and page they came from.</p>
    <div class="tips">
      <div class="tip">☑️ <b>Tick</b> documents in the library to choose what gets searched.</div>
      <div class="tip">📝 Hover a document for a <b>one-click summary</b> or to <b>open</b> it.</div>
      <div class="tip">🧠 <b>Quiz me</b> (top right) tests you on the ticked documents.</div>
      <div class="tip">📖 Every answer lists its sources. <b>Open page</b> jumps straight there.</div>
    </div>`;
  messagesEl.appendChild(w);
}

// ---------- Running a streamed task with a Stop button and a timer ----------
async function runTask(msg, url, body, onEvent) {
  const controller = new AbortController();
  state.controller = controller;
  setSendButton(true);
  const started = performance.now();
  const baseStatus = msg.status;
  const tick = setInterval(() => {
    if (!msg.status) return;
    const secs = Math.round((performance.now() - started) / 1000);
    msg.status = `${msg.statusText || baseStatus} ${secs}s`;
    const s = elements.get(msg)?.querySelector(".status");
    if (s) s.textContent = msg.status;
  }, 500);
  try {
    await streamEvents(url, body, onEvent, controller.signal);
  } catch (err) {
    if (err.name === "AbortError") {
      msg.stopped = true;
      if (!msg.content && msg.kind !== "quiz") msg.content = "Stopped before an answer was written.";
    } else {
      msg.error = true;
      msg.content = `Something went wrong: ${err.message}\n\nIs Ollama running?`;
    }
  } finally {
    clearInterval(tick);
    delete msg.status;
    delete msg.statusText;
    msg.seconds = Math.round((performance.now() - started) / 1000);
    state.controller = null;
    setSendButton(false);
    refresh(msg);
    questionEl.focus();
    await saveChat();                      // finish saving before the chat list re-reads it
    loadChats();
  }
}

function setSendButton(busy) {
  const b = $("send");
  b.textContent = busy ? "■ Stop" : "Send";
  b.classList.toggle("stop", busy);
  b.title = busy ? "Stop this answer" : "";
  $("quiz-btn").disabled = busy;
}

const isBusy = () => state.controller !== null;

function needsDocuments() {
  if (state.selected.size) return false;
  addMessage({ role: "assistant", kind: "answer", content: "No documents are ticked. Tick at least one in the library first." }, false);
  return true;
}

// ---------- Asking ----------
function apiHistory() {
  // what the model sees as the conversation: questions, answers and summaries (not quizzes)
  return state.messages
    .filter((m) => !["quiz", "quiz-cmd"].includes(m.kind) && !m.error && m.content)
    .map((m) => ({ role: m.role, content: m.content }))
    .slice(-12);
}

async function ask(question) {
  if (isBusy() || needsDocuments()) return;
  const history = apiHistory();
  addMessage({ role: "user", content: question });
  const msg = addMessage({ role: "assistant", kind: "answer", question, content: "", status: "Searching your notes…" }, false);
  await runTask(msg, "/api/ask", { question, history, documents: [...state.selected] }, (event) => {
    if (event.type === "sources") {
      msg.sources = event.sources;
      msg.query = event.query;
      msg.statusText = "Writing answer…";
      msg.status = msg.statusText;
      refresh(msg);
    } else if (event.type === "token") {
      delete msg.status;
      msg.content += event.text;
      refresh(msg);
    }
  });
}

// ---------- One-click summary ----------
async function summarize(name) {
  if (isBusy()) return;
  document.body.classList.remove("menu-open");
  addMessage({ role: "user", kind: "command", content: `📝 Summarise ${name}` });
  const msg = addMessage({ role: "assistant", kind: "summary", title: `Summary · ${name}`, content: "",
                           status: "Reading the document…" }, false);
  await runTask(msg, `/api/documents/${encodeURIComponent(name)}/summary`, {}, (event) => {
    if (event.type === "progress") {
      msg.statusText = event.text;
      msg.status = event.text;
      refresh(msg);
    } else if (event.type === "token") {
      delete msg.status;
      msg.content += event.text;
      refresh(msg);
    } else if (event.type === "done" && event.cached) {
      msg.cached = true;
    }
  });
}

// ---------- Quiz mode ----------
async function startQuiz() {
  if (isBusy() || needsDocuments()) return;
  const count = Number($("quiz-count").value);
  const scope = state.selected.size === state.docs.length ? "all documents" : [...state.selected].join(", ");
  addMessage({ role: "user", kind: "quiz-cmd", content: `🧠 Quiz me: ${count} questions from ${scope}` });
  const msg = addMessage({ role: "assistant", kind: "quiz", title: "Quiz", content: "",
                           quiz: { questions: [], picks: [] }, status: "Picking passages…" }, false);
  await runTask(msg, "/api/quiz", { documents: [...state.selected], count }, (event) => {
    if (event.type === "progress") {
      msg.statusText = event.text;
      msg.status = event.text;
      refresh(msg);
    } else if (event.type === "quiz") {
      msg.quiz = { questions: event.questions, picks: event.questions.map(() => null) };
      const dropped = event.dropped ? ` (${event.dropped} unclear question${event.dropped > 1 ? "s were" : " was"} left out after double-checking.)` : "";
      msg.content = event.questions.length
        ? `Pick an answer for each question. You'll see right away if it's correct.${dropped}`
        : "I couldn't write questions I was sure about from these documents. Try again or tick different ones.";
    }
  });
}
$("quiz-btn").onclick = startQuiz;

// ---------- Saved chats ----------
async function loadChats() {
  const chats = await (await fetch("/api/chats")).json();
  const list = $("chats");
  list.innerHTML = "";
  if (!chats.length) list.innerHTML = '<li class="empty">No saved chats yet</li>';
  for (const c of chats) {
    const li = el("li", c.id === state.chatId ? "active" : "");
    const name = el("span", "name", `💬 ${c.title}`);
    name.title = `${c.title}\n${new Date(c.updated * 1000).toLocaleString()}`;
    const actions = el("span", "actions");
    actions.appendChild(iconButton("×", "Delete chat", () => deleteChat(c.id), "delete"));
    li.append(name, actions);
    li.onclick = () => openChat(c.id);
    list.appendChild(li);
  }
}

async function saveChat() {
  if (!state.messages.length) return;
  const first = state.messages.find((m) => m.role === "user");
  const title = (first?.content || "Chat").replace(/^[📝🧠]\s*/u, "").slice(0, 60);
  // transient UI fields (status) are never saved
  const messages = state.messages.map(({ status, statusText, ...m }) => m);
  const body = JSON.stringify({ title, messages });
  const id = state.chatId;               // fixed now, in case another chat is opened meanwhile
  // saves run one after another, so an older save can never overwrite a newer one
  saveQueue = saveQueue.then(() => fetch(`/api/chats/${id}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body,
  })).catch(() => {});
  return saveQueue;
}
let saveQueue = Promise.resolve();

async function openChat(id) {
  if (isBusy()) return;
  const res = await fetch(`/api/chats/${id}`);
  if (!res.ok) return newChat();
  state.chatId = id;
  state.messages = (await res.json()).messages;
  store.set("chatId", id);
  renderAll();
  loadChats();
  document.body.classList.remove("menu-open");
}

function newChat() {
  if (isBusy()) return;
  state.chatId = newId();
  state.messages = [];
  store.set("chatId", state.chatId);
  renderAll();
  loadChats();
  document.body.classList.remove("menu-open");
  questionEl.focus();
}

async function deleteChat(id) {
  if (!confirm("Delete this chat?")) return;
  await fetch(`/api/chats/${id}`, { method: "DELETE" });
  if (id === state.chatId) newChat();
  else loadChats();
}
$("new-chat").onclick = newChat;

// ---------- Input box ----------
$("ask-form").onsubmit = (e) => {
  e.preventDefault();
  if (isBusy()) {                          // the button is "Stop" while busy
    state.controller.abort();
    return;
  }
  const question = questionEl.value.trim();
  if (!question) return;
  questionEl.value = "";
  questionEl.style.height = "auto";
  ask(question);
};
questionEl.onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    if (!isBusy()) $("ask-form").requestSubmit();
  }
};
questionEl.oninput = () => {             // grow the box with the text
  questionEl.style.height = "auto";
  questionEl.style.height = questionEl.scrollHeight + "px";
};

// ---------- Mobile menu ----------
$("menu-open").onclick = () => document.body.classList.add("menu-open");
$("menu-close").onclick = () => document.body.classList.remove("menu-open");
$("backdrop").onclick = () => document.body.classList.remove("menu-open");

// ---------- Start ----------
(async () => {
  await loadDocuments();
  const last = store.get("chatId", null);
  if (last) await openChat(last);
  else newChat();
  loadChats();
})();
