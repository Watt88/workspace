"""Shared fixtures for the notebook tests: a temporary library with three synthetic books (RU PDF, EN PDF, RU EPUB).
No models, no network and no claude: embedder/reranker/agent are replaced where a test needs them."""
import os
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

LIB = tempfile.mkdtemp(prefix="r3test-lib-")
os.environ["READER3_LIBRARY"] = LIB            # must be set before `reader3`/`server` are imported
os.environ["READER3_PASSWORD"] = "pw-test"
os.environ["READER3_BACKEND"] = "claude-code"
os.environ.pop("ANTHROPIC_API_KEY", None)

RU_PAGES = [
    "Введение. Эта книга о звукорежиссуре и сведении музыки для начинающих.",
    "Глава 1. Громкость. Громкость измеряется в децибелах, а не в процентах ползунка.",
    "Компрессор уменьшает динамический диапазон сигнала: тихие места становятся громче, громкие тише.",
    "Параметр порог (threshold) задаёт уровень, выше которого компрессор начинает работать.",
    "Глава 2. Эквализация. Эквалайзер усиливает или ослабляет выбранные частоты звука.",
    "Низкие частоты ниже восьмидесяти герц лучше срезать у всех инструментов кроме баса и бочки.",
    "Реверберация создаёт ощущение пространства; длинный хвост размывает ритм.",
    "Глава 3. Панорама. Панорамирование размещает инструменты между левым и правым каналом.",
    "Бас и бочку принято оставлять в центре, чтобы энергия была симметричной.",
    "Заключение. Сведение — это набор решений, которые вы проверяете на слух, а не по цифрам.",
    "Дополнительная пере-\nнос слов проверка: дефис на конце строки не должен ломать поиск цитаты.",
    "Последняя страница книги содержит только благодарности читателям.",
]
EN_PAGES = [
    "Polyrhythms are layers of simpler rhythms, each with a different beat subdivision.",
    "The hemiola is simply three beats with equal value spaced across two beats.",
    "In Africa the hemiola is called a cross rhythm and often repeats across the whole song.",
    "To practise 3:2 you can use the phrase Hot Cup of Tea; for 4:3 What Atrocious Weather.",
    "Two rhythms form a polyrhythm only if they have no common divisor other than one.",
    "A drum machine and a metronome help you count the grid of twelve beats for 3:4.",
    "Baroque composers used the hemiola to surprise the listener and play with rhythm.",
    "This final page only lists the recommended listening and the copyright notice.",
]
EPUB_CHAPTERS = [
    ("Начало", "Первая глава рассказывает о маленьком городе у моря и старом маяке на краю бухты."),
    ("Шторм", "Во второй главе приходит шторм: волны разбивают лодки, а смотритель маяка не спит всю ночь."),
    ("Утро", "Третья глава: утром море успокаивается, и жители находят на берегу таинственную бутылку с письмом."),
]


def _blank_pdf(path, n):
    import fitz
    doc = fitz.open()
    for _ in range(n):
        doc.new_page(width=300, height=400)
    doc.save(path)
    doc.close()


def _make_epub(path):
    from ebooklib import epub
    b = epub.EpubBook()
    b.set_identifier("test-epub")
    b.set_title("Маяк")
    b.set_language("ru")
    b.add_author("Тест Авторов")
    items = []
    for i, (title, text) in enumerate(EPUB_CHAPTERS, 1):
        c = epub.EpubHtml(title=title, file_name=f"c{i}.xhtml", lang="ru")
        c.content = f"<h1>{title}</h1><p>{text}</p>"
        b.add_item(c)
        items.append(c)
    b.toc = tuple(items)
    b.add_item(epub.EpubNcx())
    b.add_item(epub.EpubNav())
    b.spine = ["nav"] + items
    epub.write_epub(path, b)


@pytest.fixture(scope="session")
def lib():
    import pdf_support as pdfs
    import reader3
    tmp = tempfile.mkdtemp(prefix="r3test-src-")
    for bid, title, pages, lang in [("ru_data", "Сведение для начинающих", RU_PAGES, "ru"), ("en_data", "Polyrhythms Test", EN_PAGES, "en")]:
        src = os.path.join(tmp, bid + ".pdf")
        _blank_pdf(src, len(pages))
        pdfs.import_pdf(src, LIB, book_id=bid, title=title, given_texts=pages)
    ep = os.path.join(tmp, "lighthouse.epub")
    _make_epub(ep)
    reader3.import_epub(ep, LIB, book_id="lighthouse_data")
    return LIB


def book_dir(lib, bid):
    return os.path.join(lib, bid)


@pytest.fixture(scope="session")
def sources(lib):
    import notebook
    return notebook.make_sources([("ru_data", "A"), ("en_data", "B"), ("lighthouse_data", "C")], lambda b: os.path.join(lib, b))
