"""The upstream Python API the platform builds on, frozen for laya 0.3.23.

A snapshot, not a behaviour test: an upgrade that renames, re-orders or re-defaults any of these
fails here first, and the diff names what changed. Signatures are read from the installed source
(see conftest.py), so torch-backed modules are covered in the light profile too.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from typing import Any

import laya
import laya.presets
import laya.router
import laya.serve
import laya.structured
import pytest
from laya.hooks import HOOK_EVENTS, PredictContext

from laya_platform.core.adapters.agent import AgentOptions
from laya_platform.core.adapters.onnx import OnnxOptions
from laya_platform.core.adapters.upstream_router import RouterOptions
from laya_platform.core.types import BatchControls, DecisionRequest, PredictControls, RouteHints

LAYA_ALL = [
    "Agent",
    "RLAgent",
    "load",
    "fit_temperatures",
    "fit_one_temperature",
    "fit_temperature_map",
    "Router",
    "RouteDecision",
    "DEFAULT_MODELS",
    "shortlist_choice",
    "predict_shortlist",
    "embed_fn_from_agent",
    "cached_embed_fn",
    "detect_language",
    "detect_script",
    "is_english",
    "clean_email_body",
    "email_questions",
    "email_state",
    "guard_questions",
    "moderation_questions",
    "router_questions",
    "triage_questions",
    "proper_reward",
    "td_lambda_targets",
    "ece_score",
    "answer_confidence",
    "confidence_from_probs",
    "check_min_confidence",
    "flag_low_confidence",
    "apply_confidence_gate",
    "GATE_STATES",
    "render_options",
    "QTYPES",
    "QTYPE_NAMES",
    "LayaRouter",
    "LayaGuardrail",
    "LayaGuardrailError",
    "LayaTriage",
    "LayaEvaluator",
    "LayaDecision",
    "PredictContext",
    "PredictHook",
    "Hook",
    "BaseHook",
    "AsyncHook",
    "decide",
    "decide_batch",
    "DecisionResult",
    "PINNED_REVISIONS",
    "__version__",
]

_HOOK_KWARGS = "hooks=None, on_predict_start=None, on_predict_end=None"
SIGNATURES = {
    # --- Agent / load (laya.agent imports torch: read from source)
    ("laya.agent", "Agent"): (
        "(self, model_id_or_path='convaiinnovations/laya', device=None, token=None, "
        "subfolder=None, fast=False, compile=False, revision=None, expected_sha256=None, "
        f"lang_temperatures=None, {_HOOK_KWARGS}, hooks_raise=True, hooks_concurrent=True, "
        "hooks_timeout=None, calibration=None)"
    ),
    ("laya.agent", "load"): (
        "(model_id_or_path='convaiinnovations/laya', device=None, token=None, subfolder=None, "
        "fast=False, compile=False, revision=None, expected_sha256=None, lang_temperatures=None, "
        f"{_HOOK_KWARGS}, hooks_raise=True, hooks_concurrent=True, hooks_timeout=None, "
        "calibration=None)"
    ),
    ("laya.agent", "Agent.system_one"): (
        f"(self, state, questions, lang=None, {_HOOK_KWARGS}, hooks_raise=None, "
        "hooks_timeout=None, max_len=None, head_max_len=None, min_confidence=None)"
    ),
    ("laya.agent", "Agent.predict_batch"): (
        f"(self, states, questions, batch_size=None, lang=None, {_HOOK_KWARGS}, "
        "hooks_raise=None, hooks_timeout=None, max_len=None, head_max_len=None, "
        "sort_by_length=False, min_confidence=None)"
    ),
    ("laya.agent", "Agent.predict_long"): (
        "(self, state, questions, window=None, stride=None, aggregate='auto', batch_size=None, "
        f"lang=None, {_HOOK_KWARGS}, hooks_raise=None, hooks_timeout=None)"
    ),
    ("laya.agent", "Agent.decide"): (
        "(self, state, schema=None, *, questions=None, return_details=False, "
        "min_confidence=None, **predict_kwargs)"
    ),
    ("laya.agent", "Agent.decide_batch"): (
        "(self, states, schema=None, *, questions=None, return_details=False, "
        "min_confidence=None, **predict_kwargs)"
    ),
    ("laya.agent", "Agent.fit_temperatures"): "(self, records, compute_ece=False, seed=0)",
    ("laya.agent", "Agent.save_calibration"): "(self, path)",
    ("laya.agent", "Agent.load_calibration"): "(self, path)",
    # --- Router
    ("laya.router", "Router"): (
        "(self, models=None, device=None, token=None, revision=None, revisions=None, "
        "max_loaded=2, default='english', auto_task_detection=False, standalone_repos=False, "
        f"preload=False, lang_guess=None, {_HOOK_KWARGS}, hooks_raise=True, "
        "hooks_concurrent=True, hooks_timeout=None, agent_kwargs=None, sha256_digests=None)"
    ),
    ("laya.router", "Router.route"): (
        "(self, state, questions=None, model=None, task=None, lang=None, lang_guess=None, "
        "hooks=None, hooks_raise=None, hooks_timeout=None)"
    ),
    ("laya.router", "Router.route_batch"): "(self, requests, hooks_timeout=None)",
    ("laya.router", "Router.predict"): (
        "(self, state, questions, model=None, task=None, lang=None, lang_guess=None, "
        f"{_HOOK_KWARGS}, hooks_raise=None, hooks_timeout=None, max_len=None, "
        "head_max_len=None, min_confidence=None)"
    ),
    ("laya.router", "Router.predict_batch"): (
        "(self, requests, batch_size=None, hooks_timeout=None, min_confidence=None, "
        "sort_by_length=False)"
    ),
    ("laya.router", "Router.predict_long"): (
        "(self, state, questions, model=None, task=None, lang=None, lang_guess=None, "
        "window=None, stride=None, aggregate='auto', batch_size=None, "
        f"{_HOOK_KWARGS}, hooks_raise=None, hooks_timeout=None)"
    ),
    ("laya.router", "Router.decide"): (
        "(self, state, schema=None, *, questions=None, return_details=False, "
        "min_confidence=None, **predict_kwargs)"
    ),
    ("laya.router", "Router.decide_batch"): (
        "(self, states, schema=None, *, questions=None, return_details=False, "
        "min_confidence=None, **predict_kwargs)"
    ),
    ("laya.router", "Router.attach"): "(self, name, agent)",
    ("laya.router", "Router.preload"): "(self, names=None)",
    ("laya.router", "Router.unload"): "(self, name=None)",
    ("laya.router", "normalise_name"): "(name)",
    # --- ONNX runtime (documented import path: laya.onnx_agent)
    ("laya.onnx_agent", "ONNXAgent"): (
        "(self, model_id_or_path, onnx_path='laya.onnx', token=None, subfolder=None, "
        f"revision=None, expected_sha256=None, {_HOOK_KWARGS}, hooks_raise=True, "
        "hooks_concurrent=True, hooks_timeout=None, lang_temperatures=None, calibration=None)"
    ),
    ("laya.onnx_agent", "ONNXAgent.system_one"): (
        f"(self, state, questions, lang=None, {_HOOK_KWARGS}, hooks_raise=None, "
        "hooks_timeout=None, max_len=None, head_max_len=None, min_confidence=None)"
    ),
    ("laya.onnx_agent", "ONNXAgent.predict_batch"): (
        f"(self, states, questions, batch_size=None, lang=None, {_HOOK_KWARGS}, "
        "hooks_raise=None, hooks_timeout=None, max_len=None, head_max_len=None, "
        "sort_by_length=False, min_confidence=None)"
    ),
    # --- structured decisions, confidence gate, HTTP app, shortlist, calibration
    ("laya.structured", "decide"): (
        "(runner, state, schema=None, *, questions=None, return_details=False, "
        "min_confidence=None, **predict_kwargs)"
    ),
    ("laya.structured", "decide_batch"): (
        "(runner, states, schema=None, *, questions=None, return_details=False, "
        "min_confidence=None, **predict_kwargs)"
    ),
    ("laya.structured", "questions_from_json_schema"): "(schema)",
    ("laya.structured", "answers_to_json"): "(answers, schema)",
    ("laya.confidence", "apply_confidence_gate"): "(results, min_confidence=None)",
    ("laya.confidence", "check_min_confidence"): "(v)",
    ("laya.confidence", "flag_low_confidence"): "(results, min_confidence)",
    ("laya.confidence", "answer_confidence_value"): "(answer)",
    ("laya.serve", "create_app"): "(router=None)",
    ("laya.shortlist", "predict_shortlist"): (
        "(agent, state, questions, embed_fn, k=DEFAULT_SHORTLIST_K, **predict_kwargs)"
    ),
    ("laya.shortlist", "shortlist_choice"): (
        "(state, criteria, embed_fn, k=DEFAULT_SHORTLIST_K, *, instructions=None, "
        "return_scores=False)"
    ),
    ("laya.calibrate", "fit_temperature_map"): "(records, compute_ece=False, seed=0)",
    (
        "laya.common",
        "proper_reward",
    ): "(q, target, qtype, mask, w_sph=0.5, w_rps=1.0, log_floor=-9.21)",
    ("laya.common", "render_options"): "(q)",
    ("laya.common", "answer_confidence"): "(p, k)",
    ("laya.common", "confidence_from_probs"): "(p, k)",
}

ALIASES = {
    ("laya.agent", "Agent"): {"predict": "system_one"},
    ("laya.onnx_agent", "ONNXAgent"): {"predict": "system_one"},
    ("laya.router", "Router"): {"system_one": "predict", "predict_many": "predict_batch"},
}


def test_version_is_the_pinned_one() -> None:
    assert laya.__version__ == "0.3.23"


def test_public_names_are_frozen() -> None:
    assert laya.__all__ == LAYA_ALL
    assert len(LAYA_ALL) == 51  # docs/UPSTREAM_ANALYSIS.md §2


def test_every_public_name_resolves_without_importing_it() -> None:
    # Lazy names are listed by dir(); resolving one imports torch, so only presence is checked.
    assert set(LAYA_ALL) <= set(dir(laya))


@pytest.mark.parametrize(("module", "qualname"), sorted(SIGNATURES))
def test_signature(signature: Callable[[str, str], str], module: str, qualname: str) -> None:
    assert signature(module, qualname) == SIGNATURES[(module, qualname)]


@pytest.mark.parametrize(("module", "cls"), sorted(ALIASES))
def test_method_aliases(
    class_aliases: Callable[[str, str], dict[str, str]], module: str, cls: str
) -> None:
    aliases = class_aliases(module, cls)
    assert {name: aliases.get(name) for name in ALIASES[(module, cls)]} == ALIASES[(module, cls)]


def test_route_decision_is_a_dict() -> None:
    decision: Any = laya.RouteDecision(model="english", reason="r")
    assert (decision.model, decision.reason) == ("english", "r")
    assert isinstance(decision, dict)


def test_checkpoint_registry() -> None:
    assert laya.DEFAULT_MODELS == {
        "english": ("convaiinnovations/laya", None),
        "multilingual": ("convaiinnovations/laya", "multilingual"),
        "typed-decisions": ("convaiinnovations/laya", "typed-decisions"),
    }
    assert laya.router.STANDALONE_MODELS == {
        "english": "convaiinnovations/laya",
        "multilingual": "convaiinnovations/laya-multilingual",
        "typed-decisions": "convaiinnovations/laya-typed-decisions",
    }


def test_reviewed_revisions() -> None:
    # Supply-chain pins: a change here means the upstream reviewed new weights.
    assert laya.PINNED_REVISIONS == {
        "convaiinnovations/laya": "55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851",
        "convaiinnovations/laya-multilingual": "e4e9ddf21a7b1903b7acffd8814ad4307bf63a67",
        "convaiinnovations/laya-typed-decisions": "1a793eb568e6718f15941d08f85432581df534e3",
    }


def test_hook_surface() -> None:
    assert HOOK_EVENTS == (
        "on_predict_start",
        "on_predict_end",
        "on_route",
        "on_load",
        "on_evict",
        "on_error",
    )
    assert [f.name for f in dataclasses.fields(PredictContext)] == [
        "states",
        "questions",
        "run_id",
        "results",
        "decision",
        "model",
        "agent",
        "router",
        "max_len",
        "head_max_len",
        "usage",
        "started_at",
        "elapsed_ms",
        "error",
    ]
    assert callable(PredictContext.skip)


@pytest.mark.parametrize(
    ("preset", "types"),
    [
        (
            "triage_questions",
            {
                "intent": "choice",
                "is_urgent": "noul",
                "frustration": "score",
                "refund_requested": "noul",
                "churn_risk": "noul",
            },
        ),
        (
            "email_questions",
            {
                "category": "choice",
                "is_spam": "noul",
                "is_phishing": "noul",
                "urgency": "score",
                "needs_reply": "noul",
            },
        ),
        (
            "guard_questions",
            {
                "jailbreak": "noul",
                "prompt_injection": "noul",
                "sensitive_data": "noul",
                "harm_severity": "score",
                "topic": "choice",
            },
        ),
        (
            "moderation_questions",
            {
                "toxic": "noul",
                "harassment": "noul",
                "threat": "noul",
                "spam": "noul",
                "severity": "score",
            },
        ),
        (
            "router_questions",
            {
                "difficulty": "score",
                "domain": "choice",
                "needs_tools": "noul",
                "is_sensitive": "noul",
            },
        ),
    ],
)
def test_preset_question_ids_and_types(preset: str, types: dict[str, str]) -> None:
    questions = getattr(laya, preset)()
    assert {qid: question["type"] for qid, question in questions.items()} == types


def test_http_limits() -> None:
    limits = {
        name: getattr(laya.serve, name)
        for name in (
            "MAX_QUESTIONS",
            "MAX_STATE_CHARS",
            "MAX_BATCH_STATES",
            "MAX_BODY_BYTES",
            "MAX_CHOICE_OPTIONS",
            "MAX_SCORE_LEVELS",
            "MAX_TOTAL_OPTIONS",
            "DEFAULT_MAX_CONCURRENT",
            "DEFAULT_MAX_TOKEN_BUDGET",
        )
    }
    assert limits == {
        "MAX_QUESTIONS": 64,
        "MAX_STATE_CHARS": 50000,
        "MAX_BATCH_STATES": 64,
        "MAX_BODY_BYTES": 2 * 1024 * 1024,
        "MAX_CHOICE_OPTIONS": 100,
        "MAX_SCORE_LEVELS": 32,
        "MAX_TOTAL_OPTIONS": 512,
        "DEFAULT_MAX_CONCURRENT": 16,
        "DEFAULT_MAX_TOKEN_BUDGET": 8192,
    }
    assert laya.serve.BODY_CONTROLS == (
        "model",
        "max_len",
        "head_max_len",
        "task",
        "lang",
        "lang_guess",
        "min_confidence",
    )
    assert laya.serve.BODY_REFUSALS == (
        "hooks",
        "on_predict_start",
        "on_predict_end",
        "hooks_raise",
        "hooks_timeout",
    )


# ------------------------------------------------- the platform's adapters against these signatures
def _keys(typed_dict: type) -> set[str]:
    return set(typed_dict.__required_keys__ | typed_dict.__optional_keys__)  # type: ignore[attr-defined]


def _parameters(rendered: str) -> set[str]:
    names = (part.split("=")[0].strip().lstrip("*") for part in rendered.strip("()").split(","))
    return {name for name in names if name and name not in {"self", "/", ""}}


@pytest.mark.parametrize(
    ("options", "module", "qualname"),
    [
        (RouterOptions, "laya.router", "Router"),
        (AgentOptions, "laya.agent", "load"),
        (OnnxOptions, "laya.onnx_agent", "ONNXAgent"),
    ],
)
def test_adapter_options_are_upstream_keyword_arguments(
    signature: Callable[[str, str], str], options: type, module: str, qualname: str
) -> None:
    upstream = _parameters(signature(module, qualname)) - {"model_id_or_path", "onnx_path"}
    assert _keys(options) == upstream


@pytest.mark.parametrize(
    ("controls", "qualname"),
    [
        (PredictControls, "Router.predict"),
        (RouteHints, "Router.route"),
        (BatchControls, "Router.predict_batch"),
    ],
)
def test_engine_controls_are_upstream_arguments(
    signature: Callable[[str, str], str], controls: type, qualname: str
) -> None:
    hooks = {"hooks", "on_predict_start", "on_predict_end", "hooks_raise", "hooks_timeout"}
    upstream = _parameters(signature("laya.router", qualname)) - {"state", "questions", "requests"}
    assert _keys(controls) == upstream - hooks


def test_batch_request_fields_are_what_the_router_reads_per_request() -> None:
    documented = inspect.getdoc(laya.Router.predict_batch) or ""
    for field in ("model", "task", "lang", "lang_guess", "max_len", "head_max_len"):
        assert f"``{field}``" in documented
    assert _keys(DecisionRequest) == {
        "state",
        "questions",
        "model",
        "task",
        "lang",
        "lang_guess",
        "max_len",
        "head_max_len",
    }


@pytest.mark.torch
@pytest.mark.parametrize(("module", "qualname"), sorted(SIGNATURES))
def test_source_reading_agrees_with_inspect(
    signature: Callable[[str, str], str], module: str, qualname: str
) -> None:
    target: object = __import__(module, fromlist=["_"])
    for part in qualname.split("."):
        target = getattr(target, part)
    if inspect.isclass(target):
        target = target.__init__
    rendered = inspect.signature(target).replace(  # type: ignore[arg-type]
        parameters=[
            p.replace(annotation=inspect.Parameter.empty)
            for p in inspect.signature(target).parameters.values()  # type: ignore[arg-type]
        ],
        return_annotation=inspect.Signature.empty,
    )
    from_source = signature(module, qualname).replace("DEFAULT_SHORTLIST_K", "20")
    assert str(rendered) == from_source
