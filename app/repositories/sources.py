"""Repositórios das fontes: áudios, segmentos, documentos e trechos indexáveis."""

from __future__ import annotations

from typing import Any

from app.repositories.base import BaseRepository, placeholders


class AudioRepository(BaseRepository):
    table = "audios"

    def list_by_project(self, project_id: int) -> list[dict[str, Any]]:
        return self._select("projeto_id = ?", (project_id,))

    def list_by_ids(self, ids: list[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        return self._select(f"id IN ({placeholders(len(ids))})", tuple(ids))


class SegmentRepository(BaseRepository):
    table = "segmentos_transcricao"

    def list_by_audio(self, audio_id: int) -> list[dict[str, Any]]:
        return self._select("audio_id = ?", (audio_id,), order="posicao")

    def delete_by_audio(self, audio_id: int) -> None:
        self.conn.execute("DELETE FROM segmentos_transcricao WHERE audio_id = ?", (audio_id,))

    def count_by_audio(self, audio_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM segmentos_transcricao WHERE audio_id = ?", (audio_id,)
        ).fetchone()
        return int(row[0])


class DocumentRepository(BaseRepository):
    table = "documentos"

    def list_by_project(self, project_id: int) -> list[dict[str, Any]]:
        return self._select("projeto_id = ?", (project_id,))

    def list_by_ids(self, ids: list[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        return self._select(f"id IN ({placeholders(len(ids))})", tuple(ids))


class SourceChunkRepository(BaseRepository):
    table = "trechos_fonte"
    json_columns = ("localizacao",)

    def list_by_source(self, source_type: str, source_id: int) -> list[dict[str, Any]]:
        return self._select("tipo_fonte = ? AND fonte_id = ?", (source_type, source_id), order="posicao")

    def list_by_ids(self, ids: list[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        return self._select(f"id IN ({placeholders(len(ids))})", tuple(ids))

    def delete_by_source(self, source_type: str, source_id: int) -> list[int]:
        ids = [
            r[0]
            for r in self.conn.execute(
                "SELECT id FROM trechos_fonte WHERE tipo_fonte = ? AND fonte_id = ?",
                (source_type, source_id),
            ).fetchall()
        ]
        self.conn.execute(
            "DELETE FROM trechos_fonte WHERE tipo_fonte = ? AND fonte_id = ?", (source_type, source_id)
        )
        return ids
