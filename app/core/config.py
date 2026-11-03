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


@dataclass
class Settings:
    """Parâmetros de execução. Valores padrão documentados em docs/specs.md §13."""

    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "./data")))
    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://localhost:11434"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "llama3.2:3b"))
    embedding_model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", "bge-m3"))
    ollama_timeout: int = field(default_factory=lambda: _env_int("OLLAMA_TIMEOUT", 300))
    llm_temperature: float = field(default_factory=lambda: _env_float("LLM_TEMPERATURE", 0.1))
    llm_num_ctx: int = field(default_factory=lambda: _env_int("LLM_NUM_CTX", 8192))
    embeddings_provider: str = field(default_factory=lambda: _env("EMBEDDINGS_PROVIDER", "ollama"))
    vector_store: str = field(default_factory=lambda: _env("VECTOR_STORE", "chroma"))
    whisper_model: str = field(default_factory=lambda: _env("WHISPER_MODEL", "small"))
    whisper_device: str = field(default_factory=lambda: _env("WHISPER_DEVICE", "cpu"))
    whisper_compute_type: str = field(default_factory=lambda: _env("WHISPER_COMPUTE_TYPE", "int8"))
    transcription_language: str = field(default_factory=lambda: _env("TRANSCRIPTION_LANGUAGE", "pt"))
    max_audio_mb: int = field(default_factory=lambda: _env_int("MAX_AUDIO_MB", 200))
    max_document_mb: int = field(default_factory=lambda: _env_int("MAX_DOCUMENT_MB", 20))
    chunk_size: int = field(default_factory=lambda: _env_int("CHUNK_SIZE", 1000))
    chunk_overlap: int = field(default_factory=lambda: _env_int("CHUNK_OVERLAP", 150))
    transcript_window_chars: int = field(default_factory=lambda: _env_int("TRANSCRIPT_WINDOW_CHARS", 800))
    generation_batch_chars: int = field(default_factory=lambda: _env_int("GENERATION_BATCH_CHARS", 6000))
    rag_top_k: int = field(default_factory=lambda: _env_int("RAG_TOP_K", 4))
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
