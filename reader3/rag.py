"""RAG index of a book: structure-aware chunks that remember their page / chapter, lemma-based BM25 in SQLite FTS5,
optional dense vectors (numpy), merged with reciprocal rank fusion.

One index per book, stored inside the book folder: rag.sqlite (+ vectors.npy when an embedder is available).
Locations: PDF books -> viewer page numbers (1-based); EPUB books -> chapter numbers (1-based).
"""
import json
import os
import re
import sqlite3
import threading
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import numpy as np

INDEX_VERSION = 3
DB_NAME = "rag.sqlite"
VEC_NAME = "vectors.npy"
TARGET_WORDS = 220          # a chunk closes once it holds this many words
MAX_WORDS = 340             # a single paragraph longer than this is split by sentences
OVERLAP_WORDS = 45          # tail of the previous chunk repeated at the start of the next one

WORD = re.compile(r"[а-яёa-z0-9]+", re.I)
STOP = set("""и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от меня
еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам ведь там потом себя
ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под будет ж тогда кто
этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при
наконец два об другой хоть после над больше тот через эти нас про всего них какая много разве три эту моя впрочем хорошо свою
этой перед иногда лучше чуть том нельзя такой им более всегда конечно всю между это такое так
the a an and or of to in on for with is are was were be been it this that these those as at by from not no""".split())

_morph = None
_morph_lock = threading.Lock()


def _get_morph():
    global _morph
    with _morph_lock:
        if _morph is None:
            import pymorphy3
            _morph = pymorphy3.MorphAnalyzer()
    return _morph


_stemmer_en = None


def _get_stemmer():
    global _stemmer_en
    with _morph_lock:
        if _stemmer_en is None:
            import snowballstemmer
            _stemmer_en = snowballstemmer.stemmer("english")
    return _stemmer_en


@lru_cache(maxsize=200_000)
def _lemma(token: str) -> str:
    """Russian words -> dictionary form (pymorphy3); English words -> Snowball stem; anything else as is."""
    if re.fullmatch(r"[а-я]+", token):
        return _get_morph().parse(token)[0].normal_form.replace("ё", "е")
    if len(token) > 3 and re.fullmatch(r"[a-z]+", token):
        return _get_stemmer().stemWord(token)
    return token


def norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def lemmas(text: str, drop_stop: bool = True) -> List[str]:
    out = []
    for t in WORD.findall(norm(text)):
        l = _lemma(t)
        if drop_stop and (l in STOP or t in STOP):
            continue
        out.append(l)
    return out


# ---------------------------------------------------------------- chunking

def _split_long(par: str) -> List[str]:
    if len(par.split()) <= MAX_WORDS:
        return [par]
    sents = re.split(r"(?<=[.!?…])\s+", par)
    out, cur, n = [], [], 0
    for s in sents:
        w = len(s.split())
        if cur and n + w > MAX_WORDS:
            out.append(" ".join(cur))
            cur, n = [], 0
        cur.append(s)
        n += w
    if cur:
        out.append(" ".join(cur))
    return out


def make_chunks(units: List[dict], outline: Optional[List[Tuple[int, str]]] = None) -> List[dict]:
    """units: [{"loc": int, "title": str|None, "text": str}] in reading order (a page or a chapter each).
    outline: [(loc, section title)] sorted by loc, used to name the section of a chunk.
    Returns chunks {seq, loc_start, loc_end, section, text}."""
    outline = sorted(outline or [])

    def section_at(loc, fallback):
        title = fallback
        for l, t in outline:
            if l <= loc:
                title = t
            else:
                break
        return title

    paras = []                                     # (loc, text, fallback section)
    for u in units:
        for raw in re.split(r"\n\s*\n|\n(?=\S)", u["text"]):
            raw = " ".join(raw.split())
            if len(raw) < 2:
                continue
            for piece in _split_long(raw):
                paras.append((u["loc"], piece, u.get("title")))

    chunks, cur, words = [], [], 0
    def flush():
        nonlocal cur, words
        if not cur:
            return
        text = "\n\n".join(p[1] for p in cur)
        chunks.append({"seq": len(chunks), "loc_start": cur[0][0], "loc_end": cur[-1][0],
                       "section": section_at(cur[0][0], cur[0][2]), "text": text})
        # overlap: keep trailing paragraphs worth OVERLAP_WORDS
        keep, n = [], 0
        for p in reversed(cur):
            w = len(p[1].split())
            if n + w > OVERLAP_WORDS and keep:
                break
            keep.insert(0, p)
            n += w
            if n >= OVERLAP_WORDS:
                break
        cur, words = (keep if len(keep) < len(cur) else []), (n if len(keep) < len(cur) else 0)

    for p in paras:
        w = len(p[1].split())
        if cur and words + w > TARGET_WORDS * 1.25:
            flush()
        cur.append(p)
        words += w
        if words >= TARGET_WORDS:
            flush()
    if cur and (not chunks or len(" ".join(c[1] for c in cur).split()) > OVERLAP_WORDS):
        flush()
    return chunks


# ---------------------------------------------------------------- storage

def _db_path(book_dir):
    return os.path.join(book_dir, DB_NAME)


def _vec_path(book_dir):
    return os.path.join(book_dir, VEC_NAME)


def build_index(book_dir: str, units: List[dict], outline=None, book_title: str = "", kind: str = "pdf",
                embedder=None, progress=None) -> dict:
    """(Re)builds the index of one book. `embedder` is an object with .encode_passages(list[str]) -> np.ndarray."""
    chunks = make_chunks(units, outline)
    tmp = _db_path(book_dir) + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE chunks(id INTEGER PRIMARY KEY, seq INT, loc_start INT, loc_end INT, section TEXT, text TEXT);
        CREATE VIRTUAL TABLE chunks_fts USING fts5(lem, sec, tokenize='unicode61 remove_diacritics 0');
    """)
    for i, c in enumerate(chunks):
        db.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?)", (i, c["seq"], c["loc_start"], c["loc_end"], c["section"], c["text"]))
        db.execute("INSERT INTO chunks_fts(rowid, lem, sec) VALUES (?,?,?)",
                   (i, " ".join(lemmas(c["text"])), " ".join(lemmas(c["section"] or ""))))
        if progress and i % 25 == 0:
            progress("lexical", i, len(chunks))
    model = ""
    if embedder is not None and chunks:
        vecs = []
        B = 16
        for i in range(0, len(chunks), B):
            batch = [((c["section"] + ". ") if c["section"] else "") + c["text"] for c in chunks[i:i + B]]
            vecs.append(embedder.encode_passages(batch))
            if progress:
                progress("dense", min(i + B, len(chunks)), len(chunks))
        np.save(_vec_path(book_dir), np.vstack(vecs).astype(np.float32))
        model = embedder.name
    elif os.path.exists(_vec_path(book_dir)):
        os.remove(_vec_path(book_dir))
    meta = {"version": INDEX_VERSION, "chunks": len(chunks), "kind": kind, "title": book_title, "embed_model": model}
    for k, v in meta.items():
        db.execute("INSERT INTO meta VALUES (?,?)", (k, json.dumps(v, ensure_ascii=False)))
    db.commit()
    db.close()
    # Windows cannot replace a file that is open: drop this process's cached connection to the old index first
    old = _cache.pop(os.path.abspath(book_dir), None)
    if old is not None:
        old["db"].close()
    os.replace(tmp, _db_path(book_dir))
    return meta


_cache: Dict[str, dict] = {}


def _open(book_dir: str) -> Optional[dict]:
    k = os.path.abspath(book_dir)
    p = _db_path(book_dir)
    if not os.path.exists(p):
        return None
    mtime = os.path.getmtime(p)
    c = _cache.get(k)
    if c and c["mtime"] == mtime:
        return c
    db = sqlite3.connect(p, check_same_thread=False)
    meta = {r[0]: json.loads(r[1]) for r in db.execute("SELECT key, value FROM meta")}
    vec = None
    if os.path.exists(_vec_path(book_dir)):
        vec = np.load(_vec_path(book_dir))
    c = {"db": db, "meta": meta, "vec": vec, "mtime": mtime}
    _cache[k] = c
    return c


def forget(book_dir: str) -> None:
    """Closes this process's connection to the index of a book (needed before its folder is deleted on Windows)."""
    old = _cache.pop(os.path.abspath(book_dir), None)
    if old is not None:
        try:
            old["db"].close()
        except Exception:
            pass


def index_info(book_dir: str) -> Optional[dict]:
    c = _open(book_dir)
    return dict(c["meta"], dense=c["vec"] is not None) if c else None


# ---------------------------------------------------------------- search

def _fts_query(q: str) -> Optional[str]:
    terms = []
    for t in lemmas(q):
        if t not in terms:
            terms.append(t)
    return " OR ".join('"%s"' % t.replace('"', "") for t in terms) if terms else None


def search_book(book_dir: str, query: str, k: int = 8, embedder=None, pool: int = 40) -> List[dict]:
    """Hybrid search in one book. Returns [{chunk_id, loc_start, loc_end, section, text, score, via}] best first."""
    c = _open(book_dir)
    if not c:
        return []
    db = c["db"]
    ranks: Dict[int, Dict[str, int]] = {}
    fq = _fts_query(query)
    if fq:
        try:
            rows = db.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts, 1.0, 0.4) LIMIT ?",
                              (fq, pool)).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for r, (rid,) in enumerate(rows):
            ranks.setdefault(rid, {})["bm25"] = r + 1
    dense_sim: Dict[int, float] = {}
    if embedder is not None and c["vec"] is not None:
        qv = embedder.encode_query(query)
        sims = c["vec"] @ qv
        order = np.argsort(-sims)[:pool]
        for r, i in enumerate(order):
            ranks.setdefault(int(i), {})["dense"] = r + 1
            dense_sim[int(i)] = float(sims[i])
    if not ranks:
        return []
    scored = []
    for rid, rk in ranks.items():
        score = sum(1.0 / (60 + r) for r in rk.values())
        scored.append((score, rid))
    scored.sort(reverse=True)
    out = []
    for score, rid in scored[:k]:
        row = db.execute("SELECT loc_start, loc_end, section, text FROM chunks WHERE id=?", (rid,)).fetchone()
        out.append({"chunk_id": rid, "loc_start": row[0], "loc_end": row[1], "section": row[2], "text": row[3],
                    "score": round(score, 5), "via": "+".join(sorted(ranks[rid])), "sim": dense_sim.get(rid)})
    return out
