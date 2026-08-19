"""
LLM Factory — returns the appropriate LLM and embeddings based on LOCAL_MODE.

In LOCAL_MODE  : uses ChatOpenAI pointed at any OpenAI-compatible provider:
                   - GitHub Copilot API  https://api.githubcopilot.com
                   - GitHub Models API   https://models.inference.ai.azure.com
                   - Ollama              http://localhost:11434/v1
                   - LM Studio           http://localhost:1234/v1
                 Config keys: OPENAI_API_BASE, OPENAI_API_KEY,
                              LOCAL_LLM_MODEL, LOCAL_MINI_LLM_MODEL,
                              LOCAL_EMBED_MODEL, EMBED_API_BASE
In cloud mode  : uses AzureChatOpenAI / AzureOpenAIEmbeddings.

Usage:
    from app.core.llm_factory import get_chat_llm, get_mini_llm, get_embeddings
"""
from __future__ import annotations


def get_chat_llm(
    *,
    temperature: float = 0.1,
    max_tokens: int = 4096,
    json_mode: bool = False,
):
    """Return the primary (high-capability) chat LLM."""
    from app.core.config import settings

    model_kwargs: dict = {}
    if json_mode:
        model_kwargs["response_format"] = {"type": "json_object"}

    if settings.LOCAL_MODE:
        from langchain_openai import OpenAI

        return OpenAI(
            model=settings.LOCAL_LLM_MODEL,
            base_url=settings.OPENAI_API_BASE,
            api_key=settings.OPENAI_API_KEY,
            temperature=temperature,
            max_tokens=max_tokens,
            model_kwargs=model_kwargs,
        )

    from langchain_openai import OpenAI

    return OpenAI(
        azure_deployment=settings.AZURE_OPENAI_CHAT_DEPLOYMENT,
        azure_endpoint=str(settings.AZURE_OPENAI_ENDPOINT),
        api_key=settings.AZURE_OPENAI_API_KEY.get_secret_value(),
        api_version=settings.AZURE_OPENAI_API_VERSION,
        temperature=temperature,
        max_tokens=max_tokens,
        model_kwargs=model_kwargs,
    )

def get_openchatai_llm(
    *,
    temperature: float = 0.1,
    max_tokens: int = 4096,
    json_mode: bool = False,
):
    """Return the primary (high-capability) chat LLM."""
    from app.core.config import settings

    model_kwargs: dict = {}
    if json_mode:
        model_kwargs["response_format"] = {"type": "json_object"}

    if settings.LOCAL_MODE:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.LOCAL_LLM_MODEL,
            base_url=settings.OPENAI_API_BASE,
            api_key=settings.OPENAI_API_KEY,
            temperature=temperature,
            max_tokens=max_tokens,
            model_kwargs=model_kwargs,
        )

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        azure_deployment=settings.AZURE_OPENAI_CHAT_DEPLOYMENT,
        azure_endpoint=str(settings.AZURE_OPENAI_ENDPOINT),
        api_key=settings.AZURE_OPENAI_API_KEY.get_secret_value(),
        api_version=settings.AZURE_OPENAI_API_VERSION,
        temperature=temperature,
        max_tokens=max_tokens,
        model_kwargs=model_kwargs,
    )

def get_mini_llm(
    *,
    temperature: float = 0.0,
    max_tokens: int = 200,
    json_mode: bool = False,
):
    """Return the fast/cheap mini LLM (routing, extraction)."""
    from app.core.config import settings

    model_kwargs: dict = {}
    if json_mode:
        model_kwargs["response_format"] = {"type": "json_object"}

    if settings.LOCAL_MODE:
        from langchain_openai import OpenAI

        return OpenAI(
            model=settings.LOCAL_MINI_LLM_MODEL,
            base_url=settings.OPENAI_API_BASE,
            api_key=settings.OPENAI_API_KEY,
            temperature=temperature,
            max_tokens=max_tokens,
            model_kwargs=model_kwargs,
        )

    from langchain_openai import OpenAI

    return OpenAI(
        azure_deployment=settings.AZURE_OPENAI_MINI_DEPLOYMENT,
        azure_endpoint=str(settings.AZURE_OPENAI_ENDPOINT),
        api_key=settings.AZURE_OPENAI_API_KEY.get_secret_value(),
        api_version=settings.AZURE_OPENAI_API_VERSION,
        temperature=temperature,
        max_tokens=max_tokens,
        model_kwargs=model_kwargs,
    )


def get_embeddings():
    """Return the embeddings model (local OpenAI-compat or Azure)."""
    from app.core.config import settings

    if settings.LOCAL_MODE:
        from langchain_openai import OpenAIEmbeddings

        # Use EMBED_API_BASE when set (e.g. GitHub Models for embeddings
        # while OPENAI_API_BASE points at GitHub Copilot for chat).
        embed_base = settings.EMBED_API_BASE or settings.OPENAI_API_BASE
        return OpenAIEmbeddings(
            model=settings.LOCAL_EMBED_MODEL,
            openai_api_base=embed_base,
            openai_api_key=settings.OPENAI_API_KEY,
        )

    from langchain_openai import AzureOpenAIEmbeddings

    return AzureOpenAIEmbeddings(
        model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
        azure_endpoint=str(settings.AZURE_OPENAI_ENDPOINT),
        api_key=settings.AZURE_OPENAI_API_KEY.get_secret_value(),
        api_version=settings.AZURE_OPENAI_API_VERSION,
    )
