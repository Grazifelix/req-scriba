"""Rotas de projetos e busca semântica."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import get_container, get_uow
from app.core.container import Container
from app.db.unit_of_work import UnitOfWork
from app.schemas.common import ERROR_RESPONSES
from app.schemas.projects import ProjectCreate, ProjectList, ProjectOut, ProjectUpdate
from app.schemas.sources import SearchOut
from app.services import retrieval
from app.services.projects import ProjectService

router = APIRouter(tags=["Projetos"], responses=ERROR_RESPONSES)


def _service(uow: UnitOfWork = Depends(get_uow), container: Container = Depends(get_container)) -> ProjectService:
    return ProjectService(uow, container)


@router.post("/projetos", response_model=ProjectOut, status_code=status.HTTP_201_CREATED, summary="Criar projeto")
def create_project(body: ProjectCreate, service: ProjectService = Depends(_service)):
    return service.create(body.titulo, body.descricao)


@router.get("/projetos", response_model=ProjectList, summary="Listar projetos",
            description="Lista projetos com totais de fontes, histórias prontas/pendentes, qualidade geral e backlog.")
def list_projects(service: ProjectService = Depends(_service)):
    return service.list()


@router.get("/projetos/{projeto_id}", response_model=ProjectOut, summary="Detalhar projeto")
def get_project(projeto_id: int, service: ProjectService = Depends(_service)):
    return service.get(projeto_id)


@router.patch("/projetos/{projeto_id}", response_model=ProjectOut, summary="Atualizar projeto")
def update_project(projeto_id: int, body: ProjectUpdate, service: ProjectService = Depends(_service)):
    return service.update(projeto_id, body.model_dump(exclude_unset=True))


@router.delete("/projetos/{projeto_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Excluir projeto",
               description="Remove o projeto e todos os dados associados (fontes, vetores, arquivos, histórias).")
def delete_project(projeto_id: int, service: ProjectService = Depends(_service)):
    service.delete(projeto_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/projetos/{projeto_id}/busca", response_model=SearchOut, tags=["Busca"],
            summary="Busca semântica nas fontes (RAG)",
            description="Recupera os trechos de transcrições e documentos mais similares ao termo informado.")
def semantic_search(
    projeto_id: int,
    termo: str = Query(min_length=2, max_length=500),
    limite: int = Query(default=10, ge=1, le=50),
    uow: UnitOfWork = Depends(get_uow),
    container: Container = Depends(get_container),
):
    return retrieval.search(uow, container, projeto_id, termo, limite)
