"""Schemas de qualidade, perguntas, sugestões de atualização e jobs."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from pydantic import Field, StringConstraints

from app.schemas.common import InputModel, LongText, OutputModel, ShortText, Title
from app.schemas.stories import AcceptanceCriterionIn, QualitySummary

QuestionText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


# -- Qualidade --------------------------------------------------------------
class EvaluationOut(OutputModel):
    id: int
    historia_id: int
    versao_historia: int
    qualidade_geral: QualitySummary
    avaliacao_criterios: dict[str, dict[str, Any]] = Field(description="8 critérios individuais QUS")
    analise_transversal_conjunto: dict[str, dict[str, Any]] = Field(description="5 critérios de conjunto QUS")
    lacunas: list[dict[str, Any]]
    reescrita_sugerida: Optional[str]
    criado_em: str


class EvaluationList(OutputModel):
    historia_id: int
    itens: list[EvaluationOut]
    total: int


class ComparisonOut(OutputModel):
    historia_id: int
    de: dict[str, Any]
    para: dict[str, Any]
    variacao_porcentagem: float
    criterios: list[dict[str, Any]]
    resolvidos: list[str]
    novos_defeitos: list[str]


class DefectOut(OutputModel):
    chave: str = Field(examples=["qus:atomic", "lacuna:0", "sem_evidencia:0"])
    tipo: str
    criterio: Optional[str]
    descricao: str
    acao_sugerida: Optional[str]


class DefectList(OutputModel):
    historia_id: int
    avaliacao_id: Optional[int]
    avaliacao_desatualizada: bool
    itens: list[DefectOut]
    total: int


class QualityOverviewOut(OutputModel):
    projeto_id: int
    qualidade_geral: dict[str, Any]
    distribuicao: dict[str, int]
    historias_com_problemas: int
    itens: list[dict[str, Any]]
    total: int


# -- Perguntas --------------------------------------------------------------
class QuestionGenerateRequest(InputModel):
    defeitos: Optional[list[str]] = Field(
        default=None, description="Chaves de defeitos (GET /historias/{id}/defeitos). Vazio = todos."
    )


class QuestionOut(OutputModel):
    id: int
    historia_id: int
    historia_codigo: Optional[str]
    defeito_chave: str
    tipo_defeito: str
    descricao_defeito: str
    pergunta: str
    justificativa: str
    status: str = Field(description="pendente | aprovada | rejeitada | respondida")
    status_rotulo: str
    disponivel_proxima_rodada: bool
    resposta: Optional[str]


class QuestionGenerateOut(OutputModel):
    historia_id: int
    perguntas: list[QuestionOut]
    total: int
    limitacoes: list[dict[str, Any]]


class QuestionList(OutputModel):
    itens: list[QuestionOut]
    total: int


class FollowupOut(OutputModel):
    projeto_id: int
    agrupar_por: str
    grupos: list[dict[str, Any]]
    total_grupos: int
    total_perguntas: int
    pendentes_de_resposta: int


class QuestionUpdate(InputModel):
    pergunta: QuestionText


class QuestionAnswer(InputModel):
    resposta: LongText
    resposta_tipo_fonte: Optional[Literal["audio", "documento"]] = Field(
        default=None, description="Fonte em que a resposta foi registrada (ex.: nova entrevista)"
    )
    resposta_fonte_id: Optional[int] = None


# -- Sugestões de atualização -----------------------------------------------
class AnalysisRequest(InputModel):
    audio_ids: list[int] = Field(default_factory=list, max_length=100)
    documento_ids: list[int] = Field(default_factory=list, max_length=100)


class SuggestionEdit(InputModel):
    titulo: Optional[Title] = None
    perfil: Optional[ShortText] = None
    funcionalidade: Optional[ShortText] = None
    beneficio: Optional[ShortText] = None
    criterios_aceitacao: Optional[list[AcceptanceCriterionIn]] = None


class SuggestionApprove(InputModel):
    conteudo_proposto: Optional[SuggestionEdit] = Field(
        default=None, description="Edição final opcional aplicada antes da aprovação"
    )


class SuggestionOut(OutputModel):
    id: int
    historia_id: Optional[int] = Field(description="Nulo quando é um possível novo requisito")
    tipo: str = Field(description="complementa | contradiz | novo_requisito")
    tipo_rotulo: str
    justificativa: str
    conteudo_atual: Optional[dict[str, Any]]
    conteudo_proposto: dict[str, Any]
    evidencias: list[dict[str, Any]]
    status: str
    status_rotulo: str
    editada: bool
    versao_aplicada: Optional[int]
    historia_criada_id: Optional[int]
    comparacao: Optional[dict[str, Any]] = None


class SuggestionList(OutputModel):
    itens: list[SuggestionOut]
    total: int


# -- Jobs -------------------------------------------------------------------
class JobOut(OutputModel):
    id: str
    projeto_id: int
    tipo: str
    tipo_rotulo: str
    status: str = Field(description="pendente | processando | concluido | erro")
    status_rotulo: str
    parametros: dict[str, Any]
    resultado: Optional[dict[str, Any]]
    mensagem_erro: Optional[str]
    url_status: str


class JobList(OutputModel):
    itens: list[JobOut]
    total: int
