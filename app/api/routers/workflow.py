"""Rotas de qualidade (US-07..09), perguntas (US-10..12), atualizações (US-13..15) e jobs."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status

from app.api.deps import get_actor, get_container, get_uow
from app.core.container import Container
from app.db.unit_of_work import UnitOfWork
from app.domain.enums import QuestionStatus
from app.schemas.common import ERROR_RESPONSES
from app.schemas.workflow import (
    AnalysisRequest, ComparisonOut, DefectList, EvaluationList, EvaluationOut, FollowupOut, JobList, JobOut,
    QualityOverviewOut, QuestionAnswer, QuestionGenerateOut, QuestionGenerateRequest, QuestionList, QuestionOut,
    QuestionUpdate, SuggestionApprove, SuggestionEdit, SuggestionList, SuggestionOut,
)
from app.services.jobs import JobService
from app.services.quality import QualityService
from app.services.questions import QuestionService
from app.services.updates import UpdateService, start_analysis
from app.workers import tasks

router = APIRouter(responses=ERROR_RESPONSES)


def _quality(uow: UnitOfWork = Depends(get_uow), container: Container = Depends(get_container)) -> QualityService:
    return QualityService(uow, container)


def _questions(uow: UnitOfWork = Depends(get_uow), container: Container = Depends(get_container)) -> QuestionService:
    return QuestionService(uow, container)


def _updates(uow: UnitOfWork = Depends(get_uow)) -> UpdateService:
    return UpdateService(uow)


# -- Qualidade --------------------------------------------------------------
@router.post("/historias/{historia_id}/avaliar", response_model=EvaluationOut, tags=["Qualidade"],
             summary="Avaliar/reavaliar história com QUS (US-07, US-08)",
             description="Avalia os 13 critérios QUS da versão atual. Avaliações anteriores são preservadas.")
def evaluate(historia_id: int, service: QualityService = Depends(_quality)):
    return service.evaluate(historia_id)


@router.get("/historias/{historia_id}/avaliacoes", response_model=EvaluationList, tags=["Qualidade"],
            summary="Histórico de avaliações")
def list_evaluations(historia_id: int, service: QualityService = Depends(_quality)):
    return service.history(historia_id)


@router.get("/historias/{historia_id}/avaliacoes/comparacao", response_model=ComparisonOut, tags=["Qualidade"],
            summary="Comparar avaliações (US-08 CA02)", description="Sem parâmetros, compara as duas últimas.")
def compare_evaluations(historia_id: int, de: Optional[int] = None, para: Optional[int] = None,
                        service: QualityService = Depends(_quality)):
    return service.compare(historia_id, de, para)


@router.get("/historias/{historia_id}/defeitos", response_model=DefectList, tags=["Qualidade"],
            summary="Defeitos e lacunas da história (US-09)")
def list_defects(historia_id: int, service: QualityService = Depends(_quality)):
    return service.defects(historia_id)


@router.get("/projetos/{projeto_id}/qualidade", response_model=QualityOverviewOut, tags=["Qualidade"],
            summary="Visão geral de qualidade (US-07 CA03)")
def quality_overview(projeto_id: int, service: QualityService = Depends(_quality)):
    return service.project_overview(projeto_id)


# -- Perguntas --------------------------------------------------------------
@router.post("/historias/{historia_id}/perguntas/gerar", response_model=QuestionGenerateOut, tags=["Follow-up"],
             summary="Gerar perguntas a partir dos defeitos (US-10)")
def generate_questions(historia_id: int, body: Optional[QuestionGenerateRequest] = None,
                       service: QuestionService = Depends(_questions)):
    return service.generate(historia_id, body.defeitos if body else None)


@router.get("/projetos/{projeto_id}/perguntas", response_model=QuestionList, tags=["Follow-up"],
            summary="Listar perguntas")
def list_questions(
    projeto_id: int,
    status_filter: Optional[Literal["pendente", "aprovada", "rejeitada", "respondida"]] = Query(default=None, alias="status"),
    historia_id: Optional[int] = None,
    service: QuestionService = Depends(_questions),
):
    return service.list(projeto_id, status_filter, historia_id)


@router.get("/projetos/{projeto_id}/followup", response_model=FollowupOut, tags=["Follow-up"],
            summary="Lista de follow-up organizada (US-12)",
            description="Perguntas aprovadas e respondidas agrupadas por história ou por tipo de defeito.")
def followup(projeto_id: int, agrupar_por: Literal["historia", "tipo_defeito"] = "historia",
             service: QuestionService = Depends(_questions)):
    return service.followup(projeto_id, agrupar_por)


@router.patch("/perguntas/{pergunta_id}", response_model=QuestionOut, tags=["Follow-up"],
              summary="Editar pergunta (US-11 CA01)")
def edit_question(pergunta_id: int, body: QuestionUpdate, service: QuestionService = Depends(_questions)):
    return service.edit(pergunta_id, body.pergunta)


@router.post("/perguntas/{pergunta_id}/aprovar", response_model=QuestionOut, tags=["Follow-up"],
             summary="Aprovar pergunta (US-11 CA03)")
def approve_question(pergunta_id: int, service: QuestionService = Depends(_questions)):
    return service.set_status(pergunta_id, QuestionStatus.APPROVED)


@router.post("/perguntas/{pergunta_id}/rejeitar", response_model=QuestionOut, tags=["Follow-up"],
             summary="Rejeitar pergunta (US-11 CA02)")
def reject_question(pergunta_id: int, service: QuestionService = Depends(_questions)):
    return service.set_status(pergunta_id, QuestionStatus.REJECTED)


@router.post("/perguntas/{pergunta_id}/responder", response_model=QuestionOut, tags=["Follow-up"],
             summary="Registrar resposta (US-12 CA03)")
def answer_question(pergunta_id: int, body: QuestionAnswer, service: QuestionService = Depends(_questions)):
    return service.answer(pergunta_id, body.resposta, body.resposta_tipo_fonte, body.resposta_fonte_id)


# -- Atualizações -----------------------------------------------------------
@router.post("/projetos/{projeto_id}/atualizacoes/analisar", response_model=JobOut,
             status_code=status.HTTP_202_ACCEPTED, tags=["Atualizações"],
             summary="Analisar novas fontes (US-13)",
             description="Compara novas transcrições/documentos com as histórias existentes e cria sugestões "
                         "(complementa, contradiz, novo_requisito). Nenhuma história é alterada (US-14 CA04).")
def analyze_updates(
    projeto_id: int, body: AnalysisRequest, background: BackgroundTasks,
    uow: UnitOfWork = Depends(get_uow), container: Container = Depends(get_container), actor: str = Depends(get_actor),
):
    job = start_analysis(uow, projeto_id, body.audio_ids, body.documento_ids, actor)
    uow.commit()
    background.add_task(tasks.run_update_analysis, container, job["id"])
    return job


@router.get("/projetos/{projeto_id}/sugestoes-atualizacao", response_model=SuggestionList, tags=["Atualizações"],
            summary="Listar sugestões de atualização")
def list_suggestions(
    projeto_id: int,
    status_filter: Optional[Literal["pendente", "aprovada", "rejeitada"]] = Query(default=None, alias="status"),
    service: UpdateService = Depends(_updates),
):
    return service.list(projeto_id, status_filter)


@router.get("/sugestoes-atualizacao/{sugestao_id}", response_model=SuggestionOut, tags=["Atualizações"],
            summary="Detalhar sugestão com comparação atual × proposto (US-14 CA02)")
def get_suggestion(sugestao_id: int, service: UpdateService = Depends(_updates)):
    return service.get(sugestao_id)


@router.patch("/sugestoes-atualizacao/{sugestao_id}", response_model=SuggestionOut, tags=["Atualizações"],
              summary="Editar conteúdo proposto (US-15 CA01)")
def edit_suggestion(sugestao_id: int, body: SuggestionEdit, service: UpdateService = Depends(_updates)):
    return service.edit(sugestao_id, body.model_dump(exclude_unset=True))


@router.post("/sugestoes-atualizacao/{sugestao_id}/aprovar", response_model=SuggestionOut, tags=["Atualizações"],
             summary="Aprovar e aplicar sugestão (US-15 CA02/CA04)",
             description="Aplica a alteração, cria nova versão (origem IA) e registra as novas evidências (US-18).")
def approve_suggestion(sugestao_id: int, body: Optional[SuggestionApprove] = None,
                       service: UpdateService = Depends(_updates), actor: str = Depends(get_actor)):
    changes = body.conteudo_proposto.model_dump(exclude_unset=True) if body and body.conteudo_proposto else None
    return service.approve(sugestao_id, actor, changes)


@router.post("/sugestoes-atualizacao/{sugestao_id}/rejeitar", response_model=SuggestionOut, tags=["Atualizações"],
             summary="Rejeitar sugestão (US-15 CA03)", description="A história atual é preservada.")
def reject_suggestion(sugestao_id: int, service: UpdateService = Depends(_updates), actor: str = Depends(get_actor)):
    return service.reject(sugestao_id, actor)


# -- Jobs -------------------------------------------------------------------
@router.get("/jobs/{job_id}", response_model=JobOut, tags=["Jobs"], summary="Estado de um job")
def get_job(job_id: str, uow: UnitOfWork = Depends(get_uow)):
    return JobService(uow).get(job_id)


@router.get("/projetos/{projeto_id}/jobs", response_model=JobList, tags=["Jobs"], summary="Jobs do projeto")
def list_jobs(projeto_id: int, uow: UnitOfWork = Depends(get_uow)):
    return JobService(uow).list(projeto_id)
