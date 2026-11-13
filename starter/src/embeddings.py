"""Choose a real embedding provider (API or local) and implement this contract."""
from __future__ import annotations

import json
import os
import time
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from sentence_transformers import SentenceTransformer

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
    def __init__(self, model_name="google/embeddinggemma-2"):
        self.model_name = model_name
        self.model = SentenceTransformer(model_name, device="cpu", model_kwargs={"low_cpu_mem_usage": True}, processor_kwargs={"use_fast": False})
        self.dimensions = 768

    def embed_documents(self, texts):
        return self.model.encode(
            texts,
            prompt_name="document",
            normalize_embeddings=True
        ).tolist()

    def embed_query(self, question):
        return self.model.encode(
            question,
            prompt_name="query",
            normalize_embeddings=True
        ).tolist()
