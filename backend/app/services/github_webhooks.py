"""Validate GitHub App push webhooks without depending on the SWOT pipeline."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any


class InvalidWebhook(ValueError):
    """Invalid signature or malformed webhook event."""


@dataclass(frozen=True)
class MainPushEvent:
    installation_id: int
    repository_id: int
    repository_full_name: str
    commit_sha: str
    delivery_id: str


def verify_github_signature(body: bytes, signature: str | None, secret: str) -> None:
    """Reject missing, malformed or incorrectly signed webhook payloads."""
    if not secret or not signature or not signature.startswith("sha256="):
        raise InvalidWebhook("Missing webhook secret or SHA-256 signature")
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise InvalidWebhook("Invalid webhook signature")


def parse_main_push(
    event_name: str | None,
    delivery_id: str | None,
    body: bytes,
) -> MainPushEvent | None:
    """Return only non-deleted pushes to `main` from an app installation.

    Merging a PR into main normally produces a push event as well; no extra PR
    subscription is needed. Unknown event types/branches are safely ignored.
    """
    if event_name != "push":
        return None
    if not delivery_id or len(delivery_id) > 128:
        raise InvalidWebhook("Missing GitHub delivery ID")
    try:
        data: Any = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidWebhook("Invalid JSON webhook payload") from exc
    if not isinstance(data, dict):
        raise InvalidWebhook("Invalid webhook payload shape")
    if data.get("ref") != "refs/heads/main" or data.get("deleted") is True:
        return None
    try:
        installation_id = int(data["installation"]["id"])
        repo_id = int(data["repository"]["id"])
        full_name = str(data["repository"]["full_name"])
        sha = str(data["after"])
    except (KeyError, ValueError, TypeError) as exc:
        raise InvalidWebhook("Missing installation or repository in push event") from exc
    if installation_id <= 0 or repo_id <= 0 or len(sha) != 40 or not all(c in "0123456789abcdefABCDEF" for c in sha):
        raise InvalidWebhook("Invalid installation, repository or commit SHA")
    return MainPushEvent(installation_id, repo_id, full_name, sha.lower(), delivery_id)
