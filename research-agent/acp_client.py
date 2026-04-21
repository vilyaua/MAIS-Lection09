"""Patched ACP client — fixes serialization bug in acp-sdk 1.0.3.

The upstream Client uses `content=model.model_dump_json()` in httpx calls,
which sends raw bytes without Content-Type: application/json header.
The server rejects this with 422.

This subclass overrides the internal httpx client to inject the correct
Content-Type header, making the SDK client work properly.

See: FINDINGS.md section "acp-sdk Client Serialization Bug"
Repo archived at 1.0.3 (Aug 2025) — will never be fixed upstream.
"""

from acp_sdk.client import Client


class PatchedACPClient(Client):
    """ACP Client with fixed JSON serialization."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Wrap the internal httpx client's post method to fix Content-Type
        original_post = self._client.post

        async def _patched_post(url, *, content=None, json=None, **kw):
            # If content is a string (serialized JSON) and no json kwarg,
            # pass it as content but ensure Content-Type is set
            if content is not None and json is None:
                headers = kw.pop("headers", {})
                headers["Content-Type"] = "application/json"
                return await original_post(url, content=content, headers=headers, **kw)
            return await original_post(url, content=content, json=json, **kw)

        self._client.post = _patched_post
