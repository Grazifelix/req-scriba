"""Cliente do Ollama via `requests`: chat com saída JSON, embeddings e verificação de saúde."""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

import requests

from app.core.errors import ExternalServiceError
from app.llm.parsing import extract_json

logger = logging.getLogger(__name__)

STRICT_JSON_REMINDER = (
    "Sua resposta anterior não era um JSON válido. Responda novamente APENAS com um objeto JSON "
    "válido, sem texto antes ou depois."
)


class LLMClient(Protocol):
    def generate_json(self, system: str, prompt: str) -> dict[str, Any]: ...


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        embedding_model: str,
        timeout: int = 300,
        temperature: float = 0.1,
        num_ctx: int = 8192,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.embedding_model = embedding_model
        self.timeout = timeout
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.session = session or requests.Session()

    # -- HTTP ----------------------------------------------------------------
    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            response = self.session.post(url, json=payload, timeout=self.timeout)
        except requests.Timeout as exc:
            raise ExternalServiceError(f"Tempo esgotado ao chamar o Ollama ({path}).") from exc
        except requests.RequestException as exc:
            raise ExternalServiceError(
                f"Não foi possível conectar ao Ollama em {self.base_url}. Verifique se ele está em execução."
            ) from exc
        if response.status_code >= 400:
            detail = response.text[:300]
            raise ExternalServiceError(
                f"Ollama respondeu com erro HTTP {response.status_code}.", {"resposta": detail}
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ExternalServiceError("Resposta do Ollama não é JSON.") from exc

    # -- LLM -----------------------------------------------------------------
    def _chat(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
        }
        started = time.perf_counter()
        data = self._post("/api/chat", payload)
        logger.info("LLM %s respondeu em %.1fs", self.model, time.perf_counter() - started)
        return (data.get("message") or {}).get("content", "")

    def generate_json(self, system: str, prompt: str) -> dict[str, Any]:
        """Envia o prompt e devolve o objeto JSON da resposta (1 retentativa se inválido)."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        content = self._chat(messages)
        parsed = extract_json(content)
        if parsed is not None:
            return parsed
        logger.warning("LLM retornou JSON inválido; nova tentativa com instrução estrita")
        messages += [{"role": "assistant", "content": content}, {"role": "user", "content": STRICT_JSON_REMINDER}]
        parsed = extract_json(self._chat(messages))
        if parsed is None:
            raise ExternalServiceError("O modelo de linguagem não retornou um JSON válido após nova tentativa.")
        return parsed

    # -- Embeddings ----------------------------------------------------------
    def embed(self, texts: list[str]) -> list[list[float]]:
        data = self._post("/api/embed", {"model": self.embedding_model, "input": texts})
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise ExternalServiceError("Resposta de embeddings inválida do Ollama.")
        return embeddings

    # -- Saúde ---------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        try:
            response = self.session.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            models = [m.get("name", "") for m in response.json().get("models", [])]
        except (requests.RequestException, ValueError) as exc:
            return {"status": "indisponivel", "detalhe": str(exc)[:200]}

        def available(name: str) -> bool:
            return any(m == name or m.split(":")[0] == name.split(":")[0] for m in models)

        missing = [m for m in (self.model, self.embedding_model) if not available(m)]
        return {
            "status": "ok" if not missing else "modelos_ausentes",
            "modelos_ausentes": missing,
            "modelo_llm": self.model,
            "modelo_embeddings": self.embedding_model,
        }
