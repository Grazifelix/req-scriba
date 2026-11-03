"""Modelos base e utilitários dos schemas Pydantic."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)]


class InputModel(BaseModel):
    """Entradas: rejeitam campos desconhecidos (regra de segurança)."""

    model_config = ConfigDict(extra="forbid")


class OutputModel(BaseModel):
    """Saídas: documentam os campos principais e aceitam campos adicionais."""

    model_config = ConfigDict(extra="allow")


class ErrorBody(BaseModel):
    codigo: str
    mensagem: str
    detalhes: Any = None


class ErrorResponse(BaseModel):
    erro: ErrorBody


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Recurso não encontrado"},
    409: {"model": ErrorResponse, "description": "Conflito de estado"},
    422: {"model": ErrorResponse, "description": "Entrada inválida ou regra de negócio violada"},
    502: {"model": ErrorResponse, "description": "Falha em serviço externo (Ollama, ChromaDB, ASR)"},
}
