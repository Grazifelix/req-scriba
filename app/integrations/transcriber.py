"""Mecanismo de transcrição automática (ASR) com faster-whisper (dependência opcional)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.core.errors import ExternalServiceError

logger = logging.getLogger(__name__)

INSTALL_HINT = (
    "Mecanismo de transcrição indisponível: instale o faster-whisper "
    "(pip install -r requirements-asr.txt) e use 'reprocessar'."
)
SAMPLING_RATE = 16000


@dataclass
class TranscribedSegment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptionResult:
    duration_seconds: float | None
    language: str | None
    segments: list[TranscribedSegment] = field(default_factory=list)


def decode_audio(path: Path, sampling_rate: int = SAMPLING_RATE):
    """Decodifica o áudio em float32 mono 16 kHz com PyAV.

    Substitui o decodificador do faster-whisper 1.2.x, que passa ``metadata_errors``
    para ``av.open`` — argumento removido nas versões recentes do PyAV.
    """
    import av
    import numpy as np

    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=sampling_rate)
    chunks = []
    with av.open(str(path), mode="r") as container:
        for frame in container.decode(audio=0):
            chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(frame))
        chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(None))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


class Transcriber(Protocol):
    def transcribe(self, path: Path) -> TranscriptionResult: ...
    def health(self) -> dict[str, Any]: ...


class FasterWhisperTranscriber:
    """Carrega o modelo sob demanda (primeira transcrição) e o reutiliza."""

    def __init__(self, model_size: str, device: str, compute_type: str, language: str | None) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise ExternalServiceError(INSTALL_HINT) from exc
            logger.info("Carregando modelo Whisper '%s' (%s/%s)", self.model_size, self.device, self.compute_type)
            self._model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
        return self._model

    def transcribe(self, path: Path) -> TranscriptionResult:
        model = self._load()
        started = time.perf_counter()
        try:
            audio = decode_audio(path)
            segments, info = model.transcribe(audio, language=self.language, vad_filter=True)
            result = [TranscribedSegment(s.start, s.end, s.text.strip()) for s in segments if s.text.strip()]
        except Exception as exc:  # erros de decodificação de áudio variam por formato
            raise ExternalServiceError(f"Falha ao transcrever o áudio: {exc}") from exc
        logger.info("Transcrição concluída em %.1fs (%d segmentos)", time.perf_counter() - started, len(result))
        return TranscriptionResult(getattr(info, "duration", None), getattr(info, "language", None), result)

    def health(self) -> dict[str, Any]:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return {"status": "indisponivel", "detalhe": INSTALL_HINT}
        return {"status": "ok", "modelo": self.model_size, "carregado": self._model is not None}
