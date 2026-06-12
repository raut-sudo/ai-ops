from __future__ import annotations

from langchain_openai import AzureOpenAIEmbeddings

from app.config import settings


def _embedding_client() -> AzureOpenAIEmbeddings:
    return AzureOpenAIEmbeddings(
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_key=settings.AZURE_OPENAI_API_KEY,
        api_version=settings.AZURE_OPENAI_API_VERSION,
        azure_deployment=settings.AZURE_OPENAI_DEPLOYMENT_EMBEDDING,
    )


async def embed_text(text: str) -> list[float]:
    """Embed a text query into a 1536-dim vector using Azure embeddings."""
    client = _embedding_client()
    return await client.aembed_query(text)
