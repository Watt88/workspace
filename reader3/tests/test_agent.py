"""agent_stream: how the reader drives headless Claude (stream-json parsing, errors, timeouts, cleanup), with a fake `claude`."""
import base64
import glob
import json
import os
import subprocess
import sys
import tempfile
import time

import pytest

import notebook_api as na

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_claude.py")


@pytest.fixture(autouse=True)
def fake_claude(monkeypatch):
    real = subprocess.Popen

    def popen(cmd, **kw):
        return real([sys.executable, FAKE] + list(cmd[1:]), **kw)

    monkeypatch.setattr(na.subprocess, "Popen", popen)
    monkeypatch.setitem(na.CTX, "backend", "claude-code")
    monkeypatch.setitem(na.CTX, "port", lambda: 8123)


@pytest.fixture()
def scenario(monkeypatch):
    def set_(name):
        monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", name)
    return set_


def run(srcs, prompt="вопрос", system="системная инструкция", **kw):
    return list(na.agent_stream(system, prompt, srcs, **kw))


def leftovers():
    tmp = tempfile.gettempdir()
    return set(glob.glob(os.path.join(tmp, "nb-sys-*")) + glob.glob(os.path.join(tmp, "nb-mcp-*")))


def test_normal_stream(sources, scenario):
    scenario("ok")
    ev = run(sources[:1])
    kinds = [e["type"] for e in ev]
    assert kinds == ["delta", "status", "delta", "delta", "done"]
    assert ev[1] == {"type": "status", "tool": "search", "input": {"query": "q", "source": "A"}}      # the mcp__book__ prefix is stripped
    assert "".join(e["text"] for e in ev if e["type"] == "delta") == "Сейчас поищу. Ответ готов."


def test_command_line_and_mcp_config(sources, scenario):
    scenario("echo")
    ev = run(sources[:2], prompt="что такое ритм?", system="СИСТЕМНЫЙ ТЕКСТ")
    info = json.loads(ev[0]["text"])
    a = info["args"]
    assert "-p" in a and a[a.index("--output-format") + 1] == "stream-json" and "--include-partial-messages" in a
    assert "--strict-mcp-config" in a and a[a.index("--tools") + 1] == ""                      # no built-in tools: only the book tools
    assert a[a.index("--allowedTools") + 1].startswith("mcp__book__")
    assert info["stdin"] == "что такое ритм?"
    cfg = info["--mcp-config"]
    server = json.loads(cfg)["mcpServers"]["book"]
    assert server["env"]["NB_TOKEN"] == na.TOKEN and server["env"]["NB_URL"] == "http://127.0.0.1:8123"
    assert server["env"]["NB_BOOKS"] == "ru_data:A,en_data:B"
    assert server["args"][0].endswith("rag_mcp.py")
    assert info["--system-prompt-file"] == "СИСТЕМНЫЙ ТЕКСТ"


def test_api_key_is_not_passed_to_claude(sources, scenario, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    assert "ANTHROPIC_API_KEY" not in na.clean_env() and "ANTHROPIC_AUTH_TOKEN" not in na.clean_env()


def test_images_use_stream_json_input(sources, scenario):
    scenario("echo")
    img = base64.b64encode(b"\xff\xd8fakejpeg").decode()
    ev = run(sources[:1], prompt="что на странице?", images=[img])
    info = json.loads(ev[0]["text"])
    assert info["args"][info["args"].index("--input-format") + 1] == "stream-json"
    msg = json.loads(info["stdin"])
    blocks = msg["message"]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["data"] == img and blocks[-1] == {"type": "text", "text": "что на странице?"}


def test_mcp_server_failure_is_reported(sources, scenario):
    scenario("mcp_fail")
    ev = run(sources[:1])
    assert ev[0]["type"] == "error" and "failed" in ev[0]["message"]
    assert ev[-1]["type"] == "done"


def test_result_error(sources, scenario):
    scenario("is_error")
    ev = run(sources[:1])
    assert any(e["type"] == "error" and "rate limit" in e["message"] for e in ev) and ev[-1]["type"] == "done"


def test_process_crash_reports_stderr(sources, scenario):
    scenario("crash")
    ev = run(sources[:1])
    assert ev[0]["type"] == "error" and "not logged in" in ev[0]["message"]


def test_garbage_lines_are_skipped(sources, scenario):
    scenario("garbage")
    ev = run(sources[:1])
    assert "".join(e["text"] for e in ev if e["type"] == "delta") == "привет"


def test_timeout_kills_the_process(sources, scenario):
    scenario("hang")
    t0 = time.time()
    ev = run(sources[:1], timeout=1)
    assert time.time() - t0 < 15
    assert ev[-1]["type"] == "done"


def test_wrong_backend_does_not_spawn(sources, monkeypatch):
    monkeypatch.setitem(na.CTX, "backend", "anthropic-api")
    ev = run(sources[:1])
    assert len(ev) == 1 and ev[0]["type"] == "error" and "READER3_BACKEND=claude-code" in ev[0]["message"]


def test_cleanup_temp_files_and_slot_release(sources, scenario):
    before = leftovers()
    free0 = na._agent_slots._value
    for sc in ("ok", "crash", "mcp_fail"):
        scenario(sc)
        run(sources[:1])
    assert leftovers() == before and na._agent_slots._value == free0


def test_generator_closed_early_cleans_up(sources, scenario):
    scenario("ok")
    before, free0 = leftovers(), na._agent_slots._value
    g = na.agent_stream("s", "q", sources[:1])
    next(g)
    g.close()                                      # the browser closed the stream mid-answer
    assert leftovers() == before and na._agent_slots._value == free0


def test_chat_drops_preface_text_before_tool_call(lib, monkeypatch):
    """The text written before a tool call must not reach the verification (and the UI clears it on `status`)."""
    from fastapi.testclient import TestClient
    import server
    c = TestClient(server.app, client=("127.0.0.1", 50000), follow_redirects=False)
    c.post("/login", data={"password": "pw-test", "next": "/"})
    monkeypatch.setattr(na, "embedder", lambda: None)
    monkeypatch.setattr(na.rag_rerank, "rerank", lambda q, p: None)
    monkeypatch.setattr(na.notebook, "ensure_index", na.notebook.ensure_index)
    na.notebook.ensure_index(os.path.join(lib, "ru_data"), None)

    def stub(system, prompt, srcs, images=None, timeout=900):
        yield {"type": "delta", "text": "Сейчас проверю [[A:3|выдумка из прелюдии которой нет в книге]]. "}
        yield {"type": "status", "tool": "search", "input": {"query": "x"}}
        yield {"type": "delta", "text": "Итог: [[A:3|Компрессор уменьшает динамический диапазон]]"}
        yield {"type": "done"}
    monkeypatch.setattr(na, "agent_stream", stub)
    r = c.post("/api/notebook/chat", json={"books": ["ru_data"], "messages": [{"role": "user", "content": "q"}]})
    ver = [json.loads(l[6:]) for l in r.text.split("\n") if l.startswith("data: ") and '"citations"' in l][0]["citations"]
    assert [x["status"] for x in ver] == ["ok"]
