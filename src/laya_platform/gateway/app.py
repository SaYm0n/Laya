"""The gateway: the upstream ``/v1/systemone`` app mounted as is, plus the platform's own API.

=========================  ==========  ====================================================
route                      scope       what it does
=========================  ==========  ====================================================
``/v1/systemone[/batch]``  upstream    ``laya.serve.create_app`` mounted unchanged (wire
``/health``                            contract and ``LAYA_API_KEY`` auth are the upstream's)
``POST /api/v1/decide``    decide      one decision of a DecisionSpec, audited; never acts
``POST /api/v1/route``     route       which checkpoint would answer (System-1 only)
``GET /ready``             (none)      database, specs and engine are usable
``GET /metrics``           metrics     Prometheus exposition
``GET /api/v1/specs``      admin       loaded specs and their effective mode
``GET|PUT /api/v1/flags``  admin       kill switch and per-spec mode, without a deploy
=========================  ==========  ====================================================

Modes (``DecisionSpec.mode``): ``offline`` is evaluation only (``decide`` answers 409),
``shadow`` decides and audits but returns no suggestion, ``advisory`` also returns the suggestion
for a human. ``act`` is always false here: automation needs the DecisionPolicy (Block B). The
kill switch turns every spec into shadow at once.
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
from laya_platform.core.spec import MODES, DecisionSpec
from laya_platform.gateway.metrics import GatewayMetrics
from laya_platform.gateway.settings import GatewaySettings, Scope, hash_key
from laya_platform.gateway.specs import SpecStore
from laya_platform.storage import AuditEvent, Database

KILL_SWITCH = "kill_switch"
MODE_FLAG = "mode:"


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


def state_length(state: Any) -> int:
    """The state's length as ``laya.serve`` measures it: the text that will be tokenized."""
    return len(state) if isinstance(state, str) else len(json.dumps(state, ensure_ascii=False))


def input_hmac(key: bytes, state: Any) -> str:
    canonical = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hmac.new(key, canonical.encode("utf-8"), sha256).hexdigest()


def create_app(
    settings: GatewaySettings,
    *,
    engine: DecisionEngine | None = None,
    database: Database | None = None,
) -> FastAPI:
    engine = engine if engine is not None else settings.engine.build()
    database = database if database is not None else Database(settings.database_url)
    database.upgrade()
    specs = SpecStore.load(settings.specs_dir, over_http=isinstance(engine, RemoteEngine))
    metrics = GatewayMetrics()
    hmac_key = settings.hmac_key.get_secret_value().encode("utf-8")
    keys = {key.sha256: key for key in settings.api_keys}

    app = FastAPI(title="laya-platform gateway", version=__version__)
    app.state.engine, app.state.database, app.state.specs = engine, database, specs
    app.state.metrics, app.state.settings = metrics, settings

    def require(scope: Scope) -> Callable[..., None]:
        def check(authorization: Annotated[str | None, Header()] = None) -> None:
            if settings.auth_disabled:
                return
            token = (authorization or "").removeprefix("Bearer ").strip()
            digest = hash_key(token) if token else ""
            key = next((k for h, k in keys.items() if hmac.compare_digest(h, digest)), None)
            if key is None:
                raise HTTPException(401, "invalid or missing bearer token")
            if scope not in key.scopes:
                raise HTTPException(403, f"this key has no {scope!r} scope")

        return check

    def effective_mode(spec: DecisionSpec, flags: dict[str, Any]) -> str:
        if flags.get(KILL_SWITCH):
            return "shadow"
        override = flags.get(MODE_FLAG + spec.id)
        return override if override in MODES else spec.mode

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
        summary: dict[str, Any] = {}
        for qid, answer in answers.items():
            confidence = answer_confidence_value(answer)
            band = spec.policy.outcome(confidence) if spec.policy else None
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
        event.model = payload.get("model")
        event.answers, event.agreement = summary, agreement
        event.latency_ms = elapsed * 1000.0
        database.record(event)
        metrics.decisions.labels(spec.id, mode).inc()
        metrics.latency.labels(spec.id).observe(elapsed)
        suggestion = None
        if mode == "advisory":
            suggestion = {
                "values": values,
                "answers": answers,
                "bands": {qid: s["band"] for qid, s in summary.items()} if spec.policy else None,
                "calibration_ref": spec.policy.calibration_ref if spec.policy else None,
                "routing": payload.get("routing"),
            }
        return {
            "trace_id": trace_id,
            "spec": {"id": spec.id, "version": spec.version},
            "mode": mode,
            "act": False,
            "suggestion": suggestion,
            "agreement": agreement,
        }

    @app.post("/api/v1/route", dependencies=[Depends(require("route"))])
    def route(body: RouteRequest) -> dict[str, Any]:
        questions = None
        if body.spec is not None:
            spec = specs.get(body.spec)
            if spec is None:
                raise HTTPException(404, f"unknown DecisionSpec {body.spec!r}")
            questions = spec.to_questions()
        hints = body.model_dump(include={"model", "task", "lang", "lang_guess"}, exclude_none=True)
        try:
            return dict(engine.route(body.state, questions, **hints))
        except UnsupportedOperationError as exc:
            raise HTTPException(501, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/ready")
    def ready() -> JSONResponse:
        checks = {
            "database": database.ping(),
            "specs": len(specs),
            "engine": type(engine).__name__,
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
                "source": "schema" if spec.json_schema is not None else "questions",
                "calibration_ref": spec.policy.calibration_ref if spec.policy else None,
            }
            for spec in specs
        ]

    @app.get("/api/v1/flags", dependencies=[Depends(require("admin"))])
    def get_flags() -> dict[str, Any]:
        return database.flags()

    @app.put("/api/v1/flags/{name}", dependencies=[Depends(require("admin"))])
    def put_flag(name: str, body: FlagUpdate) -> dict[str, Any]:
        if name == KILL_SWITCH:
            if body.value is not None and not isinstance(body.value, bool):
                raise HTTPException(422, "kill_switch takes true, false or null")
        elif name.startswith(MODE_FLAG) and specs.get(name.removeprefix(MODE_FLAG)) is not None:
            if body.value is not None and body.value not in MODES:
                raise HTTPException(422, f"a mode flag takes one of {list(MODES)} or null")
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
