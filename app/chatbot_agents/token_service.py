"""
Embed token generation and verification for chatbot launcher usage.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

from app.core.config import CHATBOT_EMBED_SECRET, CHATBOT_EMBED_TOKEN_TTL_MINUTES, JWT_ALGORITHM


class ChatbotEmbedTokenService:
    """Service for issuing and validating chatbot embed tokens."""

    def create_token(
        self,
        chatbot_id: str,
        origin: str,
        version: int,
        expires_in_minutes: Optional[int] = None,
        never_expires: bool = False,
    ) -> Dict[str, Optional[str]]:
        """Create embed token scoped to chatbot and origin."""
        now = datetime.now(timezone.utc)

        payload = {
            "sub": chatbot_id,
            "type": "chatbot_embed",
            "origin": origin,
            "ver": int(version),
            "iat": int(now.timestamp()),
        }
        expires_at: Optional[datetime] = None
        if never_expires:
            payload["nexp"] = True
        else:
            ttl_minutes = int(expires_in_minutes or CHATBOT_EMBED_TOKEN_TTL_MINUTES)
            expires_at = now + timedelta(minutes=ttl_minutes)
            payload["exp"] = int(expires_at.timestamp())

        token = jwt.encode(payload, CHATBOT_EMBED_SECRET, algorithm=JWT_ALGORITHM)

        return {
            "token": token,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "never_expires": bool(never_expires),
        }

    def create_session_token(self, session_id: str, chatbot_id: str, origin: str, visitor_session_id: str) -> Dict[str, str]:
        """Create short-lived session token for websocket subscriptions."""
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=CHATBOT_EMBED_TOKEN_TTL_MINUTES)

        payload = {
            "sub": session_id,
            "type": "chatbot_embed_session",
            "chatbot_id": chatbot_id,
            "origin": origin,
            "visitor_session_id": visitor_session_id,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
        token = jwt.encode(payload, CHATBOT_EMBED_SECRET, algorithm=JWT_ALGORITHM)

        return {"token": token, "expires_at": expires_at.isoformat()}

    def verify_token(self, token: str) -> Dict[str, Any]:
        """Verify token signature and claims."""
        try:
            payload = jwt.decode(token, CHATBOT_EMBED_SECRET, algorithms=[JWT_ALGORITHM])
        except ExpiredSignatureError as exc:
            raise ValueError("Embed token has expired") from exc
        except JWTError as exc:
            raise ValueError("Invalid embed token") from exc

        if payload.get("type") != "chatbot_embed":
            raise ValueError("Invalid embed token type")
        if not payload.get("sub"):
            raise ValueError("Invalid embed token subject")
        if not payload.get("origin"):
            raise ValueError("Invalid embed token origin")
        if "ver" not in payload:
            raise ValueError("Invalid embed token version")
        if "exp" not in payload and not payload.get("nexp"):
            raise ValueError("Invalid embed token expiry")

        return payload

    def verify_session_token(self, token: str) -> Dict[str, Any]:
        """Verify widget session token."""
        try:
            payload = jwt.decode(token, CHATBOT_EMBED_SECRET, algorithms=[JWT_ALGORITHM])
        except ExpiredSignatureError as exc:
            raise ValueError("Session token has expired") from exc
        except JWTError as exc:
            raise ValueError("Invalid session token") from exc

        if payload.get("type") != "chatbot_embed_session":
            raise ValueError("Invalid session token type")
        if not payload.get("sub"):
            raise ValueError("Invalid session token subject")
        if not payload.get("chatbot_id"):
            raise ValueError("Invalid session token chatbot id")
        if not payload.get("origin"):
            raise ValueError("Invalid session token origin")
        if not payload.get("visitor_session_id"):
            raise ValueError("Invalid session token visitor session")

        return payload


chatbot_embed_token_service = ChatbotEmbedTokenService()
