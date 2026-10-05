import os
import re
import hmac
import json
import time
import asyncio
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


# --- Password protection (required before exposing the reader to the internet) ---

PASSWORD = os.environ.get("READER3_PASSWORD", "")
SESSION_DAYS = 180
COOKIE = "reader3_session"
PUBLIC_PATHS = ("/login", "/static/", "/manifest.webmanifest", "/favicon.ico")


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


SECRET = _load_secret() if PASSWORD else b""


def _sign(expires: int) -> str:
    # The password hash is part of the signature: changing the password logs everyone out
    msg = f"{expires}:{hashlib.sha256(PASSWORD.encode()).hexdigest()}".encode()
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
    if not PASSWORD:
        # Never serve an unprotected library to the internet
        if is_proxied(request) and os.environ.get("READER3_ALLOW_PUBLIC") != "1":
            return PlainTextResponse(
                "reader3: доступ из интернета без пароля запрещён. "
                "Задайте переменную окружения READER3_PASSWORD и перезапустите сервер.", status_code=403)
        return await call_next(request)
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


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/"):
    if not PASSWORD or valid_session(request.cookies.get(COOKIE)):
        return RedirectResponse(_safe_next(next), status_code=303)
    return templates.TemplateResponse(request, "login.html", {"next": _safe_next(next), "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, password: str = Form(""), next: str = Form("/")):
    if not PASSWORD:
        return RedirectResponse("/", status_code=303)
    ip = _client_ip(request)
    now = time.time()
    recent = [t for t in _failed_logins.get(ip, []) if now - t < 600]
    if len(recent) >= 10:
        return templates.TemplateResponse(request, "login.html", {
            "next": _safe_next(next), "error": "Слишком много попыток. Попробуйте через несколько минут."}, status_code=429)
    if not hmac.compare_digest(password.encode(), PASSWORD.encode()):
        recent.append(now)
        _failed_logins[ip] = recent
        await asyncio.sleep(1)  # slows down password guessing
        return templates.TemplateResponse(request, "login.html", {
            "next": _safe_next(next), "error": "Неверный пароль"}, status_code=401)
    _failed_logins.pop(ip, None)
    response = RedirectResponse(_safe_next(next), status_code=303)
    response.set_cookie(COOKIE, make_session(), max_age=SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=request.url.scheme == "https")
    return response


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
    return {
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
    return FileResponse(img_path, headers={"Cache-Control": "public, max-age=86400"})


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
    if not name.lower().endswith(".epub"):
        raise HTTPException(status_code=400, detail="Only .epub files are supported")

    base = slugify(os.path.splitext(name)[0])
    book_id, n = f"{base}_data", 1
    while book_dir(book_id) or os.path.exists(os.path.join(LIBRARY_DIR, book_id)):
        n += 1
        book_id = f"{base}-{n}_data"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = os.path.join(tmp, name)
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        try:
            book_id, book = import_epub(tmp_path, LIBRARY_DIR, book_id)
        except Exception as e:
            shutil.rmtree(os.path.join(LIBRARY_DIR, book_id), ignore_errors=True)
            raise HTTPException(status_code=422, detail=f"Could not read this EPUB: {e}")
    return book_summary(book_id, book)


@app.delete("/api/books/{book_id}")
def delete_book(book_id: str):
    path = book_dir(book_id)
    if not path:
        raise HTTPException(status_code=404, detail="Book not found")
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

    async def generate():
        try:
            client = anthropic.AsyncAnthropic()
            async with client.beta.messages.stream(
                model=AI_MODEL,
                max_tokens=64000,
                # The book context is stable across the conversation, so it is cached
                system=[{"type": "text", "text": SYSTEM_PROMPT},
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


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8123"))
    print(f"Starting server at http://{host}:{port}")
    if host in ("0.0.0.0", "::"):
        # Show the address to open on a phone or tablet in the same network
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("10.255.255.255", 1))
                print(f"On your phone or tablet: http://{sock.getsockname()[0]}:{port}")
        except OSError:
            pass
    if not PASSWORD:
        print("Password protection is off. Set READER3_PASSWORD before exposing the reader to the internet.")
    # Trust X-Forwarded-Proto from the tunnel so the session cookie is marked Secure over HTTPS
    uvicorn.run(app, host=host, port=port, proxy_headers=True,
                forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"))
