"""Schemas de projetos."""

from __future__ import annotations

from typing import Optional

from pydantic import Field

from app.schemas.common import Description, InputModel, OutputModel, Title


class ProjectCreate(InputModel):
    titulo: Title = Field(examples=["Sistema de Gestão Escolar"])
    descricao: Optional[Description] = Field(default=None, examples=["Elicitação com a secretaria da escola."])


class ProjectUpdate(InputModel):
    titulo: Optional[Title] = None
    descricao: Optional[Description] = None


class OverallQuality(OutputModel):
    porcentagem: Optional[float] = Field(description="Média das porcentagens QUS das histórias avaliadas")
    status: str = Field(description="Péssima, Baixa, Regular, Alta, Excelente ou Sem avaliação")
    historias_avaliadas: int


class ProjectOut(OutputModel):
    id: int
    titulo: str
    descricao: Optional[str]
    total_audios: int
    total_documentos: int
    total_historias_prontas: int = Field(description="Histórias aprovadas")
    total_historias_pendentes: int = Field(description="Histórias em revisão ou com falta de informação")
    total_historias_desaprovadas: int
    qualidade_geral: OverallQuality
    backlog: list[int] = Field(description="Ids das histórias não desaprovadas")
    backlog_aprovado: list[int] = Field(description="Ids das histórias aprovadas")
    criado_em: str
    atualizado_em: str


class ProjectList(OutputModel):
    itens: list[ProjectOut]
    total: int
