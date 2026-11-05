"""Container de dependências: monta e expõe as integrações usadas pelos serviços.

Testes constroem o container com fakes (sem rede); produção usa ``build_container(settings)``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from app.core.config import Settings
from app.db.database import Database
from app.db.unit_of_work import UnitOfWork
from app.integrations.embeddings import Embedder, HashEmbedder, RemoteEmbedder
from app.integrations.llm import LLMClient, is_local_url
from app.integrations.ollama import OllamaClient
from app.integrations.openai_compatible import OpenAICompatibleClient
from app.integrations.storage import FileStorage
from app.integrations.transcriber import FasterWhisperTranscriber, Transcriber
from app.integrations.vector_store import ChromaVectorStore, InMemoryVectorStore, VectorStore, collection_name

logger = logging.getLogger(__name__)

HASH_PROVIDER = "hash"
OLLAMA_PROVIDER = "ollama"
OPENAI_COMPATIBLE_PROVIDER = "openai_compatible"


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


def _build_client(
    settings: Settings, provider: str, base_url: str, api_key: str, variable_prefix: str
) -> OllamaClient | OpenAICompatibleClient:
    if provider == OLLAMA_PROVIDER:
        return OllamaClient(
            base_url or settings.ollama_url, settings.llm_model, settings.embedding_model,
            timeout=settings.llm_timeout, temperature=settings.llm_temperature, num_ctx=settings.llm_num_ctx,
        )
    if provider == OPENAI_COMPATIBLE_PROVIDER:
        if not base_url:
            raise ValueError(f"O provedor {provider} exige a variável REQSCRIBA_{variable_prefix}_BASE_URL.")
        return OpenAICompatibleClient(
            base_url, settings.llm_model, settings.embedding_model, api_key=api_key,
            timeout=settings.llm_timeout, temperature=settings.llm_temperature,
        )
    raise ValueError(
        f"Provedor '{provider}' desconhecido em REQSCRIBA_{variable_prefix}_PROVIDER. "
        f"Use {OLLAMA_PROVIDER} ou {OPENAI_COMPATIBLE_PROVIDER}."
    )


def _warn_if_remote(purpose: str, base_url: str) -> None:
    if not is_local_url(base_url):
        logger.warning(
            "%s usa o servidor %s, fora desta máquina: o conteúdo das fontes será enviado a ele.",
            purpose, urlparse(base_url).hostname,
        )


def build_llm(settings: Settings) -> LLMClient:
    client = _build_client(settings, settings.llm_provider, settings.llm_base_url, settings.llm_api_key, "LLM")
    _warn_if_remote("O LLM", client.base_url)
    return client


def build_embedder(settings: Settings) -> Embedder:
    provider = settings.embeddings_provider
    if provider == HASH_PROVIDER:
        return HashEmbedder()
    base_url, api_key = settings.embeddings_base_url, settings.embeddings_api_key
    if not base_url and provider == settings.llm_provider:
        # Mesmo provedor do LLM e sem URL própria. A chave só é herdada junto com a URL,
        # para nunca ser enviada a um servidor diferente.
        base_url, api_key = settings.llm_base_url, settings.llm_api_key
    client = _build_client(settings, provider, base_url, api_key, "EMBEDDINGS")
    _warn_if_remote("O provedor de embeddings", client.base_url)
    return RemoteEmbedder(client)


def embedding_fingerprint(settings: Settings) -> str:
    """Identifica o espaço vetorial em uso, separando os índices de modelos diferentes."""
    return HASH_PROVIDER if settings.embeddings_provider == HASH_PROVIDER else settings.embedding_model


def build_container(settings: Settings) -> Container:
    settings.ensure_dirs()
    db = Database(settings.db_path)
    db.initialize()
    vector_store: VectorStore = (
        InMemoryVectorStore()
        if settings.vector_store == "memory"
        else ChromaVectorStore(settings.chroma_dir, collection_name(embedding_fingerprint(settings)))
    )
    transcriber = FasterWhisperTranscriber(
        settings.whisper_model,
        settings.whisper_device,
        settings.whisper_compute_type,
        settings.transcription_language or None,
    )
    logger.info("Container pronto (dados em %s)", settings.data_dir.resolve())
    return Container(
        settings, db, FileStorage(settings.upload_dir), build_llm(settings), build_embedder(settings),
        vector_store, transcriber,
    )
