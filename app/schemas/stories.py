"""Schemas de Histórias de Usuário, evidências e versões."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import Field

from app.schemas.common import InputModel, LongText, OutputModel, ShortText, Title


class AcceptanceCriterionIn(InputModel):
    titulo: Optional[Title] = None
    criterio: LongText = Field(examples=["Dado que estou logado, quando clico em exportar, então recebo um PDF."])


class CriterionSourceOut(OutputModel):
    trecho_id: int = Field(description="Id permanente do trecho da fonte (trechos_fonte.id)")
    evidencia_id: Optional[int] = Field(default=None, description="Evidência da história que copia o trecho")
    afirmacoes_sustentadas: list[str] = Field(description="Partes do critério sustentadas: dado | quando | entao")


class AcceptanceCriterionOut(OutputModel):
    id: str = Field(examples=["CA01"], description="Código pela posição na lista (muda se a ordem mudar)")
    criterio_id: Optional[int] = Field(default=None, description="Id permanente do critério")
    versao_historia: Optional[int] = Field(default=None, description="Versão da história em que o critério entrou")
    status: Optional[str] = Field(default=None, description="vigente | substituido")
    situacao: Optional[str] = Field(
        default=None, description="valido | desatualizado (a história mudou depois do critério; confirme ou regenere)"
    )
    validado_na_versao: Optional[int] = None
    desatualizado_na_versao: Optional[int] = None
    motivo_situacao: Optional[str] = None
    titulo: str
    criterio: str = Field(description="Texto completo 'Dado …, quando …, então …'")
    dado: Optional[str] = Field(default=None, description="Presente nos critérios gerados pela IA")
    quando: Optional[str] = None
    entao: Optional[str] = None
    fontes: list[CriterionSourceOut] = Field(default_factory=list, description="Trechos que sustentam o critério")
    informacoes_ausentes: list[dict[str, Any]] = Field(default_factory=list)
    afirmacoes_sem_evidencia: list[str] = Field(default_factory=list)
    origem: Optional[str] = Field(default=None, description="'ia' quando gerado por GERACAO_CRITERIOS_ACEITACAO")


class StoryCreate(InputModel):
    titulo: Optional[Title] = None
    perfil: Optional[ShortText] = Field(default=None, examples=["coordenador pedagógico"])
    funcionalidade: ShortText = Field(examples=["emitir o boletim da turma"])
    beneficio: Optional[ShortText] = Field(default=None, examples=["acompanhar o desempenho dos alunos"])
    criterios_aceitacao: list[AcceptanceCriterionIn] = Field(default_factory=list, max_length=15)


class StoryUpdate(InputModel):
    titulo: Optional[Title] = None
    perfil: Optional[ShortText] = None
    funcionalidade: Optional[ShortText] = None
    beneficio: Optional[ShortText] = None
    historia: Optional[LongText] = Field(
        default=None, description="Texto completo no padrão 'Como …, quero …, para …' (alternativa aos campos)"
    )
    criterios_aceitacao: Optional[list[AcceptanceCriterionIn]] = Field(default=None, max_length=15)


class RewriteApply(InputModel):
    historia: LongText = Field(
        description="Reescrita sugerida pela avaliação. Para dividir a história, use 'H1: Como …; H2: Como …'.",
        examples=["H1: Como professor, quero lançar notas, para registrar o desempenho; "
                  "H2: Como professor, quero corrigir notas, para manter o boletim correto"],
    )


class GenerationRequest(InputModel):
    audio_ids: list[int] = Field(default_factory=list, max_length=100)
    documento_ids: list[int] = Field(default_factory=list, max_length=100)
    analise_id: Optional[int] = Field(
        default=None, description="Análise aprovada a usar; sem valor, usa a aprovada mais recente do projeto."
    )


class CriteriaRegenerationRequest(InputModel):
    escopo: Literal["auto", "desatualizados", "todos"] = Field(
        default="auto", description="auto: os desatualizados, ou todos se não houver nenhum"
    )
    criterio_ids: list[int] = Field(
        default_factory=list, max_length=50, description="Regera só estes critérios (os demais são mantidos)"
    )


class CriteriaGenerationRequest(InputModel):
    historia_ids: list[int] = Field(
        default_factory=list, max_length=500,
        description="Histórias a processar; vazio = todas as ativas não aprovadas do projeto.",
    )
    substituir: bool = Field(
        default=False, description="Também regera histórias com critérios válidos (sem isso, entram só as sem "
                                   "critérios e as com critérios desatualizados). A versão anterior é preservada."
    )


class EvidenceAuditRequest(InputModel):
    historia_ids: list[int] = Field(
        default_factory=list, max_length=500, description="Histórias a auditar; vazio = todas as ativas do projeto."
    )


class EvidenceAuditReview(InputModel):
    item: str = Field(max_length=60, examples=["componente:funcionalidade", "criterio:12"],
                      description="'componente:<perfil|funcionalidade|beneficio>' ou 'criterio:<criterio_id>'")
    decisao: Literal["confirma", "contesta"] = Field(description="O analista confirma ou contesta o resultado do modelo")
    observacao: Optional[LongText] = None


class QualitySummary(OutputModel):
    porcentagem: float
    status: str
    criterios_atendidos: Optional[int]
    total_criterios: int


class StorySummaryOut(OutputModel):
    id: int
    projeto_id: int
    codigo: str
    titulo: str
    historia: str
    status: str = Field(description="em_revisao | falta_informacao | aprovada | desaprovada")
    status_rotulo: str
    validada: bool = Field(description="True apenas para histórias aprovadas por um humano")
    qualidade_geral: Optional[QualitySummary]
    reescrita_sugerida: Optional[str] = Field(
        default=None, description="Reescrita sugerida pela avaliação de qualidade mais recente, se houver"
    )
    versao_atual: int
    origem: str


class StoryList(OutputModel):
    itens: list[StorySummaryOut]
    total: int


class EvidenceOut(OutputModel):
    evidencia_id: int
    tipo_fonte: str
    nome_arquivo: str
    trecho: str
    origem: str = Field(description="extracao | atualizacao")
    trecho_id: Optional[int] = Field(default=None, description="Trecho da fonte original (GET /trechos/{trecho_id})")
    afirmacao: Optional[str] = None
    afirmacoes_sustentadas: list[str] = Field(
        default_factory=list, description="Partes da história que o trecho sustenta: perfil | funcionalidade | beneficio"
    )


class VersionOut(OutputModel):
    numero: int
    tipo_alteracao: str = Field(description="manual | automatica")
    origem_alteracao: str
    originada_por_ia: bool
    historia: Optional[str]
    conteudo: dict[str, Any]
    evidencia_ids: list[int]
    criterio_ids: list[int] = Field(default_factory=list, description="Critérios vigentes nesta versão")
    fontes: list[dict[str, Any]] = Field(
        default_factory=list, description="Fontes que motivaram a versão: as das evidências vinculadas desde a anterior"
    )
    status_historia: Optional[str] = Field(default=None, description="Status da história quando a versão foi criada")
    responsavel: str
    data_hora: str


class StoryOut(StorySummaryOut):
    perfil: Optional[str]
    funcionalidade: Optional[str]
    beneficio: Optional[str]
    criterios_aceitacao: list[AcceptanceCriterionOut]
    fontes_audio: list[EvidenceOut]
    fontes_documentos: list[EvidenceOut]
    possui_suporte_documental: bool
    afirmacoes_sem_evidencia: list[str]
    informacoes_ausentes: list[dict[str, Any]]
    criterios_qus: Optional[dict[str, Any]]
    sugestao_scriba: Optional[str]
    atualizacoes_pendentes: int
    versoes: list[VersionOut]
    auditoria_evidencias: Optional[dict[str, Any]] = Field(
        default=None, description="Auditoria semântica mais recente das evidências (componentes, critérios, resumo)"
    )


class EvidenceList(OutputModel):
    historia_id: int
    versao: Optional[int]
    fontes_audio: list[EvidenceOut]
    fontes_documentos: list[EvidenceOut]
    possui_suporte_documental: bool
    itens: list[EvidenceOut]
    total: int


class TraceabilityOut(OutputModel):
    historia_id: int
    codigo: str
    versao_atual: int
    possui_suporte_documental: bool
    versoes: list[dict[str, Any]]
    navegacao: list[dict[str, Any]]
    total_evidencias: int


class VersionList(OutputModel):
    historia_id: int
    itens: list[VersionOut]
    total: int


class RewriteResult(OutputModel):
    historia: StoryOut = Field(description="A história atual, com o texto da primeira parte.")
    historias_criadas: list[StoryOut] = Field(description="Histórias novas, uma para cada parte além da primeira.")
