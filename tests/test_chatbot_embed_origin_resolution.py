"""
Tests for embed origin resolution helper.
"""

from app.api.v1.routers.chatbot_embed import _resolve_request_origin


def test_resolve_request_origin_prefers_embed_origin():
    resolved = _resolve_request_origin(
        origin_header="https://origin.example.com",
        embed_origin="https://embed.example.com/path",
        referer_header="https://ref.example.com/page",
    )
    assert resolved == "https://embed.example.com"


def test_resolve_request_origin_falls_back_to_origin_header():
    resolved = _resolve_request_origin(
        origin_header="https://origin.example.com",
        embed_origin=None,
        referer_header="https://ref.example.com/page",
    )
    assert resolved == "https://origin.example.com"


def test_resolve_request_origin_falls_back_to_referer():
    resolved = _resolve_request_origin(
        origin_header=None,
        embed_origin=None,
        referer_header="https://site.example.com/some/page?x=1",
    )
    assert resolved == "https://site.example.com"
