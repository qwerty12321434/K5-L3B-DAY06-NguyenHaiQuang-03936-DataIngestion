"""Use Qwen3-Embedding through the local Ollama HTTP API."""

import json
from math import isfinite
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MODEL_NAME = "qwen3-embedding:0.6b"
DIMENSIONS = 1024
QUERY_INSTRUCTION = (
    "Retrieve passages from the Vietnamese personal data protection decree "
    "and its forms that answer the question."
)


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the same order."""
        ...

    def embed_query(self, question: str) -> list[float]:
        """Return a query vector compatible with the stored document vectors."""
        ...


class OllamaEmbedder:
    model_name = MODEL_NAME
    dimensions = DIMENSIONS

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.version = self._request("/api/version")["version"]
        models = self._request("/api/tags")["models"]
        model = next((item for item in models if item["name"] == self.model_name), None)
        if model is None:
            raise RuntimeError(f"Model missing. Run: ollama pull {self.model_name}")
        self.digest = model["digest"]
        self.quantization = model["details"]["quantization_level"]

    def _request(self, path: str, payload: dict | None = None) -> dict:
        request = Request(
            self.base_url + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=180) as response:
                return json.load(response)
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama {path} returned {error.code}: {detail}") from error
        except (URLError, TimeoutError) as error:
            raise RuntimeError(f"Cannot reach Ollama at {self.base_url}: {error}") from error

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._request(
            "/api/embed", {"model": self.model_name, "input": texts, "truncate": False}
        )
        vectors = response["embeddings"]
        if len(vectors) != len(texts) or any(
            len(vector) != self.dimensions or not all(isfinite(value) for value in vector)
            or not any(vector)
            for vector in vectors
        ):
            raise RuntimeError("Ollama returned missing or invalid embedding vectors")
        return vectors

    def embed_query(self, question: str) -> list[float]:
        return self.embed_documents([f"Instruct: {QUERY_INSTRUCTION}\nQuery: {question}"])[0]
