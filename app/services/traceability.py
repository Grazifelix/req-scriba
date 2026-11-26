"""Rastreabilidade (Milestone 2): validação estrutural dos vínculos e recuperação da fonte original.

Estrutural ≠ semântica: aqui só se verifica o que o código consegue afirmar com certeza (referências que
existem, ids únicos, citações iguais ao trecho atual, campos obrigatórios, vínculos das versões). Se o trecho
sustenta mesmo a afirmação é a auditoria semântica (``services.evidence_audit``).
"""

from __future__ import annotations

from typing import Any

from app.core.errors import NotFoundError
from app.db.unit_of_work import UnitOfWork
from app.domain.traceability import validate_story
from app.services import presenters
from app.services.common import require_project, require_story
from app.services.retrieval import describe_chunk


class TraceabilityService:
    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    def _validate(self, story: dict) -> dict[str, Any]:
        evidences = self.uow.evidences.list_by_story(story["id"])
        chunk_ids = [e["trecho_id"] for e in evidences if e["trecho_id"] is not None]
        chunks = {c["id"]: c for c in self.uow.chunks.list_by_ids(chunk_ids)}
        return validate_story(
            story, evidences, self.uow.criteria.list_current(story["id"]), self.uow.criteria.list_by_story(story["id"]),
            self.uow.versions.list_by_story(story["id"]), chunks,
        )

    def validate_story(self, story_id: int) -> dict[str, Any]:
        return self._validate(require_story(self.uow, story_id))

    def validate_project(self, project_id: int) -> dict[str, Any]:
        """Validação de todas as histórias ativas; ``itens`` traz só as que têm algum problema."""
        require_project(self.uow, project_id)
        reports = [self._validate(s) for s in self.uow.stories.list_active(project_id)]
        by_type: dict[str, int] = {}
        for report in reports:
            for issue in report["problemas"]:
                by_type[issue["tipo"]] = by_type.get(issue["tipo"], 0) + 1
        problems = [r for r in reports if r["problemas"]]
        return {
            "projeto_id": project_id, "historias": len(reports), "historias_validas": sum(r["valida"] for r in reports),
            "erros": sum(r["erros"] for r in reports), "avisos": sum(r["avisos"] for r in reports),
            "por_tipo": by_type, **presenters.listing(problems),
        }

    def chunk(self, chunk_id: int) -> dict[str, Any]:
        """Fonte original de um trecho: arquivo, localização, texto atual e onde ele é usado como evidência."""
        [chunk] = self.uow.chunks.list_by_ids([chunk_id]) or [None]
        if chunk is None:
            raise NotFoundError.for_entity("Trecho", chunk_id)
        usages = []
        for evidence in self.uow.evidences.list_by_chunk(chunk_id):
            story = self.uow.stories.get(evidence["historia_id"])
            criteria = [c for c in self.uow.criteria.list_by_story(story["id"])
                        if any(f.get("trecho_id") == chunk_id for f in c["fontes"])]
            usages.append({
                "historia_id": story["id"], "historia_codigo": story["codigo"], "evidencia_id": evidence["id"],
                "afirmacoes_sustentadas": evidence["afirmacoes_sustentadas"],
                "texto_igual_ao_atual": " ".join(evidence["trecho"].split()) == " ".join(chunk["texto"].split()),
                "criterios": [{"criterio_id": c["id"], "codigo": c["codigo"], "status": c["status"]} for c in criteria],
            })
        return {**presenters.chunk(chunk), "descricao": describe_chunk(chunk), "projeto_id": chunk["projeto_id"],
                "usos": usages}
