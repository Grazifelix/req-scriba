"""Jobs assíncronos persistidos (geração de histórias e análise de atualização)."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.errors import BusinessValidationError, NotFoundError
from app.core.time import utc_now_iso
from app.db.unit_of_work import UnitOfWork
from app.domain.enums import JobStatus, JobType, SourceStatus
from app.services import presenters
from app.services.common import require_project


def validate_sources(uow: UnitOfWork, project_id: int, audio_ids: list[int], document_ids: list[int]) -> None:
    """RN11: fontes precisam existir, pertencer ao projeto e estar com status 'concluido'."""
    if not audio_ids and not document_ids:
        raise BusinessValidationError("Selecione ao menos uma fonte (áudio ou documento).")
    problems = []
    found_audios = {a["id"]: a for a in uow.audios.list_by_ids(audio_ids)}
    found_docs = {d["id"]: d for d in uow.documents.list_by_ids(document_ids)}
    for kind, ids, found in (("audio", audio_ids, found_audios), ("documento", document_ids, found_docs)):
        for source_id in ids:
            source = found.get(source_id)
            if not source or source["projeto_id"] != project_id:
                problems.append({"fonte": f"{kind}:{source_id}", "motivo": "não encontrada neste projeto"})
            elif source["status"] != SourceStatus.COMPLETED.value:
                problems.append({
                    "fonte": f"{kind}:{source_id}",
                    "motivo": f"status '{source['status']}' — apenas fontes concluídas podem ser usadas",
                    "mensagem_erro": source["mensagem_erro"],
                })
    if problems:
        raise BusinessValidationError("Há fontes inválidas na seleção.", {"fontes": problems})


class JobService:
    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    def create(self, project_id: int, job_type: JobType, params: dict) -> dict:
        require_project(self.uow, project_id)
        now = utc_now_iso()
        job_id = uuid.uuid4().hex
        self.uow.jobs.insert({
            "id": job_id, "projeto_id": project_id, "tipo": job_type.value, "status": JobStatus.PENDING.value,
            "parametros": params, "criado_em": now, "atualizado_em": now,
        })
        return self.uow.jobs.get(job_id)

    def get(self, job_id: str) -> dict:
        job = self.uow.jobs.get(job_id)
        if not job:
            raise NotFoundError.for_entity("Job", job_id)
        return presenters.job(job)

    def list(self, project_id: int) -> dict:
        require_project(self.uow, project_id)
        return presenters.listing([presenters.job(j) for j in self.uow.jobs.list_by_project(project_id)])

    def mark(self, job_id: str, status: JobStatus, result: dict | None = None, error: str | None = None) -> None:
        now = utc_now_iso()
        values: dict[str, Any] = {"status": status.value, "atualizado_em": now}
        if result is not None:
            values["resultado"] = result
        if error is not None:
            values["mensagem_erro"] = error
        if status in (JobStatus.COMPLETED, JobStatus.ERROR):
            values["concluido_em"] = now
        self.uow.jobs.update(job_id, values)
