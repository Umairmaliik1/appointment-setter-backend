"""
Backfill chatbot_agents collection with v2 production fields.

Usage:
  python scripts/migrate_chatbot_agents_v2.py
"""

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict

# Ensure project root is importable when running this script directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.store import store


def build_defaults(chatbot: Dict[str, Any]) -> Dict[str, Any]:
    launcher_label = chatbot.get("launcher_label") or "Chat with us"
    primary_color = chatbot.get("theme", {}).get("primary_color", "#2563eb")
    status = chatbot.get("status", "active")
    return {
        "status": status,
        "domain_key": "custom",
        "custom_domain_name": chatbot.get("name", "General Assistant"),
        "behavior_config": {
            "persona": "Helpful assistant",
            "goal": "Help users with clear and concise answers",
            "tone": "friendly",
            "response_style": "balanced",
            "allowed_topics": [],
            "blocked_topics": [],
            "escalation_instructions": "Escalate complex or sensitive requests to a human agent.",
            "custom_instructions": "",
            "language": "en",
        },
        "knowledge_config": {
            "business_facts": chatbot.get("welcome_message", "General chatbot assistant."),
            "faq_items": [],
        },
        "launcher_config": {
            "mode": "launcher",
            "position": "bottom-right",
            "button_label": launcher_label,
            "button_icon": "message-circle",
            "accent_color": primary_color,
        },
        "embed_token_version": 1,
    }


async def run() -> None:
    chatbots = await store.list_chatbot_agents(limit=10000, offset=0)
    updated = 0
    skipped = 0

    for chatbot in chatbots:
        chatbot_id = chatbot.get("id")
        if not chatbot_id:
            skipped += 1
            continue

        required_fields = [
            "domain_key",
            "behavior_config",
            "knowledge_config",
            "launcher_config",
            "embed_token_version",
        ]
        if all(field in chatbot for field in required_fields):
            skipped += 1
            continue

        defaults = build_defaults(chatbot)
        await store.update_chatbot_agent(chatbot_id, defaults)
        updated += 1

    print(f"Completed chatbot v2 migration. Updated: {updated}, Skipped: {skipped}")


if __name__ == "__main__":
    asyncio.run(run())
