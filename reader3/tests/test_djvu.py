"""DjVu end to end with the real DjVuLibre tools (skipped when they are not installed)."""
import os
import subprocess
import tempfile

import pytest
from fastapi.testclient import TestClient

import notebook_api as na

na._warmup = lambda: None
import djvu_support as djv  # noqa: E402
import pdf_support as pdfs  # noqa: E402
import server  # noqa: E402

pytestmark = pytest.mark.skipif(not (djv.available() and djv.find_tool("cjb2") and djv.find_tool("djvm")), reason="DjVuLibre is not installed")

W, H = 200, 260
PAGES = ["Compressor reduces the dynamic range of a signal.", "Reverb creates the feeling of space around the sound."]


def _pbm(path, seed):
    rows = []
    for y in range(H):
        rows.append(bytes((0xAA if (y // 8 + seed) % 2 else 0x00) for _ in range((W + 7) // 8)))
    with open(path, "wb") as f:
        f.write(b"P4\n%d %d\n" % (W, H) + b"".join(rows))


def _run(*args):
    p = subprocess.run(list(args), capture_output=True)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")


@pytest.fixture(scope="module")
def djvu_file():
    d = tempfile.mkdtemp(prefix="r3djvu-")
    pages = []
    for i in range(2):
        pbm, dj = os.path.join(d, f"p{i}.pbm"), os.path.join(d, f"p{i}.djvu")
        _pbm(pbm, i)
        _run(djv.find_tool("cjb2"), pbm, dj)
        pages.append(dj)
    book = os.path.join(d, "test book.djvu")
    _run(djv.find_tool("djvm"), "-c", book, *pages)
    script = ""
    for i, t in enumerate(PAGES, 1):
        txt = os.path.join(d, f"t{i}.txt")
        with open(txt, "w", encoding="utf-8") as f:
            f.write(f'(page 0 0 {W} {H} (line 0 0 {W} 20 "{t}"))')
        script += f"select {i}; set-txt {txt}; "
    outl = os.path.join(d, "outline.txt")
    with open(outl, "w", encoding="utf-8") as f:
        f.write('(bookmarks ("Chapter one" "#1" ("Part 1.1" "#1")) ("Chapter two" "#2"))')
    script += f"set-outline {outl}; "
    _run(djv.find_tool("djvused"), book, "-e", script, "-s")
    return book


def test_tools_basic_reading(djvu_file):
    assert djv.page_count(djvu_file) == 2
    assert djv.page_size(djvu_file, 0) == (W, H)
    texts = djv.texts(djvu_file, 2)
    assert texts[0].startswith("Compressor reduces") and texts[1].startswith("Reverb")
    assert djv.outline(djvu_file, 2) == [[1, "Chapter one", 0], [2, "Part 1.1", 0], [1, "Chapter two", 1]]


def test_render_jpeg_and_png(djvu_file):
    from PIL import Image
    import io
    jpg = djv.render_jpeg(djvu_file, 0, 400)
    im = Image.open(io.BytesIO(jpg))
    assert im.format == "JPEG" and im.size == (400, 520)                          # the aspect ratio is kept
    png = Image.open(io.BytesIO(djv.render_png_for_ocr(djvu_file, 1, long_side=100)))
    assert png.mode == "L" and abs(max(png.size) - 100) <= 3


def test_broken_file_raises(tmp_path):
    bad = tmp_path / "bad.djvu"
    bad.write_bytes(b"AT&TFORM not really")
    with pytest.raises(RuntimeError):
        djv.page_count(str(bad))


def test_import_and_reader_pipeline(djvu_file, tmp_path, monkeypatch):
    monkeypatch.setattr(pdfs, "ocr_available", lambda: False)
    monkeypatch.setattr(pdfs, "ensure_ocr", lambda d: None)
    bid, book = pdfs.import_djvu(djvu_file, str(tmp_path), book_id="dj_data", title="DjVu тест")
    d = str(tmp_path / bid)
    assert pdfs.is_djvu_book(d) and pdfs.is_pdf_book(d) and os.path.exists(pdfs.source_file(d)) and pdfs.source_file(d).endswith("book.djvu")
    info = pdfs.info(d)
    assert info["pages"] == 2 and info["ocr_pending"] == 0 and info["outline"][0][1] == "Chapter one"
    assert pdfs.page_text(d, 0).startswith("Compressor") and pdfs.search(d, "reverb")[0]["page"] == 1
    img = pdfs.render_page(d, 1, 600)
    assert open(img, "rb").read(2) == b"\xff\xd8" and os.path.basename(img) == "1_600.jpg"
    assert book.metadata.title == "DjVu тест" and len(book.spine) == 2 and os.path.exists(os.path.join(d, "images", "cover.jpg"))


def test_upload_and_notebook_index(djvu_file, lib, monkeypatch):
    monkeypatch.setattr(pdfs, "ocr_available", lambda: False)
    monkeypatch.setattr(pdfs, "ensure_ocr", lambda d: None)
    c = TestClient(server.app, client=("127.0.0.1", 50000), follow_redirects=False)
    assert c.post("/login", data={"password": "pw-test", "next": "/"}).status_code in (302, 303)
    r = c.post("/api/books", files={"file": ("test book.djvu", open(djvu_file, "rb").read(), "application/octet-stream")})
    assert r.status_code == 200 and r.json()["kind"] == "pdf", r.text
    bid = r.json()["id"]
    f = c.get(f"/api/pdf/{bid}/file")
    assert f.status_code == 200 and f.headers["content-type"] == "image/vnd.djvu" and f.content[:8] == b"AT&TFORM"
    assert c.get(f"/api/pdf/{bid}/page/1.jpg").content[:2] == b"\xff\xd8"
    import notebook
    d = os.path.join(lib, bid)
    notebook.ensure_index(d, None, force=True)
    src = notebook.make_sources([(bid, "A")], lambda b: d)[0]
    assert src.kind == "pdf" and src.lang == "ru" or src.lang == "en"
    res = notebook.search([src], "reverb space", k=2, embedder=None, reranker=None)
    assert res and res[0]["loc_start"] <= 2 <= res[0]["loc_end"]
    assert notebook.verify_quote(src, 2, "Reverb creates the feeling of space")["status"] == "ok"
    assert c.delete(f"/api/books/{bid}").json() == {"ok": True}
