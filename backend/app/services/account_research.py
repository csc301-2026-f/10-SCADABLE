"""Authenticate server-to-server, per-account research key overrides.

Clerk privateMetadata is owned by the Next.js server. Only that server may
forward a signed-in user's key for *one* research request over HTTPS. Secrets
are kept in memory for the background task; never stored in our database or
placed in environment variables (which would leak across concurrent users).

The ordinary public research API still works with the deployment key while
account routing is being connected on the frontend.
"""

import os
import secrets

from fastapi import Header, HTTPException


# No untrusted browser request can choose another user's API key without the
# distinct server-only bridge token. The Next.js proxy must authenticate its
# own Clerk user before looking up their privateMetadata.openaiApiKey.
def get_account_research_key(
    account_openai_key: str | None = Header(
        default=None, alias="X-Scadable-Account-OpenAI-Key"
    ),
    bridge_token: str | None = Header(
        default=None, alias="X-Scadable-Research-Bridge-Token"
    ),
) -> str | None:
    """Validate an optional privileged override; None means deployment key.

    Two headers must travel together. Never log, serialize, or store either
    value, including in 401/422 errors.
    """
    if account_openai_key is None and bridge_token is None:
        return None

    expected = os.getenv("ACCOUNT_RESEARCH_BRIDGE_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status_code=503, detail="Account-based research is not configured."
        )
    if not bridge_token or not secrets.compare_digest(bridge_token, expected):
        raise HTTPException(status_code=401, detail="Unauthorized research request.")
    if not account_openai_key:
        raise HTTPException(status_code=422, detail="Missing account OpenAI key.")

    key = account_openai_key.strip()
    if (not key.startswith("sk-") or len(key) < 20 or len(key) > 512
            or any(ch in key for ch in "\r\n\0")):
        raise HTTPException(status_code=422, detail="Invalid account OpenAI key.")
    return key
