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
from app.services.analysis import AnalysisService
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
    # Em produção o job de geração só gera as US (critérios e auditoria sob demanda). Aqui as duas etapas ficam
    # ligadas porque muitos cenários usam o job para montar histórias já com critérios e auditoria; o padrão
    # desligado é coberto em test_services_criteria.py.
    settings = Settings(data_dir=tmp_path / "data", generation_criteria=True, generation_evidence_audit=True)
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


def ensure_approved_analysis(container: Container, project_id: int) -> int:
    """Pré-requisito da geração de histórias: uma análise das fontes aprovada (cria uma manual se não houver)."""
    with container.uow() as uow:
        existing = uow.analyses.latest_reviewed(project_id)
        if existing:
            return existing["id"]
        return AnalysisService(uow).create(project_id, {"sistema": "Sistema escolar", "revisada": True, "itens": [
            {"tipo": "usuario", "texto": "coordenador pedagógico"},
            {"tipo": "funcionalidade", "texto": "emitir o boletim da turma", "complemento": "coordenador pedagógico"},
        ]}, "ana")["id"]


@pytest.fixture
def audio_id(container: Container, project_id: int) -> int:
    return upload_audio(container, project_id)


@pytest.fixture
def document_id(container: Container, project_id: int) -> int:
    return upload_document(container, project_id)
