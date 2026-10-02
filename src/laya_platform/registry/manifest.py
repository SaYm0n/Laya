"""Specialist manifest: what a specialist is, where its weights are, and what produced it.

Weights never go to git. ``source`` is what ``laya.load`` takes (a local directory or a Hub
repository); ``revision`` and ``sha256`` pin it, and the upstream verifies the digests on load
(``expected_sha256``: path relative to the checkpoint -> hex digest).
"""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from laya_platform.core.spec import ID_PATTERN

Name = Annotated[str, StringConstraints(pattern=ID_PATTERN, max_length=128)]
Version = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]
HEX64 = r"^[0-9a-f]{64}$"


class SpecialistManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Name
    version: Version
    #: The upstream checkpoint it was trained from (``english``, ``multilingual``, ...).
    base: Annotated[str, StringConstraints(min_length=1)]
    #: What ``laya.load`` receives: a local directory or a Hub repository.
    source: Annotated[str, StringConstraints(min_length=1)]
    revision: str | None = None
    sha256: dict[str, Annotated[str, StringConstraints(pattern=HEX64)]] = Field(
        default_factory=dict
    )
    calibration: str | None = None
    decision_specs: Annotated[tuple[Name, ...], Field(min_length=1)]
    languages: tuple[str, ...] = ()
    #: Provenance: dataset files and digests, training run (upstream commit, script digest, args).
    dataset: dict[str, Any] = Field(default_factory=dict)
    training: dict[str, Any] = Field(default_factory=dict)
    license: str | None = None
    owner: str | None = None

    @field_validator("sha256")
    @classmethod
    def _relative_paths(cls, value: dict[str, str]) -> dict[str, str]:
        for path in value:  # POSIX paths, as in a Hub repo, whatever the host OS
            posix = PurePosixPath(path)
            if posix.is_absolute() or ".." in posix.parts or "\\" in path or ":" in path:
                raise ValueError(f"sha256 keys are paths inside the checkpoint, got {path!r}")
        return value

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    def load_options(self) -> dict[str, Any]:
        """Keyword arguments for ``laya.load`` (``AgentEngine.from_checkpoint``)."""
        options: dict[str, Any] = {}
        if self.revision:
            options["revision"] = self.revision
        if self.sha256:
            options["expected_sha256"] = dict(self.sha256)
        if self.calibration:
            options["calibration"] = self.calibration
        return options


def load_manifest(path: str | os.PathLike[str]) -> SpecialistManifest:
    file = Path(path)
    text = file.read_text(encoding="utf-8-sig")
    data = json.loads(text) if file.suffix.lower() == ".json" else yaml.safe_load(text)
    return SpecialistManifest.model_validate(data)


def write_manifest(manifest: SpecialistManifest, path: str | os.PathLike[str]) -> None:
    data = manifest.model_dump(mode="json", exclude_defaults=False)
    Path(path).write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), "utf-8")
