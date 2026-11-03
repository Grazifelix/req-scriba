"""Schemas de Histórias de Usuário, evidências e versões."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import Field

from app.schemas.common import InputModel, LongText, OutputModel, ShortText, Title


class AcceptanceCriterionIn(InputModel):
    titulo: Optional[Title] = None
    criterio: LongText = Field(examples=["Dado que estou logado, quando clico em exportar, então recebo um PDF."])


class AcceptanceCriterionOut(OutputModel):
    id: str = Field(examples=["CA01"])
    titulo: str
    criterio: str


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


class GenerationRequest(InputModel):
    audio_ids: list[int] = Field(default_factory=list, max_length=100)
    documento_ids: list[int] = Field(default_factory=list, max_length=100)


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


class VersionOut(OutputModel):
    numero: int
    tipo_alteracao: str = Field(description="manual | automatica")
    origem_alteracao: str
    originada_por_ia: bool
    historia: Optional[str]
    conteudo: dict[str, Any]
    evidencia_ids: list[int]
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
