"""Local embeddings. Hash mode is an explicitly lexical offline test baseline."""
import hashlib
import re
from functools import lru_cache
import numpy as np
from app.config import settings

STOP = set('a an the and or for to in of with at on me find looking want jobs job i my please show only'.split())
def tokens(text):
    return [t for t in re.findall(r'[a-z0-9+#.]+', text.lower()) if t not in STOP and len(t)>1]

def model_id():
    cfg = settings()
    return 'hash-lexical-v1' if cfg.embedding_backend == 'hash' else cfg.embedding_model

@lru_cache
def model():
    from fastembed import TextEmbedding
    return TextEmbedding(settings().embedding_model, cache_dir=settings().model_cache, threads=2)

def embed(texts):
    if settings().embedding_backend == 'hash':
        rows = []
        for value in texts:
            vec = np.zeros(384, dtype=float)
            for token in tokens(value):
                digest = hashlib.sha256(token.encode()).digest()
                vec[int.from_bytes(digest[:4], 'big') % 384] += 1
            rows.append((vec / max(np.linalg.norm(vec), 1e-9)).tolist())
        return rows
    rows = list(model().embed(texts))
    if any(len(row)!=384 for row in rows):
        raise ValueError('This schema requires 384-dimensional embeddings')
    return [(row / max(np.linalg.norm(row), 1e-9)).tolist() for row in rows]
