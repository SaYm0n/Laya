"""The upstream fine-tune recipe the platform drives (F8) is the pinned file, with the functions
and signatures ``laya_platform.training.finetune`` calls.

The recipe lives in the upstream *repository*, not in the wheel, so checking it needs the network
(``--run-network``); importing it also needs torch (the full install). The pinned digest makes
any upstream change a deliberate update of ``upstream_compat.FINETUNE_SCRIPT``.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from laya.router import STANDALONE_MODELS

from laya_platform.core import upstream_compat

SIGNATURES = {
    "prepare_model": "(model_dir)",
    "build_training_item": "(tokenizer, cfg, state, question, gold_question)",
    "train": "(args, model_dir, items_path, device)",
}


@pytest.mark.network
def test_the_pinned_recipe_is_what_the_upstream_serves(tmp_path: Path) -> None:
    file = upstream_compat.fetch_script(upstream_compat.FINETUNE_SCRIPT, tmp_path)
    assert file.read_text(encoding="utf-8").startswith('"""Laya typed-decisions fine-tuning')


@pytest.mark.network
@pytest.mark.torch
def test_the_recipe_has_the_functions_the_platform_calls(tmp_path: Path) -> None:
    upstream_compat.fetch_script(upstream_compat.FINETUNE_SCRIPT, tmp_path)
    recipe = upstream_compat.load_script(upstream_compat.FINETUNE_SCRIPT, tmp_path)
    for name, signature in SIGNATURES.items():
        assert str(inspect.signature(getattr(recipe, name))) == signature, name
    # finetune.run_finetune uses the recipe's own imports instead of importing ML runtimes.
    assert hasattr(recipe, "torch")
    assert hasattr(recipe, "AutoTokenizer")
    # finetune.prepare_base fetches a named base with it, so prepare_model never falls back to
    # downloading the English checkpoint (its MODEL_ID) for another base.
    assert {"repo_id", "revision", "local_dir"} <= set(
        inspect.signature(recipe.snapshot_download).parameters
    )
    assert STANDALONE_MODELS["english"] == recipe.MODEL_ID
