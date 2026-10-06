"""Dense embedder for the book index. Optional: without sentence-transformers the index stays lexical-only.

Model families need different prefixes (a silent quality killer if mixed up):
  e5            "query: " / "passage: "
  bge-m3        none
  Qwen3-Embed   instruction on the query side only
"""
import os
import threading

# Antivirus HTTPS scanning (Kaspersky) re-signs traffic with a root that only the Windows store trusts: use that store,
# verification stays ON. Needed for the first download of a model from huggingface.co.
try:
    try:
        import truststore
    except ImportError:
        from pip._vendor import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

import numpy as np

DEFAULT_MODEL = os.environ.get("READER3_EMBED_MODEL", "microsoft/harrier-oss-v1-0.6b")
_lock = threading.Lock()
_instances = {}


class STEmbedder:
    def __init__(self, name: str, device: str = None):
        from sentence_transformers import SentenceTransformer
        if device is None:
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except Exception:
                device = "cpu"
        self.name = name
        self.device = device
        self.model = SentenceTransformer(name, device=device, trust_remote_code=True)
        if device == "cuda":
            self.model.half()                       # fp16: half the memory, same ranking quality for retrieval
        self.model.max_seq_length = min(getattr(self.model, "max_seq_length", 512) or 512, 512)
        low = name.lower()
        if "e5" in low:
            self.qp, self.pp = "query: ", "passage: "
        elif "qwen3" in low or "harrier" in low:
            self.qp = "Instruct: Given a question about a book, retrieve passages that answer it\nQuery: "
            self.pp = ""
        else:
            self.qp = self.pp = ""

    def _enc(self, texts, batch):
        v = self.model.encode(texts, batch_size=batch, normalize_embeddings=True, show_progress_bar=False,
                              convert_to_numpy=True)
        return np.asarray(v, dtype=np.float32)

    def encode_passages(self, texts):
        return self._enc([self.pp + t for t in texts], 16)

    def encode_query(self, text):
        return self._enc([self.qp + text], 1)[0]


def get_embedder(name: str = None):
    """Shared embedder, or None when sentence-transformers is not installed / the model cannot be loaded."""
    name = name or DEFAULT_MODEL
    with _lock:
        if name in _instances:
            return _instances[name]
        try:
            emb = STEmbedder(name)
        except Exception as e:
            print(f"Dense embedder unavailable ({name}): {e}")
            emb = None
        _instances[name] = emb
        return emb
