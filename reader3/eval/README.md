# Оценка качества блокнота

Запуск из `ws/reader3` (нужны GPU-модели; сквозной прогон тратит подписку Claude):

    uv run python eval/build_dataset.py 14   # вопросы, написанные моделью, цитата проверяется кодом -> out/auto.json
    uv run python eval/hand_dataset.py       # 25 вопросов вручную + 8 без ответа -> out/hand.json
    uv run python eval/run_retrieval.py      # поиск: BM25 / гибрид / гибрид+реранкер, отказ -> out/retrieval.md
    uv run python eval/run_e2e.py 20 12      # настоящий агент через сервер :8133 -> out/e2e.md

Для `run_e2e.py` нужен тестовый сервер (`READER3_LIBRARY=<testlib> READER3_PASSWORD=test123 READER3_BACKEND=claude-code PORT=8133`).
Попадание = текст найденного фрагмента содержит эталонную цитату (проверка кодом). Результаты последнего прогона: `out/*.md`.
