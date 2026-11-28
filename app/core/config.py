"""Configuração da aplicação lida de variáveis de ambiente com prefixo REQSCRIBA_."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # python-dotenv é opcional: carrega .env se estiver instalado
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - ambiente sem dotenv
    pass

PREFIX = "REQSCRIBA_"


def _env(name: str, default: str) -> str:
    return os.environ.get(PREFIX + name, default)


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name, "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "sim", "yes", "on")


@dataclass
class Settings:
    """Parâmetros de execução. Valores padrão documentados em docs/specs.md §13."""

    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "./data")))
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "ollama"))
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", ""))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY", ""), repr=False)
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "llama4:scout"))
    llm_timeout: int = field(default_factory=lambda: _env_int("LLM_TIMEOUT", _env_int("OLLAMA_TIMEOUT", 900)))
    llm_temperature: float = field(default_factory=lambda: _env_float("LLM_TEMPERATURE", 0.1))
    llm_num_ctx: int = field(default_factory=lambda: _env_int("LLM_NUM_CTX", 16384))  # só no Ollama
    # Saída estruturada: envia o JSON Schema do prompt ao provedor (Ollama `format`, OpenAI `json_schema`).
    llm_structured_output: bool = field(default_factory=lambda: _env_bool("LLM_STRUCTURED_OUTPUT", False))
    # Geração de critérios de aceitação: trechos relacionados (RAG) por história, além das evidências dela
    criteria_related_chunks: int = field(default_factory=lambda: _env_int("CRITERIA_RELATED_CHUNKS", 2))
    embeddings_provider: str = field(default_factory=lambda: _env("EMBEDDINGS_PROVIDER", "ollama"))
    embeddings_base_url: str = field(default_factory=lambda: _env("EMBEDDINGS_BASE_URL", ""))
    embeddings_api_key: str = field(default_factory=lambda: _env("EMBEDDINGS_API_KEY", ""), repr=False)
    embedding_model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", "bge-m3"))
    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://localhost:11434"))
    vector_store: str = field(default_factory=lambda: _env("VECTOR_STORE", "chroma"))
    whisper_model: str = field(default_factory=lambda: _env("WHISPER_MODEL", "small"))
    whisper_device: str = field(default_factory=lambda: _env("WHISPER_DEVICE", "cpu"))
    whisper_compute_type: str = field(default_factory=lambda: _env("WHISPER_COMPUTE_TYPE", "int8"))
    transcription_language: str = field(default_factory=lambda: _env("TRANSCRIPTION_LANGUAGE", "pt"))
    # faster_whisper (local, no processo da API) | openai_compatible (servidor HTTP, ex.: GPU via túnel SSH)
    transcription_provider: str = field(default_factory=lambda: _env("TRANSCRIPTION_PROVIDER", "faster_whisper"))
    transcription_base_url: str = field(default_factory=lambda: _env("TRANSCRIPTION_BASE_URL", ""))
    transcription_api_key: str = field(default_factory=lambda: _env("TRANSCRIPTION_API_KEY", ""), repr=False)
    transcription_timeout: int = field(default_factory=lambda: _env_int("TRANSCRIPTION_TIMEOUT", 1800))
    max_audio_mb: int = field(default_factory=lambda: _env_int("MAX_AUDIO_MB", 500))
    max_document_mb: int = field(default_factory=lambda: _env_int("MAX_DOCUMENT_MB", 20))
    chunk_size: int = field(default_factory=lambda: _env_int("CHUNK_SIZE", 1000))
    chunk_overlap: int = field(default_factory=lambda: _env_int("CHUNK_OVERLAP", 150))
    transcript_window_chars: int = field(default_factory=lambda: _env_int("TRANSCRIPT_WINDOW_CHARS", 800))
    generation_batch_chars: int = field(default_factory=lambda: _env_int("GENERATION_BATCH_CHARS", 6000))
    rag_top_k: int = field(default_factory=lambda: _env_int("RAG_TOP_K", 4))
    # Passadas extras da extração de HU (geração e novas fontes): contexto prévio e consolidação final
    generation_context_pass: bool = field(default_factory=lambda: _env_bool("GENERATION_CONTEXT_PASS", True))
    generation_context_chars: int = field(default_factory=lambda: _env_int("GENERATION_CONTEXT_CHARS", 1200))
    generation_consolidation: bool = field(default_factory=lambda: _env_bool("GENERATION_CONSOLIDATION", True))
    # Etapa de critérios de aceitação dentro do job de geração. Padrão desligado: o job gera só as US e os critérios
    # são gerados sob demanda na tela da história (ou em lote)
    generation_criteria: bool = field(default_factory=lambda: _env_bool("GENERATION_CRITERIA", False))
    # Auditoria semântica das evidências (VALIDACAO_EVIDENCIAS) ao fim do job de geração. Padrão desligado: sob demanda
    generation_evidence_audit: bool = field(default_factory=lambda: _env_bool("GENERATION_EVIDENCE_AUDIT", False))
    # Análise de novas fontes: histórias mais próximas por embeddings para cada trecho novo (posição no ranking),
    # máximo de histórias por chamada e evidências atuais enviadas por história
    impact_top_stories: int = field(default_factory=lambda: _env_int("IMPACT_TOP_STORIES", 3))
    impact_max_stories: int = field(default_factory=lambda: _env_int("IMPACT_MAX_STORIES", 12))
    impact_evidences_per_story: int = field(default_factory=lambda: _env_int("IMPACT_EVIDENCES_PER_STORY", 3))
    cors_origins: list[str] = field(
        default_factory=lambda: [
            o.strip()
            for o in _env("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")
            if o.strip()
        ]
    )
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "reqscriba.db"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.chroma_dir, self.upload_dir):
            path.mkdir(parents=True, exist_ok=True)
