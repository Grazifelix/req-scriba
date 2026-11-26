"""Mecanismo de transcrição automática (ASR) com faster-whisper (dependência opcional)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

import requests

from app.core.errors import ExternalServiceError
from app.integrations.llm import check_model, request_json

logger = logging.getLogger(__name__)

REMOTE_PROVIDER = "openai_compatible"
REMOTE_LABEL = "servidor de transcrição"

INSTALL_HINT = (
    "Mecanismo de transcrição indisponível: instale o faster-whisper "
    "(pip install -r requirements-asr.txt) e use 'reprocessar'."
)
SAMPLING_RATE = 16000

CancelCheck = Callable[[], bool]


class TranscriptionCancelled(Exception):
    """A transcrição foi interrompida porque ``should_cancel`` passou a devolver ``True``."""


def check_cancelled(should_cancel: CancelCheck | None) -> None:
    if should_cancel is not None and should_cancel():
        raise TranscriptionCancelled


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
    def transcribe(self, path: Path, should_cancel: CancelCheck | None = None) -> TranscriptionResult: ...
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

    def transcribe(self, path: Path, should_cancel: CancelCheck | None = None) -> TranscriptionResult:
        """Transcreve o áudio; ``should_cancel`` é consultado entre segmentos para interromper cedo."""
        model = self._load()
        started = time.perf_counter()
        try:
            audio = decode_audio(path)
            check_cancelled(should_cancel)
            # ``segments`` é um gerador preguiçoso: cada segmento só é inferido ao ser iterado.
            segments, info = model.transcribe(audio, language=self.language, vad_filter=True)
            result = []
            for s in segments:
                check_cancelled(should_cancel)
                if s.text.strip():
                    result.append(TranscribedSegment(s.start, s.end, s.text.strip()))
        except TranscriptionCancelled:
            logger.info("Transcrição cancelada após %.1fs", time.perf_counter() - started)
            raise
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


class RemoteTranscriber:
    """Transcrição num servidor HTTP com a rota `/audio/transcriptions` da OpenAI (D36).

    Serve para o servidor próprio do repositório `req-scriba-asr` (ex.: GPU remota via túnel SSH) ou
    qualquer serviço compatível. A chamada é única: o cancelamento é verificado antes do envio e
    depois da resposta.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        language: str | None,
        api_key: str = "",
        timeout: int = 1800,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.language = language
        self.api_key = api_key
        self.timeout = timeout
        self.session = session or requests.Session()
        if api_key:
            self.session.headers["Authorization"] = f"Bearer {api_key}"

    def transcribe(self, path: Path, should_cancel: CancelCheck | None = None) -> TranscriptionResult:
        check_cancelled(should_cancel)
        fields = {"model": self.model, "response_format": "verbose_json", "timestamp_granularities[]": "segment"}
        if self.language:
            fields["language"] = self.language
        started = time.perf_counter()
        with path.open("rb") as audio:
            data = request_json(
                self.session, f"{self.base_url}/audio/transcriptions", self.timeout, REMOTE_LABEL,
                secret=self.api_key, data=fields, files={"file": (path.name, audio)},
            )
        check_cancelled(should_cancel)
        segments = _parse_segments(data)
        logger.info(
            "Transcrição remota (%s) concluída em %.1fs (%d segmentos)",
            self.model, time.perf_counter() - started, len(segments),
        )
        return TranscriptionResult(data.get("duration"), data.get("language"), segments)

    def _list_models(self) -> list[str]:
        response = self.session.get(f"{self.base_url}/models", timeout=5)
        response.raise_for_status()
        return [m.get("id", "") for m in response.json().get("data", [])]

    def health(self) -> dict[str, Any]:
        return check_model(REMOTE_PROVIDER, self.model, self._list_models)


def _parse_segments(data: dict[str, Any]) -> list[TranscribedSegment]:
    try:
        return [
            TranscribedSegment(float(s["start"]), float(s["end"]), s["text"].strip())
            for s in data["segments"]
            if (s.get("text") or "").strip()
        ]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ExternalServiceError(f"Resposta de transcrição inválida do {REMOTE_LABEL}.") from exc
