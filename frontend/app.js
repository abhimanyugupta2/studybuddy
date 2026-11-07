// StudyBuddy chat page: talks to the FastAPI backend in backend/server.py.
const $ = (id) => document.getElementById(id);
const messagesEl = $("messages");
const questionEl = $("question");
let history = [];          // [{role, content}], sent with each question for follow-ups
let busy = false;

// ---------- Library ----------
async function loadDocuments() {
  const docs = await (await fetch("/api/documents")).json();
  const list = $("documents");
  list.innerHTML = "";
  if (!docs.length) {
    list.innerHTML = '<li class="empty">No PDFs yet</li>';
    return;
  }
  for (const d of docs) {
    const li = document.createElement("li");
    li.innerHTML = `<span>📄</span><span class="name"></span><span class="meta"></span>
                    <button class="delete" title="Remove">×</button>`;
    li.querySelector(".name").textContent = d.name;
    li.querySelector(".name").title = d.name;
    li.querySelector(".meta").textContent = `${d.pages}p`;
    li.querySelector(".delete").onclick = () => deleteDocument(d.name);
    list.appendChild(li);
  }
}

async function uploadFiles(files) {
  const label = $("upload");
  for (const file of files) {
    label.classList.add("busy");
    $("upload-text").textContent = `Reading ${file.name}…`;
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/api/documents", { method: "POST", body: form });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(`Couldn't add ${file.name}: ${err.detail || res.statusText}`);
    }
  }
  label.classList.remove("busy");
  $("upload-text").textContent = "＋ Add PDF";
  $("file-input").value = "";
  loadDocuments();
}

async function deleteDocument(name) {
  if (!confirm(`Remove "${name}" from your library?`)) return;
  await fetch(`/api/documents/${encodeURIComponent(name)}`, { method: "DELETE" });
  loadDocuments();
}

$("file-input").onchange = (e) => uploadFiles(e.target.files);
const dropZone = $("upload");
dropZone.ondragover = (e) => { e.preventDefault(); dropZone.classList.add("dragging"); };
dropZone.ondragleave = () => dropZone.classList.remove("dragging");
dropZone.ondrop = (e) => {
  e.preventDefault();
  dropZone.classList.remove("dragging");
  uploadFiles([...e.dataTransfer.files].filter((f) => f.name.toLowerCase().endsWith(".pdf")));
};

// ---------- Chat ----------
function addMessage(role, text = "") {
  $("welcome")?.remove();
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.textContent = text;
  messagesEl.appendChild(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return el;
}

function showSources(el, query, question, sources) {
  if (query && query.trim().toLowerCase() !== question.trim().toLowerCase()) {
    const s = document.createElement("div");
    s.className = "searched";
    s.textContent = `Searched for: “${query}”`;
    el.appendChild(s);
  }
  if (!sources.length) return;
  const box = document.createElement("div");
  box.className = "sources";
  for (const src of sources) {
    const d = document.createElement("details");
    d.innerHTML = "<summary></summary><pre></pre>";
    d.querySelector("summary").textContent = `${src.label} · ${src.score.toFixed(2)}`;
    d.querySelector("pre").textContent = src.text;
    box.appendChild(d);
  }
  el.appendChild(box);
}

async function ask(question) {
  busy = true;
  $("send").disabled = true;
  addMessage("user", question);
  const bot = addMessage("bot");
  const answerEl = document.createElement("span");
  answerEl.innerHTML = '<span class="thinking">Searching your notes…</span>';
  bot.appendChild(answerEl);

  let answer = "", sources = [], query = question;
  try {
    const res = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, history }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);

    // The answer streams back as one JSON event per line.
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
        if (event.type === "sources") {
          sources = event.sources;
          query = event.query;
          answerEl.innerHTML = '<span class="thinking">Writing answer…</span>';
        } else if (event.type === "token") {
          answer += event.text;
          answerEl.textContent = answer;
        } else if (event.type === "error") {
          throw new Error(event.message);
        }
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }
    }
    showSources(bot, query, question, sources);
    history.push({ role: "user", content: question }, { role: "assistant", content: answer });
  } catch (err) {
    bot.classList.add("error");
    answerEl.textContent = `Something went wrong: ${err.message}\nIs Ollama running?`;
  } finally {
    busy = false;
    $("send").disabled = false;
    messagesEl.scrollTop = messagesEl.scrollHeight;
    questionEl.focus();
  }
}

$("ask-form").onsubmit = (e) => {
  e.preventDefault();
  const question = questionEl.value.trim();
  if (!question || busy) return;
  questionEl.value = "";
  questionEl.style.height = "auto";
  ask(question);
};
questionEl.onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("ask-form").requestSubmit();
  }
};
questionEl.oninput = () => {            // grow the box with the text
  questionEl.style.height = "auto";
  questionEl.style.height = questionEl.scrollHeight + "px";
};

$("new-chat").onclick = () => {
  history = [];
  messagesEl.innerHTML = "";
  addMessage("bot", "New chat started. What would you like to know?");
};

loadDocuments();
questionEl.focus();
