"""Public chatbot embed runtime endpoints."""

import asyncio
from html import escape
from typing import Optional
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from app.api.v1.schemas.chatbot_agent import (
    ChatbotEmbedConfigResponse,
    ChatbotEmbedStreamRequest,
    CreateEmbedSessionRequest,
    CreateEmbedSessionResponse,
    VisitorMessageRequest,
)
from app.chatbot_agents.live_chat_service import chatbot_live_chat_service
from app.chatbot_agents.service import chatbot_agent_service
from app.core.async_redis import async_redis_client
from app.core.config import CHATBOT_ALLOW_ANY_ORIGIN
from app.core.security import SecurityService

router = APIRouter(prefix="/chatbot-embed", tags=["chatbot-embed"])


def _allow_origin_bypass() -> bool:
    return CHATBOT_ALLOW_ANY_ORIGIN


def _normalize_origin(value: Optional[str]) -> str:
    if not value:
        return ""
    raw = value.strip().rstrip("/")
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return raw


def _resolve_request_origin(
    origin_header: Optional[str], embed_origin: Optional[str], referer_header: Optional[str] = None
) -> str:
    return _normalize_origin(embed_origin) or _normalize_origin(origin_header) or _normalize_origin(referer_header)


async def _enforce_public_rate_limit(request: Request, operation: str, limit: int, window_seconds: int) -> None:
    client_ip = request.client.host if request.client else "unknown"
    security_service = SecurityService()
    await security_service.enforce_rate_limit(client_ip, limit=limit, window_seconds=window_seconds, operation=operation)


def _raise_embed_error(exc: Exception) -> None:
    message = str(exc)
    if "temporarily disabled" in message:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=message) from exc
    if "not found" in message.lower():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message) from exc
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=message) from exc


@router.get("/config", response_model=ChatbotEmbedConfigResponse)
async def get_chatbot_embed_config(
    request: Request,
    token: str = Query(...),
    origin: Optional[str] = Header(None, alias="Origin"),
    embed_origin: Optional[str] = Query(None),
):
    await _enforce_public_rate_limit(request, operation="chatbot_embed_config", limit=60, window_seconds=60)

    request_origin = _resolve_request_origin(origin, embed_origin)
    if not request_origin and not _allow_origin_bypass():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Origin is required")
    if not request_origin:
        request_origin = "dev://no-origin"

    try:
        config = await chatbot_agent_service.get_public_embed_config(token=token, request_origin=request_origin)
    except ValueError as exc:
        _raise_embed_error(exc)
    return ChatbotEmbedConfigResponse(**config)


@router.post("/stream")
async def stream_chatbot_embed_response(
    request: Request,
    payload: ChatbotEmbedStreamRequest,
    token: str = Query(...),
    origin: Optional[str] = Header(None, alias="Origin"),
    embed_origin: Optional[str] = Query(None),
):
    await _enforce_public_rate_limit(request, operation="chatbot_embed_stream", limit=20, window_seconds=60)

    request_origin = _resolve_request_origin(origin, embed_origin)
    if not request_origin and not _allow_origin_bypass():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Origin is required")
    if not request_origin:
        request_origin = "dev://no-origin"

    try:
        config = await chatbot_agent_service.get_public_embed_config(token=token, request_origin=request_origin)
    except ValueError as exc:
        _raise_embed_error(exc)

    chatbot = await chatbot_agent_service.get_chatbot_agent(config["chatbot_id"])
    if not chatbot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chatbot not found")

    request_id = request.headers.get("x-request-id")
    client_ip = request.client.host if request.client else "unknown"
    history_payload = [entry.dict() for entry in payload.history]
    stream = chatbot_agent_service.stream_chat_reply(
        chatbot=chatbot,
        message=payload.message,
        history=history_payload,
        request_id=request_id,
        client_ip=client_ip,
    )

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(stream, media_type="text/event-stream", headers=headers)


@router.post("/sessions", response_model=CreateEmbedSessionResponse)
async def create_or_restore_chat_session(
    request: Request,
    payload: CreateEmbedSessionRequest,
    token: str = Query(...),
    origin: Optional[str] = Header(None, alias="Origin"),
    embed_origin: Optional[str] = Query(None),
):
    await _enforce_public_rate_limit(request, operation="chatbot_embed_sessions", limit=60, window_seconds=60)
    request_origin = _resolve_request_origin(origin, embed_origin)
    if not request_origin and not _allow_origin_bypass():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Origin is required")
    if not request_origin:
        request_origin = "dev://no-origin"

    try:
        session_payload = await chatbot_live_chat_service.create_or_restore_session(
            token=token,
            request_origin=request_origin,
            visitor_session_id=payload.visitor_session_id,
            page_url=payload.page_url,
            page_title=payload.page_title,
        )
    except Exception as exc:
        _raise_embed_error(exc)

    return CreateEmbedSessionResponse(**session_payload)


@router.post("/sessions/{session_id}/messages", response_model=CreateEmbedSessionResponse)
async def create_widget_message(
    session_id: str,
    request: Request,
    payload: VisitorMessageRequest,
    token: str = Query(...),
    origin: Optional[str] = Header(None, alias="Origin"),
    embed_origin: Optional[str] = Query(None),
):
    await _enforce_public_rate_limit(request, operation="chatbot_embed_message", limit=40, window_seconds=60)
    request_origin = _resolve_request_origin(origin, embed_origin)
    if not request_origin and not _allow_origin_bypass():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Origin is required")
    if not request_origin:
        request_origin = "dev://no-origin"

    try:
        message = await chatbot_live_chat_service.create_visitor_message(
            session_id=session_id,
            token=token,
            request_origin=request_origin,
            visitor_session_id=payload.visitor_session_id,
            message=payload.message,
        )
        session = await chatbot_live_chat_service._verify_widget_message_access(
            session_id=session_id,
            token=token,
            request_origin=request_origin,
            visitor_session_id=payload.visitor_session_id,
        )
    except Exception as exc:
        _raise_embed_error(exc)

    return CreateEmbedSessionResponse(session=session, messages=[message], session_token="")


@router.websocket("/live/{session_id}")
async def chatbot_embed_live_socket(websocket: WebSocket, session_id: str, session_token: str = Query(...)):
    request_origin = _normalize_origin(websocket.query_params.get("embed_origin"))
    if not request_origin:
        request_origin = _normalize_origin(websocket.headers.get("origin")) or "dev://no-origin"

    try:
        await chatbot_live_chat_service.verify_widget_session(session_id, session_token, request_origin)
    except Exception as exc:
        await websocket.close(code=4401, reason=str(exc))
        return

    await websocket.accept()
    client = await async_redis_client.get_client()
    pubsub = client.pubsub()
    await pubsub.subscribe(chatbot_live_chat_service.session_channel(session_id))
    await chatbot_live_chat_service._set_presence(session_id, "visitor", session_id)

    async def forward_events():
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message.get("type") == "message":
                    await websocket.send_text(message.get("data", "{}"))
                await asyncio.sleep(0.05)
        finally:
            await pubsub.unsubscribe(chatbot_live_chat_service.session_channel(session_id))
            await pubsub.close()

    task = asyncio.create_task(forward_events())
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        try:
            await task
        except Exception:
            pass
        await chatbot_live_chat_service._clear_presence(session_id, "visitor", session_id)


@router.get("/loader.js", include_in_schema=False)
async def get_chatbot_embed_loader(
    request: Request,
    token: str = Query(..., min_length=1),
    embed_origin: Optional[str] = Query(None),
    origin: Optional[str] = Header(None, alias="Origin"),
    referer: Optional[str] = Header(None, alias="Referer"),
):
    await _enforce_public_rate_limit(request, operation="chatbot_embed_loader", limit=120, window_seconds=60)

    request_origin = _resolve_request_origin(origin, embed_origin, referer)
    if not request_origin and not _allow_origin_bypass():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Origin is required")
    if not request_origin:
        request_origin = "dev://no-origin"

    try:
        config = await chatbot_agent_service.get_public_embed_config(token=token, request_origin=request_origin)
    except ValueError as exc:
        _raise_embed_error(exc)
    chatbot = await chatbot_agent_service.get_chatbot_agent(config["chatbot_id"])
    if not chatbot:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chatbot not found")

    launcher = chatbot["launcher_config"]
    position = launcher["position"]
    label = launcher["button_label"]
    accent = launcher["accent_color"]
    panel_query_params = {"token": token}
    if request_origin and request_origin != "dev://no-origin":
        panel_query_params["embed_origin"] = request_origin
    panel_url = f"{request.url_for('get_chatbot_embed_panel')}?{urlencode(panel_query_params)}"
    left = "20px" if position == "bottom-left" else "auto"
    right = "20px" if position == "bottom-right" else "auto"
    align_items = "flex-start" if position == "bottom-left" else "flex-end"

    script = f"""
(function () {{
  if (window.__asChatbotLauncherLoaded) return;
  window.__asChatbotLauncherLoaded = true;

  var root = document.createElement('div');
  root.id = 'as-chatbot-launcher-root';
  root.style.position = 'fixed';
  root.style.bottom = '20px';
  root.style.left = '{left}';
  root.style.right = '{right}';
  root.style.zIndex = '999999';
  root.style.fontFamily = 'Arial, sans-serif';
  root.style.display = 'flex';
  root.style.flexDirection = 'column';
  root.style.alignItems = '{align_items}';
  root.style.gap = '10px';

  var button = document.createElement('button');
  button.type = 'button';
  button.textContent = {label!r};
  button.style.border = '0';
  button.style.borderRadius = '999px';
  button.style.padding = '12px 16px';
  button.style.background = {accent!r};
  button.style.color = '#fff';
  button.style.fontWeight = '600';
  button.style.cursor = 'pointer';
  button.style.boxShadow = '0 8px 24px rgba(2, 6, 23, 0.25)';
  button.style.alignSelf = '{align_items}';

  var panel = document.createElement('div');
  panel.style.width = '380px';
  panel.style.maxWidth = 'calc(100vw - 24px)';
  panel.style.height = '560px';
  panel.style.maxHeight = '70vh';
  panel.style.background = '#fff';
  panel.style.border = '1px solid #d1d5db';
  panel.style.borderRadius = '14px';
  panel.style.overflow = 'hidden';
  panel.style.boxShadow = '0 16px 40px rgba(2, 6, 23, 0.26)';
  panel.style.display = 'none';

  var iframe = document.createElement('iframe');
  var iframeUrl = new URL({panel_url!r});
  iframeUrl.searchParams.set('page_url', window.location.href || '');
  iframeUrl.searchParams.set('page_title', document.title || '');
  iframe.src = iframeUrl.toString();
  iframe.title = 'Chatbot Panel';
  iframe.style.width = '100%';
  iframe.style.height = '100%';
  iframe.style.border = '0';
  iframe.loading = 'lazy';
  iframe.referrerPolicy = 'strict-origin-when-cross-origin';
  panel.appendChild(iframe);

  button.addEventListener('click', function () {{
    panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
  }});

  document.addEventListener('keydown', function (event) {{
    if (event.key === 'Escape') {{
      panel.style.display = 'none';
    }}
  }});

  root.appendChild(panel);
  root.appendChild(button);
  document.body.appendChild(root);
}})();
"""

    return Response(content=script, media_type="application/javascript")


@router.get("/panel", response_class=HTMLResponse, include_in_schema=False, name="get_chatbot_embed_panel")
async def get_chatbot_embed_panel(
    token: str = Query(...),
    embed_origin: Optional[str] = Query(None),
    page_url: Optional[str] = Query(None),
    page_title: Optional[str] = Query(None),
):
    safe_token = escape(token, quote=True)
    safe_embed_origin = escape(_normalize_origin(embed_origin), quote=True)
    safe_page_url = escape((page_url or '').strip(), quote=True)
    safe_page_title = escape((page_title or '').strip(), quote=True)

    html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Chatbot</title>
  <style>
    :root {{
      --primary: #2563eb;
      --bg: #ffffff;
      --text: #0f172a;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: "Inter", "Segoe UI", Arial, sans-serif; background: var(--bg); color: var(--text); }}
    .panel {{ display: flex; flex-direction: column; height: 100vh; background: var(--bg); }}
    .header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 16px;
      border-bottom: 1px solid #e2e8f0;
      background:
        radial-gradient(circle at 85% -40%, rgba(37,99,235,0.16), transparent 54%),
        linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
    }}
    .header-left {{ display: flex; align-items: center; gap: 10px; min-width: 0; }}
    .header-icon {{
      width: 34px;
      height: 34px;
      border-radius: 10px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: #0f172a;
      color: #ffffff;
      font-size: 14px;
      font-weight: 700;
      letter-spacing: -0.02em;
      box-shadow: 0 8px 20px rgba(15,23,42,0.2);
      flex-shrink: 0;
    }}
    .header-main {{ min-width: 0; }}
    .header-title {{ margin: 0; font-size: 18px; font-weight: 700; letter-spacing: -0.02em; color: #0f172a; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .header-subtitle {{ margin-top: 2px; font-size: 12px; color: #64748b; font-weight: 500; }}
    .header-status {{ display: inline-flex; align-items: center; gap: 6px; border: 1px solid #dbeafe; background: #eff6ff; color: #1d4ed8; border-radius: 999px; padding: 6px 10px; font-size: 11px; font-weight: 600; letter-spacing: 0.02em; white-space: nowrap; }}
    .header-status::before {{ content: ""; width: 7px; height: 7px; border-radius: 999px; background: #22c55e; box-shadow: 0 0 0 2px rgba(34,197,94,0.16); }}
    .messages {{ flex: 1; overflow-y: auto; padding: 14px; background: linear-gradient(180deg, rgba(248,250,252,0.92), rgba(255,255,255,1)); }}
    .message {{ max-width: 85%; padding: 11px 13px; border-radius: 16px; margin-bottom: 10px; white-space: pre-wrap; line-height: 1.45; font-size: 14px; }}
    .message.visitor {{ margin-left: auto; background: var(--primary); color: white; border-bottom-right-radius: 6px; }}
    .message.bot, .message.human {{ margin-right: auto; background: #f1f5f9; color: #0f172a; border: 1px solid #e2e8f0; border-bottom-left-radius: 6px; }}
    .message.system {{ margin-left: auto; margin-right: auto; background: #e2e8f0; color: #334155; font-size: 12px; text-align: center; }}
    .status {{ display: none; padding: 8px 14px; font-size: 12px; color: #64748b; border-top: 1px solid #eef2f7; }}
    .status.is-visible {{ display: block; }}
    .composer-wrap {{ display: flex; gap: 10px; padding: 12px; border-top: 1px solid #e5e7eb; background: #fff; }}
    .composer {{ flex: 1; min-height: 44px; max-height: 120px; resize: vertical; border: 1px solid #cbd5e1; border-radius: 14px; padding: 11px 12px; font: inherit; outline: none; }}
    .composer:focus {{ border-color: var(--primary); box-shadow: 0 0 0 3px rgba(37,99,235,0.12); }}
    .send-btn {{ border: 0; border-radius: 14px; background: var(--primary); color: white; font-weight: 600; padding: 0 18px; cursor: pointer; box-shadow: 0 8px 18px rgba(37,99,235,0.3); }}
    .send-btn:disabled, .composer:disabled {{ opacity: 0.6; cursor: not-allowed; }}
  </style>
</head>
<body>
  <div class="panel">
    <div class="header">
      <div class="header-left">
        <div class="header-icon">CS</div>
        <div class="header-main">
          <h1 class="header-title" id="chatbot-title">Loading chatbot...</h1>
          <div class="header-subtitle">Live support</div>
        </div>
      </div>
      <div class="header-status">Online</div>
    </div>
    <div class="messages" id="chatbot-messages"></div>
    <div class="status" id="chatbot-status">Initializing chatbot...</div>
    <div class="composer-wrap">
      <textarea id="chatbot-input" class="composer" rows="1" placeholder="Type your message..."></textarea>
      <button id="chatbot-send" class="send-btn" type="button">Send</button>
    </div>
  </div>
  <script>
    (async function () {{
      const token = {safe_token!r};
      const embedOrigin = {safe_embed_origin!r};
      const pageUrl = {safe_page_url!r};
      const pageTitle = {safe_page_title!r};
      const title = document.getElementById('chatbot-title');
      const messages = document.getElementById('chatbot-messages');
      const status = document.getElementById('chatbot-status');
      const input = document.getElementById('chatbot-input');
      const sendButton = document.getElementById('chatbot-send');
      const messageIds = new Set();
      let session = null;
      let sessionToken = '';
      let visitorSessionId = '';
      let socket = null;
      let reconnectTimer = null;
      let refreshingSessionToken = false;
      const hiddenSystemMessages = new Set([
        'A team member joined the chat.',
        'The chatbot is back in the conversation.'
      ]);

      const setStatus = function(message, visible) {{
        status.textContent = message || '';
        status.classList.toggle('is-visible', Boolean(visible && message));
      }};

      const getVisitorKey = function(chatbotId) {{
        return 'samai-chatbot-visitor:' + chatbotId + ':' + (embedOrigin || 'unknown');
      }};

      const generateVisitorId = function() {{
        return 'visitor-' + Math.random().toString(36).slice(2) + Date.now().toString(36);
      }};

      const getWsBase = function() {{
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return wsProtocol + '//' + window.location.host;
      }};

      const setComposerDisabled = function(disabled) {{
        input.disabled = disabled;
        sendButton.disabled = disabled;
      }};

      const renderStatus = function() {{
        if (!session) {{
          setStatus('', false);
          return;
        }}
        if (session.status === 'closed') {{
          setStatus('This chat has been closed.', true);
          setComposerDisabled(true);
          return;
        }}
        setStatus('', false);
      }};

      const appendMessage = function(message) {{
        if (!message || !message.id || messageIds.has(message.id)) {{
          return;
        }}
        if (message.sender_type === 'system' && hiddenSystemMessages.has(message.content || '')) {{
          messageIds.add(message.id);
          return;
        }}
        messageIds.add(message.id);
        const el = document.createElement('div');
        el.className = 'message ' + message.sender_type;
        el.textContent = message.content || '';
        messages.appendChild(el);
        messages.scrollTop = messages.scrollHeight;
      }};

      const connectSocket = function() {{
        if (!session || !sessionToken) return;
        if (socket) {{
          socket.close();
        }}
        const wsUrl = new URL(getWsBase() + '/api/v1/chatbot-embed/live/' + session.id);
        wsUrl.searchParams.set('session_token', sessionToken);
        if (embedOrigin) {{
          wsUrl.searchParams.set('embed_origin', embedOrigin);
        }}
        socket = new WebSocket(wsUrl.toString());
        socket.onmessage = function(event) {{
          try {{
            const payload = JSON.parse(event.data || '{{}}');
            if (payload.type === 'message.created' && payload.message) {{
              appendMessage(payload.message);
            }}
            if ((payload.type === 'session.state_changed' || payload.type === 'takeover.started' || payload.type === 'takeover.released' || payload.type === 'chat.closed') && payload.session) {{
              session = payload.session;
              renderStatus();
            }}
          }} catch (_error) {{}}
        }};
        socket.onclose = function() {{
          if (session && session.status === 'open') {{
            reconnectTimer = window.setTimeout(async function() {{
              const refreshed = await restoreSessionToken();
              if (refreshed) {{
                connectSocket();
              }}
            }}, 1500);
          }}
        }};
      }};

      const restoreSessionToken = async function() {{
        if (!session || refreshingSessionToken) return false;
        if (!visitorSessionId) return false;

        refreshingSessionToken = true;
        try {{
          const sessionUrl = new URL('./sessions', window.location.href);
          sessionUrl.searchParams.set('token', token);
          if (embedOrigin) {{
            sessionUrl.searchParams.set('embed_origin', embedOrigin);
          }}
          const sessionResponse = await fetch(sessionUrl.toString(), {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json', 'Accept': 'application/json' }},
            body: JSON.stringify({{ visitor_session_id: visitorSessionId, page_url: pageUrl, page_title: pageTitle }})
          }});
          const sessionPayload = await sessionResponse.json();
          if (!sessionResponse.ok) {{
            throw new Error(sessionPayload?.detail || 'Failed to refresh chat session');
          }}
          session = sessionPayload.session || session;
          sessionToken = sessionPayload.session_token || sessionToken;
          renderStatus();
          return Boolean(sessionToken);
        }} catch (error) {{
          setStatus(error?.message || 'Live connection lost', true);
          return false;
        }} finally {{
          refreshingSessionToken = false;
        }}
      }};

      const submitMessage = async function() {{
        const text = (input.value || '').trim();
        if (!text || !session) return;
        sendButton.disabled = true;
        try {{
          const url = new URL('./sessions/' + session.id + '/messages', window.location.href);
          url.searchParams.set('token', token);
          if (embedOrigin) {{
            url.searchParams.set('embed_origin', embedOrigin);
          }}
          const visitorSessionId = window.localStorage.getItem(getVisitorKey(session.chatbot_id));
          const response = await fetch(url.toString(), {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json', 'Accept': 'application/json' }},
            body: JSON.stringify({{ visitor_session_id: visitorSessionId, message: text }})
          }});
        const payload = await response.json();
        if (!response.ok) {{
          throw new Error(payload?.detail || 'Unable to send message');
        }}
        input.value = '';
        setStatus('', false);
      }} catch (error) {{
          setStatus(error?.message || 'Unable to send message', true);
      }} finally {{
          sendButton.disabled = false;
          input.focus();
        }}
      }};

      if (!token) {{
        setStatus('Missing token.', true);
        setComposerDisabled(true);
        return;
      }}

      try {{
        const configUrl = new URL('./config', window.location.href);
        configUrl.searchParams.set('token', token);
        if (embedOrigin) {{
          configUrl.searchParams.set('embed_origin', embedOrigin);
        }}
        const configResponse = await fetch(configUrl.toString(), {{ method: 'GET', headers: {{ 'Accept': 'application/json' }} }});
        const configPayload = await configResponse.json();
        if (!configResponse.ok) {{
          throw new Error(configPayload?.detail || 'Failed to load chatbot config');
        }}

        title.textContent = configPayload.name || 'Chatbot';
        document.documentElement.style.setProperty('--primary', configPayload.theme?.primary_color || '#2563eb');
        document.documentElement.style.setProperty('--bg', configPayload.theme?.background_color || '#ffffff');
        document.documentElement.style.setProperty('--text', configPayload.theme?.text_color || '#0f172a');

        const visitorStorageKey = getVisitorKey(configPayload.chatbot_id);
        visitorSessionId = window.localStorage.getItem(visitorStorageKey);
        if (!visitorSessionId) {{
          visitorSessionId = generateVisitorId();
          window.localStorage.setItem(visitorStorageKey, visitorSessionId);
        }}

        const sessionUrl = new URL('./sessions', window.location.href);
        sessionUrl.searchParams.set('token', token);
        if (embedOrigin) {{
          sessionUrl.searchParams.set('embed_origin', embedOrigin);
        }}
        const sessionResponse = await fetch(sessionUrl.toString(), {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json', 'Accept': 'application/json' }},
          body: JSON.stringify({{ visitor_session_id: visitorSessionId, page_url: pageUrl, page_title: pageTitle }})
        }});
        const sessionPayload = await sessionResponse.json();
        if (!sessionResponse.ok) {{
          throw new Error(sessionPayload?.detail || 'Failed to create chat session');
        }}

        session = sessionPayload.session;
        sessionToken = sessionPayload.session_token;
        messages.innerHTML = '';
        (sessionPayload.messages || []).forEach(appendMessage);
        renderStatus();
        setComposerDisabled(false);
        connectSocket();
        input.focus();
      }} catch (error) {{
        messages.innerHTML = '';
        const el = document.createElement('div');
        el.className = 'message system';
        el.textContent = error?.message || 'Unable to initialize chatbot.';
        messages.appendChild(el);
        setComposerDisabled(true);
        setStatus('Initialization failed', true);
        return;
      }}

      sendButton.addEventListener('click', submitMessage);
      input.addEventListener('keydown', function(event) {{
        if (event.key === 'Enter' && !event.shiftKey) {{
          event.preventDefault();
          submitMessage();
        }}
      }});
      window.addEventListener('beforeunload', function() {{
        if (reconnectTimer) window.clearTimeout(reconnectTimer);
        if (socket) socket.close();
      }});
    }})();
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html)
