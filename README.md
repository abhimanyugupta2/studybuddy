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

**Features**
- ☑️ **Choose documents:** tick the ones to search; your choice is remembered.
- ⏹️ **Stop:** the Send button becomes Stop while an answer is written (with a live timer); stopping also stops the model.
- 📝 **One-click summaries:** the 📝 button on a document. Long documents are summarised part by part; summaries are saved.
- 💾 **Saved chats:** every chat is saved on your computer and listed in the sidebar.
- 📖 **Document viewer:** "Open page" on any source shows that page with the passage highlighted.
- 🧠 **Quiz mode:** multiple-choice questions from the ticked documents. Each question is double-checked by
  having the model answer it from the source, and unclear ones are replaced.

## Run in the terminal instead

Put PDFs or photos in `backend/data/`, then:

```bash
python ask.py data
```

## How it works

| File | Job |
|---|---|
| `backend/ingest.py` | Any PDF/scan/photo → layout-aware text (headings, tables; page headers/footers dropped; OCR where there's no text layer) → chunks of ~800 characters, split at section headings |
| `backend/retrieve.py` | Chunks → 768-number vectors (`nomic-embed-text`). Hybrid search: every wording of the question is ranked by meaning (cosine similarity) and keywords (BM25), plus an imagined answer (HyDE); all rankings merged by Reciprocal Rank Fusion → the best chunks up to ~8,000 characters. Off-topic questions (score below `MIN_SCORE`) get no chunks |
| `backend/ask.py` | Peeks at the documents, then plans the search: a standalone question, 2 rephrasings in the documents' own words and an imagined answer. `llama3.1:8b` answers **only** from the retrieved chunks and quotes its evidence |
| `backend/study.py` | One-click summaries (map-reduce for long documents) and verified quizzes |
| `backend/server.py` | FastAPI: documents, page images with highlights, streamed answers/summaries/quizzes (stoppable), saved chats, the web page |
| `frontend/` | The chat page (plain HTML, CSS and JavaScript) |

Your documents live in `backend/data/`, their index files and summaries in `backend/data/.index/`, and
saved chats in `backend/data/.chats/`; none of it is committed to git. Documents are re-processed automatically when they change or when `ingest.py` changes.
