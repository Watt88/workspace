"""Glue between library books (PDF or EPUB) and the notebook: what to index, searching across books with reranking,
reading a location back, and checking that a quoted passage really stands where the model says it does."""
import json
import os
import pickle
import re
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import pdf_support as pdfs
import rag

ALIASES = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
SUMMARIES = "nb_summaries.json"
GUIDE = "nb_guide.json"


# ---------------------------------------------------------------- books

def load_pickle(book_dir: str):
    # book.pkl is written by this reader itself into its own library folder (same trust as server.load_book);
    # it is never taken from an upload or from the network.
    with open(os.path.join(book_dir, "book.pkl"), "rb") as f:
        return pickle.load(f)


def kind_of(book_dir: str) -> str:
    return "pdf" if pdfs.is_pdf_book(book_dir) else "epub"


@dataclass
class Source:
    id: str
    alias: str
    dir: str
    kind: str
    title: str
    lang: str


def make_sources(pairs: List[Tuple[str, str]], resolve: Callable[[str], Optional[str]]) -> List[Source]:
    """pairs: [(book_id, alias)] -> Sources (books that are missing are skipped)."""
    out = []
    for book_id, alias in pairs:
        d = resolve(book_id)
        if not d:
            continue
        book = load_pickle(d)
        out.append(Source(book_id, alias, d, kind_of(d), book.metadata.title, book.metadata.language or "ru"))
    return out


def parse_books_param(s: str) -> List[Tuple[str, str]]:
    """'id1:A,id2:B' -> [(id1, A), (id2, B)]"""
    pairs = []
    for part in (s or "").split(","):
        if ":" in part:
            i, a = part.rsplit(":", 1)
            pairs.append((i.strip(), a.strip()))
    return pairs


def collect(book_dir: str):
    """(units, outline, title, kind). Locations: PDF -> viewer page (1-based); EPUB -> chapter number (1-based)."""
    book = load_pickle(book_dir)
    title = book.metadata.title
    if pdfs.is_pdf_book(book_dir):
        d = pdfs._read_pages(book_dir)
        units = [{"loc": i + 1, "title": None, "text": p["t"]} for i, p in enumerate(d["pages"])]
        outline = [(pg + 1, t) for lvl, t, pg in d["outline"] if lvl <= 3]
        return units, outline, title, "pdf"
    units, outline = [], []
    for i, ch in enumerate(book.spine):
        text = (ch.text or "").strip()
        if not text:
            continue
        units.append({"loc": i + 1, "title": ch.title, "text": text})
        outline.append((i + 1, ch.title))
    return units, outline, title, "epub"


def ensure_index(book_dir: str, embedder=None, force: bool = False, progress=None) -> dict:
    info = rag.index_info(book_dir)
    want = embedder.name if embedder is not None else ""
    if info and not force and info.get("version") == rag.INDEX_VERSION and info.get("embed_model", "") == want:
        return info
    units, outline, title, kind = collect(book_dir)
    return rag.build_index(book_dir, units, outline, title, kind, embedder, progress)


def location_label(kind: str, loc_start: int, loc_end: int) -> str:
    if kind == "pdf":
        return f"стр. {loc_start}" if loc_start == loc_end else f"стр. {loc_start}–{loc_end}"
    return f"гл. {loc_start}"


def loc_token(kind: str, loc: int) -> str:
    return str(loc) if kind == "pdf" else f"c{loc}"


def parse_loc(token: str) -> Tuple[str, int]:
    """'41' -> ('pdf', 41); 'c3' -> ('epub', 3); a range '41-42' -> its first number."""
    token = token.strip()
    kind = "epub" if token[:1] in ("c", "C") else "pdf"
    m = re.search(r"\d+", token)
    return kind, int(m.group()) if m else 1


# ---------------------------------------------------------------- reading

def n_locs(book_dir: str) -> int:
    if pdfs.is_pdf_book(book_dir):
        return pdfs.page_count(book_dir)
    return len(load_pickle(book_dir).spine)


def loc_text(book_dir: str, loc: int) -> str:
    if pdfs.is_pdf_book(book_dir):
        return pdfs.page_text(book_dir, loc - 1, block=False)
    book = load_pickle(book_dir)
    if 1 <= loc <= len(book.spine):
        return (book.spine[loc - 1].text or "").strip()
    return ""


def read_location(book_dir: str, start: int, end: Optional[int] = None, max_chars: int = 14000) -> dict:
    """Text of pages (PDF) or chapters (EPUB) start..end (1-based, inclusive), capped; reports where it stopped."""
    end = end or start
    kind = kind_of(book_dir)
    total = n_locs(book_dir)
    parts, used, last = [], 0, start - 1
    for loc in range(max(1, start), min(end, total) + 1):
        t = loc_text(book_dir, loc)
        if kind == "pdf":
            block = f"[стр. {loc}]\n{t or '(на странице нет текста)'}"
        else:
            title = load_pickle(book_dir).spine[loc - 1].title
            block = f"[гл. {loc} «{title}»]\n{t}"
        if parts and used + len(block) > max_chars:
            break
        if not parts and len(block) > max_chars:
            block = block[:max_chars] + "\n(текст обрезан; прочтите продолжение отдельным вызовом или уточните поиском)"
        parts.append(block)
        used += len(block)
        last = loc
    return {"kind": kind, "text": "\n\n".join(parts), "from": start, "to": last, "total": total}


def outline_of(book_dir: str) -> List[dict]:
    if pdfs.is_pdf_book(book_dir):
        d = pdfs._read_pages(book_dir)
        return [{"level": lvl, "title": t, "loc": pg + 1} for lvl, t, pg in d["outline"]]
    book = load_pickle(book_dir)
    return [{"level": 1, "title": ch.title, "loc": i + 1} for i, ch in enumerate(book.spine)]


def sections_of(book_dir: str) -> List[dict]:
    """Reading sections with their extent: [{title, loc_start, loc_end}] (chapters; PDFs without outline: 15-page windows)."""
    total = n_locs(book_dir)
    items = [o for o in outline_of(book_dir) if o["level"] <= 2]
    if not items:
        step = 15
        items = [{"level": 1, "title": f"Страницы {i}–{min(i + step - 1, total)}", "loc": i} for i in range(1, total + 1, step)]
    items = sorted(items, key=lambda o: o["loc"])
    out = []
    for i, o in enumerate(items):
        end = items[i + 1]["loc"] - 1 if i + 1 < len(items) else total
        if end >= o["loc"]:
            out.append({"title": o["title"], "loc_start": o["loc"], "loc_end": end})
    return out


# ---------------------------------------------------------------- search

def search(sources: List[Source], query: str, k: int = 6, embedder=None, reranker=None, pool: int = 20) -> List[dict]:
    """Hybrid search in every source, one rerank over the union, adjacent duplicates removed. Best first."""
    cands = []
    for s in sources:
        info = rag.index_info(s.dir)
        use_dense = embedder if (info and info.get("dense") and info.get("embed_model") == getattr(embedder, "name", None)) else None
        for r in rag.search_book(s.dir, query, k=pool, embedder=use_dense):
            r["source"] = s
            cands.append(r)
    if not cands:
        return []
    scores = reranker([c["text"] for c in cands]) if reranker else None
    for i, c in enumerate(cands):
        c["relevance"] = scores[i] if scores is not None else None
    if scores is not None:
        cands.sort(key=lambda c: c["relevance"], reverse=True)
    else:
        cands.sort(key=lambda c: c["score"], reverse=True)
    out, taken = [], {}
    for c in cands:
        s = c["source"]
        if any(abs(c["chunk_id"] - j) == 1 for j in taken.get(s.id, [])):
            continue                          # the neighbour overlaps this chunk by design: one of the two is enough
        taken.setdefault(s.id, []).append(c["chunk_id"])
        out.append(c)
        if len(out) >= k:
            break
    return out


def mark_pages(s: Source, r: dict, text: str) -> str:
    """A chunk can span several pages/chapters: put a marker before the first paragraph of each one, so that the model cites the
    page the quoted words are really on, not the first page of the chunk."""
    a, b = r["loc_start"], r["loc_end"]
    if a == b:
        return text
    try:
        comps = _locs_compact(s.dir)
    except Exception:
        return text
    out, cur = [], None
    for par in text.split("\n\n"):
        c = _compact(par)
        loc = None
        for key in (c, c[:40]):
            if len(key) >= 8:
                loc = next((p for p in range(a, b + 1) if 1 <= p <= len(comps) and key in comps[p - 1]), None)
                if loc:
                    break
        if loc and loc != cur:
            out.append(f"⟨{location_label(s.kind, loc, loc)}⟩")
            cur = loc
        out.append(par)
    return "\n\n".join(out)


def format_results(results: List[dict], query: str, threshold: float, snippet_chars: int = 1500) -> str:
    if not results:
        return f"По запросу «{query}» ничего не найдено."
    lines = [f"Результаты поиска по запросу «{query}». Релевантность 0..1 — оценка реранкера."]
    best = max((r["relevance"] for r in results if r.get("relevance") is not None), default=None)
    for r in results:
        s = r["source"]
        tag = f"[{s.alias}:{loc_token(s.kind, r['loc_start'])}]"
        where = location_label(s.kind, r["loc_start"], r["loc_end"])
        rel = "" if r.get("relevance") is None else f"; релевантность {r['relevance']:.2f}"
        sec = f"; раздел «{r['section']}»" if r.get("section") else ""
        text = mark_pages(s, r, r["text"].strip())
        if len(text) > snippet_chars:
            text = text[:snippet_chars].rsplit(" ", 1)[0] + " …"
        lines.append(f"\n{tag} — «{s.title}», {where}{sec}{rel}\n{text}")
    if best is not None and best < threshold:
        lines.append(f"\nВНИМАНИЕ: лучшая релевантность {best:.3f} ниже порога {threshold}. Вероятно, в источниках нет ответа "
                     "на этот запрос; попробуйте другую формулировку или честно скажите, что ответа нет.")
    return "\n".join(lines)


# ---------------------------------------------------------------- exact phrases and quote verification

_compact_cache: Dict[Tuple[str, float], List[str]] = {}
_cache_lock = threading.Lock()


def _compact(text: str) -> str:
    return re.sub(r"[^0-9a-zа-я]", "", text.lower().replace("ё", "е"))


def _locs_compact(book_dir: str) -> List[str]:
    key = (os.path.abspath(book_dir), os.path.getmtime(os.path.join(book_dir, "pages.json" if pdfs.is_pdf_book(book_dir) else "book.pkl")))
    with _cache_lock:
        c = _compact_cache.get(key)
        if c is None:
            c = [_compact(loc_text(book_dir, i)) for i in range(1, n_locs(book_dir) + 1)]
            _compact_cache.clear()
            _compact_cache[key] = c
        return c


def find_exact(sources: List[Source], phrase: str, limit: int = 10) -> List[dict]:
    q = _compact(phrase)
    if len(q) < 4:
        return []
    out = []
    for s in sources:
        for i, c in enumerate(_locs_compact(s.dir)):
            if q in c:
                out.append({"source": s, "loc": i + 1})
                if len(out) >= limit:
                    return out
    return out


def verify_quote(source: Source, loc: int, quote: str) -> dict:
    """Is `quote` really on that page/chapter? -> {status: ok|moved|fuzzy|missing, loc: where it was found}.
    ok = verbatim (ignoring case, punctuation, spacing, hyphenation) at the cited place or one page away;
    moved = verbatim, but elsewhere in the book; fuzzy = most word triples of the quote are there; missing."""
    q = _compact(quote)
    if len(q) < 8:
        return {"status": "missing", "loc": None}
    comp = _locs_compact(source.dir)
    n = len(comp)
    near = [loc] if source.kind == "epub" else [loc, loc - 1, loc + 1]
    for p in near:
        if 1 <= p <= n and q in comp[p - 1]:
            return {"status": "ok", "loc": p}
    for i, c in enumerate(comp):
        if q in c:
            return {"status": "moved", "loc": i + 1}
    # fuzzy: share of the quote's word triples found around the cited place (the model may drop a word)
    words = re.findall(r"[0-9a-zа-я]+", quote.lower().replace("ё", "е"))
    if len(words) >= 4:
        triples = ["".join(words[i:i + 3]) for i in range(len(words) - 2)]
        for p in near:
            if 1 <= p <= n:
                hit = sum(1 for t in triples if t in comp[p - 1])
                if hit / len(triples) >= 0.8:
                    return {"status": "fuzzy", "loc": p}
    return {"status": "missing", "loc": None}


def locate_quote(text: str, quote: str) -> Optional[Tuple[int, int]]:
    """Character span of `quote` inside `text` (original indices), ignoring case, punctuation and spacing."""
    keep, idx = [], []
    for i, ch in enumerate(text):
        c = ch.lower().replace("ё", "е")
        if c.isalnum():
            keep.append(c)
            idx.append(i)
    q = _compact(quote)
    if not q:
        return None
    pos = "".join(keep).find(q)
    if pos < 0:
        return None
    return idx[pos], idx[pos + len(q) - 1] + 1


CITE = re.compile(r"\[\[\s*([A-Za-z]):\s*([cC]?\d+(?:\s*[-–]\s*\d+)?)\s*(?:\|\s*(.+?))?\s*\]\]", re.S)


def extract_citations(text: str) -> List[dict]:
    out = []
    for m in CITE.finditer(text):
        out.append({"alias": m.group(1).upper(), "loc": m.group(2).replace(" ", ""), "quote": (m.group(3) or "").strip(),
                    "span": [m.start(), m.end()]})
    return out


def verify_citations(text: str, sources: List[Source]) -> List[dict]:
    by_alias = {s.alias: s for s in sources}
    results = []
    for c in extract_citations(text):
        s = by_alias.get(c["alias"])
        if s is None:
            results.append({**c, "status": "nosource", "found": None})
            continue
        _, loc = parse_loc(c["loc"])
        if not c["quote"]:
            results.append({**c, "status": "noquote", "found": None})
            continue
        v = verify_quote(s, loc, c["quote"])
        results.append({**c, "status": v["status"], "found": v["loc"]})
    return results


# ---------------------------------------------------------------- derived artifacts (summaries, guide)

def read_json(book_dir: str, name: str):
    p = os.path.join(book_dir, name)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(book_dir: str, name: str, data) -> None:
    tmp = os.path.join(book_dir, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(book_dir, name))
