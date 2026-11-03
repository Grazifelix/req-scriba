"""Verificação de saúde dos componentes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app import __version__
from app.api.deps import get_container
from app.core.container import Container

router = APIRouter(tags=["Saúde"])


@router.get("/health", summary="Status dos componentes",
            description="Verifica SQLite, ChromaDB, Ollama (modelos LLM e de embeddings) e o mecanismo de transcrição.")
def health(container: Container = Depends(get_container)) -> dict[str, Any]:
    try:
        database = {"status": "ok" if container.db.ping() else "indisponivel"}
    except Exception as exc:  # reporta, não derruba a verificação
        database = {"status": "indisponivel", "detalhe": str(exc)[:200]}
    llm_health = getattr(container.llm, "health", None)
    components = {
        "banco_relacional": database,
        "banco_vetorial": container.vector_store.health(),
        "ollama": llm_health() if callable(llm_health) else {"status": "ok", "detalhe": "cliente simulado"},
        "transcricao": container.transcriber.health(),
    }
    overall = "ok" if all(c.get("status") == "ok" for c in components.values()) else "degradado"
    return {"status": overall, "versao": __version__, "componentes": components}
