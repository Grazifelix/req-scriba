"""Ponto de entrada das tarefas de background agendadas pelos routers.

Cada função recebe o Container e um id, abre suas próprias transações e nunca propaga exceções
(falhas são persistidas como status 'erro' na fonte ou no job).
"""

from __future__ import annotations

from app.services.audios import transcribe_audio
from app.services.documents import process_document
from app.services.generation import run_generation
from app.services.updates import run_update_analysis

__all__ = ["transcribe_audio", "process_document", "run_generation", "run_update_analysis"]
