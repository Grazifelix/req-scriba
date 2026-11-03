"""Fixtures: container com SQLite temporário e integrações simuladas."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.container import Container
from app.db.database import Database
from app.integrations.embeddings import HashEmbedder
from app.integrations.storage import FileStorage
from app.integrations.vector_store import InMemoryVectorStore
from app.services.audios import AudioService, transcribe_audio
from app.services.documents import DocumentService, process_document
from app.services.projects import ProjectService
from tests.fakes import FakeLLM, FakeTranscriber

WAV_BYTES = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 64
DOC_TEXT = (
    "Escopo do sistema escolar.\n\nO boletim deve ser emitido em PDF ao final de cada bimestre.\n\n"
    "A secretaria realiza a matrícula dos alunos e mantém o cadastro atualizado."
)


@pytest.fixture
def container(tmp_path: Path) -> Container:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    db = Database(settings.db_path)
    db.initialize()
    return Container(
        settings=settings, db=db, storage=FileStorage(settings.upload_dir), llm=FakeLLM(),
        embedder=HashEmbedder(), vector_store=InMemoryVectorStore(), transcriber=FakeTranscriber(),
    )


@pytest.fixture
def project_id(container: Container) -> int:
    with container.uow() as uow:
        return ProjectService(uow, container).create("Escola", "Projeto de teste")["id"]


def upload_audio(container: Container, project_id: int, name: str = "reuniao.wav", data: bytes = WAV_BYTES) -> int:
    with container.uow() as uow:
        audio_id = AudioService(uow, container).upload(project_id, name, "audio/wav", io.BytesIO(data))["id"]
    transcribe_audio(container, audio_id)
    return audio_id


def upload_document(container: Container, project_id: int, text: str = DOC_TEXT, name: str = "escopo.txt") -> int:
    with container.uow() as uow:
        document_id = DocumentService(uow, container).upload(
            project_id, name, "text/plain", io.BytesIO(text.encode("utf-8"))
        )["id"]
    process_document(container, document_id)
    return document_id


@pytest.fixture
def audio_id(container: Container, project_id: int) -> int:
    return upload_audio(container, project_id)


@pytest.fixture
def document_id(container: Container, project_id: int) -> int:
    return upload_document(container, project_id)
