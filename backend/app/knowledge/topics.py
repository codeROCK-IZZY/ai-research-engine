"""Topic discovery: cluster sources by meaning, label clusters by distinctive words.

1. Each source is represented by the mean of its chunk embeddings (``avg(embedding)`` in
   PostgreSQL), L2-normalised so k-means approximates cosine similarity.
2. k-means (k-means++ seeding, fixed seed → reproducible) with
   k = clamp(round(sqrt(n / 2)), 2, 8).
3. Each topic is labelled with the words that are frequent in its sources' text but rare in
   the other topics (TF-IDF over the topic "documents"). Labels are deterministic, not AI-written.
"""

from __future__ import annotations

import math
from collections import Counter

import numpy as np

from app.ai.textutil import terms


def kmeans(x: np.ndarray, k: int, seed: int = 7, iters: int = 50) -> np.ndarray:
    rng = np.random.default_rng(seed)
    centers = [x[rng.integers(len(x))]]
    for _ in range(1, k):
        d2 = np.min([np.sum((x - c) ** 2, axis=1) for c in centers], axis=0)
        probs = d2 / d2.sum() if d2.sum() > 0 else np.full(len(x), 1 / len(x))
        centers.append(x[rng.choice(len(x), p=probs)])
    c = np.array(centers)
    labels = np.zeros(len(x), dtype=int)
    for _ in range(iters):
        new = np.argmin(((x[:, None, :] - c[None, :, :]) ** 2).sum(axis=2), axis=1)
        if np.array_equal(new, labels) and _ > 0:
            break
        labels = new
        for j in range(k):
            if np.any(labels == j):
                c[j] = x[labels == j].mean(axis=0)
    return labels


def choose_k(n: int) -> int:
    return max(2, min(8, round(math.sqrt(n / 2))))


def label_topics(texts_by_topic: dict[int, str], top: int = 4) -> dict[int, list[str]]:
    counts = {t: Counter(w for w in terms(txt) if len(w) > 3 and not w.isdigit()) for t, txt in texts_by_topic.items()}
    df = Counter(w for c in counts.values() for w in c)
    n = len(counts)
    out = {}
    for t, c in counts.items():
        total = sum(c.values()) or 1
        scored = {w: (cnt / total) * math.log((1 + n) / (1 + df[w])) for w, cnt in c.items()}
        out[t] = [w for w, _ in sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))[:top]]
    return out
