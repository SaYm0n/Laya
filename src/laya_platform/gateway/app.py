"""The gateway: the upstream ``/v1/systemone`` app mounted as is, plus the platform's own API.

=========================  ==========  ====================================================
route                      scope       what it does
=========================  ==========  ====================================================
``/v1/systemone[/batch]``  upstream    ``laya.serve.create_app`` mounted unchanged (wire
``/health``                            contract and ``LAYA_API_KEY`` auth are the upstream's)
``POST /api/v1/decide``    decide      one decision of a DecisionSpec, audited
``POST /api/v1/route``     route       who would answer: Laya, an LLM tier or a human
``GET /api/v1/reviews``    review      the human review queue
``POST /api/v1/reviews/    review      a human's answer to a queued decision
{id}/resolve``
``GET /ready``             (none)      database, specs and engine are usable
``GET /metrics``           metrics     Prometheus exposition
``GET /api/v1/specs``      admin       loaded specs and their effective mode
``GET|PUT /api/v1/flags``  admin       kill switch and per-spec mode, without a deploy
=========================  ==========  ====================================================

Modes (``DecisionSpec.mode``): ``offline`` is evaluation only (``decide`` answers 409);
``shadow`` decides and audits, returns nothing to act on and calls no LLM; ``advisory`` returns
the suggestion -- System-2's when the policy escalates and a tier is configured; ``gated``
also sends what the policy does not automate to an LLM tier or the review queue, and sets
``act`` only for an ``auto`` outcome inside the spec's canary. System-2 answers are never acted
on. The kill switch turns every spec into shadow at once.
"""

from __future__ import annotations

import hmac
import json
import time
import uuid
from collections.abc import Callable
from hashlib import sha256
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from laya.serve import MAX_BODY_BYTES, MAX_STATE_CHARS
from laya.serve import create_app as create_upstream_app
from pydantic import BaseModel, ConfigDict

from laya_platform import __version__
from laya_platform.core.adapters import RemoteEngine, UpstreamRouterEngine
from laya_platform.core.answers import answer_confidence_value, spec_values
from laya_platform.core.engine import DecisionEngine
from laya_platform.core.errors import UnsupportedOperationError
from laya_platform.core.policy import PolicyDecision, canary_selected, evaluate
from laya_platform.core.spec import MODES, DecisionSpec
from laya_platform.gateway.metrics import GatewayMetrics
from laya_platform.gateway.settings import GatewaySettings, Scope, hash_key
from laya_platform.gateway.specs import SpecStore
from laya_platform.llm import LLMCall, LLMError, LLMGateway
from laya_platform.storage import AuditEvent, Database, ReviewItem

KILL_SWITCH = "kill_switch"
MODE_FLAG = "mode:"
LOCAL_RESOLVER = "local"


class DecideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: str
    state: str | dict[str, Any] | list[Any]
    incumbent: dict[str, Any] | None = None


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str | dict[str, Any] | list[Any]
    spec: str | None = None
    model: str | None = None
    task: str | None = None
    lang: str | None = None
    lang_guess: str | None = None


class FlagUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Any = None


class Resolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any]
    note: str | None = None


def state_length(state: Any) -> int:
    """The state's length as ``laya.serve`` measures it: the text that will be tokenized."""
    return len(state) if isinstance(state, str) else len(json.dumps(state, ensure_ascii=False))


def input_hmac(key: bytes, state: Any) -> str:
    canonical = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hmac.new(key, canonical.encode("utf-8"), sha256).hexdigest()


def call_record(call: LLMCall) -> dict[str, Any]:
    return {
        "tier": call.tier,
        "provider": call.provider,
        "model": call.model,
        "outcome": call.outcome,
        "input_tokens": call.input_tokens,
        "output_tokens": call.output_tokens,
        "cost": call.cost,
        "latency_ms": call.latency_ms,
        "error": call.error,
    }


def review_record(item: ReviewItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "trace_id": item.trace_id,
        "spec": {"id": item.spec_id, "version": item.spec_version},
        "reason": item.reason,
        "suggestion": item.suggestion,
        "status": item.status,
        "created_at": item.created_at.isoformat(),
        "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
        "resolver": item.resolver,
        "resolution": item.resolution,
    }


def _check_escalation_tiers(specs: SpecStore, llm: LLMGateway | None) -> None:
    for spec in specs:
        tier = spec.policy.escalation_tier if spec.policy else None
        if tier is not None and (llm is None or tier not in llm.tiers):
            raise ValueError(f"{spec.id}: escalation_tier {tier!r} is not a configured LLM tier")


def create_app(
    settings: GatewaySettings,
    *,
    engine: DecisionEngine | None = None,
    database: Database | None = None,
    llm: LLMGateway | None = None,
) -> FastAPI:
    engine = engine if engine is not None else settings.engine.build()
    database = database if database is not None else Database(settings.database_url)
    if llm is None and settings.llm is not None:
        llm = LLMGateway(settings.llm)
    database.upgrade()
    specs = SpecStore.load(settings.specs_dir, over_http=isinstance(engine, RemoteEngine))
    _check_escalation_tiers(specs, llm)
    metrics = GatewayMetrics()
    hmac_key = settings.hmac_key.get_secret_value().encode("utf-8")
    keys = {key.sha256: key for key in settings.api_keys}

    app = FastAPI(title="laya-platform gateway", version=__version__)
    app.state.engine, app.state.database, app.state.specs = engine, database, specs
    app.state.metrics, app.state.settings, app.state.llm = metrics, settings, llm

    def require(scope: Scope) -> Callable[..., None]:
        def check(request: Request, authorization: Annotated[str | None, Header()] = None) -> None:
            """Authorize; the calling key's name goes to ``request.state.caller``."""
            if settings.auth_disabled:
                request.state.caller = LOCAL_RESOLVER
                return
            token = (authorization or "").removeprefix("Bearer ").strip()
            digest = hash_key(token) if token else ""
            key = next((k for h, k in keys.items() if hmac.compare_digest(h, digest)), None)
            if key is None:
                raise HTTPException(401, "invalid or missing bearer token")
            if scope not in key.scopes:
                raise HTTPException(403, f"this key has no {scope!r} scope")
            request.state.caller = key.name

        return check

    def effective_mode(spec: DecisionSpec, flags: dict[str, Any]) -> str:
        if flags.get(KILL_SWITCH):
            return "shadow"
        override = flags.get(MODE_FLAG + spec.id)
        if override in MODES and (override != "gated" or spec.gated_problem() is None):
            return str(override)
        return spec.mode

    def ask_system2(
        spec: DecisionSpec, tier: str, state: Any
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """System-2's values (None when it gave none) and the record of the attempt."""
        if llm is None:  # unreachable: start-up refuses a tier without an LLM gateway
            return None, {"tier": tier, "outcome": "unavailable", "error": "no LLM gateway"}
        try:
            values, call = llm.decide(tier, spec, state)
        except LLMError as exc:
            failed = exc.call or LLMCall(tier, "", "", exc.kind, error=str(exc))
            metrics.observe_llm(failed)
            return None, call_record(failed)
        metrics.observe_llm(call)
        return values, {**call_record(call), "values": values}

    @app.middleware("http")
    async def limit_body(request: Request, call_next: Any) -> Any:
        if request.url.path.startswith("/api/"):
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
                return JSONResponse({"detail": "request body too large"}, status_code=413)
        return await call_next(request)

    @app.post("/api/v1/decide", dependencies=[Depends(require("decide"))])
    def decide(body: DecideRequest) -> dict[str, Any]:
        spec = specs.get(body.spec)
        if spec is None:
            raise HTTPException(404, f"unknown DecisionSpec {body.spec!r}")
        mode = effective_mode(spec, database.flags())
        if mode == "offline":
            raise HTTPException(409, f"{spec.id} is offline: evaluation only")
        if state_length(body.state) > MAX_STATE_CHARS:
            raise HTTPException(413, f"state too large (> {MAX_STATE_CHARS} chars)")
        trace_id = str(uuid.uuid4())
        event = AuditEvent(
            trace_id=trace_id,
            spec_id=spec.id,
            spec_version=spec.version,
            mode=mode,
            input_hmac=input_hmac(hmac_key, body.state),
            answers={},
            incumbent=body.incumbent,
            data_classification=settings.data_classification,
        )
        started = time.perf_counter()
        try:
            payload = engine.predict(body.state, spec.to_questions(), **spec.predict_controls())
        except ValueError as exc:
            _fail(event, started, f"{type(exc).__name__}: {exc}")
            raise HTTPException(422, str(exc)) from None
        except Exception as exc:  # noqa: BLE001 -- audited, never leaked to the caller
            _fail(event, started, f"{type(exc).__name__}: {exc}")
            raise HTTPException(500, "decision failed") from None
        finally:
            if event.error is not None:
                database.record(event)
                metrics.errors.labels(spec.id).inc()
        elapsed = time.perf_counter() - started
        answers = payload.get("answers", {})
        values = spec_values(spec, answers)
        verdict = evaluate(spec, payload, values)
        summary: dict[str, Any] = {}
        for qid, answer in answers.items():
            confidence = answer_confidence_value(answer)
            band = verdict.bands.get(qid) if verdict else None
            summary[qid] = {"value": values.get(qid), "answer_confidence": confidence, "band": band}
            if confidence is not None:
                metrics.confidence.labels(spec.id, qid).observe(confidence)
            if band is not None:
                metrics.bands.labels(spec.id, qid, band).inc()
        agreement = None
        if body.incumbent is not None:
            agreement = {
                qid: body.incumbent[qid] == values.get(qid)
                for qid in body.incumbent
                if qid in values
            }
            for qid, agree in agreement.items():
                metrics.agreement.labels(spec.id, qid, str(agree).lower()).inc()

        act = bool(
            mode == "gated"
            and verdict is not None
            and verdict.outcome == "auto"
            and spec.policy is not None
            and canary_selected(event.input_hmac, spec.policy.canary)
        )
        suggested, source, system2, review_id = values, "system1", None, None
        if verdict is not None and verdict.outcome != "auto" and mode in ("advisory", "gated"):
            tier = spec.policy.escalation_tier if spec.policy else None
            if verdict.outcome == "escalate" and tier is not None:
                answered, system2 = ask_system2(spec, tier, body.state)
                if answered is not None:
                    suggested, source = answered, "system2"
            if mode == "gated" and source == "system1":
                review_id = _queue(database, event, spec, verdict, values, system2)
                metrics.reviews.labels(spec.id).inc()

        event.model = payload.get("model")
        event.answers, event.agreement = summary, agreement
        event.latency_ms = elapsed * 1000.0
        event.outcome = verdict.outcome if verdict else None
        event.reasons = verdict.reasons if verdict else None
        event.act, event.system2 = act, system2
        database.record(event)
        metrics.decisions.labels(spec.id, mode).inc()
        metrics.latency.labels(spec.id).observe(elapsed)
        if verdict is not None:
            metrics.outcomes.labels(spec.id, mode, verdict.outcome).inc()
        if act:
            metrics.acts.labels(spec.id).inc()
        suggestion = None
        if mode != "shadow":
            suggestion = {
                "values": suggested,
                "source": source,
                "answers": answers,
                "bands": verdict.bands if verdict else None,
                "calibration_ref": spec.policy.calibration_ref if spec.policy else None,
                "routing": payload.get("routing"),
            }
        return {
            "trace_id": trace_id,
            "spec": {"id": spec.id, "version": spec.version},
            "mode": mode,
            "act": act,
            "outcome": verdict.outcome if verdict else None,
            "reasons": verdict.reasons if verdict else [],
            "suggestion": suggestion,
            "system2": system2 if mode != "shadow" else None,
            "review_id": review_id,
            "agreement": agreement,
        }

    @app.post("/api/v1/route", dependencies=[Depends(require("route"))])
    def route(body: RouteRequest) -> dict[str, Any]:
        hints = body.model_dump(include={"model", "task", "lang", "lang_guess"}, exclude_none=True)
        if body.spec is None:
            try:
                return dict(engine.route(body.state, None, **hints))
            except UnsupportedOperationError as exc:
                raise HTTPException(501, str(exc)) from None
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        spec = specs.get(body.spec)
        if spec is None:
            raise HTTPException(404, f"unknown DecisionSpec {body.spec!r}")
        if state_length(body.state) > MAX_STATE_CHARS:
            raise HTTPException(413, f"state too large (> {MAX_STATE_CHARS} chars)")
        # A dry run of System-1 and the policy: nothing is audited, queued or sent to an LLM.
        controls = {**spec.predict_controls(), **hints}
        try:
            payload = engine.predict(body.state, spec.to_questions(), **controls)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        verdict = evaluate(spec, payload, spec_values(spec, payload.get("answers", {})))
        tier = spec.policy.escalation_tier if spec.policy else None
        if verdict is None or verdict.outcome == "auto":
            system = "laya"
        elif verdict.outcome == "escalate" and tier is not None:
            system = "llm"
        else:
            system = "human"
        return {
            "system": system,
            "outcome": verdict.outcome if verdict else None,
            "reasons": verdict.reasons if verdict else [],
            "tier": tier if system == "llm" else None,
            "routing": payload.get("routing"),
        }

    @app.get("/api/v1/reviews", dependencies=[Depends(require("review"))])
    def list_reviews(status: str = "open", limit: int = 100) -> list[dict[str, Any]]:
        if status not in ("open", "resolved", "all"):
            raise HTTPException(422, "status is open, resolved or all")
        chosen = None if status == "all" else status
        return [review_record(item) for item in database.reviews(chosen, max(1, min(limit, 500)))]

    @app.post("/api/v1/reviews/{review_id}/resolve", dependencies=[Depends(require("review"))])
    def resolve_review(review_id: int, body: Resolution, request: Request) -> dict[str, Any]:
        item = database.review(review_id)
        if item is None:
            raise HTTPException(404, f"unknown review {review_id}")
        spec = specs.get(item.spec_id)
        if spec is not None:
            unknown = sorted(set(body.values) - set(spec.to_questions()))
            if unknown:
                raise HTTPException(422, f"unknown question(s) {unknown}")
        resolution = {"values": body.values, "note": body.note}
        resolved = database.resolve_review(review_id, resolution, str(request.state.caller))
        if resolved is None:
            raise HTTPException(409, f"review {review_id} is already resolved")
        metrics.resolutions.labels(item.spec_id).inc()
        return review_record(resolved)

    @app.get("/ready")
    def ready() -> JSONResponse:
        checks = {
            "database": database.ping(),
            "specs": len(specs),
            "engine": type(engine).__name__,
            "llm_tiers": llm.tiers if llm is not None else [],
        }
        ok = bool(checks["database"]) and len(specs) > 0
        return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)

    @app.get("/metrics", dependencies=[Depends(require("metrics"))])
    def prometheus() -> Response:
        return Response(metrics.exposition(), media_type="text/plain; version=0.0.4")

    @app.get("/api/v1/specs", dependencies=[Depends(require("admin"))])
    def list_specs() -> list[dict[str, Any]]:
        flags = database.flags()
        return [
            {
                "id": spec.id,
                "version": spec.version,
                "mode": spec.mode,
                "effective_mode": effective_mode(spec, flags),
                "risk": spec.risk,
                "source": "schema" if spec.json_schema is not None else "questions",
                "calibration_ref": spec.policy.calibration_ref if spec.policy else None,
                "escalation_tier": spec.policy.escalation_tier if spec.policy else None,
            }
            for spec in specs
        ]

    @app.get("/api/v1/flags", dependencies=[Depends(require("admin"))])
    def get_flags() -> dict[str, Any]:
        return database.flags()

    @app.put("/api/v1/flags/{name}", dependencies=[Depends(require("admin"))])
    def put_flag(name: str, body: FlagUpdate) -> dict[str, Any]:
        spec = specs.get(name.removeprefix(MODE_FLAG)) if name.startswith(MODE_FLAG) else None
        if name == KILL_SWITCH:
            if body.value is not None and not isinstance(body.value, bool):
                raise HTTPException(422, "kill_switch takes true, false or null")
        elif spec is not None:
            if body.value is not None and body.value not in MODES:
                raise HTTPException(422, f"a mode flag takes one of {list(MODES)} or null")
            if body.value == "gated" and (problem := spec.gated_problem()):
                raise HTTPException(422, f"{spec.id} cannot run gated: it {problem}")
        else:
            raise HTTPException(404, f"unknown flag {name!r}")
        database.set_flag(name, body.value)
        return database.flags()

    if isinstance(engine, UpstreamRouterEngine):
        # Last, so the platform's routes win and everything else reaches the upstream app.
        app.mount("/", create_upstream_app(engine.router))
    return app


def _fail(event: AuditEvent, started: float, error: str) -> None:
    event.error = error
    event.latency_ms = (time.perf_counter() - started) * 1000.0


def _queue(
    database: Database,
    event: AuditEvent,
    spec: DecisionSpec,
    verdict: PolicyDecision,
    values: dict[str, Any],
    system2: dict[str, Any] | None,
) -> int:
    reason = "; ".join(verdict.reasons) or verdict.outcome
    if system2 is not None:
        reason += f"; system2 {system2.get('outcome')}: {system2.get('error')}"
    item = ReviewItem(
        trace_id=event.trace_id,
        spec_id=spec.id,
        spec_version=spec.version,
        reason=reason,
        suggestion={"values": values, "source": "system1", "outcome": verdict.outcome},
        status="open",
    )
    return database.open_review(item)
