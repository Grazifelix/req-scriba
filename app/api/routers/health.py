"""Verificação de saúde dos componentes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app import __version__
from app.api.deps import get_container
from app.core.container import Container
from app.services.indexing import vector_index_health

router = APIRouter(tags=["Saúde"])


def _database_and_index_health(container: Container) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        if not container.db.ping():
            return {"status": "indisponivel"}, container.vector_store.health()
        with container.uow() as uow:
            return {"status": "ok"}, vector_index_health(uow, container)
    except Exception as exc:  # reporta, não derruba a verificação
        return {"status": "indisponivel", "detalhe": str(exc)[:200]}, container.vector_store.health()


@router.get("/health", summary="Status dos componentes",
            description="Verifica SQLite, ChromaDB (e se falta reindexar), o LLM e os embeddings configurados "
                        "(provedor e modelos) e o mecanismo de transcrição.")
def health(container: Container = Depends(get_container)) -> dict[str, Any]:
    database, vector_index = _database_and_index_health(container)
    components = {
        "banco_relacional": database,
        "banco_vetorial": vector_index,
        "llm": container.llm.health(),
        "embeddings": container.embedder.health(),
        "transcricao": container.transcriber.health(),
    }
    overall = "ok" if all(c.get("status") == "ok" for c in components.values()) else "degradado"
    return {"status": overall, "versao": __version__, "componentes": components}
