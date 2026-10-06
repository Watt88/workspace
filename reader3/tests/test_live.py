"""Opt-in end-to-end check with the real `claude` CLI and the real MCP server (uses the subscription: READER3_LIVE=1).
Models are replaced by lexical search; the point is the whole loop: question -> tool calls -> answer -> verified citations."""
import json
import os
import socket
import threading
import time
import urllib.request
import http.cookiejar

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("READER3_LIVE") != "1", reason="set READER3_LIVE=1 to run against the real claude CLI")

import notebook_api as na  # noqa: E402

na._warmup = lambda: None
import server  # noqa: E402
import uvicorn  # noqa: E402


@pytest.fixture(scope="module")
def live(lib):
    mp = pytest.MonkeyPatch()
    mp.setattr(na, "embedder", lambda: None)
    mp.setattr(na.rag_rerank, "rerank", lambda q, p: None)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    mp.setitem(na.CTX, "port", lambda: port)                       # the MCP process calls back to this port
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    while not srv.started:
        time.sleep(0.1)
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.open(urllib.request.Request(f"http://127.0.0.1:{port}/login", data=b"password=pw-test&next=%2F"))
    base = f"http://127.0.0.1:{port}"
    for b in ("en_data", "ru_data"):
        op.open(urllib.request.Request(base + "/api/notebook/prepare", data=json.dumps({"book": b, "guide": False}).encode(),
                                       headers={"Content-Type": "application/json"}))
    for _ in range(100):
        cards = json.load(op.open(base + "/api/notebook/sources"))["books"]
        if all(c["index"]["ready"] for c in cards if c["id"] in ("en_data", "ru_data")):
            break
        time.sleep(0.3)
    yield op, base
    srv.should_exit = True
    th.join(5)
    mp.undo()


def chat(live, books, q):
    op, base = live
    req = urllib.request.Request(base + "/api/notebook/chat", headers={"Content-Type": "application/json"},
                                 data=json.dumps({"books": books, "messages": [{"role": "user", "content": q}], "length": "short"}).encode())
    text, tools, verify, err, ev = "", [], [], [], None
    for raw in op.open(req, timeout=300):
        line = raw.decode().rstrip()
        if line.startswith("event: "):
            ev = line[7:]
        elif line.startswith("data: "):
            d = json.loads(line[6:])
            if ev == "delta":
                text += d["text"]
            elif ev == "status":
                tools.append(d["tool"])
            elif ev == "verify":
                verify = d["citations"]
            elif ev == "error":
                err.append(d["message"])
    return text, tools, verify, err


def test_russian_question_to_english_book_has_verified_citations(live):
    text, tools, verify, err = chat(live, ["en_data"], "Что такое гемиола? Приведи цитату из книги.")
    assert not err, err
    assert "search" in tools or "read" in tools                       # the model really used the book tools
    assert verify and all(c["status"] in ("ok", "moved", "fuzzy") for c in verify), verify
    assert "hemiola" in " ".join(c["quote"].lower() for c in verify)


def test_unanswerable_question_is_refused_without_invented_quotes(live):
    text, tools, verify, err = chat(live, ["ru_data"], "Какой рецепт борща приводит автор?")
    assert not err, err
    assert all(c["status"] in ("ok", "moved", "fuzzy") for c in verify), verify          # never a fabricated quotation
    assert any(w in text.lower() for w in ("нет", "не нашёл", "не упомин", "не содержит", "отсутств"))
