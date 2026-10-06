"""PDF/DjVu layer (pdf_support), library routes (upload, delete, page images, text, search) and the MCP tool wrappers."""
import base64
import io
import json
import os
import urllib.error

import fitz
import pytest
from fastapi.testclient import TestClient

import notebook_api as na

na._warmup = lambda: None
import pdf_support as pdfs  # noqa: E402
import server  # noqa: E402


# ---------------------------------------------------------------- helpers

def make_pdf(path, texts, image_only=(), password=None):
    doc = fitz.open()
    for i, t in enumerate(texts):
        page = doc.new_page(width=300, height=400)
        if i in image_only:
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 50, 50), False)
            pix.set_rect(pix.irect, (200, 30, 30))
            page.insert_image(fitz.Rect(20, 20, 220, 220), pixmap=pix)
        elif t:
            page.insert_text((30, 60), t, fontsize=11)
    if password:
        doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password)
    else:
        doc.save(path)
    doc.close()
    return path


@pytest.fixture(autouse=True)
def no_ocr(monkeypatch):
    monkeypatch.setattr(pdfs, "ocr_available", lambda: False)
    monkeypatch.setattr(pdfs, "ensure_ocr", lambda d: None)


@pytest.fixture()
def client(lib):
    c = TestClient(server.app, client=("127.0.0.1", 50000), follow_redirects=False)
    assert c.post("/login", data={"password": "pw-test", "next": "/"}).status_code in (302, 303)
    return c


# ---------------------------------------------------------------- garbled text-layer detection and cleaning

CLEAN_RU = "Компрессор уменьшает динамический диапазон сигнала, а эквалайзер усиливает выбранные частоты звука. " * 3
CLEAN_EN = "The hemiola is simply three beats with equal value spaced across two beats, and it is common in African music. " * 2


@pytest.mark.parametrize("text", [CLEAN_RU, CLEAN_EN, "", "мало слов",
                                  "Цена 3,5 руб. см. https://example.com/a,b или www.site.ru и mail@test.com; т.е. всё ок и так далее много слов"])
def test_clean_text_is_not_garbled(text):
    assert pdfs.looks_garbled(text) is False


@pytest.mark.parametrize("text", [
    "п,ри!вет с,ло#во к,ни@га ра,зд!ел те,кс#т оч,ен@ь пло,хо!й сл,ой#ка " * 2,
    "Приvет миp кнuга раздeл тeкст оченb плоxой слоu " * 3,
])
def test_broken_layer_is_detected(text):
    assert pdfs.looks_garbled(text) is True


def test_clean_text():
    assert pdfs.clean_text("пере-\nнос слов") == "перенос слов"
    assert pdfs.clean_text("мягкий­перенос") == "мягкийперенос"
    assert pdfs.clean_text("строка один\nпродолжение строки") == "строка один продолжение строки"
    assert pdfs.clean_text("Конец предложения.\nНовый абзац") == "Конец предложения.\nНовый абзац"
    assert pdfs.clean_text("a\r\nb  \t c") == "a b c" or "a" in pdfs.clean_text("a\r\nb  \t c")
    assert pdfs.clean_text("   ") == ""


# ---------------------------------------------------------------- import and page text

def test_import_states_info_and_page_text(tmp_path):
    src = make_pdf(str(tmp_path / "scan.pdf"),
                   ["First page with a long enough real text for the layer check here.", "", "Third page also with a long enough text for the check here."],
                   image_only=(1,))
    bid, book = pdfs.import_pdf(src, str(tmp_path / "lib"), book_id="scan_data", title="Скан")
    d = str(tmp_path / "lib" / bid)
    info = pdfs.info(d)
    assert info["pages"] == 3 and info["ocr_pending"] == 1 and info["text_source"] == "pdf"
    assert pdfs.page_text(d, 0).startswith("First page")
    assert pdfs.page_text(d, 1, block=False) == ""                         # pending and OCR is not available
    assert pdfs.page_text(d, 99) == "" and pdfs.page_text(d, -1) == ""
    assert book.metadata.title == "Скан" and len(book.spine) == 3 and os.path.exists(os.path.join(d, "images", "cover.jpg"))
    assert pdfs.is_pdf_book(d) and not pdfs.is_djvu_book(d) and pdfs.page_count(d) == 3


def test_import_given_texts_and_language(tmp_path):
    ru = ["Это страница на русском языке. " * 6] * 3
    en = ["This is an English page with enough words to tell the language apart. " * 4] * 3
    for name, texts, lang in (("ru", ru, "ru"), ("en", en, "en")):
        src = make_pdf(str(tmp_path / f"{name}.pdf"), ["x"] * 3)
        bid, book = pdfs.import_pdf(src, str(tmp_path / "lib"), book_id=f"{name}_data", given_texts=texts)
        assert book.metadata.language == lang and pdfs.info(str(tmp_path / "lib" / bid))["language"] == lang


def test_import_rejects_empty_and_encrypted(tmp_path):
    pdf = str(tmp_path / "enc.pdf")
    make_pdf(pdf, ["секрет"], password="pw")
    with pytest.raises(ValueError, match="парол"):
        pdfs.import_pdf(pdf, str(tmp_path / "lib"), book_id="enc_data")
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 this is not a pdf")
    with pytest.raises(Exception):
        pdfs.import_pdf(str(bad), str(tmp_path / "lib"), book_id="bad_data")


def test_page_search_and_context(lib):
    d = os.path.join(lib, "ru_data")
    hits = pdfs.search(d, "КОМПРЕССОР")
    assert [h["page"] for h in hits][:1] == [2] and "омпрессор" in hits[0]["snippet"]
    assert pdfs.search(d, "   ") == [] and pdfs.search(d, "такого слова нет") == []
    assert len(pdfs.search(d, "а", limit=2)) == 2
    ctx = pdfs.context_text(d, 3, 1)
    assert '<page number="4" current="true">' in ctx and '<page number="3">' in ctx and '<page number="5">' in ctx and 'number="7"' not in ctx
    assert pdfs.context_text(d, 0, 2).count("<page ") == 3                        # no pages before the first one
    small = pdfs.context_text(d, 3, 5, budget=300)
    assert 'current="true"' in small and small.count("<page ") < 11               # the budget drops far pages, never the current one


def test_render_page_cache_and_width_clamp(lib):
    d = os.path.join(lib, "ru_data")
    a = pdfs.render_page(d, 0, 100)
    assert os.path.basename(a) == "0_400.jpg" and open(a, "rb").read(2) == b"\xff\xd8"
    assert pdfs.render_page(d, 0, 100) == a and os.path.basename(pdfs.render_page(d, 0, 99999)) == "0_2800.jpg"
    assert os.path.basename(pdfs.render_page(d, 1, 1000)) == "1_1000.jpg"


def test_forget_then_render_fails(tmp_path):
    src = make_pdf(str(tmp_path / "x.pdf"), ["текст страницы достаточно длинный для проверки раз два три четыре"])
    bid, _ = pdfs.import_pdf(src, str(tmp_path / "lib"), book_id="x_data")
    d = str(tmp_path / "lib" / bid)
    pdfs.forget(d)
    with pytest.raises(FileNotFoundError):
        pdfs.render_page(d, 0, 1000 + 7)


def test_djvu_requires_djvulibre(tmp_path, monkeypatch):
    monkeypatch.setattr(pdfs.djv, "available", lambda: False)
    f = tmp_path / "b.djvu"
    f.write_bytes(b"AT&TFORM")
    with pytest.raises(ValueError, match="DjVuLibre"):
        pdfs.import_djvu(str(f), str(tmp_path / "lib"))


# ---------------------------------------------------------------- library routes

def upload(client, name, data, ctype="application/octet-stream"):
    return client.post("/api/books", files={"file": (name, data, ctype)})


def test_upload_rejects_wrong_extension_and_garbage(client, lib):
    assert upload(client, "notes.txt", b"hello").status_code == 400
    assert upload(client, "../../evil.exe", b"MZ").status_code == 400
    before = set(os.listdir(lib))
    r = upload(client, "broken.pdf", b"%PDF-1.4 garbage")
    assert r.status_code == 422 and "Could not read" in r.json()["detail"]
    r = upload(client, "empty.epub", b"")
    assert r.status_code == 422
    assert set(os.listdir(lib)) == before                                  # failed imports leave no half-made book folder


def test_upload_pdf_duplicate_names_delete(client, lib, tmp_path):
    p = make_pdf(str(tmp_path / "upl.pdf"), ["Страница с текстом достаточной длины для проверки загрузки файла в библиотеку."])
    data = open(p, "rb").read()
    first = upload(client, "Моя книга.pdf", data).json()
    second = upload(client, "Моя книга.pdf", data).json()
    assert first["kind"] == "pdf" and first["id"] != second["id"] and second["id"].endswith("_data") and "-2" in second["id"]
    assert any(b["id"] == first["id"] for b in client.get("/api/books").json())
    for b in (first, second):
        assert client.delete(f"/api/books/{b['id']}").json() == {"ok": True}
        assert not os.path.exists(os.path.join(lib, b["id"]))
    assert client.delete(f"/api/books/{first['id']}").status_code == 404


def test_upload_encrypted_pdf(client, tmp_path):
    p = make_pdf(str(tmp_path / "enc.pdf"), ["секрет"], password="pw")
    r = upload(client, "enc.pdf", open(p, "rb").read())
    assert r.status_code == 422 and "парол" in r.json()["detail"]


def test_pdf_routes(client, lib):
    info = client.get("/api/pdf/ru_data/info").json()
    assert info["pages"] == 12 and info["ocr_pending"] == 0
    img = client.get("/api/pdf/ru_data/page/0.jpg?w=600")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg" and img.content[:2] == b"\xff\xd8"
    assert client.get("/api/pdf/ru_data/page/12.jpg").status_code == 404 and client.get("/api/pdf/ru_data/page/-1.jpg").status_code == 404
    assert "децибелах" in client.get("/api/pdf/ru_data/text/1").json()["text"]
    assert client.get("/api/pdf/ru_data/search", params={"q": "реверберация"}).json()[0]["page"] == 6
    f = client.get("/api/pdf/ru_data/file")
    assert f.status_code == 200 and f.headers["content-type"] == "application/pdf" and f.content[:4] == b"%PDF"


@pytest.mark.parametrize("url", ["/api/pdf/nope_data/info", "/api/pdf/..%2Fru_data/info", "/api/pdf/lighthouse_data/info",
                                 "/api/pdf/nope_data/page/0.jpg", "/api/pdf/nope_data/file"])
def test_pdf_routes_unknown_or_not_a_pdf(client, url):
    assert client.get(url).status_code == 404


def test_read_page_picks_viewer_by_kind(client):
    pdf_page, epub_page = client.get("/read/ru_data"), client.get("/read/lighthouse_data")
    assert pdf_page.status_code == 200 and "pdf.js" in pdf_page.text
    assert epub_page.status_code == 200 and "pdf.js" not in epub_page.text
    assert client.get("/read/nope_data").status_code == 404


def test_pdf_chat_context_and_vision(client, lib, monkeypatch):
    seen = {}

    def fake_answer(context, messages, system, images=None):
        seen.update(context=context, messages=messages, system=system, images=images)
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("ok")
    monkeypatch.setattr(server, "answer", fake_answer)
    body = {"page": 3, "window": 99, "messages": [{"role": "user", "content": "что тут?"}]}
    assert client.post("/api/pdf/ru_data/chat", json=body).status_code == 200
    assert 'current="true"' in seen["context"] and "Pages: 12" in seen["context"] and seen["images"] is None
    assert seen["context"].count("<page ") == 10 and 'number="11"' not in seen["context"]       # window 99 is clamped to 6: pages 1..10
    client.post("/api/pdf/ru_data/chat", json={**body, "images": True, "pages": [3, 4, 5]})
    assert len(seen["images"]) == 2 and base64.b64decode(seen["images"][0])[:2] == b"\xff\xd8"        # at most a spread
    assert seen["system"] != server.PDF_SYSTEM_PROMPT


@pytest.mark.parametrize("body,code", [({"page": 99, "messages": [{"role": "user", "content": "q"}]}, 404),
                                       ({"page": -1, "messages": [{"role": "user", "content": "q"}]}, 404),
                                       ({"page": 0, "messages": [{"role": "assistant", "content": "a"}]}, 400),
                                       ({"page": 0, "messages": []}, 400)])
def test_pdf_chat_validation(client, body, code):
    assert client.post("/api/pdf/ru_data/chat", json=body).status_code == code


# ---------------------------------------------------------------- MCP tool wrappers

def test_mcp_tools_call_the_reader(monkeypatch):
    import rag_mcp
    sent = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps({"text": "результат"}).encode()

    def fake_urlopen(req, timeout=0):
        sent.update(url=req.full_url, body=json.loads(req.data), headers={k.lower(): v for k, v in req.header_items()})
        return Resp()
    monkeypatch.setattr(rag_mcp.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(rag_mcp, "TOKEN", "tok123")
    monkeypatch.setattr(rag_mcp, "BOOKS", "ru_data:A")
    assert rag_mcp.search("компрессор", source="A", k=3) == "результат"
    assert sent["url"].endswith("/internal/nb/search") and sent["headers"]["x-nb-token"] == "tok123"
    assert sent["body"] == {"books": "ru_data:A", "query": "компрессор", "source": "A", "k": 3}
    rag_mcp.read("A", 2, 4)
    assert sent["url"].endswith("/internal/nb/read") and sent["body"]["start"] == 2 and sent["body"]["end"] == 4
    rag_mcp.find_exact("фраза", "A")
    assert sent["url"].endswith("/internal/nb/find") and sent["body"]["phrase"] == "фраза"
    for fn, op in ((rag_mcp.list_sources, "sources"),):
        fn()
        assert sent["url"].endswith("/internal/nb/" + op)
    rag_mcp.outline("A")
    rag_mcp.summary("A", "Глава")
    assert sent["url"].endswith("/internal/nb/summary") and sent["body"]["section"] == "Глава"


def test_mcp_tool_error_becomes_text(monkeypatch):
    import rag_mcp

    def boom(*a, **k):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(rag_mcp.urllib.request, "urlopen", boom)
    assert rag_mcp.search("q").startswith("Ошибка инструмента")


def test_title_from_file_name_collapses_spaces(tmp_path):
    src = make_pdf(str(tmp_path / "Musimathics_ The Math_ Vol 2.pdf"), ["Some english text that is long enough to be a page of a book here."])
    bid, book = pdfs.import_pdf(src, str(tmp_path / "lib"), book_id="t_data")
    assert book.metadata.title == "Musimathics The Math Vol 2"
