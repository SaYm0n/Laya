"""Non-public upstream objects the platform depends on (laya 0.3.23), frozen.

They are reached only through ``laya_platform.core.upstream_compat`` (enforced by
tests/unit/test_architecture_imports.py). F2 implements no training: this freezes that each audited
internal still exists with the signature the F8 training loop will be written against, and that
the registry in ``upstream_compat`` names exactly these. Signatures are read from the installed
source, so the light profile checks them without torch.
"""

from __future__ import annotations

import ast
import importlib.util
from collections.abc import Callable
from pathlib import Path

import laya
import pytest

from laya_platform.core import MissingRuntimeError
from laya_platform.core import upstream_compat as compat

INTERNAL_SIGNATURES = {
    "laya.common.build_model": "(cfg, encoder_dir=None, pretrained=True, revision=None)",
    "laya.common.build_sequence": (
        "(tok, state, q, max_len=512, head_max_len=192, option_order=None, truncate_left=False, "
        "state_ids=None, return_stats=False, return_truncation_stats=False)"
    ),
    "laya.common.collate_items": "(batch, pad_id)",
    "laya.agent._fix_tokenizer_config": "(path)",
    "laya.agent.Agent._check_question": "(qid, qdef)",
    "laya.calibrate.records_from_labeled": "(agent, pairs)",
}

#: Training-side names the upstream exports publicly (laya.__all__): no adapter needed for them.
PUBLIC_TRAINING_NAMES = (
    "proper_reward",
    "render_options",
    "QTYPES",
    "QTYPE_NAMES",
    "td_lambda_targets",
)


def test_the_registry_names_exactly_the_audited_internals() -> None:
    assert sorted(api.path for api in compat.INTERNAL_APIS) == sorted(INTERNAL_SIGNATURES)
    assert all(api.reason for api in compat.INTERNAL_APIS)
    assert compat.CHECK_QUESTION in compat.INTERNAL_APIS


@pytest.mark.parametrize("api", compat.INTERNAL_APIS, ids=lambda api: api.path)
def test_each_internal_keeps_its_signature(
    signature: Callable[[str, str], str], api: compat.InternalApi
) -> None:
    assert signature(api.module, api.qualname) == INTERNAL_SIGNATURES[api.path]


@pytest.mark.parametrize("name", PUBLIC_TRAINING_NAMES)
def test_training_helpers_that_are_public_stay_public(name: str) -> None:
    # Listed as internal in the Phase 0 matrix; in 0.3.23 they are part of laya.__all__.
    assert name in laya.__all__


def test_question_types() -> None:
    # QTYPES lives in laya.common (torch); its literal is checked in the source.
    spec = importlib.util.find_spec("laya.common")
    assert spec is not None
    assert spec.origin is not None
    tree = ast.parse(Path(spec.origin).read_text(encoding="utf-8"))
    values = [
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "QTYPES" for t in node.targets)
    ]
    assert values == [{"choice": 0, "score": 1, "noul": 2}]


def test_without_torch_the_accessors_say_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compat, "torch_available", lambda: False)
    for api in compat.INTERNAL_APIS:
        with pytest.raises(MissingRuntimeError, match="full profile"):
            compat.resolve(api)
    with pytest.raises(MissingRuntimeError):
        compat.check_question("q", {"type": "noul", "instructions": "x"})


@pytest.mark.torch
@pytest.mark.parametrize("api", compat.INTERNAL_APIS, ids=lambda api: api.path)
def test_each_internal_resolves(api: compat.InternalApi) -> None:
    assert callable(compat.resolve(api))


@pytest.mark.torch
def test_check_question_is_the_upstream_validator() -> None:
    compat.check_question("q", {"type": "noul", "instructions": "x"})
    with pytest.raises(ValueError, match="question 'q': unknown type 'rating'"):
        compat.check_question("q", {"type": "rating", "instructions": "x"})
