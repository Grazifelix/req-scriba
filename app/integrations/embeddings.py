"""Provedores de embeddings: Ollama (produção) e Hash determinístico (testes/modo offline)."""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

from app.domain.text import normalize


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    """Embeddings via Ollama (`/api/embed`) em lotes."""

    def __init__(self, client, batch_size: int = 32) -> None:  # client: OllamaClient
        self.client = client
        self.batch_size = batch_size

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            vectors.extend(self.client.embed(texts[i : i + self.batch_size]))
        return vectors


class HashEmbedder:
    """Bag-of-words com hashing (sem rede). Útil para testes e para rodar sem Ollama de embeddings."""

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        for token in normalize(text).split():
            digest = hashlib.md5(token.encode("utf-8")).digest()
            vec[int.from_bytes(digest[:4], "little") % self.dimensions] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]
