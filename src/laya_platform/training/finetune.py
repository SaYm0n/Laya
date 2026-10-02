"""Fine-tune a specialist with the upstream's own recipe (F8), unchanged.

The recipe -- RLCD on soft targets, a temperature fit, the checkpoint export -- is the upstream's
``notebooks/laya_finetune_typed_decisions_mps.py``, pinned by commit and digest in
``laya_platform.core.upstream_compat.FINETUNE_SCRIPT`` and imported as is. This module only
feeds it the platform's datasets (the ``state``/``questions``/``gold`` rows the upstream trains
on) and describes the result as a specialist manifest. Running it needs the full install, the
base checkpoint and, in practice, a GPU (``--run-weights``/``--run-gpu`` territory, never CI).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

from laya_platform.core import upstream_compat
from laya_platform.core.spec import DecisionSpec
from laya_platform.evaluation.run import expected_label
from laya_platform.registry.manifest import SpecialistManifest

#: Files of a checkpoint directory the manifest pins (laya.load verifies them).
PINNED_FILES = ("model.safetensors", "rl_agent_config.json")


def option_keys(question: Mapping[str, Any]) -> list[Any]:
    """The options in the order the upstream reads them (choice: criteria keys / list items)."""
    if question["type"] == "noul":
        return ["false", "true"]
    if question["type"] == "score":
        return [str(i) for i in range(len(question["criteria"]))]
    return list(question["criteria"])


def normalized_question(question: Mapping[str, Any]) -> dict[str, Any]:
    """A choice with list criteria as the ``{label: None}`` mapping the recipe expects (the same
    normalization ``Agent._to_internal`` applies at inference)."""
    if question["type"] == "choice" and isinstance(question.get("criteria"), list):
        return {**question, "criteria": dict.fromkeys(question["criteria"])}
    return dict(question)


def gold_for(spec: DecisionSpec, row: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Soft targets of one row: its teacher ``gold`` when it has one, else one-hot ``expected``."""
    questions = spec.to_questions()
    if row.get("gold"):
        return {qid: dict(g) for qid, g in row["gold"].items() if qid in questions}
    gold = {}
    for qid, value in (row.get("expected") or {}).items():
        if qid in questions:
            label = expected_label(spec, questions[qid], value)
            gold[qid] = {"probabilities": {label: 1.0}}
    return gold


def training_rows(spec: DecisionSpec, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Dataset rows -> the upstream training rows (``state``, ``questions``, ``gold``)."""
    questions = spec.to_questions()
    out = []
    for row in rows:
        gold = gold_for(spec, row)
        if gold:
            asked = {qid: normalized_question(questions[qid]) for qid in gold}
            out.append({"state": row["state"], "questions": asked, "gold": gold})
    return out


def upstream_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Rows already in the upstream's training format, to mix public data in (replay).

    The format of ``LocalLLaMA/typed-decisions`` as ``Dataset.to_json`` writes it: ``state``,
    ``questions`` and ``gold`` are JSON strings, ``gold`` maps question ids to
    ``{"probabilities": {label: p}}``. Questions without gold are dropped, as the recipe does.
    """
    out = []
    for row in rows:
        state, questions, gold = (json.loads(row[key]) for key in ("state", "questions", "gold"))
        asked = {qid: normalized_question(q) for qid, q in questions.items() if qid in gold}
        if asked:
            out.append({"state": state, "questions": asked, "gold": {q: gold[q] for q in asked}})
    return out


def _gold_by_key(question: Mapping[str, Any], gold: Mapping[str, Any]) -> dict[str, Any]:
    """Probabilities keyed by the recipe's own option keys (a JSON file turns int labels into
    strings; the recipe looks them up by the criteria's key objects)."""
    probabilities = gold.get("probabilities", {})
    keys = option_keys(question)
    return {"probabilities": {k: float(probabilities.get(str(k), 0.0)) for k in keys}}


def build_items(
    recipe: ModuleType, tokenizer: Any, cfg: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> tuple[list[Any], int]:
    """Training items through the recipe's ``build_training_item``; and how many it skipped."""
    items, skipped = [], 0
    for row in rows:
        for qid, question in row["questions"].items():
            item = recipe.build_training_item(
                tokenizer, cfg, row["state"], question, _gold_by_key(question, row["gold"][qid])
            )
            if item is None:
                skipped += 1
            else:
                items.append(item)
    return items, skipped


def pick_device(recipe: ModuleType, requested: str) -> str:
    torch = recipe.torch  # the recipe's own import: this module never imports torch itself
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def run_finetune(
    recipe: ModuleType,
    *,
    base_dir: str | os.PathLike[str],
    rows: Sequence[Mapping[str, Any]],
    out_dir: str | os.PathLike[str],
    device: str = "auto",
    epochs: int = 4,
    micro_batch: int = 2,
    grad_accum: int = 16,
    calib_max: int = 400,
    checkpointing: bool = True,
) -> dict[str, Any]:
    """Train with the upstream recipe on ``rows`` (training rows); returns what was run.

    torch and the tokenizer class are the recipe's own imports (``recipe.torch``,
    ``recipe.AutoTokenizer``): the platform's code never imports an ML runtime directly.
    """
    torch = recipe.torch
    model_dir = recipe.prepare_model(str(base_dir))  # downloads only when the directory is empty
    cfg = json.loads((Path(model_dir) / "rl_agent_config.json").read_text(encoding="utf-8"))
    # As the recipe's own preprocessing does, from the base checkpoint's config.
    cfg = {**cfg, "max_len": cfg.get("max_len", 1024), "head_max_len": cfg.get("head_max_len", 256)}
    tokenizer = recipe.AutoTokenizer.from_pretrained(Path(model_dir) / "tokenizer")
    items, skipped = build_items(recipe, tokenizer, cfg, rows)
    if not items:
        raise ValueError("no training item could be built from these rows")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    items_path = out / "train_items.pt"
    torch.save(items, items_path)
    args = SimpleNamespace(
        epochs=epochs,
        micro_batch=micro_batch,
        grad_accum=grad_accum,
        calib_max=calib_max,
        no_checkpointing=not checkpointing,
        output_dir=str(out),
    )
    chosen = pick_device(recipe, device)
    recipe.train(args, model_dir, str(items_path), torch.device(chosen))
    items_path.unlink()  # derived from the dataset; the manifest points at the dataset instead
    return {
        "items": len(items),
        "skipped": skipped,
        "device": chosen,
        "args": {k: v for k, v in vars(args).items() if k != "output_dir"},
        "recipe": {
            "repository": upstream_compat.UPSTREAM_REPOSITORY,
            "commit": upstream_compat.FINETUNE_SCRIPT.commit,
            "path": upstream_compat.FINETUNE_SCRIPT.path,
            "sha256": upstream_compat.FINETUNE_SCRIPT.sha256,
        },
    }


def file_digests(checkpoint_dir: str | os.PathLike[str]) -> dict[str, str]:
    """sha256 of the files ``laya.load`` reads from a fine-tuned checkpoint directory."""
    root = Path(checkpoint_dir)
    files = [root / name for name in PINNED_FILES]
    for sub in ("encoder", "tokenizer"):
        files += sorted(p for p in (root / sub).rglob("*") if p.is_file())
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in files
        if p.is_file()
    }


def specialist_manifest(
    checkpoint_dir: str | os.PathLike[str],
    *,
    name: str,
    version: str,
    base: str,
    spec: DecisionSpec,
    languages: Sequence[str],
    dataset: Mapping[str, Any],
    training: Mapping[str, Any],
    owner: str | None = None,
) -> SpecialistManifest:
    return SpecialistManifest(
        name=name,
        version=version,
        base=base,
        source=str(Path(checkpoint_dir).resolve()),
        sha256=file_digests(checkpoint_dir),
        decision_specs=(spec.id,),
        languages=tuple(languages),
        dataset=dict(dataset),
        training=dict(training),
        owner=owner,
    )
