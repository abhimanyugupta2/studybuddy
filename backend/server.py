"""
Phase 5: StudyBuddy as a web API (and Phase 6: it also serves the chat page).
Phase 8: choose documents, stop answers, summaries, saved chats, document viewer, quizzes.

Endpoints:  GET    /api/documents               list documents (PDFs, photos, scans)
            POST   /api/documents               upload a PDF or image (form field "file")
            DELETE /api/documents/{name}        remove a document and its index files
            GET    /api/documents/{name}/file   the original file
            GET    /api/documents/{name}/pages/{n}.png?hl=...   page n as an image, passage highlighted
            POST   /api/documents/{name}/summary   streamed one-click summary
            POST   /api/ask                     {"question", "history", "documents"} -> streamed answer
            POST   /api/quiz                    {"documents", "count"} -> streamed quiz
            GET/PUT/DELETE /api/chats[/{id}]    saved chat history
Run:        uvicorn server:app --reload     then open http://localhost:8000

Every streamed endpoint sends newline-delimited JSON events. If the browser disconnects
(the Stop button), streaming stops and the connection to Ollama is closed so the model stops too.
"""
import json
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import pymupdf
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import iterate_in_threadpool

from ask import NOT_FOUND, standalone_question, stream_answer
from ingest import SUPPORTED, chunks_path_for
from retrieve import EMBED_DIM, label, list_documents as document_files, load_library, search, vectors_path
from study import quiz_events, summary_events

DATA_DIR = Path(__file__).parent / "data"
CHATS_DIR = DATA_DIR / ".chats"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
MAX_UPLOAD_MB = 50
CHAT_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")

# One tuple, swapped in a single step, so a question never sees new chunks with old vectors.
EMPTY = ([], np.zeros((0, EMBED_DIM), dtype=np.float32))
library = {"index": EMPTY}


def reload_library():
    """(Re)build the in-memory index from every document in data/."""
    library["index"] = load_library(DATA_DIR) if document_files(DATA_DIR) else EMPTY


def summary_path_for(doc_path):
    return chunks_path_for(doc_path).with_name(f"{Path(doc_path).name}.summary.md")


def remove_document_files(doc_path):
    for path in (doc_path, chunks_path_for(doc_path), vectors_path(chunks_path_for(doc_path)),
                 summary_path_for(doc_path)):
        path.unlink(missing_ok=True)


def is_readable(data, suffix):
    """Let PyMuPDF try to open it - a real check, not just trusting the file name."""
    try:
        doc = pymupdf.open(stream=data, filetype=suffix.lstrip("."))
        doc.load_page(0).get_pixmap(dpi=10)               # actually decode it, not just the header
        doc.close()
        return True
    except Exception:
        return False


def document_path(name):
    """The file for a library document name, or a 404. Path(name).name blocks ../ tricks."""
    doc_path = DATA_DIR / Path(name).name
    if doc_path.suffix.lower() not in SUPPORTED or not doc_path.exists():
        raise HTTPException(404, "No such document.")
    return doc_path


def selected(documents):
    """The chunks and vectors of only the documents the user ticked (None = all of them)."""
    chunks, vectors = library["index"]
    if documents is None:
        return chunks, vectors
    keep = [i for i, c in enumerate(chunks) if c["source"] in set(documents)]
    return [chunks[i] for i in keep], vectors[keep]


def stream_events(request, events):
    """Send a generator of event dicts as NDJSON, stopping (and closing it) if the browser leaves."""
    async def body():
        try:
            async for event in iterate_in_threadpool(events):
                if await request.is_disconnected():
                    break
                yield json.dumps(event) + "\n"
        finally:
            try:
                events.close()                          # closes the Ollama connection too
            except ValueError:
                pass    # still busy in a worker thread; it's closed when garbage-collected
    return StreamingResponse(body(), media_type="application/x-ndjson")


def safely(events):
    """Turn any error (e.g. Ollama isn't running) into an error event instead of a broken stream."""
    try:
        yield from events
    except Exception as e:
        yield {"type": "error", "message": str(e)}


@asynccontextmanager
async def lifespan(app):
    DATA_DIR.mkdir(exist_ok=True)
    CHATS_DIR.mkdir(exist_ok=True)
    for leftover in CHATS_DIR.glob("*.tmp"):            # from a save interrupted by a crash
        leftover.unlink(missing_ok=True)
    reload_library()
    yield


app = FastAPI(title="StudyBuddy", lifespan=lifespan)


# ---------- Documents ----------
@app.get("/api/documents")
def list_documents():
    docs = {}
    for c in library["index"][0]:
        d = docs.setdefault(c["source"], {"name": c["source"], "chunks": 0, "pages": 0})
        d["chunks"] += 1
        d["pages"] = max(d["pages"], *c["pages"])
    return sorted(docs.values(), key=lambda d: d["name"].lower())


@app.post("/api/documents")
def upload_document(file: UploadFile):
    name = Path(file.filename or "").name               # strip any folder parts
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED:
        raise HTTPException(400, f"Supported files: {', '.join(sorted(SUPPORTED))}")
    data = file.file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {MAX_UPLOAD_MB} MB.")
    if not is_readable(data, suffix):
        raise HTTPException(400, f"That file doesn't look like a valid {suffix} file.")
    doc_path = DATA_DIR / name
    doc_path.write_bytes(data)
    summary_path_for(doc_path).unlink(missing_ok=True)  # a replaced file needs a fresh summary
    try:
        reload_library()                                # cleans, OCRs if needed, embeds
    except Exception as e:
        remove_document_files(doc_path)
        reload_library()
        raise HTTPException(422, f"Couldn't read that file: {e}")
    added = [d for d in list_documents() if d["name"] == name]
    if not added:
        remove_document_files(doc_path)
        reload_library()
        raise HTTPException(422, "No readable text found in that file, even with OCR.")
    return added[0]


@app.delete("/api/documents/{name}")
def delete_document(name: str):
    doc_path = document_path(name)
    remove_document_files(doc_path)
    reload_library()
    return {"deleted": doc_path.name}


@app.get("/api/documents/{name}/file")
def document_file(name: str):
    return FileResponse(document_path(name))            # the original, for "open in new tab"


@app.get("/api/documents/{name}/pages/{number}.png")
def document_page(name: str, number: int, hl: str = ""):
    """One page drawn as an image, with the passage in `hl` highlighted. Works in every browser
    (no PDF plugin needed). Highlighting needs a text layer, so scans just show the page."""
    doc = pymupdf.open(document_path(name))
    try:
        if not 1 <= number <= doc.page_count:
            raise HTTPException(404, "No such page.")
        page = doc[number - 1]
        for line in hl.splitlines()[:25]:
            words = re.sub(r"[*#|]", " ", line).split()
            for start in range(0, min(len(words), 30), 6):        # match in 6-word pieces, so
                phrase = " ".join(words[start:start + 6])          # line breaks in the PDF don't
                if len(phrase) >= 12:                              # stop the highlight
                    quads = page.search_for(phrase, quads=True)
                    if quads:
                        page.add_highlight_annot(quads)            # in memory only; file untouched
        png = page.get_pixmap(dpi=110, annots=True).tobytes("png")
    finally:
        doc.close()
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/documents/{name}/summary")
def document_summary(name: str, request: Request):
    doc_path = document_path(name)
    chunks, _ = selected([doc_path.name])
    return stream_events(request, safely(summary_events(doc_path.name, chunks,
                                                        summary_path_for(doc_path))))


# ---------- Asking ----------
class Message(BaseModel):
    role: str
    content: str


class AskRequest(BaseModel):
    question: str
    history: list[Message] = []
    documents: list[str] | None = None                  # None = search everything


def ask_events(question, history, documents):
    chunks, vectors = selected(documents)
    if not chunks:
        yield {"type": "sources", "query": question, "sources": []}
        yield {"type": "token", "text": "No documents are selected. Tick at least one in the library."}
        yield {"type": "done"}
        return
    query = standalone_question(question, history)
    results = search(query, chunks, vectors)
    sources = [{"label": label(c), "name": c["source"], "page": c["pages"][0], "score": round(s, 3),
                "section": c.get("section"), "text": c["text"]} for s, c in results]
    yield {"type": "sources", "query": query, "sources": sources}
    for piece in stream_answer(question, results, history) if results else [NOT_FOUND]:
        yield {"type": "token", "text": piece}
    yield {"type": "done"}


@app.post("/api/ask")
def ask(req: AskRequest, request: Request):
    """Events: {"type": "sources", ...}, {"type": "token", "text": ...} pieces, {"type": "done"}."""
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "Question is empty.")
    history = [m.model_dump() for m in req.history if m.role in ("user", "assistant")]
    return stream_events(request, safely(ask_events(question, history, req.documents)))


# ---------- Quiz ----------
class QuizRequest(BaseModel):
    documents: list[str] | None = None
    count: int = 5


@app.post("/api/quiz")
def quiz(req: QuizRequest, request: Request):
    chunks, _ = selected(req.documents)
    if not chunks:
        raise HTTPException(400, "No documents are selected. Tick at least one in the library.")
    return stream_events(request, safely(quiz_events(chunks, max(1, min(req.count, 10)))))


# ---------- Saved chats ----------
class ChatIn(BaseModel):
    title: str
    messages: list[dict]


def chat_path(chat_id):
    if not CHAT_ID.match(chat_id):
        raise HTTPException(400, "Bad chat id.")
    return CHATS_DIR / f"{chat_id}.json"


@app.get("/api/chats")
def list_chats():
    chats = []
    for path in CHATS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            chats.append({"id": path.stem, "title": data.get("title", "Chat"),
                          "updated": data.get("updated", 0)})
        except (OSError, ValueError):
            continue                                    # skip a half-written or broken file
    return sorted(chats, key=lambda c: c["updated"], reverse=True)


@app.get("/api/chats/{chat_id}")
def get_chat(chat_id: str):
    path = chat_path(chat_id)
    if not path.exists():
        raise HTTPException(404, "No such chat.")
    return json.loads(path.read_text(encoding="utf-8"))


@app.put("/api/chats/{chat_id}")
def save_chat(chat_id: str, chat: ChatIn):
    data = {"title": chat.title[:120], "messages": chat.messages, "updated": time.time()}
    path = chat_path(chat_id)
    tmp = path.with_name(f"{chat_id}.{uuid.uuid4().hex}.tmp")      # one temp file per save
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for _ in range(20):                                 # write-then-swap: never a half-written chat
        try:
            tmp.replace(path)
            return {"id": chat_id, "updated": data["updated"]}
        except PermissionError:                         # Windows: someone is reading it right now
            time.sleep(0.05)
    tmp.unlink(missing_ok=True)
    raise HTTPException(503, "Chat file is busy, try again.")


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str):
    chat_path(chat_id).unlink(missing_ok=True)
    return {"deleted": chat_id}


# ---------- The chat page (Phase 6) ----------
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
