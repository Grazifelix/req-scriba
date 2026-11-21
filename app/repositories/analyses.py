"""Repositórios da análise das fontes (uma por geração) e de seus itens."""

from __future__ import annotations

from typing import Any

from app.repositories.base import BaseRepository


class AnalysisRepository(BaseRepository):
    table = "analises"
    json_columns = ("fontes",)

    def list_by_project(self, project_id: int) -> list[dict[str, Any]]:
        return self._select("projeto_id = ?", (project_id,), order="id DESC")

    def latest_reviewed(self, project_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM analises WHERE projeto_id = ? AND revisada = 1 ORDER BY id DESC LIMIT 1", (project_id,)
        ).fetchone()
        return self._decode(row)


class AnalysisItemRepository(BaseRepository):
    table = "itens_analise"
    json_columns = ("referencias", "evidencias", "historia_ids")

    def list_by_analysis(self, analysis_id: int) -> list[dict[str, Any]]:
        return self._select("analise_id = ?", (analysis_id,), order="posicao, id")

    def count_by_analysis(self, analysis_id: int) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM itens_analise WHERE analise_id = ?", (analysis_id,)).fetchone()
        return int(row[0])

    def next_position(self, analysis_id: int) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(posicao), -1) + 1 FROM itens_analise WHERE analise_id = ?", (analysis_id,)
        ).fetchone()
        return int(row[0])
