"""Routing contract of ``laya.Router`` (laya 0.3.23), with no weights.

``route`` loads nothing, so the real precedence, aliases, detection and reasons run here as they do
in production. ``predict``/``predict_batch`` run on the real Router too, over a StubAgent attached
in place of the checkpoints: what the Router adds (the ``routing`` block, the language it hands the
agent, the grouping of a batch) is the upstream's; only the forward pass is stubbed. The answers of
a real checkpoint are covered by the weights-gated tests.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import laya
import pytest
from laya import Router
from laya.router import normalise_name

from laya_platform.core.adapters import UpstreamRouterEngine

QUESTIONS: dict[str, dict[str, Any]] = {
    "intent": {
        "type": "choice",
        "instructions": "What does the customer want?",
        "criteria": ["a", "b"],
    }
}
CUSTOMER_SERVICE = {
    name: {"type": "noul", "instructions": name}
    for name in ("action", "category", "churn_risk", "needs_human", "urgency")
}
ENGLISH = "Hi, we were billed twice for March and need a refund for the duplicate charge."
PORTUGUESE = "Fui cobrado duas vezes em março, quero o reembolso"
SHORT_PORTUGUESE = "Quero cancelar"
HINDI = "मुझसे दो बार शुल्क लिया गया"


@pytest.mark.parametrize(
    ("alias", "name"),
    [
        ("english", "english"),
        ("en", "english"),
        ("laya", "english"),
        ("default", "english"),
        ("  English ", "english"),
        ("multilingual", "multilingual"),
        ("multi", "multilingual"),
        ("ml", "multilingual"),
        ("laya-multilingual", "multilingual"),
        ("typed-decisions", "typed-decisions"),
        ("typed", "typed-decisions"),
        ("typed_decisions", "typed-decisions"),
        ("decisions", "typed-decisions"),
        ("laya-typed-decisions", "typed-decisions"),
    ],
)
def test_checkpoint_names_and_aliases(alias: str, name: str) -> None:
    assert normalise_name(alias) == name


@pytest.mark.parametrize("unknown", ["pt", "portuguese", "jev-1", "laya-pharma-v1", ""])
def test_only_three_checkpoints_exist(unknown: str) -> None:
    # The Router cannot register a fourth checkpoint: specialists are selected above it (F7).
    with pytest.raises(ValueError, match="unknown model"):
        normalise_name(unknown)


def test_a_decision_has_exactly_the_documented_keys() -> None:
    decision = Router().route(ENGLISH)
    assert isinstance(decision, laya.RouteDecision)
    assert set(decision) == {"model", "repo", "reason", "detection", "workflow"}


@pytest.mark.parametrize(
    ("options", "repos"),
    [
        (
            {},
            {
                "english": "convaiinnovations/laya",
                "multilingual": "convaiinnovations/laya/multilingual",
                "typed-decisions": "convaiinnovations/laya/typed-decisions",
            },
        ),
        (
            {"standalone_repos": True},
            {
                "english": "convaiinnovations/laya",
                "multilingual": "convaiinnovations/laya-multilingual",
                "typed-decisions": "convaiinnovations/laya-typed-decisions",
            },
        ),
    ],
)
def test_repo_of_each_checkpoint(options: dict[str, Any], repos: dict[str, str]) -> None:
    router = Router(**options)
    assert {name: router.route(ENGLISH, model=name)["repo"] for name in repos} == repos


# ----------------------------------------------------------------------------------- precedence
def test_model_beats_everything() -> None:
    decision = Router(auto_task_detection=True, lang_guess="en").route(
        ENGLISH, CUSTOMER_SERVICE, model="ml", task="typed_decisions", lang="en", lang_guess="en"
    )
    assert (decision["model"], decision["reason"]) == ("multilingual", "explicit model='ml'")
    assert decision["detection"] is None


def test_task_beats_workflow_and_language() -> None:
    decision = Router(auto_task_detection=True).route(
        PORTUGUESE, CUSTOMER_SERVICE, task="typed-decisions", lang="pt"
    )
    assert (decision["model"], decision["reason"]) == (
        "typed-decisions",
        "explicit task='typed-decisions'",
    )


def test_an_unknown_task_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown model"):
        Router().route(ENGLISH, task="summarisation")


def test_detected_workflow_routes_only_when_opted_in() -> None:
    opted_in = Router(auto_task_detection=True).route(ENGLISH, CUSTOMER_SERVICE, lang="pt")
    assert opted_in["model"] == "typed-decisions"
    assert opted_in["workflow"] == "customer_service"
    assert opted_in["reason"] == (
        "question ids match the 'customer_service' typed-decisions workflow"
    )
    default = Router().route(ENGLISH, CUSTOMER_SERVICE, lang="pt")
    assert default["model"] == "multilingual"
    assert default["workflow"] == "customer_service"  # still reported, not acted on


def test_workflow_needs_the_exact_question_ids() -> None:
    almost = {**CUSTOMER_SERVICE, "extra": {"type": "noul", "instructions": "x"}}
    decision = Router(auto_task_detection=True).route(ENGLISH, almost)
    assert decision["model"] == "english"
    assert decision["workflow"] is None


def test_lang_beats_lang_guess() -> None:
    decision = Router(lang_guess="pt").route(PORTUGUESE, lang="en", lang_guess="pt")
    assert (decision["model"], decision["reason"]) == ("english", "explicit lang='en'")


def test_per_call_lang_guess_beats_the_installed_one() -> None:
    decision = Router(lang_guess="en").route(ENGLISH, lang_guess="pt")
    assert decision["model"] == "multilingual"
    assert decision["reason"] == "lang_guess: the caller identified this as non-English text"


def test_installed_lang_guess_beats_detection() -> None:
    seen: list[Any] = []

    def detector(state: Any) -> str:
        seen.append(state)
        return "pt-BR"

    decision = Router(lang_guess=detector).route(ENGLISH)
    assert decision["model"] == "multilingual"
    assert decision["reason"] == (
        "Router(lang_guess=...): the caller identified this as non-English text"
    )
    assert seen == [ENGLISH]


def test_an_abstaining_lang_guess_falls_through_to_detection() -> None:
    decision = Router(lang_guess=lambda state: None).route(PORTUGUESE)
    assert decision["model"] == "multilingual"
    assert decision["reason"] == "Latin script but language looks like 'pt', not English"


@pytest.mark.parametrize(
    ("code", "model"),
    [
        ("en", "english"),
        ("en-US", "english"),
        ("en_US.UTF-8", "english"),
        ("EN", "english"),
        ("pt", "multilingual"),
        ("pt-BR", "multilingual"),
        ("es", "multilingual"),
    ],
)
def test_language_codes(code: str, model: str) -> None:
    assert Router().route(SHORT_PORTUGUESE, lang=code)["model"] == model


@pytest.mark.parametrize("code", ["", "   ", "C", "POSIX", "C.UTF-8", "und", "zxx", "mul"])
def test_codes_that_name_no_language_fall_through(code: str) -> None:
    decision = Router().route(PORTUGUESE, lang=code)
    assert decision["reason"] == "Latin script but language looks like 'pt', not English"


# ------------------------------------------------------------------------------------ detection
def test_english_text() -> None:
    decision = Router().route({"body": ENGLISH})
    assert (decision["model"], decision["reason"]) == ("english", "English Latin text")
    assert decision["detection"]["script"] == "latin"


def test_portuguese_text() -> None:
    decision = Router().route(PORTUGUESE)
    assert decision["model"] == "multilingual"
    assert decision["detection"]["language"] == "pt"


def test_non_latin_script() -> None:
    decision = Router().route(HINDI)
    assert decision["model"] == "multilingual"
    assert decision["reason"].startswith("non-Latin script (devanagari, ")


def test_no_letters_takes_the_default() -> None:
    decision = Router(default="ml").route("12345 !!!")
    assert (decision["model"], decision["reason"]) == (
        "multilingual",
        "no letters detected in state; using default (multilingual)",
    )


def test_short_portuguese_takes_the_default_checkpoint() -> None:
    # docs/UPSTREAM_ANALYSIS.md §4: short pt-BR text carries no language evidence, so it goes to
    # `default` -- English unless the deployment says otherwise.
    reason = "Latin script, language not identified and no non-English letters; using default (%s)"
    stock = Router().route(SHORT_PORTUGUESE)
    assert (stock["model"], stock["reason"]) == ("english", reason % "english")
    brazil = Router(default="multilingual").route(SHORT_PORTUGUESE)
    assert (brazil["model"], brazil["reason"]) == ("multilingual", reason % "multilingual")


def test_an_unknown_default_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="unknown model"):
        Router(default="pt")


# ------------------------------------------------------------------------------------- batches
def test_route_batch_keeps_input_order() -> None:
    decisions = Router().route_batch(
        [
            {"state": ENGLISH, "questions": QUESTIONS},
            {"state": PORTUGUESE, "questions": QUESTIONS},
            {"state": ENGLISH, "questions": QUESTIONS, "model": "typed"},
        ]
    )
    assert [d["model"] for d in decisions] == ["english", "multilingual", "typed-decisions"]


@pytest.mark.parametrize(
    ("requests", "error", "message"),
    [
        (
            [{"state": ENGLISH, "questions": QUESTIONS}, {"questions": QUESTIONS}],
            ValueError,
            "request 1 is missing required key 'state'",
        ),
        ([{"state": ENGLISH}], ValueError, "request 0 is missing required key 'questions'"),
        (["not a dict"], TypeError, "request 0 must be a dict"),
        ([{"state": ENGLISH, "questions": ["x"]}], TypeError, "'questions' must be a dict"),
        ("not a list", TypeError, "requests must be a sequence"),
    ],
)
def test_route_batch_errors(requests: Any, error: type[Exception], message: str) -> None:
    with pytest.raises(error, match=message):
        Router().route_batch(requests)


# ------------------------------------------------------------- what predict adds around the agent
def test_predict_adds_the_routing_block(router_factory: Callable[..., Router]) -> None:
    router = router_factory()
    payload = router.predict(PORTUGUESE, QUESTIONS)
    assert payload["routing"] == dict(router.route(PORTUGUESE, QUESTIONS))
    assert set(payload) == {"model", "answers", "usage", "routing"}


def test_predict_hands_the_detected_language_to_the_agent(
    router_factory: Callable[..., Router], stub_agent: Any
) -> None:
    router_factory().predict(PORTUGUESE, QUESTIONS, max_len=256, head_max_len=64)
    assert stub_agent.calls[-1] == (
        "system_one",
        {"state": PORTUGUESE, "lang": "pt", "max_len": 256, "head_max_len": 64},
    )
    router_factory().predict(ENGLISH, QUESTIONS, lang="en-GB")
    assert stub_agent.calls[-1][1]["lang"] == "en-GB"


def test_predict_batch_groups_by_checkpoint_and_restores_order(
    router_factory: Callable[..., Router], stub_agent: Any
) -> None:
    other = {"q": {"type": "noul", "instructions": "x"}}
    payloads = router_factory().predict_batch(
        [
            {"state": ENGLISH, "questions": QUESTIONS},
            {"state": PORTUGUESE, "questions": QUESTIONS},
            {"state": ENGLISH, "questions": QUESTIONS},
            {"state": ENGLISH, "questions": other},
        ]
    )
    assert [p["routing"]["model"] for p in payloads] == [
        "english",
        "multilingual",
        "english",
        "english",
    ]
    assert [set(p["answers"]) for p in payloads] == [{"intent"}, {"intent"}, {"intent"}, {"q"}]
    batches = [call for call in stub_agent.calls if call[0] == "predict_batch"]
    assert [call[1]["states"] for call in batches] == [[ENGLISH, ENGLISH], [ENGLISH], [PORTUGUESE]]


def test_an_on_route_hook_may_replace_the_decision(router_factory: Callable[..., Router]) -> None:
    class Pin(laya.BaseHook):  # type: ignore[misc]
        def on_route(self, ctx: Any) -> None:
            ctx.decision = laya.RouteDecision(
                model="multilingual", repo="pinned", reason="pinned", detection=None, workflow=None
            )

    payload = router_factory(hooks=[Pin()]).predict(ENGLISH, QUESTIONS)
    assert payload["routing"]["reason"] == "pinned"


# ----------------------------------------------------------- the adapter adds and removes nothing
def test_upstream_router_engine_returns_the_upstream_results(
    router_factory: Callable[..., Router],
) -> None:
    router = router_factory(default="multilingual")
    engine = UpstreamRouterEngine(router)
    assert engine.router is router
    assert engine.route(SHORT_PORTUGUESE, QUESTIONS) == router.route(SHORT_PORTUGUESE, QUESTIONS)
    assert engine.predict(PORTUGUESE, QUESTIONS, min_confidence=0.5) == router.predict(
        PORTUGUESE, QUESTIONS, min_confidence=0.5
    )
    requests: list[Any] = [{"state": ENGLISH, "questions": QUESTIONS, "lang": "pt"}]
    assert engine.predict_batch(requests) == router.predict_batch(requests)


def test_upstream_router_engine_create_keeps_the_upstream_defaults() -> None:
    engine = UpstreamRouterEngine.create()
    assert engine.route(SHORT_PORTUGUESE)["model"] == "english"
    brazil = UpstreamRouterEngine.create(default="multilingual", lang_guess=lambda state: None)
    assert brazil.route(SHORT_PORTUGUESE)["model"] == "multilingual"
    assert engine.router.loaded == []  # nothing was downloaded or built
