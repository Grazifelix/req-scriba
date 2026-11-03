"""Hierarquia de erros de aplicação. Convertidos em JSON por app/main.py."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code = 400
    code = "erro"

    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"erro": {"codigo": self.code, "mensagem": self.message, "detalhes": self.details}}


class NotFoundError(AppError):
    status_code = 404
    code = "nao_encontrado"

    @classmethod
    def for_entity(cls, entity: str, entity_id: Any) -> "NotFoundError":
        return cls(f"{entity} {entity_id} não encontrado(a).", {"recurso": entity, "id": entity_id})


class BusinessValidationError(AppError):
    status_code = 422
    code = "validacao"


class ConflictError(AppError):
    status_code = 409
    code = "conflito"


class ExternalServiceError(AppError):
    """Falha em serviço externo (Ollama, ChromaDB, mecanismo de transcrição)."""

    status_code = 502
    code = "servico_externo"
