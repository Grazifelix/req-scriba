"""Adaptador do Ollama via `requests`: API nativa de chat, embeddings e verificação de saúde."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from app.core.errors import ExternalServiceError
from app.integrations.llm import check_model, generate_json_with_retry, post_json

logger = logging.getLogger(__name__)

PROVIDER = "ollama"
PROVIDER_LABEL = "Ollama"


def _same_base_name(listed: str, model: str) -> bool:
    return listed == model or listed.split(":")[0] == model.split(":")[0]


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        embedding_model: str,
        timeout: int = 900,
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

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return post_json(self.session, f"{self.base_url}{path}", payload, self.timeout, PROVIDER_LABEL)

    # -- LLM -----------------------------------------------------------------
    def _chat(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "think": False,  # modelos com raciocínio (qwen3.5) gastam muito mais tokens e tempo sem ganho aqui
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx},
        }
        started = time.perf_counter()
        data = self._post("/api/chat", payload)
        logger.info("LLM %s respondeu em %.1fs", self.model, time.perf_counter() - started)
        return (data.get("message") or {}).get("content", "")

    def generate_json(self, system: str, prompt: str) -> dict[str, Any]:
        return generate_json_with_retry(self._chat, system, prompt)

    # -- Embeddings ----------------------------------------------------------
    def embed(self, texts: list[str]) -> list[list[float]]:
        data = self._post("/api/embed", {"model": self.embedding_model, "input": texts})
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise ExternalServiceError("Resposta de embeddings inválida do Ollama.")
        return embeddings

    # -- Saúde ---------------------------------------------------------------
    def _list_models(self) -> list[str]:
        response = self.session.get(f"{self.base_url}/api/tags", timeout=5)
        response.raise_for_status()
        return [m.get("name", "") for m in response.json().get("models", [])]

    def health(self) -> dict[str, Any]:
        return check_model(PROVIDER, self.model, self._list_models, _same_base_name)

    def embedding_health(self) -> dict[str, Any]:
        return check_model(PROVIDER, self.embedding_model, self._list_models, _same_base_name)
