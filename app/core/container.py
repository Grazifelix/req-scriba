"""Container de dependências: monta e expõe as integrações usadas pelos serviços.

Testes constroem o container com fakes (sem rede); produção usa ``build_container(settings)``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.config import Settings
from app.db.database import Database
from app.db.unit_of_work import UnitOfWork
from app.integrations.embeddings import Embedder, HashEmbedder, OllamaEmbedder
from app.integrations.ollama import LLMClient, OllamaClient
from app.integrations.storage import FileStorage
from app.integrations.transcriber import FasterWhisperTranscriber, Transcriber
from app.integrations.vector_store import ChromaVectorStore, InMemoryVectorStore, VectorStore

logger = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    db: Database
    storage: FileStorage
    llm: LLMClient
    embedder: Embedder
    vector_store: VectorStore
    transcriber: Transcriber

    def uow(self) -> UnitOfWork:
        return UnitOfWork(self.db)


def build_container(settings: Settings) -> Container:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    db.initialize()
    ollama = OllamaClient(
        settings.ollama_url,
        settings.llm_model,
        settings.embedding_model,
        timeout=settings.ollama_timeout,
        temperature=settings.llm_temperature,
        num_ctx=settings.llm_num_ctx,
    )
    embedder: Embedder = HashEmbedder() if settings.embeddings_provider == "hash" else OllamaEmbedder(ollama)
    vector_store: VectorStore = (
        InMemoryVectorStore() if settings.vector_store == "memory" else ChromaVectorStore(settings.chroma_dir)
    )
    transcriber = FasterWhisperTranscriber(
        settings.whisper_model,
        settings.whisper_device,
        settings.whisper_compute_type,
        settings.transcription_language or None,
    )
    logger.info("Container pronto (dados em %s)", settings.data_dir.resolve())
    return Container(settings, db, FileStorage(settings.upload_dir), ollama, embedder, vector_store, transcriber)
