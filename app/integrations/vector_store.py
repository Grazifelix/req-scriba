"""Banco vetorial: ChromaDB persistente (produção) e implementação em memória (testes)."""

from __future__ import annotations

import hashlib
import logging
import math
from pathlib import Path
from typing import Any, Protocol

from app.core.errors import ExternalServiceError

logger = logging.getLogger(__name__)

COLLECTION_NAME = "reqscriba_trechos"


def collection_name(embedding_model: str) -> str:
    """Uma coleção por modelo: vetores de modelos diferentes não são comparáveis e nunca se misturam."""
    digest = hashlib.sha256(embedding_model.encode("utf-8")).hexdigest()[:12]
    return f"{COLLECTION_NAME}__{digest}"


class VectorStore(Protocol):
    def upsert(self, ids: list[str], texts: list[str], embeddings: list[list[float]], metadatas: list[dict]) -> None: ...
    def delete(self, ids: list[str]) -> None: ...
    def delete_where(self, where: dict[str, Any]) -> None: ...
    def query(self, embedding: list[float], top_k: int, where: dict[str, Any]) -> list[dict[str, Any]]: ...
    def health(self) -> dict[str, Any]: ...


def build_where(project_id: int, source_keys: list[str] | None = None) -> dict[str, Any]:
    """Filtro de metadados no formato do Chroma (``$and`` exige 2+ condições)."""
    conditions: list[dict[str, Any]] = [{"projeto_id": project_id}]
    if source_keys:
        conditions.append({"fonte_chave": {"$in": source_keys}})
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


class ChromaVectorStore:
    def __init__(self, path: Path, collection: str = COLLECTION_NAME) -> None:
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
        except ImportError as exc:  # pragma: no cover - depende do ambiente
            raise ExternalServiceError("ChromaDB não está instalado (pip install chromadb).") from exc
        self._client = chromadb.PersistentClient(
            path=str(path), settings=ChromaSettings(anonymized_telemetry=False)
        )
        self._collection = self._client.get_or_create_collection(
            collection, metadata={"hnsw:space": "cosine"}, embedding_function=None
        )

    def upsert(self, ids, texts, embeddings, metadatas) -> None:
        if not ids:
            return
        try:
            self._collection.upsert(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas)
        except Exception as exc:  # chromadb lança tipos diversos
            raise ExternalServiceError(f"Falha ao indexar no ChromaDB: {exc}") from exc

    def delete(self, ids: list[str]) -> None:
        if ids:
            self._collection.delete(ids=ids)

    def delete_where(self, where: dict[str, Any]) -> None:
        self._collection.delete(where=where)

    def query(self, embedding, top_k, where) -> list[dict[str, Any]]:
        try:
            result = self._collection.query(
                query_embeddings=[embedding], n_results=top_k, where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            raise ExternalServiceError(f"Falha na consulta ao ChromaDB: {exc}") from exc
        ids = (result.get("ids") or [[]])[0]
        return [
            {
                "id": ids[i],
                "texto": result["documents"][0][i],
                "metadados": result["metadatas"][0][i],
                "distancia": result["distances"][0][i],
            }
            for i in range(len(ids))
        ]

    def health(self) -> dict[str, Any]:
        try:
            return {"status": "ok", "vetores": self._collection.count()}
        except Exception as exc:
            return {"status": "indisponivel", "detalhe": str(exc)[:200]}


def _matches(meta: dict[str, Any], where: dict[str, Any]) -> bool:
    if "$and" in where:
        return all(_matches(meta, cond) for cond in where["$and"])
    for key, expected in where.items():
        if isinstance(expected, dict) and "$in" in expected:
            if meta.get(key) not in expected["$in"]:
                return False
        elif meta.get(key) != expected:
            return False
    return True


class InMemoryVectorStore:
    """Implementação simples com distância cosseno, usada em testes."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    def upsert(self, ids, texts, embeddings, metadatas) -> None:
        for i, item_id in enumerate(ids):
            self.items[item_id] = {"texto": texts[i], "embedding": embeddings[i], "metadados": metadatas[i]}

    def delete(self, ids: list[str]) -> None:
        for item_id in ids:
            self.items.pop(item_id, None)

    def delete_where(self, where: dict[str, Any]) -> None:
        for item_id in [k for k, v in self.items.items() if _matches(v["metadados"], where)]:
            del self.items[item_id]

    @staticmethod
    def _distance(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(y * y for y in b)) or 1.0
        return 1 - dot / (na * nb)

    def query(self, embedding, top_k, where) -> list[dict[str, Any]]:
        candidates = [
            {"id": k, "texto": v["texto"], "metadados": v["metadados"], "distancia": self._distance(embedding, v["embedding"])}
            for k, v in self.items.items()
            if _matches(v["metadados"], where)
        ]
        return sorted(candidates, key=lambda c: c["distancia"])[:top_k]

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "vetores": len(self.items)}
