"""Áudios e transcrições (US-01, US-02, US-03)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import BinaryIO

from app.core.container import Container
from app.core.errors import AppError, BusinessValidationError, ConflictError, NotFoundError
from app.core.time import utc_now_iso
from app.db.unit_of_work import UnitOfWork
from app.domain.enums import SourceStatus, SourceType
from app.domain.text import contains_normalized
from app.integrations.storage import AUDIO_EXTENSIONS
from app.services import presenters
from app.services.common import require_audio, require_project
from app.services.indexing import index_audio, remove_source_chunks

logger = logging.getLogger(__name__)


class AudioService:
    def __init__(self, uow: UnitOfWork, container: Container) -> None:
        self.uow = uow
        self.container = container

    def _present(self, audio: dict) -> dict:
        return presenters.audio(audio, self.uow.segments.count_by_audio(audio["id"]))

    def upload(self, project_id: int, filename: str, content_type: str | None, stream: BinaryIO) -> dict:
        """US-01 CA01/CA02: valida, armazena e associa ao projeto com status 'pendente'."""
        require_project(self.uow, project_id)
        stored = self.container.storage.save(
            project_id, filename, stream, AUDIO_EXTENSIONS, self.container.settings.max_audio_mb * 1024 * 1024
        )
        now = utc_now_iso()
        audio_id = self.uow.audios.insert({
            "projeto_id": project_id, "nome_arquivo": stored.original_name, "caminho_arquivo": str(stored.path),
            "tipo_conteudo": stored.media_type, "tamanho_bytes": stored.size_bytes,
            "status": SourceStatus.PENDING.value, "criado_em": now, "atualizado_em": now,
        })
        return self.get(audio_id)

    def list(self, project_id: int) -> dict:
        require_project(self.uow, project_id)
        return presenters.listing([self._present(a) for a in self.uow.audios.list_by_project(project_id)])

    def get(self, audio_id: int) -> dict:
        return self._present(require_audio(self.uow, audio_id))

    def retry(self, audio_id: int) -> dict:
        """US-02 CA03: permite nova tentativa após falha (ou nova transcrição)."""
        audio = require_audio(self.uow, audio_id)
        if audio["status"] in (SourceStatus.PENDING.value, SourceStatus.PROCESSING.value):
            raise ConflictError("O áudio já está aguardando ou em processamento.")
        self.uow.audios.update(audio_id, {
            "status": SourceStatus.PENDING.value, "mensagem_erro": None, "atualizado_em": utc_now_iso()
        })
        return self.get(audio_id)

    def transcription(self, audio_id: int) -> dict:
        """US-02 CA01/CA02, US-03 CA01: texto completo + segmentos com tempos."""
        audio = require_audio(self.uow, audio_id)
        segments = [presenters.segment(s) for s in self.uow.segments.list_by_audio(audio_id)]
        return {
            "audio_id": audio_id, "nome_arquivo": audio["nome_arquivo"], "status": audio["status"],
            "status_rotulo": presenters.audio(audio, len(segments))["status_rotulo"],
            "duracao_segundos": audio["duracao_segundos"],
            "texto_completo": " ".join(s["texto"] for s in segments),
            "segmentos": segments, "total_segmentos": len(segments),
        }

    def search(self, audio_id: int, term: str) -> dict:
        """US-03 CA02: busca ignorando acentos e maiúsculas."""
        require_audio(self.uow, audio_id)
        if len(term.strip()) < 2:
            raise BusinessValidationError("O termo de busca deve ter ao menos 2 caracteres.")
        matches = [
            presenters.segment(s) for s in self.uow.segments.list_by_audio(audio_id)
            if contains_normalized(s["texto"], term)
        ]
        return {"termo": term, **presenters.listing(matches)}

    def update_segment(self, audio_id: int, segment_id: int, text: str) -> dict:
        """US-03 CA03: salva correção e reconstrói os trechos usados nas próximas extrações."""
        audio = require_audio(self.uow, audio_id)
        segment = self.uow.segments.get(segment_id)
        if not segment or segment["audio_id"] != audio_id:
            raise NotFoundError.for_entity("Segmento", segment_id)
        self.uow.segments.update(segment_id, {
            "texto": text, "editado": int(text != segment["texto_original"]), "atualizado_em": utc_now_iso()
        })
        index_audio(self.uow, self.container, audio)
        return presenters.segment(self.uow.segments.get(segment_id))

    def file_path(self, audio_id: int) -> tuple[Path, str, str]:
        audio = require_audio(self.uow, audio_id)
        path = self.container.storage.resolve(audio["caminho_arquivo"])
        if not path.exists():
            raise NotFoundError("Arquivo de áudio não encontrado no armazenamento.")
        return path, audio["tipo_conteudo"] or "application/octet-stream", audio["nome_arquivo"]

    def delete(self, audio_id: int) -> None:
        audio = require_audio(self.uow, audio_id)
        remove_source_chunks(self.uow, self.container, SourceType.AUDIO.value, audio_id)
        self.uow.audios.delete(audio_id)
        self.container.storage.remove(audio["caminho_arquivo"])


def transcribe_audio(container: Container, audio_id: int) -> None:
    """Tarefa de background: pendente → processando → concluido | erro (US-01 CA03, US-02)."""
    with container.uow() as uow:
        audio = uow.audios.get(audio_id)
        if not audio:
            return
        uow.audios.update(audio_id, {"status": SourceStatus.PROCESSING.value, "atualizado_em": utc_now_iso()})
    try:
        path = container.storage.resolve(audio["caminho_arquivo"])
        result = container.transcriber.transcribe(path)
        if not result.segments:
            raise BusinessValidationError("Nenhuma fala foi identificada na gravação.")
        with container.uow() as uow:
            uow.segments.delete_by_audio(audio_id)
            now = utc_now_iso()
            for position, seg in enumerate(result.segments):
                uow.segments.insert({
                    "audio_id": audio_id, "posicao": position, "inicio_segundos": round(seg.start, 2),
                    "fim_segundos": round(seg.end, 2), "texto": seg.text, "texto_original": seg.text,
                    "editado": 0, "atualizado_em": now,
                })
            index_audio(uow, container, audio)
            uow.audios.update(audio_id, {
                "status": SourceStatus.COMPLETED.value, "mensagem_erro": None, "processado_em": now,
                "duracao_segundos": result.duration_seconds or result.segments[-1].end, "atualizado_em": now,
            })
    except AppError as exc:
        _mark_error(container, audio_id, exc.message)
    except Exception as exc:  # falha inesperada: registra e expõe mensagem genérica
        logger.exception("Falha inesperada ao transcrever áudio %s", audio_id)
        _mark_error(container, audio_id, f"Erro inesperado no processamento: {type(exc).__name__}")


def _mark_error(container: Container, audio_id: int, message: str) -> None:
    logger.warning("Áudio %s com erro: %s", audio_id, message)
    with container.uow() as uow:
        uow.audios.update(audio_id, {
            "status": SourceStatus.ERROR.value, "mensagem_erro": message, "atualizado_em": utc_now_iso()
        })
