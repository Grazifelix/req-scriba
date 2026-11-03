"""US-01..US-04: fontes, transcrição, revisão e documentos."""

import io

import pytest

from app.core.errors import BusinessValidationError, ConflictError, ExternalServiceError, NotFoundError
from app.services.audios import AudioService, transcribe_audio
from app.services.documents import DocumentService
from app.services.projects import ProjectService
from app.services.retrieval import search
from tests.conftest import WAV_BYTES, upload_document


def test_upload_audio_associates_project_and_starts_pending(container, project_id):
    with container.uow() as uow:
        audio = AudioService(uow, container).upload(project_id, "reuniao.wav", "audio/wav", io.BytesIO(WAV_BYTES))
    assert audio["projeto_id"] == project_id and audio["status"] == "pendente"  # US-01 CA01/CA03
    assert audio["status_rotulo"] == "Aguardando transcrição"


def test_upload_invalid_audio_reports_reason(container, project_id):
    with container.uow() as uow, pytest.raises(BusinessValidationError, match="não suportado"):
        AudioService(uow, container).upload(project_id, "planilha.xlsx", None, io.BytesIO(b"PK"))  # US-01 CA02


def test_upload_to_missing_project(container):
    with container.uow() as uow, pytest.raises(NotFoundError):
        AudioService(uow, container).upload(999, "a.wav", None, io.BytesIO(WAV_BYTES))


def test_transcription_produces_timed_segments(container, audio_id):
    with container.uow() as uow:
        service = AudioService(uow, container)
        audio = service.get(audio_id)
        transcript = service.transcription(audio_id)
    assert audio["status"] == "concluido" and audio["status_rotulo"] == "Transcrito"  # US-01 CA03
    assert audio["duracao_segundos"] == 14.0
    assert transcript["total_segmentos"] == 3  # US-02 CA01/CA02
    first = transcript["segmentos"][0]
    assert first["inicio_segundos"] == 0.0 and first["fim_formatado"] == "00:00:05"
    assert first["url_momento"].endswith(f"/audios/{audio_id}/arquivo#t=0.0")  # US-03 CA04
    assert "boletim" in transcript["texto_completo"]


def test_transcription_failure_is_flagged_and_retryable(container, project_id):
    container.transcriber.error = ExternalServiceError("Mecanismo de transcrição indisponível")
    with container.uow() as uow:
        audio_id = AudioService(uow, container).upload(project_id, "a.wav", None, io.BytesIO(WAV_BYTES))["id"]
    transcribe_audio(container, audio_id)
    with container.uow() as uow:
        audio = AudioService(uow, container).get(audio_id)
    assert audio["status"] == "erro" and "indisponível" in audio["mensagem_erro"]  # US-02 CA03
    assert audio["pode_reprocessar"]
    container.transcriber.error = None
    with container.uow() as uow:
        assert AudioService(uow, container).retry(audio_id)["status"] == "pendente"
    transcribe_audio(container, audio_id)
    with container.uow() as uow:
        assert AudioService(uow, container).get(audio_id)["status"] == "concluido"


def test_unexpected_transcription_error_is_persisted(container, project_id):
    container.transcriber.error = RuntimeError("bug")
    with container.uow() as uow:
        audio_id = AudioService(uow, container).upload(project_id, "a.wav", None, io.BytesIO(WAV_BYTES))["id"]
    transcribe_audio(container, audio_id)
    with container.uow() as uow:
        assert "RuntimeError" in AudioService(uow, container).get(audio_id)["mensagem_erro"]


def test_retry_while_pending_is_conflict(container, project_id):
    with container.uow() as uow:
        service = AudioService(uow, container)
        audio_id = service.upload(project_id, "a.wav", None, io.BytesIO(WAV_BYTES))["id"]
        with pytest.raises(ConflictError):
            service.retry(audio_id)


def test_search_transcription_ignores_accents(container, audio_id):
    with container.uow() as uow:
        result = AudioService(uow, container).search(audio_id, "ALUNOS")  # US-03 CA02
        assert result["total"] == 2
        assert AudioService(uow, container).search(audio_id, "inexistente")["total"] == 0


def test_segment_correction_feeds_next_extraction(container, project_id, audio_id):
    with container.uow() as uow:
        service = AudioService(uow, container)
        segment_id = service.transcription(audio_id)["segmentos"][0]["id"]
        updated = service.update_segment(audio_id, segment_id, "Precisamos emitir o histórico escolar.")
    assert updated["editado"] and updated["texto_original"].startswith("Precisamos emitir o boletim")
    with container.uow() as uow:
        chunks = uow.chunks.list_by_source("audio", audio_id)
    assert "histórico escolar" in chunks[0]["texto"] and "boletim" not in chunks[0]["texto"]  # US-03 CA03
    from app.services.generation import build_batches

    with container.uow() as uow:
        batches = build_batches(uow, container, project_id, [audio_id], [])
    assert "histórico escolar" in batches[0][0]["texto"]


def test_segment_from_other_audio_is_not_found(container, project_id, audio_id):
    with container.uow() as uow, pytest.raises(NotFoundError):
        AudioService(uow, container).update_segment(audio_id, 9999, "x")


def test_document_is_extracted_chunked_and_indexed(container, document_id):
    with container.uow() as uow:
        service = DocumentService(uow, container)
        document = service.get(document_id)
        content = service.content(document_id)
    assert document["status"] == "concluido" and document["total_trechos"] >= 1  # US-04 CA01/CA02
    assert "boletim deve ser emitido em PDF" in content["texto_extraido"]
    chunk = content["trechos"][0]
    assert chunk["localizacao"]["inicio_caractere"] == 0
    assert len(container.vector_store.items) == document["total_trechos"]


def test_document_without_text_fails_and_is_not_used(container, project_id):
    document_id = upload_document(container, project_id, text="   \n  ", name="vazio.txt")
    with container.uow() as uow:
        document = DocumentService(uow, container).get(document_id)
        assert document["status"] == "erro" and "não possui texto extraível" in document["mensagem_erro"]  # US-04 CA03
        assert uow.chunks.list_by_source("documento", document_id) == []
    from app.services.generation import start_generation

    with container.uow() as uow, pytest.raises(BusinessValidationError) as exc:
        start_generation(uow, project_id, [], [document_id], "ana")
    assert exc.value.details["fontes"][0]["fonte"] == f"documento:{document_id}"


def test_semantic_search_returns_relevant_chunks(container, project_id, audio_id, document_id):
    with container.uow() as uow:
        result = search(uow, container, project_id, "boletim PDF bimestre", 3)
    assert result["total"] >= 1
    assert all(item["relevancia"] >= 0 for item in result["itens"])
    assert any("boletim" in item["texto"] for item in result["itens"])


def test_project_counts_and_delete_cleans_everything(container, project_id, audio_id, document_id):
    with container.uow() as uow:
        project = ProjectService(uow, container).get(project_id)
        path = uow.audios.get(audio_id)["caminho_arquivo"]
    assert project["total_audios"] == 1 and project["total_documentos"] == 1
    assert project["qualidade_geral"]["status"] == "Sem avaliação"
    with container.uow() as uow:
        ProjectService(uow, container).delete(project_id)
    assert container.vector_store.items == {}
    from pathlib import Path

    assert not Path(path).exists()
    with container.uow() as uow, pytest.raises(NotFoundError):
        ProjectService(uow, container).get(project_id)
