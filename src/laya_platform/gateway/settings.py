"""Gateway configuration: a YAML/JSON file plus secrets from the environment.

Secrets never live in the file: the HMAC key comes from ``LAYA_PLATFORM_HMAC_KEY`` and API keys
are configured by their SHA-256 (``laya-platform hash-key`` prints one). The gateway refuses to
start with real data and no DG-1 approval reference (ARCHITECTURE_PROPOSAL.md §6.15), and with no
API key unless authentication is explicitly disabled (local development only).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, model_validator

from laya_platform.llm.config import LLMSettings

if TYPE_CHECKING:
    from laya_platform.core.engine import DecisionEngine

HMAC_KEY_ENV = "LAYA_PLATFORM_HMAC_KEY"
Scope = Literal["decide", "route", "admin", "metrics", "review"]


def hash_key(key: str) -> str:
    """The SHA-256 under which an API key is configured."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class ApiKey(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Annotated[str, StringConstraints(min_length=1)]
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    scopes: Annotated[tuple[Scope, ...], Field(min_length=1)]


class EngineConfig(BaseModel):
    """Which DecisionEngine serves the gateway. ``router`` options are ``laya.Router``'s."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["router", "remote"] = "router"
    router: dict[str, Any] = Field(default_factory=dict)
    remote_url: str | None = None
    remote_api_key_env: str | None = None

    @model_validator(mode="after")
    def _remote_needs_url(self) -> Self:
        if self.kind == "remote" and not self.remote_url:
            raise ValueError("engine.kind 'remote' needs engine.remote_url")
        return self

    def build(self) -> DecisionEngine:
        """The engine this configuration names (nothing is downloaded until first use)."""
        from laya_platform.core.adapters import RemoteEngine, UpstreamRouterEngine

        if self.kind == "remote":
            env = self.remote_api_key_env
            api_key = os.environ.get(env) if env else None
            return RemoteEngine(str(self.remote_url), api_key=api_key)
        return UpstreamRouterEngine.create(**self.router)


class GatewaySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    specs_dir: Path
    database_url: str = "sqlite:///laya_platform.db"
    hmac_key: SecretStr
    api_keys: tuple[ApiKey, ...] = ()
    auth_disabled: bool = False
    data_classification: Literal["synthetic", "public", "real"] = "synthetic"
    dg1_approval_ref: str | None = None
    engine: EngineConfig = Field(default_factory=EngineConfig)
    #: System-2 tiers (Block B). Unset: no LLM; escalations go to the review queue.
    llm: LLMSettings | None = None

    @model_validator(mode="after")
    def _safe_to_start(self) -> Self:
        if len(self.hmac_key.get_secret_value()) < 32:
            raise ValueError("the HMAC key must have at least 32 characters")
        if self.data_classification == "real" and not (self.dg1_approval_ref or "").strip():
            raise ValueError(
                "real data needs an approved Data Governance Gate: set dg1_approval_ref "
                "(ARCHITECTURE_PROPOSAL.md §6.15)"
            )
        if not self.api_keys and not self.auth_disabled:
            raise ValueError("configure at least one API key, or set auth_disabled for local use")
        return self

    @classmethod
    def load(cls, path: str | os.PathLike[str], **overrides: Any) -> GatewaySettings:
        """Read a settings file; the HMAC key comes from the environment."""
        file = Path(path)
        data = yaml.safe_load(file.read_text(encoding="utf-8-sig")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{file}: settings must be a mapping")
        if "hmac_key" in data:
            raise ValueError(f"{file}: put the HMAC key in ${HMAC_KEY_ENV}, not in the file")
        data["hmac_key"] = os.environ.get(HMAC_KEY_ENV, "")
        specs_dir = Path(data.get("specs_dir", "specs"))
        data["specs_dir"] = specs_dir if specs_dir.is_absolute() else file.parent / specs_dir
        return cls.model_validate({**data, **overrides})
