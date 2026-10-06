"""Builds the evaluation set: LLM-written hard questions with code-verified gold quotes, across all test books.

    uv run python eval/build_dataset.py [per_book]      (output: eval/out/auto.json)

Each item: {book, loc, q (Russian question), q_en (search query in the BOOK's language), quote (verbatim), answer, kind: "auto"}.
A question is kept only if its gold quote is found on the gold page by notebook.verify_quote (status ok)."""
import json
import os
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import notebook  # noqa: E402
import notebook_api as na  # noqa: E402

LIB = os.environ.get("EVAL_LIB", r"C:\WORK\PROJECTS\reader3\testlib")
OUT = os.path.join(HERE, "out")
PER_BOOK = int(sys.argv[1]) if len(sys.argv) > 1 else 12
random.seed(42)

PROMPT = """Вот фрагмент книги «{title}» (язык книги: {lang}).

<fragment>
{text}
</fragment>

Составь ОДИН трудный вопрос на РУССКОМ языке, ответ на который содержится именно в этом фрагменте. Требования: вопрос понятен без фрагмента \
(не пиши «в тексте», «в этом отрывке»); перефразируй, не повторяй слова ответа дословно; вопрос про конкретный факт, определение, число, \
пример или причину, а не «о чём отрывок».

Верни ТОЛЬКО JSON без пояснений: {{"q": "вопрос по-русски", "q_en": "поисковый запрос из 4-10 ключевых слов НА ЯЗЫКЕ КНИГИ ({lang})", \
"quote": "дословная выдержка 8–20 слов из фрагмента, подтверждающая ответ (скопируй точно)", "answer": "ответ в 1–2 предложениях по-русски"}}"""


def candidate_units(d, kind):
    """(loc, text) pieces that are good for a question: real prose, away from the covers, indexes and bibliographies."""
    n = notebook.n_locs(d)
    out = []
    lo, hi = int(n * 0.08) + 1, int(n * 0.92)
    for loc in range(lo, max(lo + 1, hi)):
        t = notebook.loc_text(d, loc)
        words = t.split()
        if len(words) < 90:
            continue
        letters = sum(ch.isalpha() for ch in t) / max(1, len(t))
        if letters < 0.7 or len(re.findall(r"\.{4,}|\bISBN\b|\bpp?\.\s*\d", t)) > 2:
            continue                                                  # tables of contents, bibliographies, formula pages
        if kind == "epub" and len(words) > 700:                       # a chapter is long: a random window of it
            a = random.randint(0, len(words) - 600)
            t = " ".join(words[a:a + 600])
        out.append((loc, t))
    return out


def make_item(job):
    src, loc, text = job
    prompt = PROMPT.format(title=src.title, lang=src.lang, text=text[:6000])
    for _ in range(2):
        try:
            d = na.extract_json(na.claude_text(prompt, model="sonnet", effort="low", timeout=300))
            q, q_en, quote = (d.get("q") or "").strip(), (d.get("q_en") or "").strip(), (d.get("quote") or "").strip()
            if len(q) < 15 or len(quote.split()) < 6:
                continue
            v = notebook.verify_quote(src, loc, quote)
            if v["status"] != "ok":
                continue
            return {"book": src.id, "lang": src.lang, "kind_book": src.kind, "loc": loc, "q": q, "q_en": q_en or q, "quote": quote,
                    "answer": (d.get("answer") or "").strip(), "kind": "auto"}
        except Exception as e:
            err = str(e)[:100]
    return None


def main():
    os.makedirs(OUT, exist_ok=True)
    books = sorted(f for f in os.listdir(LIB) if f.endswith("_data") and os.path.exists(os.path.join(LIB, f, "rag.sqlite")))
    srcs = notebook.make_sources([(b, "A") for b in books], lambda b: os.path.join(LIB, b))
    jobs = []
    for s in srcs:
        cands = candidate_units(s.dir, s.kind)
        random.shuffle(cands)
        take = cands[: int(PER_BOOK * 1.5)]                           # spare candidates: some questions fail verification
        print(f"{s.title[:45]:45} candidates={len(cands)} taken={len(take)}", flush=True)
        jobs += [(s, loc, t) for loc, t in take]
    items = []
    with ThreadPoolExecutor(3) as ex:
        for i, it in enumerate(ex.map(make_item, jobs), 1):
            if it:
                items.append(it)
            if i % 20 == 0:
                print(f"  {i}/{len(jobs)} done, kept {len(items)}", flush=True)
    per = {}
    final = []
    for it in items:                                                  # at most PER_BOOK per book
        if per.get(it["book"], 0) < PER_BOOK:
            per[it["book"]] = per.get(it["book"], 0) + 1
            final.append(it)
    with open(os.path.join(OUT, "auto.json"), "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=1)
    print("kept", len(final), per)


if __name__ == "__main__":
    main()
