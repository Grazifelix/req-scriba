"""Schemas da análise das fontes e de seus itens."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import Field

from app.schemas.common import InputModel, OutputModel, ShortText

ItemType = Literal["participante", "usuario", "requisito_negocio", "funcionalidade", "fora_de_escopo"]


class AnalysisItemCreate(InputModel):
    tipo: ItemType = Field(description="participante | usuario | requisito_negocio | funcionalidade | fora_de_escopo")
    texto: ShortText = Field(description="Nome, papel ou descrição, conforme o tipo", examples=["cliente"])
    complemento: Optional[ShortText] = Field(
        default=None,
        description="Papel na reunião (participante), descrição (usuário), usuário (funcionalidade) ou motivo (fora de escopo)",
    )
    referencias: list[str] = Field(
        default_factory=list, max_length=30,
        description="Somente funcionalidades: códigos dos requisitos de negócio atendidos (ex.: RN1)",
    )
    trecho_ids: list[int] = Field(default_factory=list, max_length=30, description="Trechos das fontes que sustentam o item")


class AnalysisItemUpdate(InputModel):
    texto: Optional[ShortText] = None
    complemento: Optional[ShortText] = None
    referencias: Optional[list[str]] = Field(default=None, max_length=30)
    trecho_ids: Optional[list[int]] = Field(default=None, max_length=30, description="Substitui as evidências do item")


class AnalysisCreate(InputModel):
    sistema: Optional[ShortText] = Field(default=None, examples=["Pedidos por tablet na mesa do restaurante"])
    revisada: bool = False
    itens: list[AnalysisItemCreate] = Field(default_factory=list, max_length=200)


class AnalysisUpdate(InputModel):
    sistema: Optional[ShortText] = None
    revisada: Optional[bool] = Field(
        default=None, description="True: a análise passa a ser usada como contexto confirmado nas próximas gerações",
    )


class AnalysisEvidenceOut(OutputModel):
    trecho_id: int
    tipo_fonte: str
    fonte_id: int
    nome_arquivo: str
    texto: str
    localizacao: dict[str, Any]


class AnalysisItemOut(OutputModel):
    id: int
    analise_id: int
    tipo: str
    tipo_rotulo: str
    codigo: Optional[str] = Field(description="RN1, FN1… (requisitos de negócio e funcionalidades)")
    texto: str
    complemento: Optional[str]
    referencias: list[str]
    evidencias: list[AnalysisEvidenceOut]
    possui_evidencia: bool
    historia_ids: list[int] = Field(description="Histórias geradas a partir da funcionalidade")
    origem: str = Field(description="ia | manual")
    editado: bool


class AnalysisOut(OutputModel):
    id: int
    projeto_id: int
    job_id: Optional[str]
    origem: str = Field(description="ia | manual")
    sistema: Optional[str]
    fontes: list[dict[str, Any]]
    revisada: bool
    revisada_por: Optional[str]
    revisada_em: Optional[str]
    totais: dict[str, int]
    itens: list[AnalysisItemOut]
    criado_em: str
    atualizado_em: str


class AnalysisList(OutputModel):
    itens: list[AnalysisOut]
    total: int
