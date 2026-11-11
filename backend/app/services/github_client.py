"""Minimal GitHub App REST client, independent from SCADABLE baseline parsing.

The app's private key stays server-side. Installation access tokens are short-lived
and are never returned by the web API. Requires PyJWT[crypto] and httpx.
"""

from __future__ import annotations

import base64
import binascii
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

GITHUB_API = "https://api.github.com"
MAX_GITHUB_FEATURE_FILE_BYTES = 128 * 1024


class GitHubClientError(RuntimeError):
    """A safe, non-secret-bearing error for a failed GitHub API operation."""


@dataclass(frozen=True)
class GitHubRepo:
    id: int
    full_name: str
    default_branch: str
    private: bool

    @classmethod
    def from_api(cls, value: dict[str, Any]) -> GitHubRepo:
        return cls(
            id=int(value["id"]),
            full_name=str(value["full_name"]),
            default_branch=str(value.get("default_branch") or "main"),
            private=bool(value.get("private", False)),
        )


@dataclass(frozen=True)
class GitHubFileContents:
    text: str
    blob_sha: str


class GitHubAppClient:
    def __init__(
        self,
        app_id: str,
        private_key: str,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not app_id.strip() or not private_key.strip():
            raise ValueError("GitHub App ID and private key are required")
        self._app_id = app_id.strip()
        self._private_key = private_key.replace("\\n", "\n")
        self._http = http_client or httpx.Client(timeout=10.0)
        self._owns_http = http_client is None

    @classmethod
    def from_env(cls) -> GitHubAppClient:
        app_id = os.getenv("GITHUB_APP_ID", "")
        private_key = os.getenv("GITHUB_APP_PRIVATE_KEY", "")
        if not app_id or not private_key:
            raise GitHubClientError("GitHub App credentials are not configured")
        return cls(app_id, private_key)

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> GitHubAppClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _app_jwt(self) -> str:
        try:
            import jwt  # PyJWT[crypto], imported lazily for isolated route tests
        except ImportError as exc:
            raise GitHubClientError("Install PyJWT[crypto] for GitHub App authentication") from exc
        now = int(time.time())
        try:
            return jwt.encode(
                {"iat": now - 60, "exp": now + 540, "iss": self._app_id},
                self._private_key,
                algorithm="RS256",
            )
        except Exception as exc:
            # Do not expose the PEM key or raw signing details.
            raise GitHubClientError("Unable to sign GitHub App authentication token") from exc

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        params: dict[str, str | int] | None = None,
    ) -> Any:
        try:
            response = self._http.request(
                method,
                f"{GITHUB_API}{path}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                params=params,
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Never put tokens or GitHub error bodies into API responses.
            raise GitHubClientError("GitHub API request failed") from exc

    def list_installations(self) -> list[dict[str, Any]]:
        """List organizations/accounts where the GitHub App is installed."""
        installations: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self._request(
                "GET", "/app/installations", token=self._app_jwt(),
                params={"per_page": 100, "page": page},
            )
            if not isinstance(data, list):
                raise GitHubClientError("Unexpected GitHub installations response")
            for item in data:
                installations.append({
                    "id": int(item["id"]),
                    "account": str(item["account"]["login"]),
                    "account_type": str(item["account"].get("type", "")),
                })
            if len(data) < 100:
                break
            page += 1
        return installations

    def installation_token(self, installation_id: int) -> str:
        if installation_id <= 0:
            raise ValueError("installation_id must be positive")
        data = self._request(
            "POST", f"/app/installations/{installation_id}/access_tokens",
            token=self._app_jwt(),
        )
        if not isinstance(data, dict) or not isinstance(data.get("token"), str):
            raise GitHubClientError("No GitHub installation token returned")
        return data["token"]

    def list_repositories(self, installation_id: int) -> list[GitHubRepo]:
        """Return all repositories authorized for this installation (paginated)."""
        token = self.installation_token(installation_id)
        repos: list[GitHubRepo] = []
        page = 1
        while True:
            data = self._request(
                "GET", "/installation/repositories", token=token,
                params={"per_page": 100, "page": page},
            )
            if not isinstance(data, dict) or not isinstance(data.get("repositories"), list):
                raise GitHubClientError("Unexpected GitHub repositories response")
            batch = data["repositories"]
            try:
                repos.extend(GitHubRepo.from_api(item) for item in batch)
            except (ValueError, TypeError, KeyError) as exc:
                raise GitHubClientError("Invalid repository metadata from GitHub") from exc
            if len(batch) < 100:
                break
            page += 1
        return repos

    def read_repository_file_with_metadata(
        self, installation_id: int, full_name: str, file_path: str, *, ref: str = "main"
    ) -> GitHubFileContents:
        """Read a small UTF-8 file at a branch or immutable commit reference."""
        parts = full_name.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("full_name must be owner/repository")
        segments = file_path.split("/")
        if not segments or any(p in ("", ".", "..") for p in segments):
            raise ValueError("file_path must be a normal repository-relative path")
        owner, repo = (quote(part, safe="") for part in parts)
        encoded_path = "/".join(quote(part, safe="") for part in segments)
        token = self.installation_token(installation_id)
        data = self._request(
            "GET", f"/repos/{owner}/{repo}/contents/{encoded_path}",
            token=token, params={"ref": ref},
        )
        if not isinstance(data, dict) or data.get("type") != "file":
            raise GitHubClientError("Requested path is not a file")
        if data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
            raise GitHubClientError("GitHub did not return inline file contents")
        if not isinstance(data.get("size"), (int, type(None))) or (data.get("size") or 0) > MAX_GITHUB_FEATURE_FILE_BYTES:
            raise GitHubClientError("Repository file exceeds the 128 KiB limit")
        sha = data.get("sha")
        if not isinstance(sha, str) or len(sha) != 40 or any(c not in "0123456789abcdefABCDEF" for c in sha):
            # The metadata endpoint should always include the Git blob SHA.
            raise GitHubClientError("GitHub returned an invalid file SHA")
        try:
            encoded = "".join(data["content"].split())
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) > MAX_GITHUB_FEATURE_FILE_BYTES:
                raise GitHubClientError("Repository file exceeds the 128 KiB limit")
            return GitHubFileContents(text=raw.decode("utf-8"), blob_sha=sha.lower())
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise GitHubClientError("Repository file is not valid base64-encoded UTF-8") from exc

    def read_repository_file(
        self, installation_id: int, full_name: str, file_path: str, *, ref: str = "main"
    ) -> str:
        """Backwards-compatible plain-text reader (prefer metadata for sync)."""
        return self.read_repository_file_with_metadata(installation_id, full_name, file_path, ref=ref).text
