"""Local text embeddings (fastembed) for note retrieval and near-duplicate checks.

Vectors are L2-normalized float32, so cosine similarity is a dot product. They are stored
in `memory_note.embedding` as raw bytes together with the model name; a note whose stored
model differs from the current one is re-embedded on next use.
"""

import hashlib
import re
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

Vector = NDArray[np.float32]


class Embedder(Protocol):
    model_name: str

    def embed_queries(self, texts: Sequence[str]) -> Vector: ...

    def embed_passages(self, texts: Sequence[str]) -> Vector: ...


def to_blob(vector: Vector) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> Vector:
    return np.frombuffer(blob, dtype=np.float32)


def _normalize(matrix: NDArray[Any]) -> Vector:
    matrix = np.asarray(matrix, dtype=np.float32)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    normalized: Vector = (matrix / norms).astype(np.float32)
    return normalized


class FastEmbedEmbedder:
    """Loads the ONNX model on first use (a few seconds; the first ever run downloads it)."""

    def __init__(self, model_name: str, cache_dir: Path) -> None:
        self.model_name = model_name
        self._cache_dir = cache_dir
        self._model: Any = None
        self._lock = threading.Lock()
        # e5 models expect role prefixes; most others don't.
        self._query_prefix, self._passage_prefix = (
            ("query: ", "passage: ") if "e5" in model_name.lower() else ("", "")
        )

    def _load(self) -> Any:
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding  # heavy import, only when needed

                self._cache_dir.mkdir(parents=True, exist_ok=True)
                self._model = TextEmbedding(self.model_name, cache_dir=str(self._cache_dir))
            return self._model

    def _embed(self, texts: Sequence[str], prefix: str) -> Vector:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        vectors = list(self._load().embed([prefix + t for t in texts]))
        return _normalize(np.array(vectors))

    def embed_queries(self, texts: Sequence[str]) -> Vector:
        return self._embed(texts, self._query_prefix)

    def embed_passages(self, texts: Sequence[str]) -> Vector:
        return self._embed(texts, self._passage_prefix)


class FakeEmbedder:
    """Deterministic bag-of-words vectors for tests: shared words mean higher similarity."""

    model_name = "fake-bow-256"
    _DIM = 256

    def _vector(self, text: str) -> NDArray[np.float32]:
        vector = np.zeros(self._DIM, dtype=np.float32)
        for word in re.findall(r"\w+", text.casefold()):
            bucket = int(hashlib.md5(word.encode()).hexdigest(), 16) % self._DIM
            vector[bucket] += 1.0
        return vector

    def embed_queries(self, texts: Sequence[str]) -> Vector:
        return _normalize(np.array([self._vector(t) for t in texts]))

    def embed_passages(self, texts: Sequence[str]) -> Vector:
        return self.embed_queries(texts)
