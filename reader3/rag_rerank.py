"""Cross-encoder reranker (bge-reranker-v2-m3 by default), shared by the notebook search.

Measured on the author's books: +0.13..+0.23 MRR over the hybrid first stage, and the score separates questions the
book answers from questions it does not (AUC 0.97, threshold 0.02 on the sigmoid score)."""
import os
import threading
from typing import List, Optional

import rag_embed  # noqa: F401  (installs the system certificate store for the first model download)

DEFAULT_RERANKER = os.environ.get("READER3_RERANKER", "BAAI/bge-reranker-v2-m3")
THRESHOLD = float(os.environ.get("READER3_RERANK_THRESHOLD", "0.02"))

_lock = threading.Lock()          # one model, one GPU: calls are serialized
_state = {"model": None, "name": None, "tried": False}


def get_reranker():
    with _lock:
        if _state["tried"]:
            return _state["model"]
        _state["tried"] = True
        try:
            import torch
            from sentence_transformers import CrossEncoder
            kwargs = {}
            device = "cuda" if torch.cuda.is_available() else "cpu"
            if device == "cuda":
                kwargs["model_kwargs"] = {"torch_dtype": torch.float16}
            _state["model"] = CrossEncoder(DEFAULT_RERANKER, device=device, max_length=512, **kwargs)
            _state["name"] = DEFAULT_RERANKER
        except Exception as e:
            print(f"Reranker unavailable ({DEFAULT_RERANKER}): {e}")
            _state["model"] = None
        return _state["model"]


def rerank(query: str, passages: List[str]) -> Optional[List[float]]:
    """Relevance 0..1 per passage, or None when no reranker could be loaded."""
    model = get_reranker()
    if model is None or not passages:
        return None
    with _lock:
        scores = model.predict([(query, p) for p in passages], batch_size=32, show_progress_bar=False)
    return [float(s) for s in scores]
