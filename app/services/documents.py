"""Documentos do cliente (US-04): upload, extração de texto, chunking e indexação."""

from __future__ import annotations

import logging
from typing import BinaryIO

from app.core.container import Container
from app.core.errors import AppError, ConflictError
from app.core.time import utc_now_iso
from app.db.unit_of_work import UnitOfWork
from app.domain.enums import SourceStatus, SourceType
from app.integrations.extractors import extract_pages
from app.integrations.storage import DOCUMENT_EXTENSIONS
from app.services import presenters
from app.services.common import require_document, require_project
from app.services.indexing import index_document, remove_source_chunks

logger = logging.getLogger(__name__)


class DocumentService:
    def __init__(self, uow: UnitOfWork, container: Container) -> None:
        self.uow = uow
        self.container = container

    def _present(self, document: dict) -> dict:
        chunks = self.uow.chunks.list_by_source(SourceType.DOCUMENT.value, document["id"])
        return presenters.document(document, len(chunks))

    def upload(self, project_id: int, filename: str, content_type: str | None, stream: BinaryIO) -> dict:
        require_project(self.uow, project_id)
        stored = self.container.storage.save(
            project_id, filename, stream, DOCUMENT_EXTENSIONS, self.container.settings.max_document_mb * 1024 * 1024
        )
        now = utc_now_iso()
        document_id = self.uow.documents.insert({
            "projeto_id": project_id, "nome_arquivo": stored.original_name, "caminho_arquivo": str(stored.path),
            "tipo_conteudo": stored.media_type, "tamanho_bytes": stored.size_bytes,
            "status": SourceStatus.PENDING.value, "criado_em": now, "atualizado_em": now,
        })
        return self.get(document_id)

    def list(self, project_id: int) -> dict:
        require_project(self.uow, project_id)
        return presenters.listing([self._present(d) for d in self.uow.documents.list_by_project(project_id)])

    def get(self, document_id: int) -> dict:
        return self._present(require_document(self.uow, document_id))

    def content(self, document_id: int) -> dict:
        """US-04 CA02: texto extraído e trechos indexados."""
        document = require_document(self.uow, document_id)
        chunks = self.uow.chunks.list_by_source(SourceType.DOCUMENT.value, document_id)
        return {
            "documento_id": document_id, "nome_arquivo": document["nome_arquivo"], "status": document["status"],
            "status_rotulo": presenters.document(document, len(chunks))["status_rotulo"],
            "mensagem_erro": document["mensagem_erro"], "texto_extraido": document["texto_extraido"] or "",
            "trechos": [presenters.chunk(c) for c in chunks], "total_trechos": len(chunks),
        }

    def retry(self, document_id: int) -> dict:
        document = require_document(self.uow, document_id)
        if document["status"] in (SourceStatus.PENDING.value, SourceStatus.PROCESSING.value):
            raise ConflictError("O documento já está aguardando ou em processamento.")
        self.uow.documents.update(document_id, {
            "status": SourceStatus.PENDING.value, "mensagem_erro": None, "atualizado_em": utc_now_iso()
        })
        return self.get(document_id)

    def delete(self, document_id: int) -> None:
        document = require_document(self.uow, document_id)
        remove_source_chunks(self.uow, self.container, SourceType.DOCUMENT.value, document_id)
        self.uow.documents.delete(document_id)
        self.container.storage.remove(document["caminho_arquivo"])


def process_document(container: Container, document_id: int) -> None:
    """Tarefa de background: extrai texto, divide em trechos e indexa (US-04 CA02/CA03)."""
    with container.uow() as uow:
        document = uow.documents.get(document_id)
        if not document:
            return
        uow.documents.update(document_id, {"status": SourceStatus.PROCESSING.value, "atualizado_em": utc_now_iso()})
    try:
        pages = extract_pages(container.storage.resolve(document["caminho_arquivo"]))
        with container.uow() as uow:
            index_document(uow, container, document, pages)
            now = utc_now_iso()
            uow.documents.update(document_id, {
                "status": SourceStatus.COMPLETED.value, "mensagem_erro": None,
                "texto_extraido": "\n\n".join(p.text for p in pages),
                "total_paginas": sum(1 for p in pages if p.number is not None) or None,
                "processado_em": now, "atualizado_em": now,
            })
    except AppError as exc:
        _mark_error(container, document_id, exc.message)
    except Exception as exc:
        logger.exception("Falha inesperada ao processar documento %s", document_id)
        _mark_error(container, document_id, f"Erro inesperado no processamento: {type(exc).__name__}")


def _mark_error(container: Container, document_id: int, message: str) -> None:
    """CA03: falha informada; trechos removidos para que nada inexistente seja usado na geração."""
    logger.warning("Documento %s com erro: %s", document_id, message)
    with container.uow() as uow:
        remove_source_chunks(uow, container, SourceType.DOCUMENT.value, document_id)
        uow.documents.update(document_id, {
            "status": SourceStatus.ERROR.value, "mensagem_erro": message, "texto_extraido": None,
            "atualizado_em": utc_now_iso(),
        })
