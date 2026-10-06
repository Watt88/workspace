"""Pure logic of the notebook: locations, citations, quote verification, JSON extraction, DjVu outline parser."""
import os

import pytest

import notebook
import djvu_support
from conftest import RU_PAGES


# ---------------------------------------------------------------- locations

@pytest.mark.parametrize("token,expected", [("41", ("pdf", 41)), ("c3", ("epub", 3)), ("C12", ("epub", 12)),
                                            ("41-42", ("pdf", 41)), ("7–9", ("pdf", 7)), ("", ("pdf", 1)), ("abc", ("pdf", 1))])
def test_parse_loc(token, expected):
    assert notebook.parse_loc(token) == expected


def test_loc_token_roundtrip():
    for kind, n in [("pdf", 5), ("epub", 9)]:
        assert notebook.parse_loc(notebook.loc_token(kind, n)) == (kind, n)


def test_location_label():
    assert notebook.location_label("pdf", 3, 3) == "стр. 3"
    assert notebook.location_label("pdf", 3, 5) == "стр. 3–5"
    assert notebook.location_label("epub", 2, 2) == "гл. 2"


def test_parse_books_param():
    assert notebook.parse_books_param("a_data:A, b_data:B") == [("a_data", "A"), ("b_data", "B")]
    assert notebook.parse_books_param("") == []
    assert notebook.parse_books_param("junk") == []
    assert notebook.parse_books_param("id:with:colon:C") == [("id:with:colon", "C")]


def test_make_sources_skips_missing(lib):
    srcs = notebook.make_sources([("ru_data", "A"), ("nope_data", "B")],
                                 lambda b: os.path.join(lib, b) if b == "ru_data" else None)
    assert [s.id for s in srcs] == ["ru_data"]
    assert srcs[0].kind == "pdf" and srcs[0].lang == "ru"


def test_source_kinds(sources):
    assert [s.kind for s in sources] == ["pdf", "pdf", "epub"]
    assert [s.lang for s in sources] == ["ru", "en", "ru"]


# ---------------------------------------------------------------- reading

def test_n_locs_and_loc_text(sources):
    ru, en, ep = sources
    assert notebook.n_locs(ru.dir) == len(RU_PAGES)
    assert notebook.n_locs(ep.dir) >= 3
    assert "децибелах" in notebook.loc_text(ru.dir, 2)
    assert notebook.loc_text(ru.dir, 0) == "" and notebook.loc_text(ru.dir, 999) == ""


def test_read_location_range_and_cap(sources):
    ru = sources[0]
    r = notebook.read_location(ru.dir, 2, 4)
    assert (r["from"], r["to"]) == (2, 4) and "Компрессор" in r["text"] and r["total"] == len(RU_PAGES)
    small = notebook.read_location(ru.dir, 1, len(RU_PAGES), max_chars=200)
    assert small["to"] < len(RU_PAGES)                       # stops early and reports where


def test_read_location_clamps(sources):
    r = notebook.read_location(sources[0].dir, 999)
    assert r["total"] == len(RU_PAGES)


# ---------------------------------------------------------------- citations

def test_extract_citations_variants():
    t = "Это так [[A:41|слово один]] и [[b:c3 | other words ]], а ещё [[A:7–9|диапазон]] и [[C:2]] без выдержки."
    c = notebook.extract_citations(t)
    assert [(x["alias"], x["loc"], x["quote"]) for x in c] == [("A", "41", "слово один"), ("B", "c3", "other words"),
                                                                ("A", "7–9", "диапазон"), ("C", "2", "")]
    assert all(t[x["span"][0]:x["span"][1]].startswith("[[") for x in c)


def test_extract_citations_multiline_quote_and_unclosed():
    t = "[[A:5|первая строка\nвторая строка]] и недописанная [[A:6|обрыв"
    c = notebook.extract_citations(t)
    assert len(c) == 1 and "вторая" in c[0]["quote"]


def test_verify_quote_ok_same_page(sources):
    v = notebook.verify_quote(sources[0], 3, "Компрессор уменьшает динамический диапазон сигнала")
    assert v == {"status": "ok", "loc": 3}


def test_verify_quote_ignores_case_punctuation_spacing(sources):
    v = notebook.verify_quote(sources[0], 3, "компрессор   уменьшает, динамический диапазон — сигнала!")
    assert v["status"] == "ok"


def test_verify_quote_hyphenation(sources):
    v = notebook.verify_quote(sources[0], 11, "Дополнительная перенос слов проверка")
    assert v["status"] == "ok"


def test_verify_quote_adjacent_page_is_ok(sources):
    v = notebook.verify_quote(sources[0], 4, "Компрессор уменьшает динамический диапазон сигнала")      # really on page 3
    assert v == {"status": "ok", "loc": 3}


def test_verify_quote_moved(sources):
    v = notebook.verify_quote(sources[0], 10, "Эквалайзер усиливает или ослабляет выбранные частоты звука")      # page 5
    assert v == {"status": "moved", "loc": 5}


def test_verify_quote_fuzzy_extra_word(sources):
    q = "Компрессор уменьшает динамический диапазон сигнала: тихие места становятся громче, громкие тише очень"      # one extra word
    assert notebook.verify_quote(sources[0], 3, q)["status"] == "fuzzy"


def test_verify_quote_missing_and_short(sources):
    assert notebook.verify_quote(sources[0], 3, "Этой фразы в книге нет совсем нигде")["status"] == "missing"
    assert notebook.verify_quote(sources[0], 3, "ага")["status"] == "missing"
    assert notebook.verify_quote(sources[0], 3, "")["status"] == "missing"


def test_verify_quote_english_and_epub(sources):
    en, ep = sources[1], sources[2]
    assert notebook.verify_quote(en, 2, "three beats with equal value spaced across two beats")["status"] == "ok"
    assert notebook.verify_quote(ep, 2, "приходит шторм: волны разбивают лодки")["status"] in ("ok", "moved")
    assert notebook.verify_quote(ep, 1, "волны разбивают лодки")["status"] == "moved"      # chapter 2 text cited as chapter 1


def test_verify_citations_statuses(sources):
    t = ("[[A:3|Компрессор уменьшает динамический диапазон]] [[A:3|выдумка которой нет в тексте книги]] "
         "[[Z:1|нет такого источника]] [[A:3]] [[B:2|hemiola is simply three beats]]")
    res = notebook.verify_citations(t, sources)
    assert [r["status"] for r in res] == ["ok", "missing", "nosource", "noquote", "ok"]


def test_locate_quote():
    text = "Первый абзац.\nКомпрессор, уменьшает  ДИНАМИЧЕСКИЙ диапазон. Конец."
    a, b = notebook.locate_quote(text, "компрессор уменьшает динамический диапазон")
    assert text[a:b].lower().startswith("компрессор") and text[a:b].lower().endswith("диапазон")
    assert notebook.locate_quote(text, "нет такого") is None
    assert notebook.locate_quote(text, "") is None
    assert notebook.locate_quote("Ёжик в тумане", "ежик в тумане") == (0, 13)


def test_find_exact(sources):
    hits = notebook.find_exact([sources[0]], "низкие частоты ниже восьмидесяти герц")
    assert [h["loc"] for h in hits] == [6]
    assert notebook.find_exact([sources[0]], "ab") == []                       # too short to be meaningful
    assert notebook.find_exact([sources[0]], "несуществующая фраза совсем") == []


def test_outline_and_sections_do_not_crash(sources):
    for s in sources:
        assert isinstance(notebook.outline_of(s.dir), list)
        assert isinstance(notebook.sections_of(s.dir), list)


def test_json_helpers(tmp_path):
    d = str(tmp_path)
    assert notebook.read_json(d, "x.json") is None
    notebook.write_json(d, "x.json", {"a": "кириллица"})
    assert notebook.read_json(d, "x.json") == {"a": "кириллица"}
    (tmp_path / "bad.json").write_text("{oops", encoding="utf-8")
    assert notebook.read_json(d, "bad.json") is None                         # a broken file is not an error


# ---------------------------------------------------------------- JSON from model answers

def _extract():
    import notebook_api
    return notebook_api.extract_json


@pytest.mark.parametrize("raw,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Вот результат:\n{"a": {"b": [1, 2]}}\nГотово.', {"a": {"b": [1, 2]}}),
    ('текст {не json} потом {"ok": true}', {"ok": True}),
    ('{"s": "строка со скобкой } внутри", "n": 2}', {"s": "строка со скобкой } внутри", "n": 2}),
    ('{"q": "кавычка \\" внутри"}', {"q": 'кавычка " внутри'}),
])
def test_extract_json(raw, expected):
    assert _extract()(raw) == expected


@pytest.mark.parametrize("raw", ["", "просто текст", "[1, 2, 3]", '{"a": 1'])
def test_extract_json_failures(raw):
    with pytest.raises(ValueError):
        _extract()(raw)


# ---------------------------------------------------------------- DjVu helpers (no DjVuLibre needed)

def test_djvu_sexp_outline(monkeypatch):
    raw = '(bookmarks\n ("Глава 1" "#3"\n  ("Раздел 1.1" "#4"))\n ("Глава 2" "#7")\n ("Битая" "http://x")\n ("Далеко" "#999"))'
    monkeypatch.setattr(djvu_support, "_sed", lambda path, expr, timeout=180: raw)
    assert djvu_support.outline("fake.djvu", 10) == [[1, "Глава 1", 2], [2, "Раздел 1.1", 3], [1, "Глава 2", 6]]


def test_djvu_outline_empty_and_error(monkeypatch):
    monkeypatch.setattr(djvu_support, "_sed", lambda *a, **k: "")
    assert djvu_support.outline("x", 5) == []

    def boom(*a, **k):
        raise RuntimeError("no outline")
    monkeypatch.setattr(djvu_support, "_sed", boom)
    assert djvu_support.outline("x", 5) == []


def test_djvu_texts_split_and_clean(monkeypatch):
    raw = "стр\x1d один\x0c  второй\t\tтекст \x0c"
    monkeypatch.setattr(djvu_support, "_sed", lambda *a, **k: raw)
    assert djvu_support.texts("x", 2) == ["стр один", "второй текст"]


def test_djvu_find_tool_env(monkeypatch, tmp_path):
    exe = tmp_path / ("ddjvu.exe" if os.name == "nt" else "ddjvu")
    exe.write_text("")
    monkeypatch.setenv("READER3_DJVULIBRE", str(tmp_path))
    djvu_support._found.clear()
    assert djvu_support.find_tool("ddjvu") == str(exe)
    djvu_support._found.clear()
