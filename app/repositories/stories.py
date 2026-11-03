"""Repositórios de histórias, versões e evidências."""

from __future__ import annotations

from typing import Any

from app.repositories.base import BaseRepository, placeholders


class StoryRepository(BaseRepository):
    table = "historias"
    json_columns = ("criterios_aceitacao", "informacoes_ausentes", "afirmacoes_sem_evidencia")

    def list_by_project(self, project_id: int, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            return self._select("projeto_id = ? AND status = ?", (project_id, status))
        return self._select("projeto_id = ?", (project_id,))

    def list_active(self, project_id: int) -> list[dict[str, Any]]:
        """Histórias que compõem o backlog (todas exceto desaprovadas)."""
        return self._select("projeto_id = ? AND status != 'desaprovada'", (project_id,))

    def next_code(self, project_id: int) -> str:
        row = self.conn.execute(
            "SELECT MAX(CAST(SUBSTR(codigo, 4) AS INTEGER)) FROM historias WHERE projeto_id = ?",
            (project_id,),
        ).fetchone()
        return f"US-{(row[0] or 0) + 1:03d}"


class VersionRepository(BaseRepository):
    table = "versoes_historia"
    json_columns = ("conteudo", "evidencia_ids")

    def list_by_story(self, story_id: int) -> list[dict[str, Any]]:
        return self._select("historia_id = ?", (story_id,), order="numero")

    def get_by_number(self, story_id: int, number: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM versoes_historia WHERE historia_id = ? AND numero = ?", (story_id, number)
        ).fetchone()
        return self._decode(row)


class EvidenceRepository(BaseRepository):
    table = "evidencias"
    json_columns = ("localizacao",)

    def list_by_story(self, story_id: int) -> list[dict[str, Any]]:
        return self._select("historia_id = ?", (story_id,))

    def list_by_ids(self, ids: list[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        return self._select(f"id IN ({placeholders(len(ids))})", tuple(ids))
