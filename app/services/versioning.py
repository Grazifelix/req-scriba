"""Versionamento de Histórias de Usuário (US-19, US-20) — único ponto que cria versões (R4)."""

from __future__ import annotations

from app.core.time import utc_now_iso
from app.db.unit_of_work import UnitOfWork
from app.domain.enums import ChangeOrigin, ChangeType

CONTENT_FIELDS = ("titulo", "perfil", "funcionalidade", "beneficio", "historia", "criterios_aceitacao")


def snapshot(story: dict) -> dict:
    return {field: story.get(field) for field in CONTENT_FIELDS}


def create_version(
    uow: UnitOfWork,
    story_id: int,
    change_type: ChangeType,
    origin: ChangeOrigin,
    author: str,
    suggestion_id: int | None = None,
    note: str | None = None,
) -> int:
    """Cria a próxima versão com o snapshot atual e as evidências vigentes. Retorna o número."""
    story = uow.stories.get(story_id)
    previous = uow.versions.list_by_story(story_id)
    number = (previous[-1]["numero"] + 1) if previous else 1
    evidence_ids = [e["id"] for e in uow.evidences.list_by_story(story_id)]
    uow.versions.insert({
        "historia_id": story_id, "numero": number, "tipo_alteracao": change_type.value,
        "origem_alteracao": origin.value, "conteudo": snapshot(story), "evidencia_ids": evidence_ids,
        "responsavel": author, "sugestao_atualizacao_id": suggestion_id, "observacao": note,
        "criado_em": utc_now_iso(),
    })
    uow.stories.update(story_id, {"versao_atual": number})
    return number
