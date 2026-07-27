"""
Business logic service for multi-domain chatbot agents and launcher embed runtime.
"""

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.chatbot_agents.domain_templates import get_domain_template
from app.chatbot_agents.llm_providers import get_chatbot_llm_provider
from app.chatbot_agents.repository import chatbot_agent_repository
from app.chatbot_agents.token_service import chatbot_embed_token_service
from app.core.config import (
    CHATBOT_ALLOW_ANY_ORIGIN,
    CHATBOT_LOADER_BASE_URL,
    CHATBOT_RUNTIME_ENABLED,
)


class ChatbotAgentService:
    """Service for chatbot lifecycle, embedding, and runtime behavior."""

    def __init__(self):
        self.repository = chatbot_agent_repository
        self.token_service = chatbot_embed_token_service
        self.llm_provider = get_chatbot_llm_provider()

    @staticmethod
    def _is_dev_origin_bypass_enabled() -> bool:
        return CHATBOT_ALLOW_ANY_ORIGIN

    @staticmethod
    def _build_url(base_url: str, token: str, embed_origin: Optional[str] = None) -> str:
        parsed = urlsplit(base_url.strip())
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("CHATBOT_LOADER_BASE_URL must be an absolute URL")
        query_pairs = [
            (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key not in {"token", "embed_origin"}
        ]
        query_pairs.append(("token", token))
        if embed_origin:
            query_pairs.append(("embed_origin", embed_origin))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query_pairs), parsed.fragment))

    @staticmethod
    def _sse_event(event_type: str, payload: Dict[str, Any]) -> str:
        body = {"type": event_type, **payload}
        return f"data: {json.dumps(body, ensure_ascii=True)}\n\n"

    @staticmethod
    def _build_chat_contents(history: List[Dict[str, str]], message: str) -> List[Dict[str, Any]]:
        contents: List[Dict[str, Any]] = []
        for entry in history[-12:]:
            role = str(entry.get("role", "")).strip().lower()
            if role not in {"user", "assistant"}:
                continue
            text = str(entry.get("content", "")).strip()
            if not text:
                continue
            contents.append({"role": "model" if role == "assistant" else "user", "parts": [{"text": text}]})

        contents.append({"role": "user", "parts": [{"text": message.strip()}]})
        return contents

    @staticmethod
    def _hash_ip(raw_ip: str) -> str:
        return hashlib.sha256(raw_ip.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _validate_domain_configuration(chatbot: Dict[str, Any]) -> None:
        domain_key = chatbot.get("domain_key")
        custom_domain_name = chatbot.get("custom_domain_name")
        if domain_key == "custom" and not custom_domain_name:
            raise ValueError("custom_domain_name is required for custom domain")
        if domain_key != "custom" and custom_domain_name:
            raise ValueError("custom_domain_name must be empty when domain_key is not custom")

    @staticmethod
    def _ensure_runtime_fields(chatbot: Dict[str, Any]) -> None:
        required_fields = [
            "domain_key",
            "behavior_config",
            "knowledge_config",
            "launcher_config",
            "embed_token_version",
        ]
        for field in required_fields:
            if field not in chatbot:
                raise ValueError(f"Chatbot is missing required field '{field}'. Run chatbot v2 migration.")

    @staticmethod
    def _build_launcher_script(loader_url: str) -> str:
        return f'<script src="{loader_url}" async></script>'

    def _build_system_instruction(self, chatbot: Dict[str, Any]) -> str:
        self._validate_domain_configuration(chatbot)
        template = get_domain_template(chatbot["domain_key"])
        behavior = chatbot["behavior_config"]
        knowledge = chatbot["knowledge_config"]
        domain_label = chatbot["custom_domain_name"] if chatbot["domain_key"] == "custom" else template["name"]

        faq_lines = []
        for item in knowledge.get("faq_items", []):
            question = str(item.get("question", "")).strip()
            answer = str(item.get("answer", "")).strip()
            if question and answer:
                faq_lines.append(f"Q: {question}\nA: {answer}")
        faq_blob = "\n\n".join(faq_lines)

        instruction_lines = [
            "You are a production customer-facing assistant.",
            "Follow domain boundaries and avoid unsafe or non-compliant guidance.",
            "If the user asks for blocked or high-risk content, refuse politely and offer escalation.",
            "Do not fabricate facts. If unsure, state uncertainty and ask for clarification.",
            f"Domain: {domain_label}",
            f"Domain baseline: {template['baseline_prompt']}",
            f"Persona: {behavior['persona']}",
            f"Goal: {behavior['goal']}",
            f"Tone: {behavior['tone']}",
            f"Response style: {behavior['response_style']}",
            f"Language: {behavior['language']}",
            f"Allowed topics: {', '.join(behavior.get('allowed_topics', [])) or 'none specified'}",
            f"Blocked topics: {', '.join(behavior.get('blocked_topics', [])) or 'none specified'}",
            f"Escalation rule: {behavior['escalation_instructions']}",
            f"Custom instructions: {behavior.get('custom_instructions', '') or 'none'}",
            f"Business facts:\n{knowledge['business_facts']}",
            f"FAQ context:\n{faq_blob or 'No FAQ items provided.'}",
        ]
        return "\n\n".join(instruction_lines)

    async def create_chatbot_agent(self, owner_user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = {
            "owner_user_id": owner_user_id,
            "name": payload["name"],
            "welcome_message": payload["welcome_message"],
            "allowed_origins": payload["allowed_origins"],
            "theme": payload["theme"],
            "status": payload.get("status", "active"),
            "domain_key": payload["domain_key"],
            "custom_domain_name": payload.get("custom_domain_name"),
            "behavior_config": payload["behavior_config"],
            "knowledge_config": payload["knowledge_config"],
            "launcher_config": payload["launcher_config"],
            "embed_token_version": 1,
        }
        self._validate_domain_configuration(data)
        return await self.repository.create(data)

    async def list_chatbot_agents_for_user(
        self, user_id: str, role: str, limit: int = 100, offset: int = 0
    ) -> List[Dict[str, Any]]:
        return await self.repository.list_for_owner(user_id, limit=limit, offset=offset)

    async def get_chatbot_agent(self, chatbot_id: str) -> Optional[Dict[str, Any]]:
        return await self.repository.get(chatbot_id)

    def can_manage(self, chatbot: Dict[str, Any], user_id: str, role: str) -> bool:
        return chatbot.get("owner_user_id") == user_id

    async def update_chatbot_agent(self, chatbot_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        existing = await self.repository.get(chatbot_id)
        if not existing:
            return None

        payload: Dict[str, Any] = {}
        updatable_fields = [
            "name",
            "welcome_message",
            "allowed_origins",
            "theme",
            "status",
            "domain_key",
            "custom_domain_name",
            "behavior_config",
            "knowledge_config",
            "launcher_config",
        ]
        for field in updatable_fields:
            if field in update_data and update_data[field] is not None:
                payload[field] = update_data[field]

        if not payload:
            return existing

        merged = {**existing, **payload}
        self._validate_domain_configuration(merged)
        return await self.repository.update(chatbot_id, payload)

    async def delete_chatbot_agent(self, chatbot_id: str) -> bool:
        return await self.repository.delete(chatbot_id)

    async def create_embed_token(
        self,
        chatbot: Dict[str, Any],
        origin: str,
        expires_in_minutes: Optional[int] = None,
        never_expires: bool = False,
    ) -> Dict[str, Any]:
        self._ensure_runtime_fields(chatbot)
        normalized_origin = origin.rstrip("/")
        allowed_origins = [entry.rstrip("/") for entry in chatbot.get("allowed_origins", [])]
        if not self._is_dev_origin_bypass_enabled() and normalized_origin not in allowed_origins:
            raise ValueError("Origin is not allowed for this chatbot")

        token_version = int(chatbot["embed_token_version"])
        token_data = self.token_service.create_token(
            chatbot_id=chatbot["id"],
            origin=normalized_origin,
            version=token_version,
            expires_in_minutes=expires_in_minutes,
            never_expires=never_expires,
        )
        loader_base = CHATBOT_LOADER_BASE_URL.strip()
        if not loader_base:
            raise ValueError("CHATBOT_LOADER_BASE_URL is not configured")
        loader_url = self._build_url(loader_base, token_data["token"], embed_origin=normalized_origin)

        return {
            "token": token_data["token"],
            "expires_at": token_data["expires_at"],
            "token_version": token_version,
            "loader_url": loader_url,
            "launcher_script": self._build_launcher_script(loader_url),
        }

    async def get_public_embed_config(self, token: str, request_origin: str) -> Dict[str, Any]:
        claims = self.token_service.verify_token(token)
        chatbot_id = claims["sub"]
        token_origin = str(claims["origin"]).rstrip("/")
        token_version = int(claims["ver"])
        normalized_request_origin = request_origin.rstrip("/")
        if not normalized_request_origin and not self._is_dev_origin_bypass_enabled():
            raise ValueError("Origin is required")
        if not self._is_dev_origin_bypass_enabled() and token_origin != normalized_request_origin:
            raise ValueError("Request origin does not match embed token origin")

        chatbot = await self.repository.get(chatbot_id)
        if not chatbot:
            raise ValueError("Chatbot not found")
        self._ensure_runtime_fields(chatbot)

        runtime_enabled = await self.get_runtime_enabled()
        if not runtime_enabled:
            raise ValueError("Chatbot runtime is temporarily disabled")
        if chatbot.get("status") != "active":
            raise ValueError("Chatbot is inactive")
        if int(chatbot["embed_token_version"]) != token_version:
            raise ValueError("Embed token has been revoked")

        allowed_origins = [entry.rstrip("/") for entry in chatbot.get("allowed_origins", [])]
        if not self._is_dev_origin_bypass_enabled() and token_origin not in allowed_origins:
            raise ValueError("Origin is not allowed for this chatbot")

        return {
            "chatbot_id": chatbot["id"],
            "name": chatbot["name"],
            "welcome_message": chatbot["welcome_message"],
            "theme": chatbot["theme"],
            "status": chatbot["status"],
            "generated_at": datetime.now(timezone.utc),
        }

    async def revoke_embed_tokens(self, chatbot_id: str) -> Dict[str, Any]:
        chatbot = await self.repository.get(chatbot_id)
        if not chatbot:
            raise ValueError("Chatbot not found")
        self._ensure_runtime_fields(chatbot)
        next_version = int(chatbot["embed_token_version"]) + 1
        updated = await self.repository.update(chatbot_id, {"embed_token_version": next_version})
        if not updated:
            raise ValueError("Failed to revoke embed tokens")
        return updated

    async def get_runtime_logs(self, chatbot_id: str, limit: int = 100, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        return await self.repository.list_runtime_logs(chatbot_id=chatbot_id, limit=limit, status_filter=status_filter)

    async def get_runtime_enabled(self) -> bool:
        settings = await self.repository.get_runtime_settings()
        if "enabled" not in settings:
            return CHATBOT_RUNTIME_ENABLED
        return bool(settings["enabled"])

    async def set_runtime_enabled(self, enabled: bool, updated_by: str) -> Dict[str, Any]:
        return await self.repository.update_runtime_settings(enabled=enabled, updated_by=updated_by)

    async def _create_runtime_log(
        self,
        request_id: str,
        chatbot_id: str,
        status: str,
        latency_ms: int,
        raw_ip: str,
        error_code: Optional[str] = None,
    ) -> None:
        log_payload = {
            "request_id": request_id,
            "chatbot_id": chatbot_id,
            "status": status,
            "latency_ms": int(latency_ms),
            "error_code": error_code,
            "ip_hash": self._hash_ip(raw_ip or "unknown"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.repository.create_runtime_log(log_payload)

    async def stream_chat_reply(
        self,
        chatbot: Dict[str, Any],
        message: str,
        history: List[Dict[str, str]],
        request_id: Optional[str] = None,
        client_ip: str = "unknown",
    ) -> AsyncIterator[str]:
        self._ensure_runtime_fields(chatbot)
        started_at = time.perf_counter()
        runtime_request_id = request_id or str(uuid.uuid4())
        status = "success"
        error_code: Optional[str] = None

        system_instruction = self._build_system_instruction(chatbot)
        llm_payload = {
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "contents": self._build_chat_contents(history=history, message=message),
            "generationConfig": {
                "temperature": 0.45,
                "maxOutputTokens": 800,
            },
        }

        async for event in self.llm_provider.stream_reply(llm_payload):
            event_type = event.get("type", "")
            if event_type == "error":
                status = "error"
                error_code = "runtime_generation_error"
                yield self._sse_event("error", {"message": event.get("message", "Unable to generate response.")})
                continue
            if event_type == "delta":
                yield self._sse_event("delta", {"text": event.get("text", "")})
                continue
            if event_type == "done":
                payload: Dict[str, Any] = {}
                if event.get("full_text"):
                    payload["full_text"] = event["full_text"]
                yield self._sse_event("done", payload)

        latency_ms = int((time.perf_counter() - started_at) * 1000)
        await self._create_runtime_log(
            request_id=runtime_request_id,
            chatbot_id=chatbot["id"],
            status=status,
            latency_ms=latency_ms,
            raw_ip=client_ip,
            error_code=error_code,
        )


chatbot_agent_service = ChatbotAgentService()
