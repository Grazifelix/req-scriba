"""Adaptador para servidores com API compatível com a da OpenAI (locais ou em nuvem), via `requests`."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from app.core.errors import ExternalServiceError
from app.integrations.llm import check_model, generate_json_with_retry, post_json

logger = logging.getLogger(__name__)

PROVIDER = "openai_compatible"
PROVIDER_LABEL = "provedor compatível com OpenAI"


class OpenAICompatibleClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        embedding_model: str,
        api_key: str = "",
        timeout: int = 900,
        temperature: float = 0.1,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.embedding_model = embedding_model
        self.api_key = api_key
        self.timeout = timeout
        self.temperature = temperature
        self.session = session or requests.Session()
        if api_key:
            self.session.headers["Authorization"] = f"Bearer {api_key}"

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return post_json(
            self.session, f"{self.base_url}{path}", payload, self.timeout, PROVIDER_LABEL, secret=self.api_key
        )

    # -- LLM -----------------------------------------------------------------
    def _chat(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        started = time.perf_counter()
        data = self._post("/chat/completions", payload)
        logger.info("LLM %s respondeu em %.1fs", self.model, time.perf_counter() - started)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ExternalServiceError(f"Resposta de chat inválida do {PROVIDER_LABEL}.") from exc

    def generate_json(self, system: str, prompt: str) -> dict[str, Any]:
        return generate_json_with_retry(self._chat, system, prompt)

    # -- Embeddings ----------------------------------------------------------
    def embed(self, texts: list[str]) -> list[list[float]]:
        data = self._post("/embeddings", {"model": self.embedding_model, "input": texts})
        try:
            items = sorted(data["data"], key=lambda item: item["index"])
            embeddings = [item["embedding"] for item in items]
        except (KeyError, TypeError) as exc:
            raise ExternalServiceError(f"Resposta de embeddings inválida do {PROVIDER_LABEL}.") from exc
        if len(embeddings) != len(texts):
            raise ExternalServiceError(f"Resposta de embeddings inválida do {PROVIDER_LABEL}.")
        return embeddings

    # -- Saúde ---------------------------------------------------------------
    def _list_models(self) -> list[str]:
        response = self.session.get(f"{self.base_url}/models", timeout=5)
        response.raise_for_status()
        return [m.get("id", "") for m in response.json().get("data", [])]

    def health(self) -> dict[str, Any]:
        return check_model(PROVIDER, self.model, self._list_models)

    def embedding_health(self) -> dict[str, Any]:
        return check_model(PROVIDER, self.embedding_model, self._list_models)
