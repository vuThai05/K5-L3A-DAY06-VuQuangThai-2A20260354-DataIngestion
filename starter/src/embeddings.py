"""Embedding adapter supporting OpenAI API and optional local SentenceTransformers.

Configure through .env:
    EMBEDDING_PROVIDER=openai
    EMBEDDING_MODEL=text-embedding-3-small
    EMBEDDING_DIMENSIONS=1536
    OPENAI_API_KEY=...
"""
from __future__ import annotations
    
import os
import time
from typing import Protocol
from urllib.error import HTTPError, URLError
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, question: str) -> list[float]: ...


class MyEmbedder:
    """Create embeddings using the provider selected in environment variables."""

    def __init__(self, model_name: str | None = None):
        self.provider = os.getenv("EMBEDDING_PROVIDER", "openai").strip().lower()
        self.model_name = (
            model_name
            or os.getenv("EMBEDDING_MODEL")
            or ("text-embedding-3-small" if self.provider == "openai"
                else "google/embeddinggemma-2")
        ).strip()
        configured_dimensions = os.getenv("EMBEDDING_DIMENSIONS", "").strip()

        if self.provider == "openai":
            self._init_openai(configured_dimensions)
        elif self.provider == "local":
            self._init_local(configured_dimensions)
        else:
            raise ValueError(
                f"Unsupported EMBEDDING_PROVIDER={self.provider!r}. "
                "Choose 'openai' or 'local'."
            )

    def _init_openai(self, configured_dimensions: str) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "OpenAI SDK is missing. Run: pip install 'openai>=1,<3'"
            ) from exc

        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY is missing. Add it to .env; do not commit the key."
            )

        if self.model_name != "text-embedding-3-small" and not self.model_name.startswith("text-embedding-3-"):
            raise ValueError(
                "This adapter expects an OpenAI text-embedding-3-* model."
            )

        self.dimensions = int(configured_dimensions or "1536")
        if self.dimensions <= 0 or self.dimensions > 1536:
            raise ValueError(
                "For text-embedding-3-small, EMBEDDING_DIMENSIONS must be 1..1536."
            )

        self.client = OpenAI(
            api_key=api_key,
            timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "60")),
            max_retries=0,  # retries are handled below with explicit backoff
        )
        self.batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "64"))
        if self.batch_size < 1 or self.batch_size > 2048:
            raise ValueError("EMBEDDING_BATCH_SIZE must be between 1 and 2048.")

    def _init_local(self, configured_dimensions: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "Local provider requires sentence-transformers. "
                "Install it or switch EMBEDDING_PROVIDER=openai."
            ) from exc

        self.dimensions = int(configured_dimensions or "768")
        self.model = SentenceTransformer(
            self.model_name,
            device=os.getenv("EMBEDDING_DEVICE", "cpu"),
            model_kwargs={"low_cpu_mem_usage": True},
            processor_kwargs={"use_fast": False},
        )
        actual_dimensions = self.model.get_sentence_embedding_dimension()
        if actual_dimensions != self.dimensions:
            raise ValueError(
                f"Local model outputs {actual_dimensions} dimensions, but "
                f"EMBEDDING_DIMENSIONS={self.dimensions}."
            )
        self.batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "16"))

    def _openai_batch(self, texts: list[str]) -> list[list[float]]:
        """Call OpenAI with bounded retries for transient errors."""
        from openai import (
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )

        retryable = (APIConnectionError, APITimeoutError,
                     InternalServerError, RateLimitError)
        last_error = None
        for attempt in range(5):
            try:
                response = self.client.embeddings.create(
                    model=self.model_name,
                    input=texts,
                    dimensions=self.dimensions,
                    encoding_format="float",
                )
                ordered = sorted(response.data, key=lambda item: item.index)
                vectors = [item.embedding for item in ordered]
                if len(vectors) != len(texts):
                    raise RuntimeError(
                        f"OpenAI returned {len(vectors)} vectors for {len(texts)} inputs."
                    )
                for index, vector in enumerate(vectors):
                    if len(vector) != self.dimensions:
                        raise RuntimeError(
                            f"OpenAI vector {index} has {len(vector)} dimensions; "
                            f"expected {self.dimensions}."
                        )
                return vectors
            except retryable as exc:
                last_error = exc
                if attempt == 4:
                    break
                time.sleep(min(2 ** attempt, 16))
        raise RuntimeError(
            f"OpenAI Embeddings API failed after 5 attempts: {last_error}"
        ) from last_error

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not isinstance(texts, list):
            raise TypeError("texts must be a list[str].")
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("Every embedding input must be a non-empty string.")

        all_vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            if self.provider == "openai":
                vectors = self._openai_batch(batch)
            else:
                encoded = self.model.encode(
                    batch,
                    prompt_name="document",
                    normalize_embeddings=True,
                    batch_size=self.batch_size,
                    convert_to_numpy=True,
                )
                vectors = encoded.tolist()
            all_vectors.extend(vectors)

        if len(all_vectors) != len(texts):
            raise RuntimeError("Embedding count does not match input count.")
        return all_vectors

    def embed_query(self, question: str) -> list[float]:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string.")
        if self.provider == "openai":
            return self._openai_batch([question])[0]

        encoded = self.model.encode(
            question,
            prompt_name="query",
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return encoded.tolist()
    