import os
import re
import hmac
import json
import base64
import time
import asyncio
import gc
import hashlib
import secrets
import shutil
import pickle
import tempfile
import threading
from datetime import datetime
from typing import Optional, List

import anthropic
from urllib.parse import quote

from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import pdf_support as pdfs
import rag
from reader3 import Book, BookMetadata, ChapterContent, TOCEntry, LIBRARY_DIR, import_epub, slugify

HERE = os.path.dirname(os.path.abspath(__file__))

app = FastAPI()
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")

# Where are the book folders located? New books go to LIBRARY_DIR;
# folders created by the original reader3 next to the project ('.') are picked up too.
BOOK_DIRS = [LIBRARY_DIR, "."]

AI_MODEL = os.environ.get("READER3_MODEL", "claude-opus-5-5")
AI_EFFORT = os.environ.get("READER3_EFFORT", "low")
# "api" calls the Claude API with ANTHROPIC_API_KEY; "claude-code" runs the local `claude` CLI
# headless, so answers come from the Claude subscription it is logged into
AI_BACKEND = os.environ.get("READER3_BACKEND", "api")


# --- Password protection (required before exposing the reader to the internet) ---

PASSWORD = os.environ.get("READER3_PASSWORD", "")
# Without READER3_PASSWORD the password is chosen in the browser on first visit; only its hash is kept
PASSWORD_FILE = os.path.join(LIBRARY_DIR, ".password")
SESSION_DAYS = 180
COOKIE = "reader3_session"
PUBLIC_PATHS = ("/login", "/setup", "/static/", "/manifest.webmanifest", "/favicon.ico",
                "/internal/nb/")     # called by the local MCP tool server; the handler checks localhost + a secret token


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex()


def _load_password_record() -> str:
    try:
        with open(PASSWORD_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


_password_record = _load_password_record()  # "scrypt$<salt hex>$<hash hex>"


def password_set() -> bool:
    return bool(PASSWORD or _password_record)


def check_password(password: str) -> bool:
    if PASSWORD:
        return hmac.compare_digest(password.encode(), PASSWORD.encode())
    try:
        _, salt, expected = _password_record.split("$")
        return hmac.compare_digest(_hash_password(password, bytes.fromhex(salt)), expected)
    except ValueError:
        return False


def save_password(password: str):
    global _password_record
    salt = secrets.token_bytes(16)
    record = f"scrypt${salt.hex()}${_hash_password(password, salt)}"
    os.makedirs(LIBRARY_DIR, exist_ok=True)
    with open(PASSWORD_FILE, "w", encoding="utf-8") as f:
        f.write(record)
    os.chmod(PASSWORD_FILE, 0o600)
    _password_record = record


def _load_secret() -> bytes:
    """Random signing key kept next to the library, so sessions survive restarts."""
    env = os.environ.get("READER3_SECRET")
    if env:
        return env.encode()
    path = os.path.join(LIBRARY_DIR, ".session_secret")
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        os.makedirs(LIBRARY_DIR, exist_ok=True)
        key = secrets.token_bytes(32)
        with open(path, "wb") as f:
            f.write(key)
        os.chmod(path, 0o600)
        return key


SECRET = _load_secret()


def _sign(expires: int) -> str:
    # The password hash is part of the signature: changing the password logs everyone out
    msg = f"{expires}:{hashlib.sha256((PASSWORD or _password_record).encode()).hexdigest()}".encode()
    return hmac.new(SECRET, msg, hashlib.sha256).hexdigest()


def make_session() -> str:
    expires = int(time.time()) + SESSION_DAYS * 86400
    return f"{expires}.{_sign(expires)}"


def valid_session(token: Optional[str]) -> bool:
    try:
        expires, sig = (token or "").split(".", 1)
        return int(expires) > time.time() and hmac.compare_digest(sig, _sign(int(expires)))
    except ValueError:
        return False


def is_proxied(request: Request) -> bool:
    """Requests that came through Cloudflare Tunnel or another reverse proxy."""
    return any(h in request.headers for h in ("cf-connecting-ip", "x-forwarded-for", "forwarded"))


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if not password_set():
        # Never serve an unprotected library to the internet, and don't let it choose the password
        if is_proxied(request) and os.environ.get("READER3_ALLOW_PUBLIC") != "1":
            return PlainTextResponse(
                "reader3: доступ из интернета без пароля запрещён. "
                "Задайте пароль, открыв читалку в домашней сети, и обновите страницу.", status_code=403)
        if path.startswith(PUBLIC_PATHS):
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Сначала задайте пароль"}, status_code=401)
        return RedirectResponse("/setup", status_code=303)
    if path.startswith(PUBLIC_PATHS) or valid_session(request.cookies.get(COOKIE)):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Требуется вход"}, status_code=401)
    target = path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(target)}", status_code=303)


_failed_logins = {}  # ip -> [timestamps]


def _client_ip(request: Request) -> str:
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")


def _safe_next(target: str) -> str:
    return target if target.startswith("/") and not target.startswith("//") else "/"


def _logged_in(request: Request, next: str) -> RedirectResponse:
    response = RedirectResponse(_safe_next(next), status_code=303)
    response.set_cookie(COOKIE, make_session(), max_age=SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=request.url.scheme == "https")
    return response


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    if password_set():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"next": "/", "error": None, "setup": True})


@app.post("/setup", response_class=HTMLResponse)
async def setup(request: Request, password: str = Form(""), password2: str = Form("")):
    if password_set() or is_proxied(request):
        return RedirectResponse("/login", status_code=303)
    error = None
    if len(password) < 4:
        error = "Пароль должен быть не короче 4 символов"
    elif password != password2:
        error = "Пароли не совпадают"
    if error:
        return templates.TemplateResponse(request, "login.html", {"next": "/", "error": error, "setup": True},
                                          status_code=400)
    save_password(password)
    return _logged_in(request, "/")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/"):
    if not password_set():
        return RedirectResponse("/setup", status_code=303)
    if valid_session(request.cookies.get(COOKIE)):
        return RedirectResponse(_safe_next(next), status_code=303)
    return templates.TemplateResponse(request, "login.html", {"next": _safe_next(next), "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, password: str = Form(""), next: str = Form("/")):
    if not password_set():
        return RedirectResponse("/setup", status_code=303)
    ip = _client_ip(request)
    now = time.time()
    recent = [t for t in _failed_logins.get(ip, []) if now - t < 600]
    if len(recent) >= 10:
        return templates.TemplateResponse(request, "login.html", {
            "next": _safe_next(next), "error": "Слишком много попыток. Попробуйте через несколько минут."}, status_code=429)
    if not check_password(password):
        recent.append(now)
        _failed_logins[ip] = recent
        await asyncio.sleep(1)  # slows down password guessing
        return templates.TemplateResponse(request, "login.html", {
            "next": _safe_next(next), "error": "Неверный пароль"}, status_code=401)
    _failed_logins.pop(ip, None)
    return _logged_in(request, next)


@app.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE)
    return response

_state_lock = threading.Lock()
_book_cache = {}


# --- Storage helpers ---

def book_dir(book_id: str) -> Optional[str]:
    safe = os.path.basename(book_id)
    if safe != book_id or not safe.endswith("_data"):
        return None
    for root in BOOK_DIRS:
        path = os.path.join(root, safe)
        if os.path.isfile(os.path.join(path, "book.pkl")):
            return path
    return None


def list_book_ids() -> List[str]:
    seen = []
    for root in BOOK_DIRS:
        if not os.path.isdir(root):
            continue
        for item in sorted(os.listdir(root)):
            if item.endswith("_data") and item not in seen and os.path.isfile(os.path.join(root, item, "book.pkl")):
                seen.append(item)
    return seen


class _BookUnpickler(pickle.Unpickler):
    # Books pickled by the original reader3 CLI reference classes as __main__.Book etc.
    def find_class(self, module, name):
        if module == "__main__" and name in ("Book", "BookMetadata", "ChapterContent", "TOCEntry"):
            module = "reader3"
        return super().find_class(module, name)


def load_book(book_id: str) -> Optional[Book]:
    """
    Loads the book from the pickle file.
    Cached (and invalidated by mtime) so we don't re-read the disk on every click.
    """
    path = book_dir(book_id)
    if not path:
        return None
    file_path = os.path.join(path, "book.pkl")
    mtime = os.path.getmtime(file_path)
    cached = _book_cache.get(book_id)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(file_path, "rb") as f:
            book = _BookUnpickler(f).load()
    except Exception as e:
        print(f"Error loading book {book_id}: {e}")
        return None
    _book_cache[book_id] = (mtime, book)
    return book


def get_book_or_404(book_id: str) -> Book:
    book = load_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    return book


DEFAULT_STATE = {"chapter": 0, "position": 0.0, "progress": 0.0, "last_read": None,
                 "bookmarks": [], "highlights": []}


def read_state(book_id: str) -> dict:
    path = book_dir(book_id)
    state = dict(DEFAULT_STATE, bookmarks=[], highlights=[])
    if path and os.path.exists(os.path.join(path, "state.json")):
        try:
            with open(os.path.join(path, "state.json"), encoding="utf-8") as f:
                state.update(json.load(f))
        except (OSError, json.JSONDecodeError) as e:
            print(f"Error reading state for {book_id}: {e}")
    return state


def write_state(book_id: str, state: dict):
    path = book_dir(book_id)
    fd, tmp = tempfile.mkstemp(dir=path, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(path, "state.json"))


def chapter_title(book: Book, idx: int) -> str:
    ch = book.spine[idx]
    title = ch.title
    # Books processed by the original reader3 only have "Section N" titles: look them up in the TOC
    if re.fullmatch(r"Section \d+", title or ""):
        for entry in _flat_toc(book.toc):
            if entry.file_href == ch.href:
                return entry.title
    return title


def _flat_toc(entries):
    for e in entries:
        yield e
        yield from _flat_toc(e.children)


def toc_json(book: Book) -> list:
    index = {ch.href: i for i, ch in enumerate(book.spine)}

    def walk(entries):
        return [{
            "title": e.title,
            "chapter": index.get(e.file_href),
            "anchor": e.anchor or None,
            "children": walk(e.children),
        } for e in entries]
    return walk(book.toc)


def chapter_words(ch: ChapterContent) -> int:
    return getattr(ch, "words", 0) or len(ch.text.split())


def cover_url(book_id: str, book: Book) -> Optional[str]:
    cover = getattr(book, "cover", None)
    return f"/read/{book_id}/{cover}" if cover else None


def book_summary(book_id: str, book: Book) -> dict:
    state = read_state(book_id)
    is_pdf = pdfs.is_pdf_book(book_dir(book_id))
    return {
        "kind": "pdf" if is_pdf else "epub",
        "id": book_id,
        "title": book.metadata.title,
        "authors": book.metadata.authors,
        "chapters": len(book.spine),
        "words": sum(chapter_words(c) for c in book.spine),
        "cover": cover_url(book_id, book),
        "progress": state.get("progress", 0.0),
        "last_read": state.get("last_read"),
        "added": book.processed_at,
    }


# --- Pages ---

@app.get("/", response_class=HTMLResponse)
async def library_view(request: Request):
    return templates.TemplateResponse(request, "library.html", {"auth": bool(PASSWORD)})


@app.get("/read/{book_id}", response_class=HTMLResponse)
async def reader_view(request: Request, book_id: str):
    book = get_book_or_404(book_id)
    if pdfs.is_pdf_book(book_dir(book_id)):
        return templates.TemplateResponse(request, "pdf.html", {"book": book, "book_id": book_id})
    return templates.TemplateResponse(request, "reader.html", {"book": book, "book_id": book_id})


@app.get("/read/{book_id}/{chapter_index:int}")
async def legacy_chapter_url(book_id: str, chapter_index: int):
    """Old reader3 URLs: /read/<book>/<index>."""
    return RedirectResponse(f"/read/{book_id}#ch={chapter_index}")


@app.get("/read/{book_id}/images/{image_name}")
async def serve_image(book_id: str, image_name: str):
    """Serves images extracted from the book."""
    path = book_dir(book_id)
    if not path:
        raise HTTPException(status_code=404, detail="Book not found")
    img_path = os.path.join(path, "images", os.path.basename(image_name))
    if not os.path.exists(img_path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(img_path, headers={"Cache-Control": "private, max-age=86400"})


@app.get("/manifest.webmanifest")
async def manifest():
    """Lets phones and tablets install the reader as a full-screen app."""
    return JSONResponse({
        "name": "reader3",
        "short_name": "reader3",
        "description": "EPUB reader for reading books together with LLMs",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#fbfaf7",
        "theme_color": "#fbfaf7",
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            {"src": "/static/favicon.svg", "sizes": "any", "type": "image/svg+xml"},
        ],
    }, media_type="application/manifest+json")


# --- API: library ---

@app.get("/api/config")
async def get_config():
    return {"ai_enabled": ai_available(), "model": AI_MODEL}


@app.get("/api/books")
def list_books():
    books = []
    for book_id in list_book_ids():
        book = load_book(book_id)
        if book:
            books.append(book_summary(book_id, book))
    return books


@app.post("/api/books")
def upload_book(file: UploadFile = File(...)):
    name = os.path.basename(file.filename or "book.epub")
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".epub", ".pdf", ".djvu", ".djv"):
        raise HTTPException(status_code=400, detail="Only .epub, .pdf and .djvu files are supported")

    base = slugify(os.path.splitext(name)[0])
    book_id, n = f"{base}_data", 1
    while book_dir(book_id) or os.path.exists(os.path.join(LIBRARY_DIR, book_id)):
        n += 1
        book_id = f"{base}-{n}_data"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = os.path.join(tmp, name)
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        failure = None
        try:
            if ext == ".pdf":
                book_id, book = pdfs.import_pdf(tmp_path, LIBRARY_DIR, book_id)
            elif ext in (".djvu", ".djv"):
                book_id, book = pdfs.import_djvu(tmp_path, LIBRARY_DIR, book_id)
            else:
                book_id, book = import_epub(tmp_path, LIBRARY_DIR, book_id)
        except Exception as e:
            failure = re.sub(r"'[^']*[\\/]([^\\/']+)'", r"'\1'", str(e))        # keep file names, not the server's folders
        if failure is not None:
            # outside the except block: while the exception is alive, fitz still holds the file and Windows cannot delete it
            gc.collect()
            shutil.rmtree(os.path.join(LIBRARY_DIR, book_id), ignore_errors=True)
            raise HTTPException(status_code=422, detail=f"Could not read this file: {failure}")
    return book_summary(book_id, book)


@app.delete("/api/books/{book_id}")
def delete_book(book_id: str):
    path = book_dir(book_id)
    if not path:
        raise HTTPException(status_code=404, detail="Book not found")
    pdfs.forget(path)
    rag.forget(path)                                    # the notebook index (sqlite) must be closed before the folder goes
    shutil.rmtree(path)
    _book_cache.pop(book_id, None)
    return {"ok": True}


# --- API: reading ---

@app.get("/api/books/{book_id}")
def get_book(book_id: str):
    book = get_book_or_404(book_id)
    m = book.metadata
    return {
        **book_summary(book_id, book),
        "description": m.description,
        "publisher": m.publisher,
        "date": m.date,
        "language": m.language,
        "subjects": m.subjects,
        "toc": toc_json(book),
        "spine": [{"title": chapter_title(book, i), "words": chapter_words(ch)} for i, ch in enumerate(book.spine)],
    }


@app.get("/api/books/{book_id}/chapters/{idx}")
def get_chapter(book_id: str, idx: int):
    book = get_book_or_404(book_id)
    if idx < 0 or idx >= len(book.spine):
        raise HTTPException(status_code=404, detail="Chapter not found")
    ch = book.spine[idx]
    # Images are stored as relative 'images/x.jpg': make them absolute for the single-page reader
    html = re.sub(r'((?:src|href)=")images/', rf'\1/read/{book_id}/images/', ch.content)
    return {"index": idx, "title": chapter_title(book, idx), "html": html, "words": chapter_words(ch)}


@app.get("/api/books/{book_id}/search")
def search_book(book_id: str, q: str, limit: int = 300):
    book = get_book_or_404(book_id)
    q = " ".join(q.split())
    if len(q) < 2:
        return {"query": q, "results": [], "total": 0}
    pattern = re.compile(re.escape(q), re.IGNORECASE)
    results, total = [], 0
    for i, ch in enumerate(book.spine):
        for n, m in enumerate(pattern.finditer(ch.text)):
            total += 1
            if len(results) >= limit:
                continue
            start, end = m.start(), m.end()
            results.append({
                "chapter": i,
                "chapter_title": chapter_title(book, i),
                "occurrence": n,
                "before": ("…" if start > 70 else "") + ch.text[max(0, start - 70):start],
                "match": ch.text[start:end],
                "after": ch.text[end:end + 90] + ("…" if end + 90 < len(ch.text) else ""),
            })
    return {"query": q, "results": results, "total": total}


class StatePatch(BaseModel):
    chapter: Optional[int] = None
    position: Optional[float] = None
    progress: Optional[float] = None
    bookmarks: Optional[list] = None
    highlights: Optional[list] = None


@app.get("/api/books/{book_id}/state")
def get_state(book_id: str):
    get_book_or_404(book_id)
    return read_state(book_id)


@app.patch("/api/books/{book_id}/state")
def patch_state(book_id: str, patch: StatePatch):
    get_book_or_404(book_id)
    with _state_lock:
        state = read_state(book_id)
        changes = patch.model_dump(exclude_none=True)
        state.update(changes)
        if {"chapter", "position", "progress"} & changes.keys():
            state["last_read"] = datetime.now().isoformat(timespec="seconds")
        write_state(book_id, state)
    return state


# --- API: AI reading companion ---

def ai_available() -> bool:
    if AI_BACKEND == "claude-code":
        return shutil.which("claude") is not None
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.path.isdir(os.path.expanduser("~/.config/anthropic")))


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    chapter: int
    messages: List[ChatMessage]


SYSTEM_PROMPT = """You are a thoughtful reading companion. The user is reading a book in an e-reader and talks to you \
about it while reading. The book's details and the full text of the chapter they are currently reading are below.

Help them understand, discuss and remember what they read: explain difficult passages, give historical, cultural \
or scientific context, summarize, and discuss ideas and characters. Ground your answers in the chapter text and \
quote it briefly when that helps. Do not reveal plot developments beyond the current chapter unless the user \
explicitly asks for spoilers. Answer in the language the user writes in. Keep answers focused and readable: \
short paragraphs, Markdown lists or bold only where they genuinely help."""


PDF_SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
    "the full text of the chapter they are currently reading are below.",
    "the text of the page they are looking at and of a few neighbouring pages are below.") + """

This book is a PDF read page by page. The reader sees each page exactly as printed (columns, photos, screenshots, diagrams, captions); you only get the extracted text, marked <page number="N"> (N is the page number shown in the viewer; the one with current="true" is on screen). Pictures are not visible to you: when a question depends on an image or a diagram, say so and ask the user to describe it instead of guessing. Mention page numbers when helpful."""


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/books/{book_id}/chat")
async def chat(book_id: str, req: ChatRequest):
    book = get_book_or_404(book_id)
    if req.chapter < 0 or req.chapter >= len(book.spine):
        raise HTTPException(status_code=404, detail="Chapter not found")
    messages = [{"role": m.role, "content": m.content} for m in req.messages
                if m.role in ("user", "assistant") and m.content.strip()]
    if not messages or messages[-1]["role"] != "user":
        raise HTTPException(status_code=400, detail="The last message must come from the user")

    m = book.metadata
    chapter = book.spine[req.chapter]
    context = (f"<book>\nTitle: {m.title}\nAuthors: {', '.join(m.authors) or 'unknown'}\n"
               f"Language: {m.language}\n</book>\n\n"
               f"<current_chapter index=\"{req.chapter + 1}\" of=\"{len(book.spine)}\" "
               f"title=\"{chapter_title(book, req.chapter)}\">\n{chapter.text}\n</current_chapter>")

    return answer(context, messages)


PDF_SYSTEM_PROMPT_VISION = PDF_SYSTEM_PROMPT.replace(
    "Pictures are not visible to you: when a question depends on an image or a diagram, say so and ask the user to describe it instead of guessing.",
    "You are also shown an image of the page or pages on screen: use it for pictures, screenshots, diagrams and layout, and the text for exact wording.")


def answer(context: str, messages: List[dict], system_prompt: str = SYSTEM_PROMPT,
           images: Optional[List[str]] = None) -> StreamingResponse:
    """Streams the companion's reply (SSE) for a book context, through Claude Code or the Claude API."""
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    if AI_BACKEND == "claude-code":
        return StreamingResponse(claude_code_stream(context, messages, system_prompt, images),
                                 media_type="text/event-stream", headers=headers)
    if images:      # pictures of the pages on screen ride with the last question
        blocks = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b}} for b in images]
        messages = messages[:-1] + [{"role": "user", "content": blocks + [{"type": "text", "text": messages[-1]["content"]}]}]

    async def generate():
        try:
            client = anthropic.AsyncAnthropic()
            async with client.beta.messages.stream(
                model=AI_MODEL,
                max_tokens=64000,
                # The book context is stable across the conversation, so it is cached
                system=[{"type": "text", "text": system_prompt},
                        {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}}],
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": AI_EFFORT},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                async for text in stream.text_stream:
                    yield sse("delta", {"text": text})
                final = await stream.get_final_message()
            if final.stop_reason == "refusal":
                yield sse("error", {"message": "Модель отказалась отвечать на этот запрос."})
            elif final.stop_reason == "max_tokens":
                yield sse("error", {"message": "Ответ оборван: достигнут лимит длины."})
            yield sse("done", {"model": final.model})
        except anthropic.AuthenticationError:
            yield sse("error", {"message": "Нет доступа к Claude API: проверьте ANTHROPIC_API_KEY."})
        except anthropic.RateLimitError:
            yield sse("error", {"message": "Слишком много запросов к Claude API, попробуйте чуть позже."})
        except anthropic.APIStatusError as e:
            yield sse("error", {"message": f"Ошибка Claude API ({e.status_code}): {e.message}"})
        except anthropic.APIConnectionError:
            yield sse("error", {"message": "Не удалось подключиться к Claude API."})
        except Exception as e:  # e.g. no credentials configured at all
            yield sse("error", {"message": f"Ошибка: {e}"})

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- API: PDF books ---

def pdf_dir_or_404(book_id: str) -> str:
    get_book_or_404(book_id)
    path = book_dir(book_id)
    if not pdfs.is_pdf_book(path):
        raise HTTPException(status_code=404, detail="This book is not a PDF")
    return path


class PdfChatRequest(BaseModel):
    page: int
    window: int = 2
    pages: Optional[List[int]] = None   # the pages on screen (a spread has two)
    images: bool = False                # also show the model pictures of those pages
    messages: List[ChatMessage]


@app.get("/api/pdf/{book_id}/info")
def pdf_info(book_id: str):
    path = pdf_dir_or_404(book_id)
    pdfs.ensure_ocr(path)
    return pdfs.info(path)


@app.get("/api/pdf/{book_id}/page/{n}.jpg")
def pdf_page_image(book_id: str, n: int, w: int = 1200):
    path = pdf_dir_or_404(book_id)
    try:
        if n < 0 or n >= pdfs.page_count(path):
            raise HTTPException(status_code=404, detail="Page not found")
        image = pdfs.render_page(path, n, w)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Book not found")
    return FileResponse(image, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/pdf/{book_id}/text/{n}")
def pdf_page_text(book_id: str, n: int):
    path = pdf_dir_or_404(book_id)
    try:
        return {"page": n, "text": pdfs.page_text(path, n)}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Book not found")


@app.get("/api/pdf/{book_id}/search")
def pdf_search(book_id: str, q: str, limit: int = 100):
    return pdfs.search(pdf_dir_or_404(book_id), q, limit)


@app.get("/api/pdf/{book_id}/file")
def pdf_file(book_id: str):
    path = pdf_dir_or_404(book_id)
    media = "image/vnd.djvu" if pdfs.is_djvu_book(path) else "application/pdf"
    return FileResponse(pdfs.source_file(path), media_type=media)


@app.post("/api/pdf/{book_id}/chat")
def pdf_chat(book_id: str, req: PdfChatRequest):
    path = pdf_dir_or_404(book_id)
    n = pdfs.page_count(path)
    if req.page < 0 or req.page >= n:
        raise HTTPException(status_code=404, detail="Page not found")
    messages = [{"role": m.role, "content": m.content} for m in req.messages
                if m.role in ("user", "assistant") and m.content.strip()]
    if not messages or messages[-1]["role"] != "user":
        raise HTTPException(status_code=400, detail="The last message must come from the user")
    book = get_book_or_404(book_id)
    m = book.metadata
    context = (f"<book>\nTitle: {m.title}\nAuthors: {', '.join(m.authors) or 'unknown'}\n"
               f"Language: {m.language}\nPages: {n}\n</book>\n\n"
               + pdfs.context_text(path, req.page, max(0, min(req.window, 6))))
    images = None
    if req.images:
        shown = [p for p in (req.pages or [req.page]) if 0 <= p < n][:2]
        images = [base64.b64encode(open(pdfs.render_page(path, p, 1200), "rb").read()).decode() for p in shown]
    return answer(context, messages, PDF_SYSTEM_PROMPT_VISION if images else PDF_SYSTEM_PROMPT, images)


_claude_slots = threading.BoundedSemaphore(3)


def claude_code_stream(context: str, messages: List[dict], system_prompt: str = SYSTEM_PROMPT,
                       images: Optional[List[str]] = None):
    """At most three headless Claude Code processes at a time."""
    if not _claude_slots.acquire(timeout=120):
        yield sse("error", {"message": "Сервер занят другими запросами, попробуйте через минуту."})
        return
    try:
        yield from _claude_code_run(context, messages, system_prompt, images)
    finally:
        _claude_slots.release()


def _claude_code_run(context: str, messages: List[dict], system_prompt: str = SYSTEM_PROMPT,
                     images: Optional[List[str]] = None):
    """Answers through headless Claude Code (`claude -p`) on the subscription it is logged into."""
    import subprocess

    # The chapter can exceed the Windows command-line limit, so the system prompt goes through a file
    fd, prompt_file = tempfile.mkstemp(suffix=".md", prefix="reader3-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(system_prompt + "\n\n" + context)
    history = "\n\n".join(f"<{m['role']}>\n{m['content']}\n</{m['role']}>" for m in messages[:-1])
    prompt = (f"Earlier conversation:\n{history}\n\nNew message from the user:\n" if history else "") \
        + messages[-1]["content"]
    cmd = [shutil.which("claude") or "claude", "-p", "--model", AI_MODEL, "--effort", AI_EFFORT,
           "--system-prompt-file", prompt_file, "--tools", "", "--strict-mcp-config", "--setting-sources", "",
           "--disable-slash-commands", "--no-session-persistence",
           "--output-format", "stream-json", "--include-partial-messages", "--verbose"]
    if images:      # text and page pictures go in as one stream-json user message
        cmd += ["--input-format", "stream-json"]
        blocks = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b}} for b in images]
        prompt = json.dumps({"type": "user", "message": {"role": "user",
                             "content": blocks + [{"type": "text", "text": prompt}]}}) + "\n"
    # Without the key the CLI uses its own login (the subscription), not paid API credits
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}

    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, cwd=tempfile.gettempdir(), encoding="utf-8", errors="replace")
    except OSError as e:
        os.unlink(prompt_file)
        yield sse("error", {"message": f"Не удалось запустить Claude Code: {e}"})
        return
    err_chunks = []
    err_thread = threading.Thread(target=lambda: err_chunks.append(proc.stderr.read()), daemon=True)
    err_thread.start()                      # a full stderr pipe must never stall the CLI
    timer = threading.Timer(600, proc.kill)  # no answer takes more than ten minutes
    timer.start()
    errored = False
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
        model, got_text = AI_MODEL, False
        # A sync generator: Starlette iterates it in a worker thread, so blocking reads are fine
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "stream_event":
                inner = event.get("event", {})
                if inner.get("type") == "message_start":
                    model = inner.get("message", {}).get("model", model)
                delta = inner.get("delta", {})
                if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                    got_text = True
                    yield sse("delta", {"text": delta["text"]})
            elif event.get("type") == "result":
                if event.get("is_error"):
                    errored = True
                    yield sse("error", {"message": f"Claude Code: {event.get('result') or 'ошибка'}"})
                elif not got_text and event.get("result"):
                    yield sse("delta", {"text": event["result"]})
        proc.wait()
        err_thread.join(3)
        if proc.returncode and not got_text and not errored:
            err = (err_chunks[0] if err_chunks else "").strip()
            yield sse("error", {"message": f"Claude Code завершился с ошибкой: {err[-500:] or proc.returncode}"})
        yield sse("done", {"model": model})
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        try:
            os.unlink(prompt_file)
        except OSError:
            pass


# --- Notebook mode (RAG chat over books, Studio outputs, notes) ---

import notebook_api  # noqa: E402

notebook_api.register(app, {
    "book_dir": book_dir, "list_book_ids": list_book_ids, "load_book": load_book,
    "library_dir": LIBRARY_DIR, "model": AI_MODEL, "backend": AI_BACKEND,
    "port": lambda: int(os.environ.get("PORT", "8123")),
})


@app.get("/notebook", response_class=HTMLResponse)
async def notebook_view(request: Request):
    return templates.TemplateResponse(request, "notebook.html", {})


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8123"))
    print(f"reader3 запущен: http://localhost:{port}")
    if host in ("0.0.0.0", "::"):
        # Show the address to open on a phone or tablet in the same network
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("10.255.255.255", 1))
                print(f"Откройте на планшете или телефоне (та же Wi-Fi-сеть): http://{sock.getsockname()[0]}:{port}")
        except OSError:
            pass
    if not password_set():
        print("Пароль ещё не задан: откройте читалку и придумайте его на первом экране.")
    elif not PASSWORD:
        print(f"Забыли пароль? Удалите файл {PASSWORD_FILE} и перезапустите сервер.")
    # Trust X-Forwarded-Proto from the tunnel so the session cookie is marked Secure over HTTPS
    uvicorn.run(app, host=host, port=port, proxy_headers=True,
                forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"))
