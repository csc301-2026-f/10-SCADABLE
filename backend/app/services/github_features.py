"""Versioned, strictly validated SCADABLE product profile file.

Only a JSON feature manifest selected by a server-side administrator may update
SCADABLE's baseline. The GitHub file is untrusted input, not an LLM prompt or
configuration script. A failed parse never replaces the last working snapshot.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

DEFAULT_FEATURE_PATH = "docs/scadable-features.json"
MAX_FEATURE_BYTES = 128 * 1024


class FeatureFileError(ValueError):
    """The chosen repository file does not match the documented schema."""


class FeatureManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: int = Field(default=1, strict=True)
    company_name: str = Field(default="SCADABLE", max_length=60)
    website_url: str = Field(default="https://scadable.com", max_length=2048)
    domain: str = Field(default="Automated Security Compliance & Regulatory Governance SaaS", min_length=3, max_length=300)
    summary: str = Field(default="SCADABLE's compliance and regulatory governance platform.", min_length=5, max_length=2000)
    supported_frameworks: list[str] = Field(default_factory=list, max_length=80)
    core_capabilities: list[str] = Field(min_length=1, max_length=100)
    differentiators: list[str] = Field(default_factory=list, max_length=80)

    @field_validator("schema_version")
    @classmethod
    def version_is_supported(cls, v: int) -> int:
        if type(v) is not int or v != 1:
            raise ValueError("schema_version must be 1")
        return v

    @field_validator("company_name")
    @classmethod
    def is_scadable(cls, v: str) -> str:
        if v.casefold() != "scadable":
            raise ValueError("company_name must be SCADABLE")
        return "SCADABLE"

    @field_validator("website_url")
    @classmethod
    def valid_website(cls, v: str) -> str:
        parsed = urlsplit(v)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("website_url must be an HTTPS URL without credentials")
        return v

    @field_validator("supported_frameworks", "core_capabilities", "differentiators")
    @classmethod
    def nonempty_unique_strings(cls, items: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, str) or not (cleaned := item.strip()) or len(cleaned) > 300:
                raise ValueError("feature entries must be non-empty strings of up to 300 characters")
            if cleaned.casefold() not in seen:
                result.append(cleaned)
                seen.add(cleaned.casefold())
        if not result and items:
            raise ValueError("feature entries must not be empty")
        return result


def validate_feature_path(path: str) -> str:
    """Only normal JSON paths inside the selected repository are accepted."""
    path = path.strip()
    components = path.split("/")
    if (
        not path.endswith(".json")
        or len(path) > 512
        or len(components) > 20
        or any(not part or part in {".", ".."} or part.startswith(".") or "\\" in part or any(ord(c) < 32 for c in part) for part in components)
    ):
        raise FeatureFileError("Feature file must be a repository-relative .json path without hidden or parent segments")
    return path


def parse_feature_manifest(contents: str) -> dict[str, Any]:
    if len(contents.encode("utf-8")) > MAX_FEATURE_BYTES:
        raise FeatureFileError("Feature file exceeds the 128 KiB limit")
    try:
        payload = json.loads(contents)
    except json.JSONDecodeError as exc:
        raise FeatureFileError("Feature file must contain valid JSON") from exc
    if not isinstance(payload, dict):
        raise FeatureFileError("Feature file must contain one JSON object")
    try:
        manifest = FeatureManifest.model_validate(payload)
    except ValidationError as exc:
        # Field paths and validation messages are useful; never expose file contents.
        first = exc.errors()[0]
        field = ".".join(map(str, first["loc"]))
        raise FeatureFileError(f"Invalid feature manifest field '{field}': {first['msg']}") from exc
    return manifest.model_dump(exclude={"schema_version"})
