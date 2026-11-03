"""Valores de status e tipos do domínio, com rótulos legíveis (acessibilidade/UX)."""

from __future__ import annotations

from enum import Enum


class LabeledEnum(str, Enum):
    """Enum de string com rótulo legível em português."""

    @property
    def label(self) -> str:
        return LABELS[self]  # type: ignore[index]


class SourceStatus(LabeledEnum):
    PENDING = "pendente"
    PROCESSING = "processando"
    COMPLETED = "concluido"
    ERROR = "erro"


class StoryStatus(LabeledEnum):
    IN_REVIEW = "em_revisao"
    MISSING_INFO = "falta_informacao"
    APPROVED = "aprovada"
    REJECTED = "desaprovada"


class QuestionStatus(LabeledEnum):
    PENDING = "pendente"
    APPROVED = "aprovada"
    REJECTED = "rejeitada"
    ANSWERED = "respondida"


class SuggestionStatus(LabeledEnum):
    PENDING = "pendente"
    APPROVED = "aprovada"
    REJECTED = "rejeitada"


class SuggestionType(LabeledEnum):
    COMPLEMENTS = "complementa"
    CONTRADICTS = "contradiz"
    NEW_REQUIREMENT = "novo_requisito"


class JobStatus(LabeledEnum):
    PENDING = "pendente"
    PROCESSING = "processando"
    COMPLETED = "concluido"
    ERROR = "erro"


class JobType(LabeledEnum):
    STORY_GENERATION = "geracao_historias"
    UPDATE_ANALYSIS = "analise_atualizacao"


class SourceType(LabeledEnum):
    AUDIO = "audio"
    DOCUMENT = "documento"


class ChangeType(LabeledEnum):
    MANUAL = "manual"
    AUTOMATIC = "automatica"


class ChangeOrigin(LabeledEnum):
    MANUAL_CREATION = "criacao_manual"
    AI_GENERATION = "geracao_ia"
    MANUAL_EDIT = "edicao_manual"
    AI_SUGGESTION = "sugestao_ia"
    AI_SUGGESTION_EDITED = "sugestao_ia_editada"


AUDIO_STATUS_LABELS = {
    SourceStatus.PENDING: "Aguardando transcrição",
    SourceStatus.PROCESSING: "Transcrição em andamento",
    SourceStatus.COMPLETED: "Transcrito",
    SourceStatus.ERROR: "Erro na transcrição",
}

LABELS: dict[Enum, str] = {
    SourceStatus.PENDING: "Pendente",
    SourceStatus.PROCESSING: "Processando",
    SourceStatus.COMPLETED: "Concluído",
    SourceStatus.ERROR: "Erro",
    StoryStatus.IN_REVIEW: "Em revisão",
    StoryStatus.MISSING_INFO: "Falta informação",
    StoryStatus.APPROVED: "Aprovada",
    StoryStatus.REJECTED: "Desaprovada",
    QuestionStatus.PENDING: "Pendente",
    QuestionStatus.APPROVED: "Aprovada",
    QuestionStatus.REJECTED: "Rejeitada",
    QuestionStatus.ANSWERED: "Respondida",
    SuggestionStatus.PENDING: "Pendente",
    SuggestionStatus.APPROVED: "Aprovada",
    SuggestionStatus.REJECTED: "Rejeitada",
    SuggestionType.COMPLEMENTS: "Complementa história existente",
    SuggestionType.CONTRADICTS: "Contradiz história existente",
    SuggestionType.NEW_REQUIREMENT: "Possível novo requisito",
    JobStatus.PENDING: "Na fila",
    JobStatus.PROCESSING: "Em processamento",
    JobStatus.COMPLETED: "Concluído",
    JobStatus.ERROR: "Erro",
    JobType.STORY_GENERATION: "Geração de histórias",
    JobType.UPDATE_ANALYSIS: "Análise de novas fontes",
    SourceType.AUDIO: "Áudio",
    SourceType.DOCUMENT: "Documento",
    ChangeType.MANUAL: "Manual",
    ChangeType.AUTOMATIC: "Automática",
    ChangeOrigin.MANUAL_CREATION: "Criação manual",
    ChangeOrigin.AI_GENERATION: "Gerada pela IA",
    ChangeOrigin.MANUAL_EDIT: "Edição manual",
    ChangeOrigin.AI_SUGGESTION: "Sugestão da IA aplicada",
    ChangeOrigin.AI_SUGGESTION_EDITED: "Sugestão da IA editada e aplicada",
}

ALL_LABELED_ENUMS = (
    SourceStatus, StoryStatus, QuestionStatus, SuggestionStatus, SuggestionType,
    JobStatus, JobType, SourceType, ChangeType, ChangeOrigin,
)


def label_of(enum_cls: type[LabeledEnum], value: str | None) -> str | None:
    if value is None:
        return None
    return enum_cls(value).label
