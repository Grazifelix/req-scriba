"""Rotas de Histórias de Usuário, evidências e versões (US-05, US-06, US-16..20)."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status

from app.api.deps import get_actor, get_container, get_uow
from app.core.container import Container
from app.db.unit_of_work import UnitOfWork
from app.domain.enums import StoryStatus
from app.schemas.common import ERROR_RESPONSES
from app.schemas.stories import (
    EvidenceList, GenerationRequest, StoryCreate, StoryList, StoryOut, StoryUpdate, TraceabilityOut,
    VersionList, VersionOut,
)
from app.schemas.workflow import JobOut
from app.services.generation import start_generation
from app.services.stories import StoryService
from app.workers import tasks

router = APIRouter(tags=["Histórias de Usuário"], responses=ERROR_RESPONSES)
StatusFilter = Literal["em_revisao", "falta_informacao", "aprovada", "desaprovada"]


def _service(uow: UnitOfWork = Depends(get_uow)) -> StoryService:
    return StoryService(uow)


@router.post("/projetos/{projeto_id}/historias/gerar", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED,
             summary="Gerar histórias a partir das fontes (US-05)",
             description="Cria um job assíncrono. As histórias geradas ficam com status 'em_revisao' ou "
                         "'falta_informacao' e evidências vinculadas aos trechos de origem (US-16).")
def generate_stories(
    projeto_id: int, body: GenerationRequest, background: BackgroundTasks,
    uow: UnitOfWork = Depends(get_uow), container: Container = Depends(get_container), actor: str = Depends(get_actor),
):
    job = start_generation(uow, projeto_id, body.audio_ids, body.documento_ids, actor)
    uow.commit()
    background.add_task(tasks.run_generation, container, job["id"])
    return job


@router.post("/projetos/{projeto_id}/historias", response_model=StoryOut, status_code=status.HTTP_201_CREATED,
             summary="Criar história manualmente")
def create_story(projeto_id: int, body: StoryCreate, service: StoryService = Depends(_service),
                 actor: str = Depends(get_actor)):
    return service.create_manual(projeto_id, body.model_dump(), actor)


@router.get("/projetos/{projeto_id}/historias", response_model=StoryList, summary="Listar histórias")
def list_stories(projeto_id: int, status_filter: Optional[StatusFilter] = Query(default=None, alias="status"),
                 service: StoryService = Depends(_service)):
    return service.list(projeto_id, status_filter)


@router.get("/historias/{historia_id}", response_model=StoryOut, summary="Detalhar história",
            description="Inclui qualidade QUS, critérios de aceitação, fontes de áudio e documentos, versões e sugestão do Scriba.")
def get_story(historia_id: int, service: StoryService = Depends(_service)):
    return service.detail(historia_id)


@router.patch("/historias/{historia_id}", response_model=StoryOut, summary="Editar história (US-06 CA01)",
              description="Cria uma nova versão manual quando o conteúdo muda (US-19 CA02).")
def update_story(historia_id: int, body: StoryUpdate, service: StoryService = Depends(_service),
                 actor: str = Depends(get_actor)):
    return service.update(historia_id, body.model_dump(exclude_unset=True), actor)


@router.post("/historias/{historia_id}/aprovar", response_model=StoryOut, summary="Aprovar história (US-06 CA02)")
def approve_story(historia_id: int, service: StoryService = Depends(_service)):
    return service.set_status(historia_id, StoryStatus.APPROVED)


@router.post("/historias/{historia_id}/desaprovar", response_model=StoryOut,
             summary="Desaprovar história (US-06 CA03)", description="A história deixa de integrar o backlog.")
def reject_story(historia_id: int, service: StoryService = Depends(_service)):
    return service.set_status(historia_id, StoryStatus.REJECTED)


@router.get("/historias/{historia_id}/evidencias", response_model=EvidenceList, tags=["Rastreabilidade"],
            summary="Evidências da história (US-17)", description="Informe `versao` para ver as evidências de uma versão.")
def list_evidences(historia_id: int, versao: Optional[int] = Query(default=None, ge=1),
                   service: StoryService = Depends(_service)):
    return service.evidences(historia_id, versao)


@router.get("/historias/{historia_id}/rastreabilidade", response_model=TraceabilityOut, tags=["Rastreabilidade"],
            summary="Rastreabilidade por versão (US-17 CA04, US-18)")
def traceability(historia_id: int, service: StoryService = Depends(_service)):
    return service.traceability(historia_id)


@router.get("/historias/{historia_id}/versoes", response_model=VersionList, tags=["Versões"],
            summary="Histórico de versões (US-20)")
def list_versions(historia_id: int, service: StoryService = Depends(_service)):
    return service.versions(historia_id)


@router.get("/historias/{historia_id}/versoes/{numero}", response_model=VersionOut, tags=["Versões"],
            summary="Conteúdo de uma versão (US-20 CA02)")
def get_version(historia_id: int, numero: int, service: StoryService = Depends(_service)):
    return service.version(historia_id, numero)
