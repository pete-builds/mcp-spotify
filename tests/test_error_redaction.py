"""Upstream error bodies do not reach a tool result.

A tool result goes straight into an agent's context and is frequently logged
and summarised from there, so an upstream body is the wrong thing to forward
verbatim.

The token endpoint is the sharp case: the request carries the client secret in
a Basic header and the refresh token in its body, and an OAuth error response
echoes request parameters back. That is the one response on this client whose
body can plausibly contain credential material, and it was the one being pasted
in full.
"""

from __future__ import annotations

import os

import httpx
import pytest

os.environ.setdefault("SPOTIFY_CLIENT_ID", "test-id")
os.environ.setdefault("SPOTIFY_CLIENT_SECRET", "test-secret")
os.environ.setdefault("SPOTIFY_REFRESH_TOKEN", "test-refresh")

from clients.spotify import SpotifyClient, SpotifyError

SECRET = "AQD-do-not-leak-this-refresh-token"


def _describe(status: int, body: str, headers=None, context="Token refresh"):
    resp = httpx.Response(
        status,
        content=body.encode(),
        headers=headers or {"content-type": "application/json"},
        request=httpx.Request("POST", "https://accounts.spotify.com/api/token"),
    )
    return SpotifyClient._describe_error(resp, context)


def test_an_oauth_error_echoing_the_refresh_token_is_not_forwarded():
    """error_description is where OAuth servers quote the request back."""
    body = (
        '{"error": "invalid_grant", '
        f'"error_description": "Refresh token {SECRET} is not valid"}}'
    )
    message = _describe(400, body)

    assert SECRET not in message
    # The machine-readable code is safe and useful, so it is kept.
    assert "invalid_grant" in message
    assert "400" in message


def test_an_html_edge_page_is_not_pasted_into_the_result():
    """A proxy or maintenance page is not a Spotify error contract at all.

    Forwarding it verbatim is how a plain 502 becomes a page of markup in the
    model's context.
    """
    html = "<html><head><title>502 Bad Gateway</title></head><body>" + "x" * 4000
    message = _describe(502, html, headers={"content-type": "text/html"})

    assert "<html>" not in message
    assert "502" in message
    assert len(message) < 200


def test_the_web_api_json_reason_is_kept():
    """The documented shape carries a short, safe, genuinely useful message."""
    body = '{"error": {"status": 404, "message": "Playlist not found"}}'
    message = _describe(404, body, context="Spotify API GET /v1/playlists/x")

    assert "Playlist not found" in message
    assert "404" in message
    assert "/v1/playlists/x" in message


def test_a_reason_is_bounded_even_when_the_upstream_message_is_not():
    """A "short, known field" is only short if something enforces it."""
    body = '{"error": {"status": 400, "message": "%s"}}' % ("y" * 5000)
    assert len(_describe(400, body)) < 300


def test_an_empty_body_still_produces_something_diagnosable():
    """An error nobody can act on is its own problem."""
    message = _describe(500, "")
    assert "500" in message
    assert "Token refresh" in message


@pytest.mark.asyncio
async def test_a_failed_refresh_raises_without_the_body():
    """End to end through the real refresh path, not just the helper."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": "invalid_grant",
                  "error_description": f"token {SECRET} revoked"},
        )

    client = SpotifyClient(
        client_id="id", client_secret="secret", refresh_token=SECRET
    )
    # The constructor builds its own client; swap the transport rather than
    # adding an injection seam to production code just for a test.
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    with pytest.raises(SpotifyError) as caught:
        await client._refresh_access_token()

    assert SECRET not in str(caught.value)
    await client.close()
