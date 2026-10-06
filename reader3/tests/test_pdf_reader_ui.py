"""PDF/DjVu reader in the browser: page images, AI panel, vision toggle, phone layout (replaces the old manual t_chat/t_vision scripts).
The model call is replaced by a stub that records what the browser sent."""
import json

import pytest
from fastapi.responses import StreamingResponse
from playwright.sync_api import expect

import server
from test_ui import base, browser, new_page, page, prepared  # noqa: F401  (shared fixtures: live server, browser, logged-in page)


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@pytest.fixture()
def model(monkeypatch):
    seen = {"calls": []}

    def fake_answer(context, messages, system="", images=None):
        seen["calls"].append({"context": context, "messages": messages, "images": images})
        script = seen.get("script") or [("delta", {"text": "Ответ про "}), ("delta", {"text": "компрессор."}), ("done", {})]

        def gen():
            for ev, d in script:
                yield sse(ev, d)
        return StreamingResponse(gen(), media_type="text/event-stream")
    monkeypatch.setattr(server, "answer", fake_answer)
    return seen


def open_pdf(pg, base, page_no=4):
    pg.goto(f"{base}/read/ru_data#p={page_no}")
    pg.wait_for_function("document.querySelector('#pages img') && document.querySelector('#pages img').complete && document.querySelector('#pages img').naturalWidth > 0",
                         timeout=20000)


def ask(pg, text):
    if not pg.locator("#ai-input").is_visible():                 # the button toggles the panel: open it only when closed
        pg.click("#ai-btn")
    pg.wait_for_selector("#ai-input", state="visible")
    pg.fill("#ai-input", text)
    pg.press("#ai-input", "Enter")


def test_pages_render_and_toolbar(page, base):
    open_pdf(page, base)
    for ident in ("toc-btn", "search-btn", "text-btn", "ai-btn"):
        assert page.locator(f"#{ident}").count() == 1, ident
    assert page.evaluate("document.querySelector('#pages img').naturalWidth") > 0


def test_ai_answer_and_context_page(page, base, model):
    open_pdf(page, base, 4)
    page.click("#ai-btn")
    expect(page.locator(".suggestion").first).to_be_visible()
    import re
    lo, hi = map(int, re.findall(r"\d+", page.inner_text("#ai-context"))[:2])
    assert lo <= 4 <= hi                                                             # the panel says which pages it reads
    ask(page, "Что написано на этой странице?")
    expect(page.locator(".msg.assistant")).to_contain_text("Ответ про компрессор.", timeout=15000)
    call = model["calls"][0]
    assert 'current="true"' in call["context"] and "Параметр порог" in call["context"]           # page 4 text is in the context
    assert call["messages"][-1] == {"role": "user", "content": "Что написано на этой странице?"}


def test_vision_toggle_controls_images_and_is_remembered(page, base, model):
    open_pdf(page, base, 3)
    page.click("#ai-btn")
    page.wait_for_selector("#ai-vision")
    assert page.is_checked("#ai-vision")                                          # pictures are on by default
    ask(page, "Что на картинке?")
    expect(page.locator(".msg.assistant")).to_contain_text("Ответ", timeout=15000)
    imgs = model["calls"][0]["images"]
    assert imgs and 1 <= len(imgs) <= 2 and all(isinstance(i, str) and len(i) > 100 for i in imgs)
    page.uncheck("#ai-vision")
    page.reload()
    page.wait_for_selector("#pages img")
    page.click("#ai-btn")
    assert not page.is_checked("#ai-vision")                                      # remembered across reloads
    ask(page, "А теперь только текст")
    expect(page.locator(".msg.assistant").nth(1)).to_contain_text("Ответ", timeout=15000)
    assert not model["calls"][1]["images"]


def test_ai_error_is_shown_and_ui_recovers(page, base, model):
    model["script"] = [("error", {"message": "Нет доступа к Claude API"})]
    open_pdf(page, base)
    ask(page, "вопрос")
    expect(page.locator(".msg.error")).to_contain_text("Нет доступа")
    assert page.get_attribute("#ai-send", "aria-label") == "Отправить"             # the send button is back
    model["script"] = None
    page.fill("#ai-input", "ещё раз")
    page.press("#ai-input", "Enter")
    expect(page.locator(".msg.assistant").last).to_contain_text("Ответ про компрессор.", timeout=15000)


def test_phone_layout(browser, base, model):
    pg = new_page(browser, base, 390, 844, mobile=True)
    try:
        open_pdf(pg, base)
        shown = pg.evaluate("['toc-btn','search-btn','text-btn','ai-btn'].map(i => [i, getComputedStyle(document.getElementById(i)).display])")
        assert all(d != "none" for _, d in shown), shown
        assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        assert pg.errors == [], pg.errors
    finally:
        pg.context.close()
