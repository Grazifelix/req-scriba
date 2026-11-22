"""Rotas da análise das fontes: geração (job), revisão e aprovação humana de como a IA entendeu a reunião."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Response, status

from app.api.deps import get_actor, get_container, get_uow
from app.core.container import Container
from app.db.unit_of_work import UnitOfWork
from app.schemas.analyses import (
    AnalysisCreate, AnalysisItemCreate, AnalysisItemOut, AnalysisItemUpdate, AnalysisList, AnalysisOut, AnalysisUpdate,
)
from app.schemas.common import ERROR_RESPONSES
from app.schemas.workflow import AnalysisRequest, JobOut
from app.services.analysis import AnalysisService
from app.services.source_analysis import start_source_analysis
from app.workers import tasks

router = APIRouter(tags=["Análises"], responses=ERROR_RESPONSES)


def _service(uow: UnitOfWork = Depends(get_uow)) -> AnalysisService:
    return AnalysisService(uow)


@router.post("/projetos/{projeto_id}/analises/gerar", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED,
             summary="Analisar as fontes (etapa anterior à geração de histórias)",
             description="Cria um job assíncrono que lê as fontes informadas e a análise mais recente do projeto e "
                         "grava uma NOVA análise atualizada (não revisada), mantendo os itens, códigos RN/FN e edições "
                         "da anterior. Use também quando entrarem novas fontes. A geração de histórias só usa "
                         "análises aprovadas (`revisada: true`).")
def generate_analysis(
    projeto_id: int, body: AnalysisRequest, background: BackgroundTasks,
    uow: UnitOfWork = Depends(get_uow), container: Container = Depends(get_container), actor: str = Depends(get_actor),
):
    job = start_source_analysis(uow, projeto_id, body.audio_ids, body.documento_ids, actor)
    uow.commit()
    background.add_task(tasks.run_source_analysis, container, job["id"])
    return job


@router.get("/projetos/{projeto_id}/analises", response_model=AnalysisList, summary="Listar análises do projeto",
            description="Análises do projeto (mais recente primeiro), com sistema, participantes, usuários, "
                        "requisitos de negócio, funcionalidades e itens fora de escopo.")
def list_analyses(projeto_id: int, service: AnalysisService = Depends(_service)):
    return service.list(projeto_id)


@router.post("/projetos/{projeto_id}/analises", response_model=AnalysisOut, status_code=status.HTTP_201_CREATED,
             summary="Criar análise manualmente")
def create_analysis(projeto_id: int, body: AnalysisCreate, service: AnalysisService = Depends(_service),
                    actor: str = Depends(get_actor)):
    return service.create(projeto_id, body.model_dump(), actor)


@router.get("/analises/{analise_id}", response_model=AnalysisOut, summary="Detalhar análise")
def get_analysis(analise_id: int, service: AnalysisService = Depends(_service)):
    return service.detail(analise_id)


@router.patch("/analises/{analise_id}", response_model=AnalysisOut, summary="Editar análise ou marcá-la como revisada",
              description="Com `revisada: true` a análise fica aprovada e libera a geração de histórias "
                          "(vale a aprovada mais recente, ou a informada em `analise_id`).")
def update_analysis(analise_id: int, body: AnalysisUpdate, service: AnalysisService = Depends(_service),
                    actor: str = Depends(get_actor)):
    return service.update(analise_id, body.model_dump(exclude_unset=True), actor)


@router.delete("/analises/{analise_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Excluir análise",
               description="Remove a análise e seus itens. As histórias geradas não são afetadas.")
def delete_analysis(analise_id: int, service: AnalysisService = Depends(_service)):
    service.delete(analise_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/analises/{analise_id}/itens", response_model=AnalysisItemOut, status_code=status.HTTP_201_CREATED,
             summary="Adicionar item à análise",
             description="Requisitos de negócio e funcionalidades recebem códigos RN/FN automaticamente.")
def add_item(analise_id: int, body: AnalysisItemCreate, service: AnalysisService = Depends(_service)):
    return service.add_item(analise_id, body.model_dump())


@router.patch("/analises/{analise_id}/itens/{item_id}", response_model=AnalysisItemOut, summary="Editar item da análise")
def update_item(analise_id: int, item_id: int, body: AnalysisItemUpdate, service: AnalysisService = Depends(_service)):
    return service.update_item(analise_id, item_id, body.model_dump(exclude_unset=True))


@router.delete("/analises/{analise_id}/itens/{item_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Excluir item da análise",
               description="Ao excluir um requisito de negócio, ele é removido das referências das funcionalidades.")
def delete_item(analise_id: int, item_id: int, service: AnalysisService = Depends(_service)):
    service.delete_item(analise_id, item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
