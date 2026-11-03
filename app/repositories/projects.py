"""Repositório de projetos (inclui agregações para os campos derivados)."""

from __future__ import annotations

from typing import Any

from app.repositories.base import BaseRepository


class ProjectRepository(BaseRepository):
    table = "projetos"

    def list_all(self) -> list[dict[str, Any]]:
        return self._select(order="id DESC")

    def counts(self, project_id: int) -> dict[str, int]:
        row = self.conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM audios WHERE projeto_id = :p) AS total_audios,
              (SELECT COUNT(*) FROM documentos WHERE projeto_id = :p) AS total_documentos,
              (SELECT COUNT(*) FROM historias WHERE projeto_id = :p AND status = 'aprovada')
                  AS total_historias_prontas,
              (SELECT COUNT(*) FROM historias WHERE projeto_id = :p
                  AND status IN ('em_revisao', 'falta_informacao')) AS total_historias_pendentes,
              (SELECT COUNT(*) FROM historias WHERE projeto_id = :p AND status = 'desaprovada')
                  AS total_historias_desaprovadas
            """,
            {"p": project_id},
        ).fetchone()
        return dict(row)
