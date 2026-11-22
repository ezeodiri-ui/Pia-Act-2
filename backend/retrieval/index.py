"""Index layer: corpus loader, BM25 (with bigrams + title boosting), dense embeddings, LSA fallback, cross-encoder."""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend import config  # noqa: E402
from backend.retrieval.text import tokenize  # noqa: E402


# --------------------------------------------------------------------------
class Corpus:
    def __init__(self):
        self.prov = json.loads(config.PROVISIONS_JSON.read_text())
        self.chunks = [json.loads(l) for l in config.CHUNKS_JSONL.open()]
        self.by_id = {c["id"]: c for c in self.chunks}
        self.sections = {s["number"]: s for s in self.prov["sections"]}
        self.by_section = defaultdict(list)
        self.by_schedule = defaultdict(list)
        self.by_parent = defaultdict(list)
        for c in self.chunks:
            if c["kind"] == "schedule":
                self.by_schedule[c["schedule"]].append(c)
            elif c["kind"] == "section":
                self.by_section[c["section"]].append(c)
            self.by_parent[c["parent_id"]].append(c)
        self.definitions = [c for c in self.chunks if c["kind"] == "definition"]
        self.idx = {c["id"]: i for i, c in enumerate(self.chunks)}

    def section_text(self, n):
        s = self.sections[n]
        return s["text"]


def doc_tokens(c) -> list[str]:
    title = tokenize(c["title"])
    body = tokenize(c["text"])
    crumb = tokenize(c["breadcrumb"])
    toks = title * 2 + crumb + body
    if c["kind"] == "definition":
        toks += tokenize(c["term"]) * 3
    bigr = [f"{a}_{b}" for a, b in zip(body, body[1:])] + [f"{a}_{b}" for a, b in zip(title, title[1:])] * 2
    return toks + bigr


def query_tokens(q: str) -> list[str]:
    t = tokenize(q)
    return t + [f"{a}_{b}" for a, b in zip(t, t[1:])]


class BM25:
    def __init__(self, docs: list[list[str]], k1=1.2, b=0.72):
        self.k1, self.b = k1, b
        self.N = len(docs)
        self.dl = np.array([len(d) for d in docs], dtype=np.float32)
        self.avg = float(self.dl.mean())
        self.post = defaultdict(list)
        for i, d in enumerate(docs):
            for t, f in Counter(d).items():
                self.post[t].append((i, f))
        self.idf = {t: math.log(1 + (self.N - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.post.items()}

    def scores(self, qtoks: list[str]) -> np.ndarray:
        s = np.zeros(self.N, dtype=np.float32)
        for t in set(qtoks):
            if t not in self.post:
                continue
            idf = self.idf[t] * (0.6 if "_" in t else 1.0)
            for i, f in self.post[t]:
                s[i] += idf * (f * (self.k1 + 1)) / (f + self.k1 * (1 - self.b + self.b * self.dl[i] / self.avg))
        return s


# --------------------------------------------------------------------------
class LSA:
    """Offline latent-semantic fallback (TF-IDF + truncated SVD). Used only if no neural embedder can load."""

    def __init__(self, texts: list[str], dims=256):
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer
        self.vec = TfidfVectorizer(tokenizer=lambda s: tokenize(s), lowercase=False, sublinear_tf=True,
                                   ngram_range=(1, 2), min_df=2, token_pattern=None)
        X = self.vec.fit_transform(texts)
        self.svd = TruncatedSVD(n_components=min(dims, X.shape[1] - 1), random_state=7)
        Z = self.svd.fit_transform(X)
        self.Z = Z / (np.linalg.norm(Z, axis=1, keepdims=True) + 1e-9)

    def scores(self, q: str) -> np.ndarray:
        z = self.svd.transform(self.vec.transform([q]))
        z = z / (np.linalg.norm(z) + 1e-9)
        return (self.Z @ z.T).ravel()


class Dense:
    name = "dense"

    def __init__(self, texts: list[str], cache_dir: Path):
        from sentence_transformers import SentenceTransformer  # heavy import – lazy
        self.model = SentenceTransformer(config.EMBED_MODEL)
        try:
            self.model.max_seq_length = 768
        except Exception:
            pass
        self.q_prefix = ("Represent this sentence for searching relevant passages: "
                         if ("bge" in config.EMBED_MODEL.lower() and "m3" not in config.EMBED_MODEL.lower()
                             and "-en" in config.EMBED_MODEL.lower()) else "")
        cache_dir.mkdir(parents=True, exist_ok=True)
        key = cache_dir / f"emb_{config.EMBED_MODEL.replace('/', '_')}_{len(texts)}.npy"
        if key.exists():
            self.E = np.load(key)
        else:
            self.E = self.model.encode(texts, batch_size=16, normalize_embeddings=True, show_progress_bar=True)
            np.save(key, self.E)

    def scores(self, q: str) -> np.ndarray:
        v = self.model.encode([self.q_prefix + q], normalize_embeddings=True)[0]
        return self.E @ v


class Reranker:
    def __init__(self):
        from sentence_transformers import CrossEncoder
        self.model = CrossEncoder(config.RERANK_MODEL, max_length=1024)

    def score(self, query: str, docs: list[str]) -> np.ndarray:
        raw = np.array(self.model.predict([(query, d) for d in docs], batch_size=8), dtype=np.float32)
        return 1 / (1 + np.exp(-raw))
