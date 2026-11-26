"""Versionamento de Histórias de Usuário (US-19, US-20) — único ponto que cria versões (R4)."""

from __future__ import annotations

from app.core.time import utc_now_iso
from app.db.unit_of_work import UnitOfWork
from app.domain.criteria import criteria_content
from app.domain.enums import ChangeOrigin, ChangeType
from app.services.criteria_store import current_ids

CONTENT_FIELDS = ("titulo", "perfil", "funcionalidade", "beneficio", "historia", "criterios_aceitacao")


def snapshot(story: dict) -> dict:
    """Conteúdo da história; os critérios entram sem os metadados do armazenamento (id, status, versão)."""
    content = {field: story.get(field) for field in CONTENT_FIELDS}
    content["criterios_aceitacao"] = criteria_content(story.get("criterios_aceitacao"))
    return content


def motivating_sources(evidences: list[dict]) -> list[dict]:
    """Fontes das evidências novas de uma versão, agrupadas por arquivo (o que motivou a mudança)."""
    sources: dict[tuple, dict] = {}
    for e in evidences:
        entry = sources.setdefault((e["tipo_fonte"], e["fonte_id"]), {
            "tipo_fonte": e["tipo_fonte"], "fonte_id": e["fonte_id"], "nome_arquivo": e["nome_arquivo"],
            "evidencia_ids": [], "trecho_ids": [],
        })
        entry["evidencia_ids"].append(e["id"])
        if e.get("trecho_id") is not None:
            entry["trecho_ids"].append(e["trecho_id"])
    return list(sources.values())


def same_content(content: dict, story: dict) -> bool:
    """True se ``content`` (saída de ``build_content``) tem o mesmo conteúdo da história."""
    return {**content, "criterios_aceitacao": criteria_content(content.get("criterios_aceitacao"))} == snapshot(story)


def create_version(
    uow: UnitOfWork,
    story_id: int,
    change_type: ChangeType,
    origin: ChangeOrigin,
    author: str,
    suggestion_id: int | None = None,
    note: str | None = None,
) -> int:
    """Cria a próxima versão com o snapshot atual, as evidências, os critérios vigentes, as fontes que motivaram a
    mudança (as das evidências vinculadas desde a versão anterior) e o status da história. Retorna o número."""
    story = uow.stories.get(story_id)
    previous = uow.versions.list_by_story(story_id)
    number = (previous[-1]["numero"] + 1) if previous else 1
    evidences = uow.evidences.list_by_story(story_id)
    known = set(previous[-1]["evidencia_ids"]) if previous else set()
    uow.versions.insert({
        "historia_id": story_id, "numero": number, "tipo_alteracao": change_type.value,
        "origem_alteracao": origin.value, "conteudo": snapshot(story), "evidencia_ids": [e["id"] for e in evidences],
        "criterio_ids": current_ids(uow, story_id), "fontes": motivating_sources([e for e in evidences if e["id"] not in known]),
        "status_historia": story["status"], "responsavel": author, "sugestao_atualizacao_id": suggestion_id,
        "observacao": note, "criado_em": utc_now_iso(),
    })
    uow.stories.update(story_id, {"versao_atual": number})
    return number
