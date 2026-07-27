"""
LLM provider abstraction for chatbot runtime.
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List

import httpx

from app.core.config import (
    CHATBOT_LLM_PROVIDER,
    CHATBOT_STREAM_TIMEOUT_SECONDS,
    CHATBOT_TEXT_MODEL,
    GOOGLE_API_KEY,
)


class ChatbotLLMProvider(ABC):
    """Base interface for chatbot LLM providers."""

    @abstractmethod
    async def stream_reply(self, payload: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
        """Yield normalized stream events with keys: type, text/message/full_text."""
        raise NotImplementedError


class GeminiChatbotProvider(ChatbotLLMProvider):
    """Gemini provider implementation using streamGenerateContent SSE."""

    @staticmethod
    def _extract_text_from_event(event_payload: Dict[str, Any]) -> str:
        texts: List[str] = []
        for candidate in event_payload.get("candidates", []):
            content = candidate.get("content", {})
            for part in content.get("parts", []):
                text = part.get("text")
                if isinstance(text, str) and text:
                    texts.append(text)
        return "".join(texts)

    async def stream_reply(self, payload: Dict[str, Any]) -> AsyncIterator[Dict[str, Any]]:
        if not GOOGLE_API_KEY:
            yield {"type": "error", "message": "Chat runtime is not configured (missing GOOGLE_API_KEY)."}
            yield {"type": "done"}
            return

        model = CHATBOT_TEXT_MODEL.strip() or "gemini-2.5-flash-lite"
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": GOOGLE_API_KEY,
        }

        full_text = ""
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                timeout = httpx.Timeout(float(CHATBOT_STREAM_TIMEOUT_SECONDS), connect=10.0)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream("POST", endpoint, json=payload, headers=headers) as response:
                        if response.status_code >= 400:
                            error_body = (await response.aread()).decode("utf-8", errors="ignore")

                            # Retry transient quota/backpressure failures.
                            if response.status_code in {429, 500, 502, 503, 504} and attempt < max_attempts:
                                await asyncio.sleep(float(attempt * 1.5))
                                continue

                            if response.status_code == 429:
                                yield {
                                    "type": "error",
                                    "message": "Chatbot is temporarily rate-limited (429). Please try again in a moment.",
                                }
                                yield {"type": "done"}
                                return

                            message = "Failed to generate chatbot response"
                            if error_body:
                                message = f"{message}: {error_body[:300]}"
                            yield {"type": "error", "message": message}
                            yield {"type": "done"}
                            return

                        async for line in response.aiter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            data_str = line[5:].strip()
                            if not data_str or data_str == "[DONE]":
                                continue
                            try:
                                event_payload = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue

                            chunk_text = self._extract_text_from_event(event_payload)
                            if not chunk_text:
                                continue

                            if chunk_text.startswith(full_text):
                                delta = chunk_text[len(full_text) :]
                            else:
                                delta = chunk_text

                            if not delta:
                                continue
                            full_text += delta
                            yield {"type": "delta", "text": delta}
                break
            except Exception:
                if attempt < max_attempts:
                    await asyncio.sleep(float(attempt * 1.5))
                    continue
                yield {"type": "error", "message": "Unable to stream chatbot response right now."}
                yield {"type": "done"}
                return

        if not full_text.strip():
            yield {"type": "error", "message": "Unable to stream chatbot response right now."}
        else:
            yield {"type": "done", "full_text": full_text.strip()}
        if not full_text.strip():
            yield {"type": "done"}


def get_chatbot_llm_provider() -> ChatbotLLMProvider:
    """Factory for chatbot LLM providers."""
    provider = CHATBOT_LLM_PROVIDER.strip().lower()
    if provider == "gemini":
        return GeminiChatbotProvider()
    raise ValueError(f"Unsupported chatbot LLM provider: {provider}")
