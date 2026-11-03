"""Evidências de rastreabilidade: criação a partir de trechos e consulta por versão."""

from __future__ import annotations

from app.core.time import utc_now_iso
from app.db.unit_of_work import UnitOfWork

NO_SOURCE_CLAIM = "Nenhum trecho das fontes foi identificado como origem desta história."


def evidence_spec(chunk: dict, claim: str | None = None) -> dict:
    """Cópia imutável dos dados do trecho, usada para evidências e sugestões."""
    return {
        "trecho_id": chunk["id"], "tipo_fonte": chunk["tipo_fonte"], "fonte_id": chunk["fonte_id"],
        "nome_arquivo": chunk["nome_arquivo"], "trecho": chunk["texto"], "localizacao": chunk["localizacao"],
        "afirmacao": claim,
    }


def add_evidences(uow: UnitOfWork, story_id: int, specs: list[dict], origin: str) -> list[int]:
    """Registra evidências (sem duplicar o mesmo trecho na mesma história)."""
    existing = {e["trecho_id"] for e in uow.evidences.list_by_story(story_id) if e["trecho_id"]}
    now = utc_now_iso()
    ids = []
    for spec in specs:
        if spec.get("trecho_id") and spec["trecho_id"] in existing:
            continue
        ids.append(uow.evidences.insert({
            "historia_id": story_id, "tipo_fonte": spec["tipo_fonte"], "fonte_id": spec["fonte_id"],
            "nome_arquivo": spec["nome_arquivo"], "trecho_id": spec.get("trecho_id"), "trecho": spec["trecho"],
            "localizacao": spec["localizacao"], "afirmacao": spec.get("afirmacao"), "origem": origin,
            "criado_em": now,
        }))
        if spec.get("trecho_id"):
            existing.add(spec["trecho_id"])
    return ids
