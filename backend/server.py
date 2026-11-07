"""
Phase 5: StudyBuddy as a web API (and Phase 6: it also serves the chat page).

Endpoints:  GET    /api/documents          list PDFs in the library
            POST   /api/documents          upload a PDF (form field "file")
            DELETE /api/documents/{name}   remove a PDF and its index files
            POST   /api/ask                {"question", "history"} -> streamed answer
Run:        uvicorn server:app --reload     then open http://localhost:8000
"""
import json
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ask import NOT_FOUND, standalone_question, stream_answer
from ingest import chunks_path_for
from retrieve import EMBED_DIM, label, load_library, search, vectors_path

DATA_DIR = Path(__file__).parent / "data"
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
MAX_UPLOAD_MB = 50

# One tuple, swapped in a single step, so a question never sees new chunks with old vectors.
EMPTY = ([], np.zeros((0, EMBED_DIM), dtype=np.float32))
library = {"index": EMPTY}


def reload_library():
    """(Re)build the in-memory index from every PDF in data/."""
    library["index"] = load_library(DATA_DIR) if any(DATA_DIR.glob("*.pdf")) else EMPTY


def remove_document_files(pdf_path):
    for path in (pdf_path, chunks_path_for(pdf_path), vectors_path(chunks_path_for(pdf_path))):
        path.unlink(missing_ok=True)


@asynccontextmanager
async def lifespan(app):
    DATA_DIR.mkdir(exist_ok=True)
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
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported.")
    data = file.file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"PDF is larger than {MAX_UPLOAD_MB} MB.")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "That file doesn't look like a PDF.")
    pdf_path = DATA_DIR / name
    pdf_path.write_bytes(data)
    try:
        reload_library()                                # ingests + embeds the new file
    except Exception as e:
        remove_document_files(pdf_path)
        reload_library()
        raise HTTPException(422, f"Couldn't read that PDF: {e}")
    added = [d for d in list_documents() if d["name"] == name]
    if not added:
        remove_document_files(pdf_path)
        reload_library()
        raise HTTPException(422, "No text found in that PDF (is it a scanned image?).")
    return added[0]


@app.delete("/api/documents/{name}")
def delete_document(name: str):
    pdf_path = DATA_DIR / Path(name).name
    if pdf_path.suffix.lower() != ".pdf" or not pdf_path.exists():
        raise HTTPException(404, "No such document.")
    remove_document_files(pdf_path)
    reload_library()
    return {"deleted": pdf_path.name}


# ---------- Asking ----------
class Message(BaseModel):
    role: str
    content: str


class AskRequest(BaseModel):
    question: str
    history: list[Message] = []


@app.post("/api/ask")
def ask(req: AskRequest):
    """Streams newline-delimited JSON events:
       {"type": "sources", ...}, then {"type": "token", "text": ...} pieces, then {"type": "done"}."""
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "Question is empty.")
    history = [m.model_dump() for m in req.history if m.role in ("user", "assistant")]

    def events():
        try:
            query = standalone_question(question, history)
            chunks, vectors = library["index"]
            results = search(query, chunks, vectors) if chunks else []
            sources = [{"label": label(c), "score": round(s, 3), "section": c.get("section"),
                        "text": c["text"]} for s, c in results]
            yield json.dumps({"type": "sources", "query": query, "sources": sources}) + "\n"
            pieces = stream_answer(question, results, history) if results else [NOT_FOUND]
            for piece in pieces:
                yield json.dumps({"type": "token", "text": piece}) + "\n"
            yield json.dumps({"type": "done"}) + "\n"
        except Exception as e:                          # e.g. Ollama isn't running
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


# ---------- The chat page (Phase 6) ----------
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
