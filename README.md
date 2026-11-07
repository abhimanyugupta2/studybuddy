# StudyBuddy

Ask questions about your own PDFs, scans and photos of notes, and get answers from a local AI,
with the file and page each answer came from. Everything runs on your computer through [Ollama](https://ollama.com).

## Setup (once)

1. Install Ollama, then pull the two models:
   ```bash
   ollama pull llama3.1:8b
   ollama pull nomic-embed-text
   ```
2. Create the Python environment:
   ```bash
   cd backend
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

## Run the web app

```bash
cd backend
.venv\Scripts\activate
uvicorn server:app --reload
```

Open http://localhost:8000, add documents with **＋ Add PDF or photo** (or drag them onto it), and
ask away. Supported: `.pdf .png .jpg .jpeg .tif .tiff .bmp`. Scans and photos are read with OCR.
Follow-up questions like "explain that more simply" use the conversation so far.

## Run in the terminal instead

Put PDFs or photos in `backend/data/`, then:

```bash
python ask.py data
```

## How it works

| File | Job |
|---|---|
| `backend/ingest.py` | Any PDF/scan/photo → layout-aware text (headings, tables; page headers/footers dropped; OCR where there's no text layer) → chunks of ~800 characters, split at section headings |
| `backend/retrieve.py` | Chunks → 768-number vectors (`nomic-embed-text`). Hybrid search: meaning (cosine similarity) + keywords (BM25), merged by Reciprocal Rank Fusion → top 5 chunks. Off-topic questions (best meaning score below `MIN_SCORE`) get no chunks |
| `backend/ask.py` | Rewrites follow-ups into standalone questions, then has `llama3.1:8b` answer **only** from the retrieved chunks |
| `backend/server.py` | FastAPI: upload/list/delete documents, stream answers, serve the web page |
| `frontend/` | The chat page (plain HTML, CSS and JavaScript) |

Your documents live in `backend/data/` and their index files in `backend/data/.index/`; neither is
committed to git. Documents are re-processed automatically when they change or when `ingest.py` changes.
