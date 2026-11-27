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
    CriteriaGenerationRequest, CriteriaRegenerationRequest, EvidenceAuditRequest, EvidenceAuditReview, EvidenceList, GenerationRequest, RewriteApply, RewriteResult, StoryCreate, StoryList, StoryOut, StoryUpdate,
    TraceabilityOut, VersionList, VersionOut,
)
from app.schemas.workflow import JobOut
from app.services.criteria import CriteriaService, start_criteria_generation
from app.services.evidence_audit import EvidenceAuditService, start_evidence_audit
from app.services.generation import start_generation
from app.services.stories import StoryService
from app.services.traceability import TraceabilityService
from app.workers import tasks

router = APIRouter(tags=["Histórias de Usuário"], responses=ERROR_RESPONSES)
StatusFilter = Literal["em_revisao", "falta_informacao", "aprovada", "desaprovada"]


def _service(uow: UnitOfWork = Depends(get_uow)) -> StoryService:
    return StoryService(uow)


@router.post("/projetos/{projeto_id}/historias/gerar", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED,
             summary="Gerar histórias a partir das fontes (US-05)",
             description="Cria um job assíncrono. Exige uma análise das fontes aprovada pelo analista "
                         "(`analise_id` ou a aprovada mais recente), que entra no prompt como ANALISE DO PROJETO. "
                         "As histórias geradas ficam com status 'em_revisao' ou 'falta_informacao' e evidências "
                         "vinculadas aos trechos de origem (US-16).")
def generate_stories(
    projeto_id: int, body: GenerationRequest, background: BackgroundTasks,
    uow: UnitOfWork = Depends(get_uow), container: Container = Depends(get_container), actor: str = Depends(get_actor),
):
    job = start_generation(uow, projeto_id, body.audio_ids, body.documento_ids, actor, body.analise_id)
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


@router.post("/historias/{historia_id}/aplicar-reescrita", response_model=RewriteResult,
             summary="Aplicar a reescrita sugerida pela avaliação",
             description="Cria uma nova versão manual da história. Se a reescrita dividir a história "
                         "('H1: …; H2: …'), a primeira parte substitui a atual e cada parte seguinte vira uma "
                         "história nova, em revisão, com as mesmas evidências da original. Na divisão, o LLM "
                         "escreve critérios de aceitação próprios para cada parte (falha do LLM → 502, nada é gravado).")
def apply_rewrite(historia_id: int, body: RewriteApply, uow: UnitOfWork = Depends(get_uow),
                  container: Container = Depends(get_container), actor: str = Depends(get_actor)):
    return StoryService(uow, container).apply_rewrite(historia_id, body.historia, actor)


@router.post("/historias/{historia_id}/criterios/gerar", response_model=StoryOut, tags=["Critérios de aceitação"],
             summary="Gerar ou regerar critérios de aceitação de uma história",
             description="Etapa GERACAO_CRITERIOS_ACEITACAO a partir das evidências da história e de trechos "
                         "relacionados. `escopo` auto (padrão): regera os critérios desatualizados, ou todos se não "
                         "houver; `criterio_ids` regera só os escolhidos. Os que continuam valendo vão ao prompt como "
                         "já validados e são mantidos; os substituídos ficam preservados. A história não é alterada; "
                         "os critérios mudam numa nova versão (origem `geracao_criterios_ia`). História aprovada: vira "
                         "uma proposta pendente `revisao_criterios` (`proposta_criterios_id`), porque o LLM não altera "
                         "histórias aprovadas (409 se já houver uma pendente). Falha do LLM → 502.")
def generate_criteria(historia_id: int, body: Optional[CriteriaRegenerationRequest] = None,
                      uow: UnitOfWork = Depends(get_uow), container: Container = Depends(get_container),
                      actor: str = Depends(get_actor)):
    body = body or CriteriaRegenerationRequest()
    return CriteriaService(uow, container).generate(historia_id, actor, body.escopo, body.criterio_ids or None)


@router.post("/historias/{historia_id}/criterios/{criterio_id}/confirmar", response_model=StoryOut,
             tags=["Critérios de aceitação"], summary="Confirmar que um critério continua válido",
             description="Depois de uma mudança na história, o critério fica desatualizado até ser confirmado ou "
                         "regerado. Confirmar o marca válido na versão atual (não cria versão).")
def confirm_criterion(historia_id: int, criterio_id: int, uow: UnitOfWork = Depends(get_uow),
                      container: Container = Depends(get_container)):
    return CriteriaService(uow, container).confirm(historia_id, criterio_id)


@router.post("/projetos/{projeto_id}/criterios/gerar", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED,
             tags=["Critérios de aceitação"], summary="Gerar critérios de aceitação em lote",
             description="Cria um job (`geracao_criterios`) que processa várias histórias por chamada ao LLM e grava "
                         "cada lote assim que ele termina. Entram as histórias sem critérios e as com critérios "
                         "desatualizados (só esses são regerados); com `substituir`, também as demais. Histórias "
                         "aprovadas geram propostas `revisao_criterios`; desaprovadas são ignoradas.")
def generate_criteria_batch(
    projeto_id: int, background: BackgroundTasks, body: Optional[CriteriaGenerationRequest] = None,
    uow: UnitOfWork = Depends(get_uow), container: Container = Depends(get_container), actor: str = Depends(get_actor),
):
    body = body or CriteriaGenerationRequest()
    job = start_criteria_generation(uow, projeto_id, body.historia_ids, body.substituir, actor)
    uow.commit()
    background.add_task(tasks.run_criteria_generation, container, job["id"])
    return job


@router.post("/historias/{historia_id}/aprovar", response_model=StoryOut, summary="Aprovar história (US-06 CA02)")
def approve_story(historia_id: int, service: StoryService = Depends(_service)):
    return service.set_status(historia_id, StoryStatus.APPROVED)


@router.post("/historias/{historia_id}/desaprovar", response_model=StoryOut,
             summary="Desaprovar história (US-06 CA03)",
             description="A história deixa de integrar o backlog; as propostas pendentes sobre ela são canceladas.")
def reject_story(historia_id: int, service: StoryService = Depends(_service), actor: str = Depends(get_actor)):
    return service.set_status(historia_id, StoryStatus.REJECTED, actor)


@router.get("/historias/{historia_id}/evidencias", response_model=EvidenceList, tags=["Rastreabilidade"],
            summary="Evidências da história (US-17)", description="Informe `versao` para ver as evidências de uma versão.")
def list_evidences(historia_id: int, versao: Optional[int] = Query(default=None, ge=1),
                   service: StoryService = Depends(_service)):
    return service.evidences(historia_id, versao)


@router.get("/historias/{historia_id}/rastreabilidade", response_model=TraceabilityOut, tags=["Rastreabilidade"],
            summary="Rastreabilidade por versão (US-17 CA04, US-18)")
def traceability(historia_id: int, service: StoryService = Depends(_service)):
    return service.traceability(historia_id)


@router.get("/historias/{historia_id}/validacao-estrutural", tags=["Rastreabilidade"],
            summary="Validação estrutural da rastreabilidade de uma história",
            description="Verifica por código, sem LLM: referências a trechos inexistentes, ids duplicados, citações "
                        "que divergem do trecho atual da fonte, campos obrigatórios, critérios sem evidência ou "
                        "citando trechos que não são evidência da história, e vínculos das versões. Não julga se o "
                        "trecho sustenta a afirmação (isso é a auditoria semântica).")
def validate_story_traceability(historia_id: int, uow: UnitOfWork = Depends(get_uow)):
    return TraceabilityService(uow).validate_story(historia_id)


@router.get("/projetos/{projeto_id}/validacao-estrutural", tags=["Rastreabilidade"],
            summary="Validação estrutural de todas as histórias ativas do projeto",
            description="Totais por tipo de problema; `itens` traz só as histórias com algum problema.")
def validate_project_traceability(projeto_id: int, uow: UnitOfWork = Depends(get_uow)):
    return TraceabilityService(uow).validate_project(projeto_id)


@router.post("/historias/{historia_id}/auditoria-evidencias", tags=["Rastreabilidade"],
             summary="Auditar semanticamente as evidências de uma história",
             description="Prompt VALIDACAO_EVIDENCIAS: cada componente (perfil, funcionalidade, benefício) e cada "
                         "critério é avaliado só pelos trechos vinculados a ele (COMPATIVEL, PARCIALMENTE_COMPATIVEL, "
                         "INCOMPATIVEL, INSUFICIENTE, SEM_EVIDENCIA). A história não é alterada; o resultado fica "
                         "registrado para revisão humana. Falha do LLM → 502.")
def audit_story_evidence(historia_id: int, uow: UnitOfWork = Depends(get_uow),
                         container: Container = Depends(get_container)):
    return EvidenceAuditService(uow, container).audit(historia_id)


@router.get("/historias/{historia_id}/auditorias-evidencias", tags=["Rastreabilidade"],
            summary="Histórico das auditorias de evidências de uma história (mais recente primeiro)")
def list_story_audits(historia_id: int, uow: UnitOfWork = Depends(get_uow)):
    return EvidenceAuditService(uow).list(historia_id)


@router.get("/auditorias-evidencias/{auditoria_id}", tags=["Rastreabilidade"], summary="Detalhar uma auditoria")
def get_audit(auditoria_id: int, uow: UnitOfWork = Depends(get_uow)):
    return EvidenceAuditService(uow).get(auditoria_id)


@router.post("/auditorias-evidencias/{auditoria_id}/revisoes", tags=["Rastreabilidade"],
             summary="Revisão humana de um item da auditoria",
             description="O analista confirma ou contesta o resultado do modelo para um componente ou critério. "
                         "Uma nova revisão do mesmo item substitui a anterior. A história não muda.")
def review_audit(auditoria_id: int, body: EvidenceAuditReview, uow: UnitOfWork = Depends(get_uow),
                 actor: str = Depends(get_actor)):
    return EvidenceAuditService(uow).review(auditoria_id, body.item, body.decisao, body.observacao, actor)


@router.post("/projetos/{projeto_id}/auditoria-evidencias", response_model=JobOut,
             status_code=status.HTTP_202_ACCEPTED, tags=["Rastreabilidade"],
             summary="Auditar as evidências de várias histórias (job auditoria_evidencias)")
def audit_project_evidence(
    projeto_id: int, background: BackgroundTasks, body: Optional[EvidenceAuditRequest] = None,
    uow: UnitOfWork = Depends(get_uow), container: Container = Depends(get_container), actor: str = Depends(get_actor),
):
    job = start_evidence_audit(uow, projeto_id, (body or EvidenceAuditRequest()).historia_ids, actor)
    uow.commit()
    background.add_task(tasks.run_evidence_audit, container, job["id"])
    return job


@router.get("/trechos/{trecho_id}", tags=["Rastreabilidade"], summary="Fonte original de um trecho",
            description="Arquivo, localização (página ou tempo, com link para o momento do áudio), texto atual e as "
                        "histórias e critérios que usam o trecho como evidência.")
def get_chunk(trecho_id: int, uow: UnitOfWork = Depends(get_uow)):
    return TraceabilityService(uow).chunk(trecho_id)


@router.get("/historias/{historia_id}/versoes", response_model=VersionList, tags=["Versões"],
            summary="Histórico de versões (US-20)")
def list_versions(historia_id: int, service: StoryService = Depends(_service)):
    return service.versions(historia_id)


@router.get("/historias/{historia_id}/versoes/comparacao", tags=["Versões"],
            summary="Comparar duas versões (US-20)",
            description="Sem parâmetros, compara a penúltima com a última. Traz as diferenças de campos e texto, os "
                        "critérios adicionados, removidos e mantidos (por id permanente), as evidências adicionadas "
                        "e as versões intermediárias com origem, responsável e fontes que motivaram cada mudança.")
def compare_versions(historia_id: int, de: Optional[int] = Query(default=None, ge=1),
                     para: Optional[int] = Query(default=None, ge=1), service: StoryService = Depends(_service)):
    return service.compare_versions(historia_id, de, para)


@router.get("/historias/{historia_id}/versoes/{numero}", response_model=VersionOut, tags=["Versões"],
            summary="Conteúdo de uma versão (US-20 CA02)")
def get_version(historia_id: int, numero: int, service: StoryService = Depends(_service)):
    return service.version(historia_id, numero)
