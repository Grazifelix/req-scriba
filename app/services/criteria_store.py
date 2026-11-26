"""Gravação dos critérios de aceitação na tabela ``criterios_aceitacao`` (único ponto que a escreve).

``sync_criteria`` recebe a lista nova (já numerada) e a reconcilia com os critérios vigentes:

- critério com o mesmo texto (normalizado) de um vigente mantém a linha e o id permanente; posição, código,
  título e campos de rastreabilidade são atualizados se vierem na lista nova;
- critério novo vira linha nova, ligada à versão da história em que entrou (``versao_historia``);
- vigente que não está na lista nova é marcado ``substituido`` (com ``substituido_na_versao``), nunca apagado.

As fontes de cada critério recebem o ``evidencia_id`` da evidência da história que copia o trecho citado.
"""

from __future__ import annotations

from typing import Any

from app.core.time import utc_now_iso
from app.db.unit_of_work import UnitOfWork
from app.domain.criteria import CURRENT, REPLACED
from app.domain.text import normalize

UPDATABLE = ("titulo", "dado", "quando", "entao", "fontes", "informacoes_ausentes", "afirmacoes_sem_evidencia", "origem")


def _with_evidence_ids(sources: list[dict[str, Any]], evidence_by_chunk: dict[int, int]) -> list[dict[str, Any]]:
    return [{**f, "evidencia_id": evidence_by_chunk.get(f.get("trecho_id"), f.get("evidencia_id"))} for f in sources]


def _row_values(item: dict[str, Any], position: int, evidence_by_chunk: dict[int, int]) -> dict[str, Any]:
    return {
        "codigo": item.get("id") or f"CA{position:02d}", "posicao": position, "titulo": item["titulo"],
        "criterio": item["criterio"], "dado": item.get("dado"), "quando": item.get("quando"), "entao": item.get("entao"),
        "fontes": _with_evidence_ids(item.get("fontes") or [], evidence_by_chunk),
        "informacoes_ausentes": item.get("informacoes_ausentes") or [],
        "afirmacoes_sem_evidencia": item.get("afirmacoes_sem_evidencia") or [],
        "origem": item.get("origem") if item.get("origem") in ("ia", "legado") else "manual",
    }


def sync_criteria(uow: UnitOfWork, story_id: int, criteria: list[dict[str, Any]], version: int) -> list[int]:
    """Reconcilia os critérios vigentes da história com ``criteria``. Retorna os ids vigentes, na ordem."""
    now = utc_now_iso()
    evidence_by_chunk = {e["trecho_id"]: e["id"] for e in uow.evidences.list_by_story(story_id) if e["trecho_id"]}
    current = {normalize(r["criterio"]): r for r in uow.criteria.list_current(story_id)}
    kept: list[int] = []
    for position, item in enumerate(criteria, start=1):
        values = _row_values(item, position, evidence_by_chunk)
        row = current.pop(normalize(item["criterio"]), None)
        if row:
            changes = {k: values[k] for k in ("codigo", "posicao") if values[k] != row[k]}
            # campos de rastreabilidade só mudam quando a lista nova os traz (edição manual reenvia só o texto)
            changes.update({k: values[k] for k in UPDATABLE if item.get(k) is not None and values[k] != row[k]})
            if changes:
                uow.criteria.update(row["id"], {**changes, "atualizado_em": now})
            kept.append(row["id"])
        else:
            kept.append(uow.criteria.insert({
                "historia_id": story_id, **values, "versao_historia": version, "status": CURRENT,
                "criado_em": now, "atualizado_em": now,
            }))
    for row in current.values():
        uow.criteria.update(row["id"], {"status": REPLACED, "substituido_na_versao": version, "atualizado_em": now})
    return kept


def current_ids(uow: UnitOfWork, story_id: int) -> list[int]:
    return [r["id"] for r in uow.criteria.list_current(story_id)]
