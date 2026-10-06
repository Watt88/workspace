"""PDF books for reader3: pages are shown exactly as printed (rendered images), while a text version of every
page feeds the AI companion and search.

Text per page comes from the PDF's own text layer. Some PDFs (e.g. printed to PDF from iOS) carry a broken layer
that extracts as nonsense; such pages are detected and read by OCR (Tesseract) instead, in the background.
A book can also ship its own exact text (`given_texts`), which wins over both.
"""
import datetime
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Dict, List, Optional

import pymupdf as fitz

import djvu_support as djv

from reader3 import Book, BookMetadata, ChapterContent, TOCEntry, save_to_pickle, slugify

PAGES_FILE = "pages.json"
_json_lock = threading.RLock()
_render_lock = threading.Lock()
_ocr_lock = threading.Lock()
_workers: Dict[str, threading.Thread] = {}
_docs: Dict[str, "fitz.Document"] = {}
_closed = set()          # books that were deleted: nobody may reopen their PDF
_counts: Dict[str, int] = {}


def _key(book_dir: str) -> str:
    return os.path.abspath(book_dir)

# ---------------------------------------------------------------- text quality

_INNER = re.compile(r"\w[,.!?:;()#@*]\w")
_NOT_GARBLE = re.compile(r"\d[.,:/]\d|https?:|www\.|@\w+\.\w|\.(com|ru|org|net|io)\b|(?:^|\W)\w\.\w\.", re.I)


def looks_garbled(text: str) -> bool:
    """True for a broken text layer: punctuation inside words or Latin/Cyrillic mixed inside one word, far too often.

    Calibrated on a real book: broken pages score 0.06-0.40, correct ones stay below 0.06 (rule: > 0.08)."""
    toks = re.findall(r"\S+", text)
    if len(toks) < 8:
        return False
    inner = sum(1 for t in toks if _INNER.search(t) and not _NOT_GARBLE.search(t))
    mixed = sum(1 for t in toks if "-" not in t and "_" not in t and re.search("[A-Za-z]", t) and re.search("[А-Яа-яЁё]", t))
    return inner / len(toks) > 0.08 or mixed / len(toks) > 0.08


def clean_text(text: str) -> str:
    """Join end-of-line hyphenation and unwrap printed lines into paragraphs."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00ad", "")
    text = re.sub(r"(\w)-\n(?=[a-zа-яё])", r"\1", text)
    text = re.sub(r"(?<![.!?…»\"])\n(?=[a-zа-яё])", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


# ---------------------------------------------------------------- tesseract

_tess = {"checked": False, "exe": None, "lang": None}
_tess_lock = threading.Lock()


def tesseract():
    with _tess_lock:
        if not _tess["checked"]:
            exe = shutil.which("tesseract")
            if not exe:
                for cand in (r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                             "/usr/bin/tesseract", "/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract"):
                    if os.path.exists(cand):
                        exe = cand
                        break
            lang = None
            if exe:
                try:
                    out = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, timeout=30).stdout.split()
                    lang = "+".join(l for l in ("rus", "eng") if l in out) or None
                except Exception:
                    lang = None
            _tess.update(exe=exe, lang=lang, checked=True)
        return _tess["exe"], _tess["lang"]


def ocr_available() -> bool:
    exe, lang = tesseract()
    return bool(exe and lang)


# ---------------------------------------------------------------- storage

def pdf_path(book_dir: str) -> str:
    return os.path.join(book_dir, "book.pdf")


def djvu_path(book_dir: str) -> str:
    return os.path.join(book_dir, "book.djvu")


def is_djvu_book(book_dir: Optional[str]) -> bool:
    return bool(book_dir) and os.path.isfile(djvu_path(book_dir))


def is_pdf_book(book_dir: Optional[str]) -> bool:
    """A book shown page by page: a PDF or a DjVu file (same viewer, same text/OCR/RAG pipeline)."""
    return bool(book_dir) and (os.path.isfile(pdf_path(book_dir)) or os.path.isfile(djvu_path(book_dir)))


def source_file(book_dir: str) -> str:
    return djvu_path(book_dir) if is_djvu_book(book_dir) else pdf_path(book_dir)


def _read_pages(book_dir: str) -> dict:
    with _json_lock:
        with open(os.path.join(book_dir, PAGES_FILE), encoding="utf-8") as f:
            return json.load(f)


def _write_pages(book_dir: str, data: dict):
    with _json_lock:
        fd, tmp = tempfile.mkstemp(dir=book_dir, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(book_dir, PAGES_FILE))


def _doc(book_dir: str):
    k = _key(book_dir)
    if k in _closed or not os.path.isfile(pdf_path(book_dir)):
        raise FileNotFoundError("book was removed")
    d = _docs.get(k)
    if d is None or d.is_closed:
        d = fitz.open(pdf_path(book_dir))
        _docs[k] = d
    return d


def forget(book_dir: str):
    """Before deleting a book: no new users, close the handle once the current render/OCR step is done."""
    k = _key(book_dir)
    _closed.add(k)
    _counts.pop(k, None)
    with _render_lock:
        d = _docs.pop(k, None)
        if d is not None and not d.is_closed:
            d.close()


def page_count(book_dir: str) -> int:
    k = _key(book_dir)
    if k not in _counts:
        _counts[k] = _read_pages(book_dir)["n"]
    return _counts[k]


# ---------------------------------------------------------------- import

def _language(sample: str) -> str:
    cyr = len(re.findall("[А-Яа-яЁё]", sample))
    lat = len(re.findall("[A-Za-z]", sample))
    return "ru" if cyr > lat else "en"


def import_pdf(src: str, library_dir: str, book_id: Optional[str] = None, title: Optional[str] = None,
               authors: Optional[List[str]] = None, given_texts: Optional[List[str]] = None) -> (str, Book):
    """Creates `<library_dir>/<book_id>` for a PDF. Returns (book_id, Book). The text of broken pages is read in the
    background (see `ensure_ocr`)."""
    os.makedirs(library_dir, exist_ok=True)
    if book_id is None:
        book_id = slugify(os.path.splitext(os.path.basename(src))[0]) + "_data"
    out = os.path.join(library_dir, book_id)
    os.makedirs(os.path.join(out, "images"), exist_ok=True)
    _closed.discard(_key(out))
    _counts.pop(_key(out), None)
    if os.path.abspath(src) != os.path.abspath(pdf_path(out)):
        shutil.copyfile(src, pdf_path(out))

    doc = fitz.open(pdf_path(out))
    if doc.needs_pass:
        doc.close()
        raise ValueError("PDF защищён паролем")
    n = len(doc)
    if n == 0:
        doc.close()                              # an open handle would keep the half-made folder from being removed on Windows
        raise ValueError("В PDF нет страниц")
    meta = doc.metadata or {}
    name = (title or meta.get("title") or "").strip()
    if not name or name.lower() in ("untitled", "без названия"):
        name = " ".join(os.path.splitext(os.path.basename(src))[0].replace("_", " ").split())
    auth = authors if authors is not None else ([meta["author"].strip()] if (meta.get("author") or "").strip() else [])

    pages = []
    sample = ""
    garbled = 0
    for i in range(n):
        if given_texts is not None:
            text, state = given_texts[i].strip(), "given"
        else:
            text = clean_text(doc[i].get_text("text"))
            state = "pdf"
            if looks_garbled(text):
                text, state = "", "pending"
                garbled += 1
            elif len(text) < 25 and doc[i].get_images():
                state = "pending"             # a scanned page: no text layer at all
        if text and len(sample) < 20000:
            sample += " " + text
        pages.append({"t": text, "s": state})
    if garbled > n * 0.3:                      # a broken layer is a property of the whole book
        for p in pages:
            if p["s"] == "pdf" and len(p["t"]) < 25:
                p["s"] = "pending"

    outline = [[lvl, t, pg - 1] for lvl, t, pg in doc.get_toc(simple=True) if 1 <= pg <= n]
    r = doc[0].rect
    lang = _language(sample) if len(sample) > 400 else "ru"       # too little real text to tell: default to Russian
    _write_pages(out, {"n": n, "language": lang, "size": [r.width, r.height], "pages": pages, "outline": outline,
                       "ocr_available": ocr_available()})

    # cover = first page
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(480 / r.width, 480 / r.width), colorspace=fitz.csRGB, alpha=False)
    pix.save(os.path.join(out, "images", "cover.jpg"), jpg_quality=85)
    doc.close()

    book = Book(
        metadata=BookMetadata(title=name, language=lang, authors=auth),
        spine=[ChapterContent(id=f"p{i}", href=f"p{i}", title=f"Страница {i + 1}", content="", text="", order=i,
                              words=len(p["t"].split())) for i, p in enumerate(pages)],
        toc=[TOCEntry(title=t, href=f"p{pg}", file_href=f"p{pg}", anchor="") for lvl, t, pg in outline if lvl == 1],
        images={}, source_file=os.path.basename(src),
        processed_at=datetime.datetime.now().isoformat(timespec="seconds"), cover="images/cover.jpg")
    save_to_pickle(book, out)
    ensure_ocr(out)
    return book_id, book


def import_djvu(src: str, library_dir: str, book_id: Optional[str] = None, title: Optional[str] = None,
                authors: Optional[List[str]] = None) -> (str, Book):
    """Native DjVu: the file stays as book.djvu; pages come from ddjvu, text from the hidden layer (OCR for pages without)."""
    if not djv.available():
        raise ValueError("Для DjVu нужен DjVuLibre (ddjvu, djvused): положите его в tools/djvulibre или установите пакет DjVuLibre")
    os.makedirs(library_dir, exist_ok=True)
    if book_id is None:
        book_id = slugify(os.path.splitext(os.path.basename(src))[0]) + "_data"
    out = os.path.join(library_dir, book_id)
    os.makedirs(os.path.join(out, "images"), exist_ok=True)
    _closed.discard(_key(out))
    _counts.pop(_key(out), None)
    if os.path.abspath(src) != os.path.abspath(djvu_path(out)):
        shutil.copyfile(src, djvu_path(out))
    path = djvu_path(out)
    n = djv.page_count(path)
    if n == 0:
        raise ValueError("В DjVu нет страниц")
    pages, sample = [], ""
    for t in djv.texts(path, n):
        t = clean_text(t)
        if len(t) < 25 or looks_garbled(t):
            pages.append({"t": "", "s": "pending"})
        else:
            pages.append({"t": t, "s": "pdf"})
            if len(sample) < 20000:
                sample += " " + t
    name = (title or "").strip() or " ".join(os.path.splitext(os.path.basename(src))[0].replace("_", " ").split())
    w, h = djv.page_size(path, 0)
    outline = djv.outline(path, n)
    lang = _language(sample) if len(sample) > 1500 else "ru"
    _write_pages(out, {"n": n, "language": lang, "size": [float(w), float(h)], "pages": pages, "outline": outline,
                       "ocr_available": ocr_available()})
    with open(os.path.join(out, "images", "cover.jpg"), "wb") as f:
        f.write(djv.render_jpeg(path, 0, 480, 85))
    book = Book(
        metadata=BookMetadata(title=name, language=lang, authors=authors or []),
        spine=[ChapterContent(id=f"p{i}", href=f"p{i}", title=f"Страница {i + 1}", content="", text="", order=i,
                              words=len(p["t"].split())) for i, p in enumerate(pages)],
        toc=[TOCEntry(title=t, href=f"p{pg}", file_href=f"p{pg}", anchor="") for lvl, t, pg in outline if lvl == 1],
        images={}, source_file=os.path.basename(src),
        processed_at=datetime.datetime.now().isoformat(timespec="seconds"), cover="images/cover.jpg")
    save_to_pickle(book, out)
    ensure_ocr(out)
    return book_id, book


# ---------------------------------------------------------------- rendering

def render_page(book_dir: str, n: int, width: int) -> str:
    """Path of a JPEG of page n (0-based) rendered `width` px wide; cached on disk."""
    width = min(2800, max(400, int(math.ceil(width / 200.0)) * 200))
    cache = os.path.join(book_dir, "render", f"{n}_{width}.jpg")
    if os.path.exists(cache):
        return cache
    with _render_lock:
        if os.path.exists(cache):
            return cache
        if is_djvu_book(book_dir):
            if _key(book_dir) in _closed:
                raise FileNotFoundError("book was removed")
            data = djv.render_jpeg(djvu_path(book_dir), n, width)
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            tmp = cache + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, cache)
            return cache
        doc = _doc(book_dir)                      # FileNotFoundError when the book was removed
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        page = doc[n]
        zoom = width / page.rect.width
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=False)
        tmp = cache + ".tmp"
        pix.save(tmp, output="jpeg", jpg_quality=86)
        os.replace(tmp, cache)
    return cache


# ---------------------------------------------------------------- text / OCR

def info(book_dir: str) -> dict:
    d = _read_pages(book_dir)
    pending = sum(1 for p in d["pages"] if p["s"] == "pending")
    return {"pages": d["n"], "language": d["language"], "size": d["size"], "outline": d["outline"],
            "ocr_pending": pending, "ocr_available": ocr_available(),
            "text_source": "ocr" if any(p["s"] == "ocr" for p in d["pages"]) else "pdf"}


def _ocr_page(book_dir: str, n: int) -> str:
    exe, lang = tesseract()
    if not (exe and lang):
        return ""
    with _render_lock:
        if is_djvu_book(book_dir):
            if _key(book_dir) in _closed:
                raise FileNotFoundError("book was removed")
            png = djv.render_png_for_ocr(djvu_path(book_dir), n)
        else:
            doc = _doc(book_dir)
            pix = doc[n].get_pixmap(dpi=250, colorspace=fitz.csGRAY, alpha=False)
            png = pix.tobytes("png")
    out = subprocess.run([exe, "stdin", "stdout", "-l", lang, "--psm", "3", "--dpi", "250"],
                         input=png, capture_output=True, timeout=180)
    if out.returncode != 0:                       # never store a failed read as the page's text
        raise RuntimeError("tesseract failed: " + out.stderr.decode("utf-8", "replace")[-200:])
    return clean_text(out.stdout.decode("utf-8", "replace"))


def page_text(book_dir: str, n: int, block: bool = True) -> str:
    """Text of page n. A pending page is read with OCR right now (block=True) or reported empty (block=False)."""
    d = _read_pages(book_dir)
    if n < 0 or n >= d["n"]:
        return ""
    p = d["pages"][n]
    if p["s"] != "pending":
        return p["t"]
    if not block or not ocr_available():
        return ""
    with _ocr_lock:
        d = _read_pages(book_dir)
        p = d["pages"][n]
        if p["s"] != "pending":
            return p["t"]
        try:
            text = _ocr_page(book_dir, n)
        except Exception as e:
            print(f"OCR failed for page {n + 1}: {e}")
            return ""
        with _json_lock:
            d = _read_pages(book_dir)
            d["pages"][n] = {"t": text, "s": "ocr"}
            _write_pages(book_dir, d)
        return text


def ensure_ocr(book_dir: str):
    """Starts (once) a background thread that reads all pending pages in order."""
    if not ocr_available():
        return
    k = _key(book_dir)
    t = _workers.get(k)
    if t and t.is_alive():
        return
    if not any(p["s"] == "pending" for p in _read_pages(book_dir)["pages"]):
        return

    def work():
        failed = set()
        try:
            while k not in _closed:
                d = _read_pages(book_dir)
                todo = [i for i, p in enumerate(d["pages"]) if p["s"] == "pending" and i not in failed]
                if not todo:
                    break
                page_text(book_dir, todo[0])
                if _read_pages(book_dir)["pages"][todo[0]]["s"] == "pending":
                    failed.add(todo[0])           # a page that cannot be read must not stall the others
        except Exception as e:                    # e.g. the book was deleted meanwhile
            print(f"OCR stopped for {book_dir}: {e}")

    t = threading.Thread(target=work, daemon=True, name="pdf-ocr")
    _workers[k] = t
    t.start()


def search(book_dir: str, query: str, limit: int = 100) -> list:
    q = query.strip().lower()
    if not q:
        return []
    out = []
    for i, p in enumerate(_read_pages(book_dir)["pages"]):
        text = p["t"]
        pos = text.lower().find(q)
        if pos >= 0:
            a, b = max(0, pos - 60), min(len(text), pos + len(q) + 80)
            out.append({"page": i, "snippet": ("…" if a else "") + text[a:b].replace("\n", " ") + ("…" if b < len(text) else "")})
            if len(out) >= limit:
                break
    return out


def context_text(book_dir: str, page: int, window: int, budget: int = 40000) -> str:
    """Text of the current page and `window` pages around it. Only the current page may wait for OCR."""
    n = _read_pages(book_dir)["n"]
    order = [page] + [p for k in range(1, window + 1) for p in (page - k, page + k)]
    chunks = {}
    used = 0
    for p in order:
        if p < 0 or p >= n:
            continue
        t = page_text(book_dir, p, block=(p == page))
        if not t and _read_pages(book_dir)["pages"][p]["s"] == "pending":
            t = "(текст этой страницы ещё распознаётся)"
        if used + len(t) > budget and p != page:
            continue
        chunks[p] = t
        used += len(t)
    parts = []
    for p in sorted(chunks):
        cur = ' current="true"' if p == page else ""
        parts.append(f'<page number="{p + 1}"{cur}>\n{chunks[p] or "(на странице нет текста)"}\n</page>')
    return "\n\n".join(parts)


