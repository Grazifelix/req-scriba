"""Rotas de áudios/transcrições (US-01..03) e documentos (US-04)."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, Response, UploadFile, status
from fastapi.responses import FileResponse

from app.api.deps import get_container, get_uow
from app.core.container import Container
from app.db.unit_of_work import UnitOfWork
from app.integrations.storage import sanitize_filename
from app.schemas.common import ERROR_RESPONSES
from app.schemas.sources import (
    AudioList, AudioOut, DocumentContentOut, DocumentList, DocumentOut, SegmentOut, SegmentSearchOut,
    SegmentUpdate, TranscriptionOut,
)
from app.services.audios import AudioService
from app.services.documents import DocumentService
from app.workers import tasks

router = APIRouter(responses=ERROR_RESPONSES)


def _audios(uow: UnitOfWork = Depends(get_uow), container: Container = Depends(get_container)) -> AudioService:
    return AudioService(uow, container)


def _documents(uow: UnitOfWork = Depends(get_uow), container: Container = Depends(get_container)) -> DocumentService:
    return DocumentService(uow, container)


# -- Áudios -----------------------------------------------------------------
@router.post("/projetos/{projeto_id}/audios", response_model=AudioOut, status_code=status.HTTP_202_ACCEPTED,
             tags=["Áudios"], summary="Enviar gravação (US-01)",
             description="Aceita .mp3, .wav, .m4a, .ogg, .flac, .webm e .mp4. A transcrição roda em segundo plano; "
                         "acompanhe o campo `status` em GET /audios/{id}.")
def upload_audio(
    projeto_id: int, background: BackgroundTasks, arquivo: UploadFile = File(...),
    service: AudioService = Depends(_audios),
):
    audio = service.upload(projeto_id, arquivo.filename or "", arquivo.content_type, arquivo.file)
    service.uow.commit()
    background.add_task(tasks.transcribe_audio, service.container, audio["id"])
    return audio


@router.get("/projetos/{projeto_id}/audios", response_model=AudioList, tags=["Áudios"], summary="Listar áudios")
def list_audios(projeto_id: int, service: AudioService = Depends(_audios)):
    return service.list(projeto_id)


@router.get("/audios/{audio_id}", response_model=AudioOut, tags=["Áudios"], summary="Detalhar áudio e estado")
def get_audio(audio_id: int, service: AudioService = Depends(_audios)):
    return service.get(audio_id)


@router.get("/audios/{audio_id}/arquivo", tags=["Áudios"], summary="Baixar/reproduzir a gravação (US-03 CA04)",
            description="Suporta requisições Range. Use o fragmento `#t=<segundos>` para posicionar o player.",
            response_class=FileResponse)
def download_audio(audio_id: int, service: AudioService = Depends(_audios)):
    path, media_type, name = service.file_path(audio_id)
    return FileResponse(path, media_type=media_type, filename=sanitize_filename(name), content_disposition_type="inline")


@router.post("/audios/{audio_id}/reprocessar", response_model=AudioOut, status_code=status.HTTP_202_ACCEPTED,
             tags=["Áudios"], summary="Nova tentativa de transcrição (US-02 CA03)")
def retry_audio(audio_id: int, background: BackgroundTasks, service: AudioService = Depends(_audios)):
    audio = service.retry(audio_id)
    service.uow.commit()
    background.add_task(tasks.transcribe_audio, service.container, audio_id)
    return audio


@router.get("/audios/{audio_id}/transcricao", response_model=TranscriptionOut, tags=["Transcrição"],
            summary="Consultar transcrição (US-02, US-03 CA01)")
def get_transcription(audio_id: int, service: AudioService = Depends(_audios)):
    return service.transcription(audio_id)


@router.get("/audios/{audio_id}/transcricao/busca", response_model=SegmentSearchOut, tags=["Transcrição"],
            summary="Buscar na transcrição (US-03 CA02)", description="Ignora acentos e maiúsculas.")
def search_transcription(audio_id: int, termo: str = Query(min_length=2, max_length=200),
                         service: AudioService = Depends(_audios)):
    return service.search(audio_id, termo)


@router.patch("/audios/{audio_id}/segmentos/{segmento_id}", response_model=SegmentOut, tags=["Transcrição"],
              summary="Corrigir segmento (US-03 CA03)",
              description="O texto revisado substitui o original nas próximas extrações (o original é preservado).")
def update_segment(audio_id: int, segmento_id: int, body: SegmentUpdate, service: AudioService = Depends(_audios)):
    return service.update_segment(audio_id, segmento_id, body.texto)


@router.delete("/audios/{audio_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Áudios"], summary="Excluir áudio")
def delete_audio(audio_id: int, service: AudioService = Depends(_audios)):
    service.delete(audio_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- Documentos -------------------------------------------------------------
@router.post("/projetos/{projeto_id}/documentos", response_model=DocumentOut, status_code=status.HTTP_202_ACCEPTED,
             tags=["Documentos"], summary="Importar documento (US-04)",
             description="Aceita .pdf, .docx, .txt e .md. Extração e indexação rodam em segundo plano.")
def upload_document(
    projeto_id: int, background: BackgroundTasks, arquivo: UploadFile = File(...),
    service: DocumentService = Depends(_documents),
):
    document = service.upload(projeto_id, arquivo.filename or "", arquivo.content_type, arquivo.file)
    service.uow.commit()
    background.add_task(tasks.process_document, service.container, document["id"])
    return document


@router.get("/projetos/{projeto_id}/documentos", response_model=DocumentList, tags=["Documentos"],
            summary="Listar documentos")
def list_documents(projeto_id: int, service: DocumentService = Depends(_documents)):
    return service.list(projeto_id)


@router.get("/documentos/{documento_id}", response_model=DocumentOut, tags=["Documentos"], summary="Detalhar documento")
def get_document(documento_id: int, service: DocumentService = Depends(_documents)):
    return service.get(documento_id)


@router.get("/documentos/{documento_id}/conteudo", response_model=DocumentContentOut, tags=["Documentos"],
            summary="Texto extraído e trechos (US-04 CA02)")
def get_document_content(documento_id: int, service: DocumentService = Depends(_documents)):
    return service.content(documento_id)


@router.post("/documentos/{documento_id}/reprocessar", response_model=DocumentOut,
             status_code=status.HTTP_202_ACCEPTED, tags=["Documentos"], summary="Reprocessar documento")
def retry_document(documento_id: int, background: BackgroundTasks, service: DocumentService = Depends(_documents)):
    document = service.retry(documento_id)
    service.uow.commit()
    background.add_task(tasks.process_document, service.container, documento_id)
    return document


@router.delete("/documentos/{documento_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["Documentos"],
               summary="Excluir documento")
def delete_document(documento_id: int, service: DocumentService = Depends(_documents)):
    service.delete(documento_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
