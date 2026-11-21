"""Rotas da análise das fontes: revisão humana de como a IA entendeu a reunião."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import get_actor, get_uow
from app.db.unit_of_work import UnitOfWork
from app.schemas.analyses import (
    AnalysisCreate, AnalysisItemCreate, AnalysisItemOut, AnalysisItemUpdate, AnalysisList, AnalysisOut, AnalysisUpdate,
)
from app.schemas.common import ERROR_RESPONSES
from app.services.analysis import AnalysisService

router = APIRouter(tags=["Análises"], responses=ERROR_RESPONSES)


def _service(uow: UnitOfWork = Depends(get_uow)) -> AnalysisService:
    return AnalysisService(uow)


@router.get("/projetos/{projeto_id}/analises", response_model=AnalysisList, summary="Listar análises do projeto",
            description="Uma análise por geração de histórias (mais recente primeiro), com sistema, participantes, "
                        "usuários, requisitos de negócio, funcionalidades e itens fora de escopo.")
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
              description="Com `revisada: true`, a análise passa a orientar as próximas gerações do projeto "
                          "(vale a revisada mais recente).")
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
