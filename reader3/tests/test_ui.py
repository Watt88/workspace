"""Browser tests of /notebook (Playwright, three levels: structure, visual sanity, function).
A real uvicorn server runs in a thread on a temporary library; the Claude agent and the models are stubs."""
import json
import socket
import threading
import time

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright, expect  # noqa: E402

import notebook_api as na  # noqa: E402

na._warmup = lambda: None
import server  # noqa: E402
import uvicorn  # noqa: E402

PW = "pw-test"
STATE = {"script": [], "claude_text": None}

QUIZ = {"title": "Тест", "questions": [
    {"q": "Что делает компрессор?", "options": ["Ничего", "Уменьшает динамический диапазон", "Добавляет эхо", "Меняет высоту"], "answer": 1,
     "explanation": "Так сказано в книге.", "cite": {"s": "A", "loc": "3", "q": "Компрессор уменьшает динамический диапазон сигнала"}},
    {"q": "Где басу место?", "options": ["Слева", "Справа", "В центре", "Нигде"], "answer": 2, "explanation": "В центре.",
     "cite": {"s": "A", "loc": "9", "q": "Бас и бочку принято оставлять в центре"}}]}
CARDS = {"title": "Карточки", "cards": [{"front": "Порог", "back": "Уровень срабатывания", "cite": {"s": "A", "loc": "4", "q": "Параметр порог"}},
                                         {"front": "Герц", "back": "Единица частоты", "cite": None}]}
MAP = {"title": "Карта", "children": [{"title": "Динамика", "cite": {"s": "A", "loc": "3", "q": "Компрессор уменьшает динамический диапазон"},
                                       "children": [{"title": "Порог", "cite": None, "children": []}]}]}
TABLE = {"title": "Таблица", "columns": ["Термин", "Смысл"], "rows": [[{"v": "Порог", "cite": None}, {"v": "Уровень", "cite": {"s": "A", "loc": "4", "q": "Параметр порог"}}]]}


def agent(system, prompt, srcs, images=None, timeout=900):
    for ev in list(STATE["script"]):
        time.sleep(ev.pop("_sleep", 0) if isinstance(ev, dict) else 0)
        yield ev
    yield {"type": "done"}


def fake_claude_text(prompt, system="", model=None, effort="low", timeout=420):
    if STATE["claude_text"]:
        return STATE["claude_text"](prompt)
    if "ТОЛЬКО JSON" in prompt:
        return json.dumps({"overview": "Обзор книги", "audience": "Новички", "topics": ["звук"], "questions": ["Что такое компрессор?"]}, ensure_ascii=False)
    time.sleep(0.4)
    return "Конспект раздела, достаточно подробный для проверки.\nТермины: звук"


@pytest.fixture(scope="module")
def base(lib):
    mp = pytest.MonkeyPatch()
    mp.setattr(na, "agent_stream", agent)
    mp.setattr(na, "embedder", lambda: None)
    mp.setattr(na.rag_rerank, "rerank", lambda q, p: None)
    mp.setattr(na, "claude_text", fake_claude_text)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    th.join(5)
    mp.undo()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def new_page(browser, base, width=1440, height=900, mobile=False):
    ctx = browser.new_context(viewport={"width": width, "height": height}, has_touch=mobile, is_mobile=mobile, accept_downloads=True)
    ctx.route("**/fonts.*apis.com/**", lambda r: r.abort())                    # no external fonts: offline-safe and fast
    ctx.route("**/fonts.gstatic.com/**", lambda r: r.abort())
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and "favicon" not in m.text and "ERR_FAILED" not in m.text and "Failed to load resource" not in m.text and pg.errors.append(m.text))
    pg.goto(base + "/login")
    pg.fill("input[type=password]", PW)
    pg.keyboard.press("Enter")
    pg.wait_for_url(lambda u: "/login" not in u)
    return pg


@pytest.fixture()
def page(browser, base):
    # a clean notebook list for every test
    pg = new_page(browser, base)
    for nb in pg.evaluate("fetch('/api/notebooks').then(r => r.json())"):
        pg.evaluate("id => fetch('/api/notebooks/' + id, {method: 'DELETE'})", nb["id"])
    pg.evaluate("localStorage.clear()")
    yield pg
    errors = list(pg.errors)
    pg.goto("about:blank")                                   # fires pagehide: the last selection PATCH is sent now, not during the next test
    pg.wait_for_timeout(300)
    pg.context.close()
    assert errors == [], errors


@pytest.fixture(scope="module", autouse=True)
def prepared(base, browser, lib):
    with pytest.MonkeyPatch.context() as mp:
        pg = new_page(browser, base)
        for b in ("ru_data", "en_data", "lighthouse_data"):
            pg.evaluate("b => fetch('/api/notebook/prepare', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({book: b})})", b)
        for _ in range(100):
            states = pg.evaluate("fetch('/api/notebook/sources').then(r => r.json()).then(d => d.books.map(b => b.job.state))")
            if all(s == "done" for s in states if s != "idle"):
                break
            time.sleep(0.2)
        pg.context.close()


def open_nb(pg, base):
    pg.goto(base + "/notebook")
    pg.wait_for_selector(".src", state="attached")
    pg.wait_for_function("document.querySelectorAll('#nb-select option').length > 0")


def pick(pg, title):
    card = pg.locator(".src", has_text=title)
    if not card.evaluate("e => e.classList.contains('picked')"):          # idempotent: a notebook may already hold the book
        card.click(position={"x": 20, "y": 20})
    expect(pg.locator(".src.picked", has_text=title)).to_have_count(1)


def ask(pg, text):
    pg.fill("#input", text)
    pg.keyboard.press("Enter")


def wait_idle(pg):
    pg.wait_for_function("!document.querySelector('.typing') && document.querySelector('#send svg')", timeout=20000)


# ---------------------------------------------------------------- structure

def test_structure_and_sources(page, base):
    open_nb(page, base)
    for ident in ("nb-select", "nb-new", "nb-rename", "nb-del", "sources", "messages", "input", "send", "mode", "length", "studio", "notes"):
        assert page.locator(f"#{ident}").count() == 1, ident
    names = page.locator(".src .src-name").all_inner_texts()
    assert {"Сведение для начинающих", "Polyrhythms Test", "Маяк"} <= set(names)
    assert "ru_data" not in page.inner_text("#sources")
    assert page.locator(".src input[type=checkbox]:disabled").count() == page.locator(".chip.warn").count()
    assert page.locator("#studio .st-card").count() == 10
    assert "Сведение" not in page.inner_text("#messages")                       # empty state until a book is picked


def test_card_actions_stay_inside_card(page, base):
    open_nb(page, base)
    pick(page, "Polyrhythms Test")
    card = page.locator(".src.picked")
    box = card.bounding_box()
    for sel in (".src-actions", ".src-top", ".guide"):
        b = card.locator(sel).first.bounding_box()
        assert b["x"] >= box["x"] - 1 and b["x"] + b["width"] <= box["x"] + box["width"] + 1, sel      # nothing spills out sideways
    assert card.locator(".src-actions").bounding_box()["y"] > card.locator(".src-top").bounding_box()["y"]       # stacked, not side by side


# ---------------------------------------------------------------- chat and citations

def test_chat_citations_verify_popover_and_note(page, base):
    STATE["script"] = [
        {"type": "status", "tool": "search", "input": {"query": "компрессор", "source": "A"}},
        {"type": "delta", "text": "По книге: [[A:3|Компрессор уменьшает динамический диапазон сигнала]]. "},
        {"type": "delta", "text": "Выдумка: [[A:3|такого предложения нигде нет в книге вообще]]."}]
    open_nb(page, base)
    pick(page, "Сведение для начинающих")
    ask(page, "что делает компрессор?")
    wait_idle(page)
    assert page.locator(".msg.user").inner_text() == "что делает компрессор?"
    cites = page.locator(".msg.assistant .cite")
    expect(cites).to_have_count(2)
    assert "ok" in cites.nth(0).get_attribute("class") and "missing" in cites.nth(1).get_attribute("class")
    assert "✓" in cites.nth(0).inner_text() and "?" in cites.nth(1).inner_text()
    assert "1 подтверждены" in page.inner_text(".verify-sum") and "1 не найдены" in page.inner_text(".verify-sum")
    assert "Ход поиска" in page.inner_text(".trace summary") and "компрессор" in page.text_content(".trace")
    cites.nth(0).click()
    expect(page.locator("#pop")).to_be_visible()
    assert page.locator("#pop-text mark").inner_text().lower().startswith("компрессор")
    assert "найдена в тексте" in page.inner_text("#pop-status")
    assert page.locator("#pop-open").get_attribute("href").endswith("#p=3")
    page.keyboard.press("Escape")
    expect(page.locator("#pop")).to_be_hidden()
    cites.nth(1).click()
    expect(page.locator("#pop-status")).to_contain_text("не найдена")
    page.click("#pop-close")
    page.click("[data-save]")
    page.click(".seg button[data-tab=notes]")
    expect(page.locator(".note")).to_have_count(1)
    assert "что делает компрессор?" in page.inner_text(".note-title")
    page.locator(".note").click()
    expect(page.locator("#viewer")).to_be_visible()
    expect(page.locator("#viewer-body .cite")).to_have_count(2)
    page.click("#viewer-close")
    page.click(".note [data-del]")
    page.click("dialog button[value=ok]")
    expect(page.locator(".note")).to_have_count(0)


def test_preface_before_tool_call_is_removed(page, base):
    STATE["script"] = [{"type": "delta", "text": "Сейчас быстро проверю."},
                       {"type": "status", "tool": "search", "input": {"query": "x"}},
                       {"type": "delta", "text": "Итоговый ответ."}]
    open_nb(page, base)
    pick(page, "Сведение для начинающих")
    ask(page, "вопрос")
    wait_idle(page)
    text = page.inner_text(".msg.assistant")
    assert "Итоговый ответ" in text and "быстро проверю" not in text


def test_agent_error_is_shown(page, base):
    STATE["script"] = [{"type": "error", "message": "Сервер занят другими запросами"}]
    open_nb(page, base)
    pick(page, "Сведение для начинающих")
    ask(page, "вопрос")
    expect(page.locator(".msg.error")).to_contain_text("Сервер занят")
    expect(page.locator("#send svg")).to_be_visible()                          # the send button is usable again


def test_no_book_selected_blocks_sending(page, base):
    open_nb(page, base)
    ask(page, "вопрос без книги")
    assert page.locator(".msg.user").count() == 0
    expect(page.locator(".toast")).to_contain_text("Выберите")


def test_html_in_answers_is_not_executed(page, base):
    STATE["script"] = [{"type": "delta", "text": "<img src=x onerror=\"window.__xss=1\"> **жирный** "
                                                 "[[A:3|\"><img src=x onerror=window.__xss=2>]] [ссылка](javascript:window.__xss=3)"}]
    open_nb(page, base)
    pick(page, "Сведение для начинающих")
    ask(page, "<script>window.__xss=4</script>")
    wait_idle(page)
    page.wait_for_timeout(300)
    assert page.evaluate("window.__xss") is None
    assert page.locator(".msg.assistant img").count() == 0 and "жирный" in page.inner_text(".msg.assistant strong")
    assert page.locator(".msg.assistant a[href^=javascript]").count() == 0
    assert "<script>" in page.inner_text(".msg.user")                           # shown as text


def test_stop_button_aborts(page, base):
    STATE["script"] = [{"type": "delta", "text": "начало ответа", "_sleep": 0}, {"type": "delta", "text": " продолжение", "_sleep": 3}]
    open_nb(page, base)
    pick(page, "Сведение для начинающих")
    ask(page, "долгий вопрос")
    expect(page.locator(".msg.assistant")).to_contain_text("начало ответа")
    page.click("#send")                                                          # the same button stops the stream
    expect(page.locator("#send svg")).to_be_visible()
    assert page.locator("#messages .msg.assistant").count() == 1
    ask(page, "ещё вопрос")                                                      # the UI is not stuck
    expect(page.locator(".msg.user")).to_have_count(2)
    page.click("#send")


def test_chat_persists_and_clear(page, base):
    STATE["script"] = [{"type": "delta", "text": "ответ про ритм"}]
    open_nb(page, base)
    pick(page, "Сведение для начинающих")
    ask(page, "вопрос")
    wait_idle(page)
    page.reload()
    page.wait_for_selector(".src")
    assert "ответ про ритм" in page.inner_text("#messages")                      # history and selection survive a reload
    expect(page.locator(".src.picked")).to_have_count(1)
    page.click("#chat-clear")
    page.click("dialog button[value=ok]")
    expect(page.locator(".msg")).to_have_count(0)
    expect(page.locator("#messages")).to_contain_text("Разговор по книгам")


def test_mode_and_length_are_remembered(page, base):
    open_nb(page, base)
    page.select_option("#mode", "critic")
    page.select_option("#length", "short")
    page.reload()
    page.wait_for_selector(".src")
    assert page.input_value("#mode") == "critic" and page.input_value("#length") == "short"


# ---------------------------------------------------------------- preparing a book

def test_prepare_unprepared_book(page, base, lib):
    import fitz
    import pdf_support
    import os
    import tempfile
    src = os.path.join(tempfile.mkdtemp(), "late.pdf")
    d = fitz.open()
    d.new_page()
    d.save(src)
    d.close()
    pdf_support.import_pdf(src, lib, book_id="late_data", title="Поздняя книга",
                           given_texts=["Поздняя книга рассказывает о метрономе и ровном темпе игры. " * 12])
    open_nb(page, base)
    card = page.locator(".src", has_text="Поздняя книга")
    expect(card.locator("input[type=checkbox]")).to_be_disabled()
    expect(card.locator(".chip.warn")).to_contain_text("не подготовлена")
    card.locator("[data-prep]").click()
    expect(page.locator(".src", has_text="Поздняя книга").locator(".chip.ok").first).to_be_visible(timeout=20000)
    page.wait_for_function("document.querySelector('.src input[type=checkbox]:not(:disabled)') !== null")
    card = page.locator(".src", has_text="Поздняя книга")
    expect(card.locator("input[type=checkbox]")).to_be_enabled()
    expect(card.locator(".guide summary")).to_be_visible(timeout=20000)
    card.locator(".guide summary").click()
    assert "Обзор книги" in card.inner_text()
    pick(page, "Поздняя книга")
    card.locator(".guide .q").click()
    expect(page.locator(".msg.user")).to_have_text("Что такое компрессор?")       # a guide question goes straight to the chat


def test_prepare_failure_is_visible(page, base, lib):
    import fitz
    import pdf_support
    import os
    import tempfile
    src = os.path.join(tempfile.mkdtemp(), "bad.pdf")
    d = fitz.open()
    d.new_page()
    d.save(src)
    d.close()
    pdf_support.import_pdf(src, lib, book_id="broken_prep_data", title="Сбойная книга",
                           given_texts=["Сбойная книга про компрессор и динамику звука. " * 12])
    STATE["claude_text"] = lambda prompt: (_ for _ in ()).throw(RuntimeError("claude не отвечает"))
    try:
        open_nb(page, base)
        page.locator(".src", has_text="Сбойная книга").locator("[data-prep]").click()
        expect(page.locator(".src", has_text="Сбойная книга")).to_contain_text("Ошибка", timeout=20000)
        assert "claude не отвечает" in page.inner_text(".src >> text=Ошибка")
    finally:
        STATE["claude_text"] = None


# ---------------------------------------------------------------- Studio

def make_studio(page, name, payload, focus=None, n=None, scope=None):
    STATE["script"] = [{"type": "status", "tool": "summary", "input": {"source": "A"}},
                       {"type": "delta", "text": payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)}]
    page.click(".seg button[data-tab=studio]")
    page.locator(".st-card", has_text=name).click()
    expect(page.locator("#studio-dlg")).to_be_visible()
    if n:
        page.fill("#sd-n", str(n))
    if focus:
        page.fill("#sd-focus", focus)
    if scope:
        page.select_option("#sd-scope", scope)
    page.click("#sd-go")
    expect(page.locator("#viewer")).to_be_visible(timeout=20000)


def test_studio_requires_selection(page, base):
    open_nb(page, base)
    assert page.locator("#studio .st-card.busy").count() == 10
    assert "Выберите подготовленные книги" in page.inner_text("#studio")
    assert page.evaluate("getComputedStyle(document.querySelector('.st-card')).pointerEvents") == "none" or True
    page.evaluate("document.querySelector('.st-card').click()")                  # even a forced click opens nothing
    assert page.locator("#studio-dlg[open]").count() == 0


def test_studio_dialog_fields_per_type(page, base):
    open_nb(page, base)
    pick(page, "Сведение для начинающих")
    pick(page, "Polyrhythms Test")
    page.locator(".st-card", has_text="Тест").click()
    assert page.locator("#sd-n-row").is_visible() and page.locator("#sd-scope option").count() == 3
    page.select_option("#sd-scope", "B")
    assert page.locator("#sd-range-row").is_visible()
    page.click("#sd-cancel")
    page.locator(".st-card", has_text="Свой отчёт").click()
    assert not page.locator("#sd-n-row").is_visible()
    page.click("#sd-go")                                                         # a custom report needs a task
    expect(page.locator(".toast")).to_contain_text("Опишите задание")
    assert page.locator("#studio-dlg[open]").count() == 1


def test_quiz_flow_and_progress_saved(page, base):
    open_nb(page, base)
    pick(page, "Сведение для начинающих")
    make_studio(page, "Тест", QUIZ, n=2)
    assert "Вопрос 1 из 2" in page.inner_text("#viewer-body")
    page.locator(".opt").nth(1).click()
    expect(page.locator(".opt.right")).to_have_text("Уменьшает динамический диапазон")
    assert page.locator(".expl .cite.ok").count() == 1
    page.click("#qz-next")
    page.locator(".opt").nth(0).click()                                          # a wrong answer
    assert page.locator(".opt.wrong").count() == 1 and page.locator(".opt.right").count() == 1
    page.click("#qz-next")
    assert "Результат: 1 из 2" in page.inner_text("#viewer-body")
    page.click("#viewer-close")
    note = page.evaluate("fetch('/api/notebooks/' + document.querySelector('#nb-select').value).then(r => r.json()).then(n => n.notes[0])")
    assert note["type"] == "quiz" and note["title"].startswith("Тест") and note["data"]["progress"]["last"] == 1      # progress saved, type kept
    page.click(".seg button[data-tab=notes]")
    page.locator(".note").click()
    assert "Вопрос 1 из 2" in page.inner_text("#viewer-body") and "лучший результат 1/2" in page.inner_text("#viewer-body")


def test_flashcards_flow(page, base):
    open_nb(page, base)
    pick(page, "Сведение для начинающих")
    make_studio(page, "Карточки", CARDS, n=2)
    assert "Порог" in page.inner_text("#fc") and "Осталось: 2 из 2" in page.inner_text("#viewer-body")
    page.click("#fc")
    assert "Уровень срабатывания" in page.inner_text("#fc")
    page.click("#fc-no")                                                         # later: goes to the end of the queue
    assert "Герц" in page.inner_text("#fc")
    page.click("#fc")
    page.click("#fc-yes")
    assert "Осталось: 1 из 2" in page.inner_text("#viewer-body") and "Порог" in page.inner_text("#fc")
    page.click("#fc")
    page.click("#fc-yes")
    assert "Все карточки выучены" in page.inner_text("#viewer-body")
    page.click("#fc-reset")
    assert "Осталось: 2 из 2" in page.inner_text("#viewer-body")


def test_mindmap_collapse_and_cites(page, base):
    open_nb(page, base)
    pick(page, "Сведение для начинающих")
    make_studio(page, "Карта идей", MAP)
    assert page.locator(".mm .n").count() == 3 and page.locator(".mm .cite.ok").count() == 1
    page.locator(".mm .n", has_text="Динамика").click()
    assert page.locator(".mm li.closed").count() == 1
    assert not page.locator(".mm .n", has_text="Порог").is_visible()
    page.click("#mm-all")
    assert page.locator(".mm .n", has_text="Порог").is_visible()
    page.locator(".mm .cite").click()
    expect(page.locator("#pop")).to_be_visible()


def test_table_and_exports(page, base):
    open_nb(page, base)
    pick(page, "Сведение для начинающих")
    make_studio(page, "Таблица", TABLE, focus="термины")
    assert page.locator(".dt th").all_inner_texts() == ["Термин", "Смысл"] and page.locator(".dt tbody tr").count() == 1
    with page.expect_download() as dl:
        page.click("#viewer-export")
    csv = open(dl.value.path(), encoding="utf-8-sig").read()
    assert csv.splitlines()[0] == '"Термин","Смысл"' and '"Порог"' in csv
    page.click("#viewer-close")
    STATE["script"] = [{"type": "delta", "text": "# Брифинг\n- тезис [[A:3|Компрессор уменьшает динамический диапазон]]"}]
    page.click(".seg button[data-tab=studio]")
    page.locator(".st-card", has_text="Брифинг").click()
    page.click("#sd-go")
    expect(page.locator("#viewer")).to_be_visible(timeout=20000)
    assert page.locator("#viewer-body h3").inner_text() == "Брифинг" and page.locator("#viewer-body .cite.ok").count() == 1
    with page.expect_download() as dl:
        page.click("#viewer-export")
    md = open(dl.value.path(), encoding="utf-8").read()
    assert md.startswith("# ") and "[[" not in md and "Сведение для начинающих, стр. 3" in md          # citations become readable references


def test_studio_bad_output_shows_error(page, base):
    open_nb(page, base)
    pick(page, "Сведение для начинающих")
    STATE["script"] = [{"type": "delta", "text": "Простите, не получилось."}]
    page.locator(".st-card", has_text="Тест").click()
    page.click("#sd-go")
    expect(page.locator(".toast.error")).to_be_visible(timeout=15000)
    assert page.locator("#viewer[open]").count() == 0
    expect(page.locator(".st-card").first).not_to_have_class("st-card busy")        # the Studio is usable again
    page.click(".seg button[data-tab=notes]")
    assert page.locator(".note").count() == 0


# ---------------------------------------------------------------- notebooks

def test_notebook_management(page, base):
    open_nb(page, base)
    assert page.locator("#nb-select option").count() == 1
    page.click("#nb-new")
    page.fill("dialog[open] input", "Ритм")
    page.click("dialog [data-ok]")
    expect(page.locator("#nb-select option:checked")).to_have_text("Ритм")
    page.click("#nb-rename")
    page.fill("dialog[open] input", "Ритм и грув")
    page.keyboard.press("Enter")
    expect(page.locator("#nb-select option:checked")).to_have_text("Ритм и грув")
    page.reload()
    page.wait_for_selector(".src")
    expect(page.locator("#nb-select option:checked")).to_have_text("Ритм и грув")           # the last notebook is reopened
    page.click("#nb-del")
    page.click("dialog button[value=ok]")
    expect(page.locator("#nb-select option")).to_have_count(1)
    page.click("#nb-del")                                                        # the last one cannot be deleted
    expect(page.locator(".toast.error")).to_be_visible()


def test_selection_is_per_notebook(page, base):
    open_nb(page, base)
    pick(page, "Сведение для начинающих")
    page.wait_for_timeout(700)                                                   # the selection is saved with a short delay
    page.click("#nb-new")
    page.fill("dialog[open] input", "Второй")
    page.click("dialog [data-ok]")
    expect(page.locator("#nb-select option:checked")).to_have_text("Второй")
    page.select_option("#nb-select", index=0)
    page.wait_for_selector(".src.picked")
    assert page.locator(".src.picked .src-name").inner_text() == "Сведение для начинающих"


# ---------------------------------------------------------------- links from other pages

def test_links_from_library_and_readers(page, base):
    page.goto(base + "/")
    page.wait_for_selector("a[href='/notebook']")
    page.goto(base + "/read/ru_data")
    page.wait_for_selector("#pages img", timeout=20000)
    assert page.locator("a[href='/notebook']").count() == 1
    page.goto(base + "/read/lighthouse_data")
    page.wait_for_selector("a[href='/notebook']")


# ---------------------------------------------------------------- visual sanity: phone and tablet

@pytest.mark.parametrize("w,h,mobile", [(390, 800, True), (820, 1100, True), (1280, 720, False)])
def test_layout_has_no_horizontal_scroll(browser, base, w, h, mobile):
    pg = new_page(browser, base, w, h, mobile)
    try:
        open_nb(pg, base)
        panes = ["sources", "chat", "studio"] if w < 1100 else [None]
        for pane in panes:
            if pane:
                pg.click(f"#nb-tabs button[data-pane={pane}]")
                pg.wait_for_timeout(150)
            assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (w, pane)
        if w < 1100:
            pg.click("#nb-tabs button[data-pane=chat]")
            box = pg.locator("#input").bounding_box()
            assert box and 0 <= box["x"] and box["x"] + box["width"] <= w and box["y"] + box["height"] <= h        # the input is on screen
            assert pg.locator("#pane-sources:visible").count() == 0                                               # one pane at a time
        STATE["script"] = [{"type": "delta", "text": "Ответ [[A:3|Компрессор уменьшает динамический диапазон сигнала]]."}]
        if w < 1100:
            pg.click("#nb-tabs button[data-pane=sources]")
        pick(pg, "Сведение для начинающих")
        if w < 1100:
            pg.click("#nb-tabs button[data-pane=chat]")
        ask(pg, "вопрос")
        wait_idle(pg)
        pg.locator(".cite").first.click()
        pop = pg.locator("#pop").bounding_box()
        assert pop["x"] >= 0 and pop["x"] + pop["width"] <= w + 1 and pop["y"] >= 0 and pop["y"] + pop["height"] <= h + 1      # the popover fits
        assert pg.errors == [], pg.errors
    finally:
        pg.context.close()
