"""Live chat session service for chatbot human takeover."""

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.api.v1.services.auth import auth_service
from app.chatbot_agents.repository import chatbot_agent_repository
from app.chatbot_agents.service import chatbot_agent_service
from app.chatbot_agents.token_service import chatbot_embed_token_service
from app.core.async_redis import async_redis_client


class ChatbotLiveChatService:
    def __init__(self):
        self.repository = chatbot_agent_repository
        self.redis = async_redis_client

    @staticmethod
    def session_channel(session_id: str) -> str:
        return f"chatbot_live:session:{session_id}"

    @staticmethod
    def session_presence_key(session_id: str, audience: str) -> str:
        return f"chatbot_live:presence:{session_id}:{audience}"

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_origin(origin: str) -> str:
        return (origin or "").strip().rstrip("/")

    @staticmethod
    def _normalize_page_url(page_url: Optional[str], origin: str) -> str:
        value = (page_url or "").strip()
        return value or origin

    @staticmethod
    def _build_visitor_label(visitor_session_id: str) -> str:
        token = ''.join(char for char in visitor_session_id if char.isalnum())[:6] or 'guest'
        return f"Visitor {token}"

    @staticmethod
    def _message_preview(content: str, limit: int = 140) -> str:
        text = (content or '').strip()
        if len(text) <= limit:
            return text
        return f"{text[: limit - 1].rstrip()}..."

    async def _publish_event(self, session_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        client = await self.redis.get_client()
        await client.publish(self.session_channel(session_id), json.dumps({"type": event_type, **payload}))

    async def _set_presence(self, session_id: str, audience: str, actor_id: str) -> None:
        key = self.session_presence_key(session_id, audience)
        await self.redis.sadd(key, actor_id)
        await self.redis.expire(key, 3600)

    async def _clear_presence(self, session_id: str, audience: str, actor_id: str) -> None:
        key = self.session_presence_key(session_id, audience)
        await self.redis.srem(key, actor_id)

    async def _create_message(
        self,
        session: Dict[str, Any],
        sender_type: str,
        content: str,
        sender_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = self._now_iso()
        payload = {
            "id": str(uuid.uuid4()),
            "session_id": session["id"],
            "chatbot_id": session["chatbot_id"],
            "sender_type": sender_type,
            "sender_id": sender_id,
            "content": content.strip(),
            "created_at": now,
        }
        message = await self.repository.create_chat_message(payload)
        await self.repository.update_chat_session(
            session["id"],
            {
                "last_message_preview": self._message_preview(content),
                "last_activity_at": now,
            },
        )
        refreshed = await self.repository.get_chat_session(session["id"])
        await self._publish_event(session["id"], "message.created", {"message": message})
        if refreshed:
            await self._publish_event(session["id"], "session.state_changed", {"session": refreshed})
        return message

    async def create_or_restore_session(
        self,
        token: str,
        request_origin: str,
        visitor_session_id: str,
        page_url: Optional[str],
        page_title: Optional[str],
    ) -> Dict[str, Any]:
        config = await chatbot_agent_service.get_public_embed_config(token=token, request_origin=request_origin)
        chatbot = await chatbot_agent_service.get_chatbot_agent(config["chatbot_id"])
        if not chatbot:
            raise ValueError("Chatbot not found")

        normalized_origin = self._normalize_origin(request_origin)
        prior_session_count = await self.repository.count_chat_sessions_for_visitor(
            chatbot_id=chatbot["id"],
            visitor_session_id=visitor_session_id,
            origin=normalized_origin,
        )
        existing = await self.repository.find_open_chat_session(
            chatbot_id=chatbot["id"],
            visitor_session_id=visitor_session_id,
            origin=normalized_origin,
        )

        if existing:
            now = self._now_iso()
            session = await self.repository.update_chat_session(
                existing["id"],
                {
                    "page_url": self._normalize_page_url(page_url, normalized_origin),
                    "page_title": (page_title or '').strip(),
                    "last_activity_at": now,
                    "last_restored_at": now,
                    "is_returning_visitor": True,
                },
            )
        else:
            now = self._now_iso()
            payload = {
                "id": str(uuid.uuid4()),
                "chatbot_id": chatbot["id"],
                "owner_user_id": chatbot.get("owner_user_id", ''),
                "origin": normalized_origin,
                "page_url": self._normalize_page_url(page_url, normalized_origin),
                "page_title": (page_title or '').strip(),
                "visitor_session_id": visitor_session_id,
                "visitor_label": self._build_visitor_label(visitor_session_id),
                "is_returning_visitor": prior_session_count > 0,
                "status": "open",
                "control_mode": "bot",
                "assigned_operator_id": None,
                "assigned_operator_name": None,
                "started_at": now,
                "last_activity_at": now,
                "last_restored_at": None,
                "taken_over_at": None,
                "released_at": None,
                "closed_at": None,
                "last_message_preview": chatbot.get("welcome_message", 'Hello!'),
            }
            session = await self.repository.create_chat_session(payload)
            await self._create_message(session, "bot", chatbot.get("welcome_message", 'Hello!'))
            session = await self.repository.get_chat_session(session["id"])

        messages = await self.repository.list_chat_messages(session["id"], limit=250)
        session_token = chatbot_embed_token_service.create_session_token(
            session_id=session["id"],
            chatbot_id=chatbot["id"],
            origin=normalized_origin,
            visitor_session_id=visitor_session_id,
        )
        return {"session": session, "messages": messages, "session_token": session_token["token"]}

    async def verify_widget_session(self, session_id: str, session_token: str, request_origin: str) -> Dict[str, Any]:
        claims = chatbot_embed_token_service.verify_session_token(session_token)
        if claims.get("sub") != session_id:
            raise ValueError("Session token does not match session")
        if self._normalize_origin(claims.get("origin", '')) != self._normalize_origin(request_origin):
            raise ValueError("Session token origin mismatch")

        session = await self.repository.get_chat_session(session_id)
        if not session:
            raise ValueError("Chat session not found")
        if session.get("status") != "open":
            raise ValueError("Chat session is closed")
        if str(session.get("chatbot_id", '')) != str(claims.get("chatbot_id", '')):
            raise ValueError("Session token chatbot mismatch")
        if str(session.get("visitor_session_id", '')) != str(claims.get("visitor_session_id", '')):
            raise ValueError("Session token visitor mismatch")
        return session

    async def _verify_widget_message_access(
        self,
        session_id: str,
        token: str,
        request_origin: str,
        visitor_session_id: str,
    ) -> Dict[str, Any]:
        config = await chatbot_agent_service.get_public_embed_config(token=token, request_origin=request_origin)
        session = await self.repository.get_chat_session(session_id)
        if not session:
            raise ValueError("Chat session not found")
        if session.get("status") != "open":
            raise ValueError("Chat session is closed")
        if str(session.get("chatbot_id", '')) != str(config.get("chatbot_id", '')):
            raise ValueError("Chat session does not belong to this chatbot")
        if self._normalize_origin(session.get("origin", '')) != self._normalize_origin(request_origin):
            raise ValueError("Chat session origin mismatch")
        if str(session.get("visitor_session_id", '')) != str(visitor_session_id):
            raise ValueError("Visitor session mismatch")
        return session

    async def create_visitor_message(
        self,
        session_id: str,
        token: str,
        request_origin: str,
        visitor_session_id: str,
        message: str,
    ) -> Dict[str, Any]:
        session = await self._verify_widget_message_access(session_id, token, request_origin, visitor_session_id)
        visitor_message = await self._create_message(session, "visitor", message)
        latest_session = await self.repository.get_chat_session(session_id)
        if latest_session and latest_session.get("status") == "open" and latest_session.get("control_mode") == "bot":
            asyncio.create_task(self._generate_bot_reply(session_id))
        return visitor_message

    async def _generate_bot_reply(self, session_id: str) -> None:
        session = await self.repository.get_chat_session(session_id)
        if not session or session.get("status") != "open" or session.get("control_mode") != "bot":
            return

        chatbot = await chatbot_agent_service.get_chatbot_agent(session["chatbot_id"])
        if not chatbot:
            return

        messages = await self.repository.list_chat_messages(session_id, limit=250)
        last_visitor = None
        history: List[Dict[str, str]] = []
        for entry in messages:
            sender_type = str(entry.get("sender_type", '')).lower()
            content = str(entry.get("content", '')).strip()
            if not content:
                continue
            if sender_type == "visitor":
                if last_visitor is not None:
                    history.append({"role": "user", "content": last_visitor.get("content", '').strip()})
                last_visitor = entry
                continue
            if sender_type in {"bot", "human"}:
                history.append({"role": "assistant", "content": content})

        if not last_visitor:
            return

        started_at = time.perf_counter()
        status = "success"
        error_code = None
        assistant_text = ""

        try:
            llm_payload = {
                "system_instruction": {"parts": [{"text": chatbot_agent_service._build_system_instruction(chatbot)}]},
                "contents": chatbot_agent_service._build_chat_contents(history=history, message=last_visitor.get("content", '')),
                "generationConfig": {"temperature": 0.45, "maxOutputTokens": 800},
            }
            async for event in chatbot_agent_service.llm_provider.stream_reply(llm_payload):
                if event.get("type") == "delta":
                    assistant_text += event.get("text", '')
                elif event.get("type") == "done" and not assistant_text:
                    assistant_text = event.get("full_text", '') or assistant_text
                elif event.get("type") == "error":
                    status = "error"
                    error_code = "runtime_generation_error"
                    assistant_text = event.get("message", '')

            latest_session = await self.repository.get_chat_session(session_id)
            if not latest_session or latest_session.get("status") != "open" or latest_session.get("control_mode") != "bot":
                return

            if assistant_text.strip():
                await self._create_message(latest_session, "bot", assistant_text.strip())
        except Exception:
            status = "error"
            error_code = error_code or "runtime_generation_error"
        finally:
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            await chatbot_agent_service._create_runtime_log(
                request_id=f"live-{session_id}-{last_visitor.get('id', uuid.uuid4())}",
                chatbot_id=chatbot["id"],
                status=status,
                latency_ms=latency_ms,
                raw_ip="live-chat",
                error_code=error_code,
            )

    async def list_live_chats_for_user(self, current_user: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]:
        user_id = str(current_user.get("id", ''))
        sessions = await self.repository.list_chat_sessions_for_owner(user_id, limit=limit)
        return [session for session in sessions if session.get("status") == "open"]

    async def get_live_chat_detail_for_user(self, session_id: str, current_user: Dict[str, Any]) -> Dict[str, Any]:
        session = await self.repository.get_chat_session(session_id)
        if not session:
            raise LookupError("Chat session not found")
        chatbot = await chatbot_agent_service.get_chatbot_agent(session["chatbot_id"])
        if not chatbot:
            raise LookupError("Chatbot not found")
        if not chatbot_agent_service.can_manage(chatbot, user_id=str(current_user.get("id", '')), role=str(current_user.get("role", 'user')).lower()):
            raise PermissionError("Access denied")
        messages = await self.repository.list_chat_messages(session_id, limit=250)
        return {"session": session, "messages": messages}

    async def _acquire_takeover_lock(self, session_id: str, operator_id: str) -> bool:
        client = await self.redis.get_client()
        return bool(await client.set(f"chatbot_live:takeover-lock:{session_id}", operator_id, ex=10, nx=True))

    async def _release_takeover_lock(self, session_id: str) -> None:
        await self.redis.delete(f"chatbot_live:takeover-lock:{session_id}")

    async def take_over_chat(self, session_id: str, current_user: Dict[str, Any]) -> Dict[str, Any]:
        session = await self.repository.get_chat_session(session_id)
        if not session:
            raise LookupError("Chat session not found")
        if session.get("status") != "open":
            raise ValueError("Chat session is closed")

        chatbot = await chatbot_agent_service.get_chatbot_agent(session["chatbot_id"])
        if not chatbot or not chatbot_agent_service.can_manage(chatbot, user_id=str(current_user.get("id", '')), role=str(current_user.get("role", 'user')).lower()):
            raise PermissionError("Access denied")

        operator_id = str(current_user.get("id", ''))
        if not await self._acquire_takeover_lock(session_id, operator_id):
            raise RuntimeError("Chat is being claimed by another operator")

        try:
            latest = await self.repository.get_chat_session(session_id)
            if latest.get("control_mode") == "human" and latest.get("assigned_operator_id") not in {None, operator_id}:
                raise RuntimeError("Chat is already assigned to another operator")

            updated = await self.repository.update_chat_session(
                session_id,
                {
                    "control_mode": "human",
                    "assigned_operator_id": operator_id,
                    "assigned_operator_name": f"{current_user.get('first_name', '')} {current_user.get('last_name', '')}".strip() or current_user.get('email', 'Operator'),
                    "taken_over_at": self._now_iso(),
                    "last_activity_at": self._now_iso(),
                },
            )
            await self._publish_event(session_id, "takeover.started", {"session": updated})
            await self._publish_event(session_id, "session.state_changed", {"session": updated})
            system_message = await self._create_message(updated, "system", "A team member joined the chat.", sender_id=operator_id)
            return {"session": await self.repository.get_chat_session(session_id), "message": system_message}
        finally:
            await self._release_takeover_lock(session_id)

    async def release_chat(self, session_id: str, current_user: Dict[str, Any]) -> Dict[str, Any]:
        await self.get_live_chat_detail_for_user(session_id, current_user)
        updated = await self.repository.update_chat_session(
            session_id,
            {
                "control_mode": "bot",
                "assigned_operator_id": None,
                "assigned_operator_name": None,
                "released_at": self._now_iso(),
                "last_activity_at": self._now_iso(),
            },
        )
        await self._publish_event(session_id, "takeover.released", {"session": updated})
        await self._publish_event(session_id, "session.state_changed", {"session": updated})
        system_message = await self._create_message(updated, "system", "The chatbot is back in the conversation.")
        return {"session": await self.repository.get_chat_session(session_id), "message": system_message}

    async def send_operator_message(self, session_id: str, current_user: Dict[str, Any], content: str) -> Dict[str, Any]:
        detail = await self.get_live_chat_detail_for_user(session_id, current_user)
        session = detail["session"]
        operator_id = str(current_user.get("id", ''))
        if session.get("status") != "open":
            raise ValueError("Chat session is closed")
        if session.get("control_mode") != "human":
            raise ValueError("Chat must be in human takeover mode")
        if session.get("assigned_operator_id") not in {operator_id, None}:
            raise PermissionError("Chat is assigned to another operator")

        latest = await self.repository.get_chat_session(session_id)
        message = await self._create_message(latest, "human", content, sender_id=operator_id)
        return {"session": await self.repository.get_chat_session(session_id), "message": message}

    async def close_chat(self, session_id: str, current_user: Dict[str, Any]) -> Dict[str, Any]:
        await self.get_live_chat_detail_for_user(session_id, current_user)
        updated = await self.repository.update_chat_session(
            session_id,
            {
                "status": "closed",
                "closed_at": self._now_iso(),
                "last_activity_at": self._now_iso(),
            },
        )
        await self._publish_event(session_id, "chat.closed", {"session": updated})
        await self._publish_event(session_id, "session.state_changed", {"session": updated})
        system_message = await self._create_message(updated, "system", "This chat has been closed.")
        return {"session": await self.repository.get_chat_session(session_id), "message": system_message}

    async def authenticate_ws_operator(self, access_token: str) -> Dict[str, Any]:
        if not access_token or not str(access_token).strip():
            raise PermissionError("Missing access token")
        payload = auth_service.verify_token(access_token, token_type="access")
        if not payload or not payload.get("sub"):
            raise PermissionError("Access token is invalid or expired")
        user = await auth_service.get_user_by_id(str(payload.get("sub")))
        if not user:
            raise PermissionError("User not found")
        return user


chatbot_live_chat_service = ChatbotLiveChatService()
