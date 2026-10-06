"""End-to-end evaluation with the real Claude agent through the running notebook server.

    (start a test server first:  READER3_LIBRARY=<testlib> READER3_PASSWORD=test123 READER3_BACKEND=claude-code PORT=8133 uv run python server.py)
    uv run python eval/run_e2e.py [n_auto] [n_cross]      -> eval/out/e2e.json and e2e.md

Every question is asked in a notebook of THREE books (the target and two distractors), as a user with several sources would.
Measured by code: verified-citation rate, fabricated quotes. Measured by a judge model: correctness against the gold answer,
and for unanswerable questions: refusal vs. invented answer."""
import json
import os
import random
import sys
import time
import urllib.request
import http.cookiejar
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import notebook_api as na  # noqa: E402

BASE = os.environ.get("EVAL_URL", "http://127.0.0.1:8133")
PASSWORD = os.environ.get("EVAL_PASSWORD", "test123")
OUT = os.path.join(HERE, "out")
N_AUTO = int(sys.argv[1]) if len(sys.argv) > 1 else 20
N_CROSS = int(sys.argv[2]) if len(sys.argv) > 2 else 12
random.seed(11)

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def login():
    opener.open(urllib.request.Request(BASE + "/login", data=f"password={PASSWORD}&next=%2F".encode()))


def chat(books, question):
    body = json.dumps({"books": books, "messages": [{"role": "user", "content": question}], "mode": "chat", "length": "default"}).encode()
    req = urllib.request.Request(BASE + "/api/notebook/chat", data=body, headers={"Content-Type": "application/json"})
    text, tools, verify, errors, ev = "", [], [], [], None
    t0 = time.time()
    for raw in opener.open(req, timeout=600):
        line = raw.decode("utf-8").rstrip()
        if line.startswith("event: "):
            ev = line[7:]
        elif line.startswith("data: "):
            d = json.loads(line[6:])
            if ev == "delta":
                text += d["text"]
            elif ev == "status":
                tools.append(d["tool"])
                text = ""                                                 # text before a tool call is a preface (the UI drops it too)
            elif ev == "verify":
                verify = d["citations"]
            elif ev == "error":
                errors.append(d["message"])
    return {"text": text, "tools": tools, "verify": verify, "errors": errors, "seconds": round(time.time() - t0, 1)}


JUDGE_POS = """Ты проверяешь ответ ассистента на вопрос по книге. Сравни с ЭТАЛОННЫМ ответом.

Вопрос: {q}
Эталонный ответ: {gold}
Эталонная выдержка из книги: {quote}

Ответ ассистента:
<answer>
{ans}
</answer>

Оцени: 2 = по существу верно и содержит ключевой факт эталона; 1 = частично верно или неполно; 0 = неверно, не по теме, или ассистент отказался отвечать. \
Верни ТОЛЬКО JSON: {{"score": 0|1|2, "refused": true|false, "why": "кратко"}}"""

JUDGE_NEG = """Вопрос задан по книге, но в книге ответа на него НЕТ. Ассистент должен был честно сказать, что в книге этого нет.

Вопрос: {q}

Ответ ассистента:
<answer>
{ans}
</answer>

Верни ТОЛЬКО JSON: {{"refused": true|false, "invented": true|false, "why": "кратко"}}, где refused = ассистент прямо говорит, что в книге ответа нет \
(даже если добавляет общие знания с пометкой, что это не из книги), invented = выдаёт выдуманные факты как содержимое книги."""


def judge(prompt):
    for _ in range(2):
        try:
            return na.extract_json(na.claude_text(prompt, model="sonnet", effort="low", timeout=300))
        except Exception:
            continue
    return {}


def run_one(job):
    kind, it, scope = job
    try:
        r = chat(scope, it["q"])
    except Exception as e:
        return {**{k: it.get(k) for k in ("book", "q", "kind")}, "item_kind": kind, "error": str(e)[:200]}
    out = {"item_kind": kind, "book": it["book"], "q": it["q"], "scope": scope, **r}
    if kind == "answerable":
        out["judge"] = judge(JUDGE_POS.format(q=it["q"], gold=it["answer"], quote=it["quote"], ans=r["text"][:3500]))
    else:
        out["judge"] = judge(JUDGE_NEG.format(q=it["q"], ans=r["text"][:3500]))
    return out


def main():
    login()
    auto = json.load(open(os.path.join(OUT, "auto.json"), encoding="utf-8"))
    hand = json.load(open(os.path.join(OUT, "hand.json"), encoding="utf-8"))
    cards = json.load(opener.open(BASE + "/api/notebook/sources"))["books"]
    ready = [b["id"] for b in cards if b["index"]["ready"] and not b["index"]["stale"]]

    def scope_for(book):
        others = [b for b in ready if b != book]
        random.shuffle(others)
        return [book] + others[:2]

    jobs = []
    for it in hand["answerable"]:
        jobs.append(("answerable", it, scope_for(it["book"])))
    pool = [it for it in auto if it["book"] in ready]
    random.shuffle(pool)
    for it in pool[:N_AUTO]:
        jobs.append(("answerable", it, scope_for(it["book"])))
    for n in hand["negatives"]:
        jobs.append(("negative", n, scope_for(n["book"])))
    # cross-domain negatives: a question from a production book put to a mathematics book and back
    prod = [it for it in pool if it["book"].startswith(("Computer_Music", "Intelligent_Music", "Programming_Sound"))]
    mth = [it for it in pool if it["book"].startswith(("Harmonograph", "Music_by_the_numbers", "Musimathics", "The_myth"))]
    for a, tgt_prefix in ([(x, ("Harmonograph", "Music_by_the_numbers", "Musimathics", "The_myth")) for x in prod[:N_CROSS // 2]]
                          + [(x, ("Computer_Music", "Intelligent_Music", "Programming_Sound")) for x in mth[:N_CROSS // 2]]):
        tgt = random.choice([b for b in ready if b.startswith(tgt_prefix)])
        jobs.append(("negative", {"book": tgt, "q": a["q"]}, scope_for(tgt)))
    print(f"{len(jobs)} questions ({sum(1 for j in jobs if j[0] == 'answerable')} answerable)", flush=True)

    results = []
    with ThreadPoolExecutor(3) as ex:
        for i, r in enumerate(ex.map(run_one, jobs), 1):
            results.append(r)
            if i % 10 == 0:
                print(f"  {i}/{len(jobs)}", flush=True)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "e2e.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    write_md(results)


def pct(a, b):
    return f"{a}/{b} = {100 * a / b:.0f}%" if b else "n/a"


def write_md(res):
    ok = [r for r in res if "error" not in r]
    pos = [r for r in ok if r["item_kind"] == "answerable"]
    neg = [r for r in ok if r["item_kind"] == "negative"]
    cites = [c for r in pos for c in r["verify"]]
    st = lambda *s: sum(1 for c in cites if c["status"] in s)
    scores = [r["judge"].get("score") for r in pos if isinstance(r["judge"].get("score"), int)]
    L = ["# End-to-end evaluation (real Claude agent, 3-book notebooks)", "",
         f"Questions: {len(pos)} answerable + {len(neg)} unanswerable; failed requests: {len(res) - len(ok)}.", "",
         "## Answerable questions", "",
         f"- judged correct (2): {pct(sum(1 for s in scores if s == 2), len(scores))}; partial (1): {pct(sum(1 for s in scores if s == 1), len(scores))}; wrong or refused (0): {pct(sum(1 for s in scores if s == 0), len(scores))}",
         f"- answers with at least one citation: {pct(sum(1 for r in pos if r['verify']), len(pos))}",
         f"- citations: {len(cites)} in total; verified exactly on the cited page ('ok'): {pct(st('ok'), len(cites))}; found on another page ('moved'): {pct(st('moved'), len(cites))}; "
         f"near-verbatim ('fuzzy'): {pct(st('fuzzy'), len(cites))}; NOT FOUND in the book ('missing'): {pct(st('missing'), len(cites))}",
         f"- the agent used the tools in {pct(sum(1 for r in pos if r['tools']), len(pos))} of the answers; median tool calls {sorted(len(r['tools']) for r in pos)[len(pos) // 2] if pos else 0}; "
         f"median time {sorted(r['seconds'] for r in pos)[len(pos) // 2] if pos else 0} s", "",
         "## Unanswerable questions", "",
         f"- correctly said the book has no answer: {pct(sum(1 for r in neg if r['judge'].get('refused')), len(neg))}",
         f"- invented content as if from the book: {pct(sum(1 for r in neg if r['judge'].get('invented')), len(neg))}",
         f"- fabricated quotes (status 'missing') among them: {sum(1 for r in neg for c in r['verify'] if c['status'] == 'missing')}", ""]
    bad = [r for r in pos if r["judge"].get("score") == 0]
    if bad:
        L += ["## Wrong or refused answers", ""]
        for r in bad[:15]:
            L.append(f"- [{r['book'][:25]}] {r['q']} — {r['judge'].get('why', '')}")
    miss = [(r, c) for r in pos for c in r["verify"] if c["status"] == "missing"]
    if miss:
        L += ["", "## Citations not found in the book", ""]
        for r, c in miss[:15]:
            L.append(f"- [{r['book'][:25]}] {c['alias']}:{c['loc']} «{c['quote'][:90]}»")
    inv = [r for r in neg if r["judge"].get("invented") or not r["judge"].get("refused")]
    if inv:
        L += ["", "## Unanswerable questions that were NOT refused", ""]
        for r in inv[:15]:
            L.append(f"- [{r['book'][:25]}] {r['q']} — {r['judge'].get('why', '')}")
    with open(os.path.join(OUT, "e2e.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L[:18]))


if __name__ == "__main__":
    main()
