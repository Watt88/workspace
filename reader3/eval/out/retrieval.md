# Retrieval evaluation

165 answerable questions (hit = chunk text contains the gold quote).

| setting | hit@1 | hit@3 | hit@5 | hit@10 | MRR [95% CI] |
|---|---|---|---|---|---|
| bm25 / ru | 0.12 | 0.17 | 0.17 | 0.19 | 0.15 [0.10, 0.20] |
| bm25 / agent | 0.81 | 0.86 | 0.86 | 0.86 | 0.84 [0.78, 0.89] |
| hybrid / ru | 0.58 | 0.75 | 0.82 | 0.85 | 0.68 [0.61, 0.74] |
| hybrid / agent | 0.79 | 0.88 | 0.91 | 0.93 | 0.84 [0.79, 0.89] |
| hybrid+rerank / ru | 0.78 | 0.87 | 0.87 | 0.90 | 0.83 [0.77, 0.88] |
| hybrid+rerank / agent | 0.81 | 0.90 | 0.90 | 0.92 | 0.86 [0.81, 0.90] |
| books: English (rerank, ru question) (n=137) | 0.77 | 0.88 | 0.88 | 0.91 | 0.82 |
| books: English (rerank, agent query) (n=137) | 0.82 | 0.91 | 0.91 | 0.93 | 0.87 |
| books: Russian (rerank, ru question) (n=28) | 0.82 | 0.86 | 0.86 | 0.89 | 0.84 |
| books: Russian (rerank, agent query) (n=28) | 0.75 | 0.86 | 0.86 | 0.89 | 0.81 |
| hand-written (rerank, ru question) (n=25) | 0.84 | 0.92 | 0.92 | 0.96 | 0.89 |
| hand-written (rerank, agent query) (n=25) | 0.96 | 0.96 | 0.96 | 0.96 | 0.96 |
| LLM-written (rerank, ru question) (n=140) | 0.77 | 0.86 | 0.86 | 0.89 | 0.82 |
| LLM-written (rerank, agent query) (n=140) | 0.79 | 0.89 | 0.89 | 0.91 | 0.84 |

## Whole library at once (10 books in one notebook)

| query | hit@1 | hit@5 | hit@10 | MRR [95% CI] |
|---|---|---|---|---|
| ru | 0.73 | 0.85 | 0.87 | 0.79 [0.73, 0.84] |
| agent | 0.80 | 0.88 | 0.90 | 0.84 [0.79, 0.89] |

## Per book (MRR, hybrid+rerank)

| book | n | ru question | agent query |
|---|---|---|---|
| Computer_Music_Presents_Music_Producer_G | 14 | 0.75 | 0.75 |
| Harmonograph__A_Visual_Guide_to_the_Math | 14 | 0.94 | 0.87 |
| Intelligent_Music_Production_data | 14 | 0.89 | 0.75 |
| Music_by_the_numbers__from_Pythagoras_to | 14 | 0.94 | 0.76 |
| Musimathics_Volume_2_data | 14 | 0.58 | 0.95 |
| Polyrhythms_data | 26 | 0.98 | 0.98 |
| Programming_Sound_with_Pure_Data__Make_Y | 14 | 0.68 | 0.75 |
| Style_and_idea_data | 14 | 0.79 | 0.73 |
| The_Ultimate_Guide_to__The_Circle_of_Fif | 27 | 0.76 | 0.94 |
| The_myth_of_invariance__the_origin_of_th | 14 | 0.89 | 0.89 |

## Abstention (best reranker score: answerable vs not answerable)

| query | n pos / neg | AUC [95% CI] | answerable wrongly flagged | negatives caught | median score pos / neg | best threshold |
|---|---|---|---|---|---|---|
| ru | 165 / 48 | 0.987 [0.97, 1.00] | 0.05 | 0.96 | 0.78 / 0.003 | 0.023 |
| agent | 165 / 48 | 0.994 [0.99, 1.00] | 0.02 | 0.96 | 0.95 / 0.002 | 0.025 |
