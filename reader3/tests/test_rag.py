"""Chunking, lemmatisation, lexical and hybrid search (with a fake embedder: no GPU, no downloads)."""
import hashlib
import os
import re

import numpy as np
import pytest

import notebook
import rag


class FakeEmbedder:
    """Hashing bag-of-lemmas: deterministic, sensitive to shared words, good enough to exercise the dense path."""
    name = "fake/hash-64"
    D = 64

    def _vec(self, text):
        v = np.zeros(self.D, dtype=np.float32)
        for t in rag.lemmas(text):
            v[int(hashlib.md5(t.encode()).hexdigest(), 16) % self.D] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def encode_passages(self, texts):
        return np.vstack([self._vec(t) for t in texts])

    def encode_query(self, text):
        return self._vec(text)


# ---------------------------------------------------------------- lemmas

def test_lemmas_russian_morphology():
    assert rag.lemmas("компрессоры") == rag.lemmas("компрессорами") == rag.lemmas("компрессор")
    assert rag.lemmas("Ёлка") == rag.lemmas("елка")                          # ё == е


def test_lemmas_english_stemming():
    assert rag.lemmas("rhythms") == rag.lemmas("rhythm")
    assert rag.lemmas("subdivisions") == rag.lemmas("subdivision")


def test_lemmas_stopwords_and_numbers():
    assert rag.lemmas("и в на что") == []
    assert rag.lemmas("и в на что", drop_stop=False) != []
    assert "3" in rag.lemmas("ритм 3:2") and "2" in rag.lemmas("ритм 3:2")


def test_lemmas_empty():
    assert rag.lemmas("") == [] and rag.lemmas("!!! ???") == []


# ---------------------------------------------------------------- chunks

def _units(n_pages, words_per_page, prefix="слово"):
    return [{"loc": i + 1, "title": None, "text": "\n\n".join(
        " ".join(f"{prefix}{i}_{j}_{k}" for k in range(40)) for j in range(words_per_page // 40))} for i in range(n_pages)]


def test_chunks_empty():
    assert rag.make_chunks([]) == []
    assert rag.make_chunks([{"loc": 1, "title": None, "text": "  \n\n "}]) == []


def test_chunks_cover_everything_in_order():
    units = _units(10, 400)
    ch = rag.make_chunks(units)
    assert len(ch) > 3
    assert [c["seq"] for c in ch] == list(range(len(ch)))
    assert all(c["loc_start"] <= c["loc_end"] for c in ch)
    assert [c["loc_start"] for c in ch] == sorted(c["loc_start"] for c in ch)
    assert ch[0]["loc_start"] == 1 and ch[-1]["loc_end"] == 10
    seen = set()
    for c in ch:
        seen.update(re.findall(r"слово\d+_\d+_\d+", c["text"]))
    total = {w for u in units for w in re.findall(r"слово\d+_\d+_\d+", u["text"])}
    assert seen == total                                                  # no word is lost


def test_chunks_size_and_overlap():
    ch = rag.make_chunks(_units(8, 400))
    for c in ch:
        assert len(c["text"].split()) <= rag.TARGET_WORDS * 1.25 + rag.MAX_WORDS
    # the tail of a chunk is repeated at the head of the next one
    shared = [len(set(a["text"].split()) & set(b["text"].split())) for a, b in zip(ch, ch[1:])]
    assert sum(1 for s in shared if s > 0) >= len(shared) // 2


def test_chunks_long_paragraph_is_split_by_sentences():
    para = " ".join(f"Это предложение номер {i} с несколькими словами для длины." for i in range(120))
    ch = rag.make_chunks([{"loc": 1, "title": None, "text": para}])
    assert len(ch) >= 2
    assert all(len(c["text"].split()) <= rag.MAX_WORDS * 1.5 for c in ch)


def test_chunks_section_titles_from_outline():
    ch = rag.make_chunks(_units(6, 300), outline=[(1, "Введение"), (4, "Основная часть")])
    assert ch[0]["section"] == "Введение"
    assert any(c["section"] == "Основная часть" for c in ch)
    assert all(c["section"] == "Основная часть" for c in ch if c["loc_start"] >= 4)


# ---------------------------------------------------------------- index + search

@pytest.fixture()
def indexed(lib, tmp_path):
    """A private copy of the RU and EN books with a lexical index."""
    import shutil
    out = {}
    for bid in ("ru_data", "en_data"):
        d = str(tmp_path / bid)
        shutil.copytree(os.path.join(lib, bid), d)
        notebook.ensure_index(d, embedder=None, force=True)
        out[bid] = d
    return out


def test_index_info_lexical(indexed):
    info = rag.index_info(indexed["ru_data"])
    assert info["chunks"] >= 1 and info["dense"] is False and info["version"] == rag.INDEX_VERSION
    assert rag.index_info(os.path.join(os.path.dirname(indexed["ru_data"]), "absent")) is None


def test_lexical_search_russian_morphology(indexed):
    res = rag.search_book(indexed["ru_data"], "что делает компрессоры с динамикой сигнала", k=3)
    assert res and res[0]["via"] == "bm25"
    assert any(r["loc_start"] <= 3 <= r["loc_end"] for r in res[:2])


def test_lexical_search_english(indexed):
    res = rag.search_book(indexed["en_data"], "hemiolas cross rhythms", k=3)
    assert res and any(r["loc_start"] <= 3 <= r["loc_end"] or r["loc_start"] <= 2 <= r["loc_end"] for r in res)


def test_cross_language_query_finds_nothing_lexically(indexed):
    assert rag.search_book(indexed["en_data"], "полиритм гемиола", k=3) == []          # why the agent must translate queries


@pytest.mark.parametrize("q", ['"', "NEAR(", "a AND", "* OR *", "слово'; DROP TABLE chunks;--", "и в на", "", "   ", "\\", "(((", "col:val"])
def test_search_survives_hostile_queries(indexed, q):
    res = rag.search_book(indexed["ru_data"], q, k=3)
    assert isinstance(res, list)
    assert rag.index_info(indexed["ru_data"])["chunks"] >= 1                           # the index is intact


def test_search_k_and_missing_index(indexed, tmp_path):
    assert len(rag.search_book(indexed["ru_data"], "громкость децибелы компрессор частоты", k=1)) <= 1
    assert rag.search_book(str(tmp_path / "nothing"), "x") == []


def test_hybrid_search_with_fake_embedder(indexed):
    d = indexed["ru_data"]
    emb = FakeEmbedder()
    meta = notebook.ensure_index(d, embedder=emb, force=True)
    assert meta["embed_model"] == emb.name and rag.index_info(d)["dense"] is True
    res = rag.search_book(d, "компрессор динамический диапазон", k=3, embedder=emb)
    assert res and "dense" in res[0]["via"] and res[0]["sim"] is not None
    assert any(r["loc_start"] <= 3 <= r["loc_end"] for r in res)
    assert rag.search_book(d, "компрессор", k=2, embedder=None)[0]["via"] == "bm25"      # no embedder: lexical only


def test_rebuild_without_embedder_drops_vectors(indexed):
    d = indexed["ru_data"]
    notebook.ensure_index(d, embedder=FakeEmbedder(), force=True)
    assert rag.index_info(d)["dense"] is True
    notebook.ensure_index(d, embedder=None, force=True)
    assert rag.index_info(d)["dense"] is False and not os.path.exists(os.path.join(d, rag.VEC_NAME))


def test_ensure_index_is_idempotent(indexed):
    d = indexed["ru_data"]
    p = os.path.join(d, rag.DB_NAME)
    m1 = os.path.getmtime(p)
    notebook.ensure_index(d, embedder=None)               # already built: no rebuild
    assert os.path.getmtime(p) == m1


def test_search_across_sources_and_dedupe(lib, indexed):
    srcs = notebook.make_sources([("ru_data", "A"), ("en_data", "B")], lambda b: indexed[b])
    res = notebook.search(srcs, "compressor hemiola", k=4, embedder=None, reranker=None)
    assert res and all("source" in r for r in res)
    only_b = notebook.search([srcs[1]], "hemiola", k=4, embedder=None, reranker=None)
    assert only_b and all(r["source"].alias == "B" for r in only_b)


def test_search_applies_reranker_order(lib, indexed):
    d = indexed["ru_data"]
    topics = ["гитара струны", "барабаны тарелки", "скрипка смычок", "флейта дыхание", "орган трубы", "арфа педали"]
    units = [{"loc": i + 1, "title": None, "text": " ".join(f"{t} абзац {j} " + " ".join([t.split()[0]] * 70) for j in range(3))}
             for i, t in enumerate(topics)]
    rag.build_index(d, units, book_title="t", kind="pdf", embedder=None)
    srcs = notebook.make_sources([("ru_data", "A")], lambda b: d)
    q = "гитара барабаны скрипка флейта орган арфа"
    base = notebook.search(srcs, q, k=6, embedder=None, reranker=None)
    assert len(base) >= 3
    rr = notebook.search(srcs, q, k=6, embedder=None, reranker=lambda passages: [float(i) for i in range(len(passages))])
    assert [c["chunk_id"] for c in rr] != [c["chunk_id"] for c in base]            # the reranker decides the order
    assert [c["relevance"] for c in rr] == sorted((c["relevance"] for c in rr), reverse=True)


def test_format_results_marks_weak_matches(lib, indexed):
    srcs = notebook.make_sources([("ru_data", "A")], lambda b: indexed[b])
    res = notebook.search(srcs, "компрессор", k=2, embedder=None, reranker=None)
    txt = notebook.format_results(res, "компрессор", 0.02)
    assert "[A:" in txt
    assert isinstance(notebook.format_results([], "компрессор", 0.02), str)


# ---------------------------------------------------------------- page markers inside multi-page chunks

def test_chunk_text_gets_page_markers(lib, indexed):
    srcs = notebook.make_sources([("ru_data", "A")], lambda b: indexed["ru_data"])
    res = notebook.search(srcs, "компрессор динамический диапазон", k=1, embedder=None, reranker=None)
    assert res[0]["loc_start"] < 3 <= res[0]["loc_end"]                       # the whole 12-page book is one chunk
    txt = notebook.format_results(res, "q", 0.02)
    assert "⟨стр. 3⟩" in txt and txt.index("⟨стр. 3⟩") < txt.index("Компрессор уменьшает")
    assert txt.index("⟨стр. 2⟩") < txt.index("⟨стр. 3⟩") < txt.index("⟨стр. 4⟩")           # one marker per page, in order
    assert txt.count("⟨стр. 3⟩") == 1


def test_single_page_chunk_has_no_markers(sources):
    r = {"source": sources[0], "loc_start": 3, "loc_end": 3, "text": "Компрессор уменьшает динамический диапазон сигнала", "section": None,
         "relevance": None, "score": 1.0, "chunk_id": 0}
    assert "⟨" not in notebook.format_results([r], "q", 0.02)


def test_marker_matches_where_verify_finds_the_quote(sources):
    """The model copies the marked page; verification then reports `ok`, not `moved`."""
    ru = sources[0]
    r = {"source": ru, "loc_start": 1, "loc_end": 12, "section": None, "relevance": None, "score": 1.0, "chunk_id": 0,
         "text": "\n\n".join(notebook.loc_text(ru.dir, i) for i in range(1, 13))}
    txt = notebook.mark_pages(ru, r, r["text"])
    import re
    for page, quote in ((5, "Эквалайзер усиливает или ослабляет выбранные частоты звука"), (9, "Бас и бочку принято оставлять в центре")):
        marker = txt.index(f"⟨стр. {page}⟩")
        assert quote.split()[0] in txt[marker:marker + 200]
        assert notebook.verify_quote(ru, page, quote)["status"] == "ok"
