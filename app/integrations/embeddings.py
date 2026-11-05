"""Provedores de embeddings: cliente remoto em lotes e Hash determinístico (testes/modo offline)."""

from __future__ import annotations

import hashlib
import math
from typing import Any, Protocol

from app.domain.text import normalize


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def health(self) -> dict[str, Any]: ...


class EmbeddingClient(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def embedding_health(self) -> dict[str, Any]: ...


class RemoteEmbedder:
    """Envia os textos em lotes a um adaptador de provedor (Ollama, compatível com OpenAI)."""

    def __init__(self, client: EmbeddingClient, batch_size: int = 32) -> None:
        self.client = client
        self.batch_size = batch_size

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            vectors.extend(self.client.embed(texts[i : i + self.batch_size]))
        return vectors

    def health(self) -> dict[str, Any]:
        return self.client.embedding_health()


class HashEmbedder:
    """Bag-of-words com hashing (sem rede). Útil para testes e para rodar sem modelo de embeddings."""

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

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "provedor": "hash", "modelo": "hash"}
