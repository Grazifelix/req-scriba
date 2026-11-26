"""Registro das etapas de um job (orquestração do pipeline): status, duração e dados de cada etapa.

O registro vai para ``resultado.etapas`` do job, inclusive quando o job falha, para identificar em qual
etapa a falha aconteceu e quanto tempo cada uma levou.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

from app.core.errors import AppError

COMPLETED, FAILED, SKIPPED = "concluida", "erro", "ignorada"


class StageLog:
    def __init__(self) -> None:
        self.stages: list[dict[str, Any]] = []

    @contextmanager
    def run(self, name: str, fatal: bool = True) -> Iterator[dict[str, Any]]:
        """Executa uma etapa. Falha de uma etapa ``fatal=False`` é registrada e o pipeline continua."""
        entry: dict[str, Any] = {"etapa": name, "status": COMPLETED}
        self.stages.append(entry)
        started = time.perf_counter()
        try:
            yield entry
        except AppError as exc:
            entry.update(status=FAILED, erro=exc.message)
            if fatal:
                raise
        except Exception as exc:
            entry.update(status=FAILED, erro=f"Erro inesperado: {type(exc).__name__}")
            raise
        finally:
            entry["duracao_s"] = round(time.perf_counter() - started, 3)

    def skip(self, name: str, reason: str) -> None:
        self.stages.append({"etapa": name, "status": SKIPPED, "motivo": reason, "duracao_s": 0.0})

    def failed(self, name: str) -> bool:
        return any(s["etapa"] == name and s["status"] == FAILED for s in self.stages)

    def total_seconds(self) -> float:
        return round(sum(s["duracao_s"] for s in self.stages), 3)
