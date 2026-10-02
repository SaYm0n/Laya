"""The specialist registry: versions, their lifecycle, and which one answers each spec.

Every change is a recorded event (who, why, evidence). A spec's ``production`` pointer keeps the
version it replaced, so a rollback is one pointer swap -- no deploy, effective on the gateway's
next pointer refresh.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select

from laya_platform.core.spec import DecisionSpec
from laya_platform.registry.lifecycle import (
    ALLOWED,
    Gates,
    report_problems,
    shadow_evidence,
    shadow_problems,
)
from laya_platform.registry.manifest import SpecialistManifest
from laya_platform.storage import Database, Specialist, SpecialistEvent, SpecialistPointer
from laya_platform.storage.models import utc_now

ROLES = ("production", "shadow")
DEFAULT_GATES = Gates()


class RegistryError(ValueError):
    """A registry operation was refused; the message says why."""


@dataclass(frozen=True)
class Pointer:
    name: str
    version: str

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"


class SpecialistRegistry:
    def __init__(self, database: Database) -> None:
        self.database = database

    # ------------------------------------------------------------------------------- reading
    def get(self, name: str, version: str) -> tuple[SpecialistManifest, str] | None:
        with self.database.reader() as session:
            row = self._row(session, name, version)
            if row is None:
                return None
            return SpecialistManifest.model_validate(row.manifest), row.status

    def versions(self) -> list[dict[str, Any]]:
        with self.database.reader() as session:
            rows = session.scalars(select(Specialist).order_by(Specialist.name, Specialist.id))
            return [
                {
                    "name": r.name,
                    "version": r.version,
                    "status": r.status,
                    "decision_specs": r.manifest.get("decision_specs", []),
                }
                for r in rows
            ]

    def pointers(self, spec_id: str | None = None) -> dict[str, dict[str, Pointer]]:
        """``{spec_id: {role: Pointer}}``."""
        with self.database.reader() as session:
            query = select(SpecialistPointer)
            if spec_id is not None:
                query = query.where(SpecialistPointer.spec_id == spec_id)
            found: dict[str, dict[str, Pointer]] = {}
            for row in session.scalars(query):
                found.setdefault(row.spec_id, {})[row.role] = Pointer(row.name, row.version)
            return found

    def events(self, name: str | None = None) -> list[dict[str, Any]]:
        with self.database.reader() as session:
            query = select(SpecialistEvent).order_by(SpecialistEvent.id)
            if name is not None:
                query = query.where(SpecialistEvent.name == name)
            return [
                {
                    "at": e.at.isoformat(),
                    "name": e.name,
                    "version": e.version,
                    "from": e.from_status,
                    "to": e.to_status,
                    "actor": e.actor,
                    "reason": e.reason,
                    "evidence": e.evidence,
                }
                for e in session.scalars(query)
            ]

    # ------------------------------------------------------------------------------- changing
    def register(
        self, manifest: SpecialistManifest, actor: str, reason: str = "registered"
    ) -> None:
        with self.database.transaction() as session:
            if self._row(session, manifest.name, manifest.version) is not None:
                raise RegistryError(f"{manifest.key} is already registered")
            session.add(
                Specialist(
                    name=manifest.name,
                    version=manifest.version,
                    status="experimental",
                    manifest=manifest.model_dump(mode="json"),
                )
            )
            session.add(
                self._event(
                    manifest.name, manifest.version, None, "experimental", actor, reason, None
                )
            )

    def to_shadow(
        self,
        name: str,
        version: str,
        spec: DecisionSpec,
        report: Mapping[str, Any],
        actor: str,
        *,
        baseline: Mapping[str, Any] | None = None,
        gates: Gates = DEFAULT_GATES,
    ) -> None:
        """experimental -> shadow, on an evaluation report; runs beside production for ``spec``."""
        manifest = self._manifest(name, version)
        problems = report_problems(manifest, spec, report, gates, baseline)
        if problems:
            raise RegistryError(f"{manifest.key} cannot enter shadow: " + "; ".join(problems))
        evidence = {"report": report.get("id"), "baseline": (baseline or {}).get("id")}
        with self.database.transaction() as session:
            self._move(session, name, version, "shadow", actor, f"evaluated on {spec.id}", evidence)
            self._point(session, spec.id, "shadow", name, version, actor)

    def to_candidate(
        self, name: str, version: str, actor: str, *, gates: Gates = DEFAULT_GATES
    ) -> dict[str, Any]:
        """shadow -> candidate, on the shadow runs the gateway recorded for each served spec."""
        manifest = self._manifest(name, version)
        evidence: dict[str, Any] = {}
        problems = []
        for spec_id in manifest.decision_specs:
            observed = shadow_evidence(self.database.challenger_results(name, version, spec_id))
            evidence[spec_id] = observed.as_dict()
            problems += [f"{spec_id}: {p}" for p in shadow_problems(observed, gates)]
        if problems:
            raise RegistryError(f"{manifest.key} cannot become a candidate: " + "; ".join(problems))
        with self.database.transaction() as session:
            self._move(session, name, version, "candidate", actor, "shadow evidence", evidence)
        return evidence

    def to_production(self, name: str, version: str, actor: str, reason: str) -> None:
        """candidate -> production, on a recorded human approval; the old version is kept for
        rollback."""
        if not actor.strip() or not reason.strip():
            raise RegistryError("production needs a named approver and a reason")
        manifest = self._manifest(name, version)
        with self.database.transaction() as session:
            self._move(session, name, version, "production", actor, reason, None)
            for spec_id in manifest.decision_specs:
                current = session.get(SpecialistPointer, (spec_id, "production"))
                replaced = None if current is None else (current.name, current.version)
                self._point(session, spec_id, "production", name, version, actor)
                shadow = session.get(SpecialistPointer, (spec_id, "shadow"))
                if shadow is not None and (shadow.name, shadow.version) == (name, version):
                    session.delete(shadow)
                if replaced is not None and replaced != (name, version):
                    self._move(
                        session,
                        *replaced,
                        "deprecated",
                        actor,
                        f"replaced by {manifest.key} on {spec_id}",
                        None,
                        check=False,
                    )

    def deprecate(self, name: str, version: str, actor: str, reason: str) -> None:
        with self.database.transaction() as session:
            self._move(session, name, version, "deprecated", actor, reason, None)
            session.execute(
                delete(SpecialistPointer).where(
                    SpecialistPointer.name == name,
                    SpecialistPointer.version == version,
                    SpecialistPointer.role == "shadow",
                )
            )

    def rollback(self, spec_id: str, actor: str, reason: str) -> Pointer | None:
        """Point ``spec_id`` back at the version production replaced (None: back to the Router)."""
        with self.database.transaction() as session:
            pointer = session.get(SpecialistPointer, (spec_id, "production"))
            if pointer is None:
                raise RegistryError(f"{spec_id} has no production specialist to roll back")
            current = Pointer(pointer.name, pointer.version)
            self._move(
                session,
                current.name,
                current.version,
                "deprecated",
                actor,
                f"rolled back on {spec_id}: {reason}",
                None,
                check=False,
            )
            if pointer.previous_name is None or pointer.previous_version is None:
                session.delete(pointer)
                return None
            previous = Pointer(pointer.previous_name, pointer.previous_version)
            pointer.name, pointer.version = previous.name, previous.version
            pointer.previous_name = pointer.previous_version = None
            pointer.updated_at, pointer.actor = utc_now(), actor
            self._move(
                session,
                previous.name,
                previous.version,
                "production",
                actor,
                f"restored on {spec_id}: {reason}",
                None,
                check=False,
            )
            return previous

    # ------------------------------------------------------------------------------- internals
    @staticmethod
    def _row(session: Any, name: str, version: str) -> Specialist | None:
        query = select(Specialist).where(Specialist.name == name, Specialist.version == version)
        row: Specialist | None = session.scalars(query).first()
        return row

    def _manifest(self, name: str, version: str) -> SpecialistManifest:
        found = self.get(name, version)
        if found is None:
            raise RegistryError(f"{name}@{version} is not registered")
        return found[0]

    @staticmethod
    def _event(
        name: str,
        version: str,
        before: str | None,
        after: str,
        actor: str,
        reason: str,
        evidence: dict[str, Any] | None,
    ) -> SpecialistEvent:
        return SpecialistEvent(
            name=name,
            version=version,
            from_status=before,
            to_status=after,
            actor=actor,
            reason=reason,
            evidence=evidence,
        )

    def _move(
        self,
        session: Any,
        name: str,
        version: str,
        to: str,
        actor: str,
        reason: str,
        evidence: dict[str, Any] | None,
        *,
        check: bool = True,
    ) -> None:
        row = self._row(session, name, version)
        if row is None:
            raise RegistryError(f"{name}@{version} is not registered")
        if check and to not in ALLOWED[row.status]:
            raise RegistryError(f"{name}@{version} cannot go from {row.status} to {to}")
        session.add(self._event(name, version, row.status, to, actor, reason, evidence))
        row.status, row.updated_at = to, utc_now()

    @staticmethod
    def _point(session: Any, spec_id: str, role: str, name: str, version: str, actor: str) -> None:
        pointer = session.get(SpecialistPointer, (spec_id, role))
        if pointer is None:
            session.add(
                SpecialistPointer(
                    spec_id=spec_id,
                    role=role,
                    name=name,
                    version=version,
                    actor=actor,
                    updated_at=utc_now(),
                )
            )
            return
        if role == "production":
            pointer.previous_name, pointer.previous_version = pointer.name, pointer.version
        pointer.name, pointer.version = name, version
        pointer.updated_at, pointer.actor = utc_now(), actor
