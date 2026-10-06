"""Retrieval evaluation of the notebook search on the whole test library (GPU models, no LLM).

    uv run python eval/run_retrieval.py        -> eval/out/retrieval.json and retrieval.md

A result counts as a HIT when its text contains the gold quote (code check, not a model's opinion).
Modes: bm25 (lexical), hybrid (BM25 + dense, RRF), hybrid+rerank (what the app does); queries: `ru` = the user's Russian question,
`agent` = the query a good agent writes (keywords in the language of the book). Scopes: one book, or the whole library at once.
Abstention: the best reranker score separates answerable questions from questions the book cannot answer (AUC, operating point)."""
import json
import os
import random
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import notebook  # noqa: E402
import rag  # noqa: E402
import rag_embed  # noqa: E402
import rag_rerank  # noqa: E402

LIB = os.environ.get("EVAL_LIB", r"C:\WORK\PROJECTS\reader3\testlib")
OUT = os.path.join(HERE, "out")
KS = (1, 3, 5, 10)
random.seed(7)
rng = np.random.default_rng(7)

PRODUCTION = ("Computer_Music", "Intelligent_Music", "Programming_Sound")
MATH = ("Harmonograph", "Music_by_the_numbers", "Musimathics", "The_myth")


def load():
    auto = json.load(open(os.path.join(OUT, "auto.json"), encoding="utf-8"))
    hand = json.load(open(os.path.join(OUT, "hand.json"), encoding="utf-8"))
    return auto + hand["answerable"], hand["negatives"]


def rerank_fn(query):
    return lambda texts: rag_rerank.rerank(query, texts)


def first_hit(results, quote_c):
    for i, r in enumerate(results):
        if quote_c in notebook._compact(r["text"]):
            return i + 1
    return None


def metrics(ranks):
    """ranks: list of int|None -> dict of hit@k and MRR."""
    n = len(ranks)
    out = {f"hit@{k}": sum(1 for r in ranks if r and r <= k) / n for k in KS}
    out["mrr"] = sum(1 / r for r in ranks if r) / n
    return out


def boot_ci(ranks, stat, n=2000):
    arr = np.array([0 if r is None else r for r in ranks])
    vals = []
    for _ in range(n):
        s = arr[rng.integers(0, len(arr), len(arr))]
        vals.append(stat(s))
    return [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))]


def mrr_of(a):
    return float(np.mean([1 / r if r else 0 for r in a]))


def hit_of(k):
    return lambda a: float(np.mean([1 if (r and r <= k) else 0 for r in a]))


def auc(pos, neg):
    pos, neg = np.array(pos), np.array(neg)
    return float((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean())


def main():
    items, hand_negs = load()
    books = sorted(f for f in os.listdir(LIB) if f.endswith("_data") and os.path.exists(os.path.join(LIB, f, "rag.sqlite")))
    srcs = {s.id: s for s in notebook.make_sources([(b, "A") for b in books], lambda b: os.path.join(LIB, b))}
    emb = rag_embed.get_embedder()
    rag_rerank.get_reranker()
    print(f"{len(items)} answerable questions, {len(hand_negs)} hand negatives, {len(srcs)} books", flush=True)

    modes = {
        "bm25": lambda pool_srcs, q: notebook.search(pool_srcs, q, k=10, embedder=None, reranker=None),
        "hybrid": lambda pool_srcs, q: notebook.search(pool_srcs, q, k=10, embedder=emb, reranker=None),
        "hybrid+rerank": lambda pool_srcs, q: notebook.search(pool_srcs, q, k=10, embedder=emb, reranker=rerank_fn(q)),
    }
    rows = []
    for it in items:
        s = srcs.get(it["book"])
        if not s:
            continue
        rows.append(it)
    quote_c = {id(it): notebook._compact(it["quote"]) for it in rows}

    result = {"n_answerable": len(rows), "by_mode": {}, "library_wide": {}, "per_book": {}, "abstention": {}}
    ranks_store = {}
    for mname, fn in modes.items():
        for qkind, key in (("ru", "q"), ("agent", "q_en")):
            ranks = []
            for it in rows:
                res = fn([srcs[it["book"]]], it[key])
                ranks.append(first_hit(res, quote_c[id(it)]))
            ranks_store[(mname, qkind)] = ranks
            m = metrics(ranks)
            m["mrr_ci"] = boot_ci(ranks, mrr_of)
            m["hit@5_ci"] = boot_ci(ranks, hit_of(5))
            result["by_mode"][f"{mname} / {qkind}"] = m
            print(f"{mname:14} {qkind:5} n={len(ranks)} " + " ".join(f"{k}={v:.2f}" for k, v in m.items() if not k.endswith('_ci')), flush=True)

    # split: english books vs russian books, auto vs hand
    best = ranks_store[("hybrid+rerank", "agent")]
    ru_raw = ranks_store[("hybrid+rerank", "ru")]
    for label, pick in (("books: English", lambda it: it["lang"] == "en"), ("books: Russian", lambda it: it["lang"] == "ru"),
                        ("hand-written", lambda it: it["kind"] == "hand"), ("LLM-written", lambda it: it["kind"] == "auto")):
        idx = [i for i, it in enumerate(rows) if pick(it)]
        if idx:
            result["by_mode"][f"{label} (rerank, ru question)"] = metrics([ru_raw[i] for i in idx]) | {"n": len(idx)}
            result["by_mode"][f"{label} (rerank, agent query)"] = metrics([best[i] for i in idx]) | {"n": len(idx)}
    for b in books:
        idx = [i for i, it in enumerate(rows) if it["book"] == b]
        if idx:
            result["per_book"][b[:40]] = {"n": len(idx), "ru_question": metrics([ru_raw[i] for i in idx])["mrr"], "agent_query": metrics([best[i] for i in idx])["mrr"]}

    # the whole library at once (several books in one notebook): does the gold chunk still reach the top?
    all_srcs = list(srcs.values())
    for qkind, key in (("ru", "q"), ("agent", "q_en")):
        ranks = []
        for it in rows:
            res = notebook.search(all_srcs, it[key], k=10, embedder=emb, reranker=rerank_fn(it[key]))
            ranks.append(first_hit(res, quote_c[id(it)]))
        m = metrics(ranks)
        m["mrr_ci"] = boot_ci(ranks, mrr_of)
        result["library_wide"][qkind] = m
        print(f"library-wide   {qkind:5} " + " ".join(f"{k}={v:.2f}" for k, v in m.items() if not k.endswith('_ci')), flush=True)

    # abstention: best reranker score of each query against its book
    def best_score(src, q):
        res = notebook.search([src], q, k=5, embedder=emb, reranker=rerank_fn(q))
        scores = [r["relevance"] for r in res if r.get("relevance") is not None]
        return max(scores) if scores else 0.0
    pos = {"ru": [best_score(srcs[it["book"]], it["q"]) for it in rows], "agent": [best_score(srcs[it["book"]], it["q_en"]) for it in rows]}
    negs = [(srcs[n["book"]], n["q"], n["q"]) for n in hand_negs if n["book"] in srcs]
    prod = [it for it in rows if it["book"].startswith(PRODUCTION) and it["kind"] == "auto"]
    math_ = [it for it in rows if it["book"].startswith(MATH) and it["kind"] == "auto"]
    random.shuffle(prod)
    random.shuffle(math_)
    for a in prod[:20]:
        tgt = random.choice([b for b in books if b.startswith(MATH)])
        negs.append((srcs[tgt], a["q"], a["q_en"] if a["lang"] == "en" else a["q"]))
    for a in math_[:20]:
        tgt = random.choice([b for b in books if b.startswith(PRODUCTION)])
        negs.append((srcs[tgt], a["q"], a["q_en"] if a["lang"] == "en" else a["q"]))
    neg = {"ru": [best_score(s, q) for s, q, _ in negs], "agent": [best_score(s, qe) for s, _, qe in negs]}
    th = rag_rerank.THRESHOLD
    for kind in ("ru", "agent"):
        a = auc(pos[kind], neg[kind])
        # bootstrap CI of the AUC
        vals = []
        P, N = np.array(pos[kind]), np.array(neg[kind])
        for _ in range(1000):
            vals.append(auc(P[rng.integers(0, len(P), len(P))], N[rng.integers(0, len(N), len(N))]))
        result["abstention"][kind] = {
            "n_pos": len(P), "n_neg": len(N), "auc": a, "auc_ci": [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))],
            "threshold": th, "answerable_flagged_as_missing": float((P < th).mean()), "negatives_correctly_flagged": float((N < th).mean()),
            "median_pos": float(np.median(P)), "median_neg": float(np.median(N)),
            "best_threshold": float(max(np.concatenate([P, N]), key=lambda t: ((P >= t).mean() + (N < t).mean()))),
        }
        r = result["abstention"][kind]
        print(f"abstention {kind:5} AUC={a:.3f} {r['auc_ci']} flagged-answerable={r['answerable_flagged_as_missing']:.2f} "
              f"neg-caught={r['negatives_correctly_flagged']:.2f} at th={th}", flush=True)

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "retrieval.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    write_md(result)


def write_md(r):
    L = ["# Retrieval evaluation", "", f"{r['n_answerable']} answerable questions (hit = chunk text contains the gold quote).", "",
         "| setting | hit@1 | hit@3 | hit@5 | hit@10 | MRR [95% CI] |", "|---|---|---|---|---|---|"]
    for k, m in r["by_mode"].items():
        ci = f" [{m['mrr_ci'][0]:.2f}, {m['mrr_ci'][1]:.2f}]" if "mrr_ci" in m else ""
        n = f" (n={m['n']})" if "n" in m else ""
        L.append(f"| {k}{n} | {m['hit@1']:.2f} | {m['hit@3']:.2f} | {m['hit@5']:.2f} | {m['hit@10']:.2f} | {m['mrr']:.2f}{ci} |")
    L += ["", "## Whole library at once (10 books in one notebook)", "", "| query | hit@1 | hit@5 | hit@10 | MRR [95% CI] |", "|---|---|---|---|---|"]
    for k, m in r["library_wide"].items():
        L.append(f"| {k} | {m['hit@1']:.2f} | {m['hit@5']:.2f} | {m['hit@10']:.2f} | {m['mrr']:.2f} [{m['mrr_ci'][0]:.2f}, {m['mrr_ci'][1]:.2f}] |")
    L += ["", "## Per book (MRR, hybrid+rerank)", "", "| book | n | ru question | agent query |", "|---|---|---|---|"]
    for b, m in r["per_book"].items():
        L.append(f"| {b} | {m['n']} | {m['ru_question']:.2f} | {m['agent_query']:.2f} |")
    L += ["", "## Abstention (best reranker score: answerable vs not answerable)", "",
          "| query | n pos / neg | AUC [95% CI] | answerable wrongly flagged | negatives caught | median score pos / neg | best threshold |", "|---|---|---|---|---|---|---|"]
    for k, a in r["abstention"].items():
        L.append(f"| {k} | {a['n_pos']} / {a['n_neg']} | {a['auc']:.3f} [{a['auc_ci'][0]:.2f}, {a['auc_ci'][1]:.2f}] | {a['answerable_flagged_as_missing']:.2f} "
                 f"| {a['negatives_correctly_flagged']:.2f} | {a['median_pos']:.2f} / {a['median_neg']:.3f} | {a['best_threshold']:.3f} |")
    with open(os.path.join(OUT, "retrieval.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
