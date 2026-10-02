"""Data and training for specialists (Block C, F8), without weights: what to label, dataset build
(HMAC-verified join, label precedence, redaction), splits, the LLM teacher, the conversion to the
upstream recipe's rows, and the recipe driven through a stand-in module."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from laya_platform.core import DecisionSpec, load_decision_spec, upstream_compat
from laya_platform.core.errors import MissingRuntimeError
from laya_platform.llm import FakeProvider, LLMGateway, LLMProviderError, LLMSettings
from laya_platform.privacy import input_hmac
from laya_platform.storage import AuditEvent, Database, ReviewItem
from laya_platform.training import (
    DatasetBuildError,
    build_dataset,
    build_items,
    candidates,
    gold_for,
    read_jsonl,
    run_finetune,
    specialist_manifest,
    split,
    teacher_label,
    training_rows,
    upstream_rows,
    write_splits,
)

KEY = b"k" * 32
EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "support_triage" / "specs"
SPEC = load_decision_spec(EXAMPLE / "support_triage.yaml")
QSPEC = DecisionSpec.model_validate(
    {
        "id": "t.list",
        "version": 1,
        "languages": ["en"],
        "questions": {
            "team": {"type": "choice", "instructions": "Team?", "criteria": [1, 2]},
            "churn": {"type": "noul", "instructions": "Cancel?"},
        },
    }
)
VALUES = {"department": "billing", "urgency": 2, "churn_risk": True}


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    database = Database(f"sqlite:///{tmp_path / 't.db'}")
    database.upgrade()
    yield database
    database.dispose()


def _decided(
    database: Database,
    trace_id: str,
    state: Any,
    *,
    confidence: float = 0.9,
    outcome: str | None = None,
    incumbent: dict[str, Any] | None = None,
    system2: dict[str, Any] | None = None,
    agreement: dict[str, Any] | None = None,
    spec_id: str = SPEC.id,
    created_at: datetime | None = None,
) -> None:
    database.record(
        AuditEvent(
            trace_id=trace_id,
            spec_id=spec_id,
            spec_version=1,
            mode="shadow",
            input_hmac=input_hmac(KEY, state),
            answers={"churn_risk": {"value": True, "answer_confidence": confidence}},
            incumbent=incumbent,
            agreement=agreement,
            system2=system2,
            outcome=outcome,
            latency_ms=1.0,
            data_classification="synthetic",
            created_at=created_at or datetime.now(UTC),
        )
    )


# ---------------------------------------------------------------------------------- candidates
def test_candidates_put_disagreement_first_and_skip_what_humans_answered(
    database: Database,
) -> None:
    _decided(database, "calm", "a", confidence=0.99)
    _decided(database, "unsure", "b", confidence=0.4)
    _decided(database, "queued", "c", outcome="review")
    _decided(database, "split", "d", agreement={"churn_risk": False})
    _decided(database, "answered", "e", outcome="review")
    item = database.open_review(
        ReviewItem(trace_id="answered", spec_id=SPEC.id, spec_version=1, reason="r", status="open")
    )
    database.resolve_review(item, {"values": VALUES}, "ops")
    ranked = candidates(database, SPEC.id)
    assert [c["trace_id"] for c in ranked] == ["split", "queued", "unsure", "calm"]
    assert ranked[0]["reason"] == "disagreement"
    assert candidates(database, SPEC.id, limit=1)[0]["trace_id"] == "split"


# --------------------------------------------------------------------------------------- build
def test_build_joins_verified_inputs_with_the_best_label(database: Database) -> None:
    human, teacher, incumbent = "Cancelo tudo, CPF 529.982.247-25", "Ajuda", "Boleto"
    _decided(database, "h", human, incumbent={"department": "account"})
    _decided(database, "t", teacher, system2={"outcome": "ok", "values": VALUES})
    _decided(database, "i", incumbent, incumbent={"department": "billing", "unknown": 1})
    _decided(database, "u", "nobody labelled this")
    item = database.open_review(
        ReviewItem(trace_id="h", spec_id=SPEC.id, spec_version=1, reason="r", status="open")
    )
    database.resolve_review(item, {"values": VALUES}, "ops")
    exported: list[dict[str, Any]] = [
        {"trace_id": "h", "state": human, "group": "customer-1", "language": "pt"},
        {"trace_id": "t", "state": teacher},
        {"trace_id": "i", "state": incumbent, "tags": ["short"]},
        {"trace_id": "u", "state": "nobody labelled this"},
        {"trace_id": "t", "state": "tampered text"},
        {"trace_id": "missing", "state": "x"},
        {"trace_id": "h", "state": human},
    ]
    rows, stats = build_dataset(database, SPEC, exported, hmac_key=KEY)
    assert [(r["trace_id"], r["label_source"]) for r in rows] == [
        ("h", "human"),
        ("t", "teacher"),
        ("i", "incumbent"),
    ]
    assert rows[0]["state"] == "Cancelo tudo, CPF [CPF]"  # redacted by default
    assert rows[0]["expected"] == VALUES
    assert rows[0]["group"] == "customer-1"
    assert rows[2]["expected"] == {"department": "billing"}  # unknown questions dropped
    assert stats == {
        "label_human": 1,
        "label_teacher": 1,
        "label_incumbent": 1,
        "redacted": 1,
        "unlabelled": 1,
        "hmac_mismatch": 1,
        "unknown_trace": 1,
        "duplicate": 1,
    }


def test_label_sources_can_be_restricted(database: Database) -> None:
    _decided(database, "t", "x", system2={"outcome": "ok", "values": VALUES})
    rows, stats = build_dataset(
        database, SPEC, [{"trace_id": "t", "state": "x"}], hmac_key=KEY, sources=("human",)
    )
    assert rows == []
    assert stats == {"unlabelled": 1}
    with pytest.raises(DatasetBuildError, match="unknown label source"):
        build_dataset(database, SPEC, [], hmac_key=KEY, sources=("crowd",))


# --------------------------------------------------------------------------------------- split
def _rows(n: int) -> list[dict[str, Any]]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        {
            "state": f"s{i}",
            "expected": {"churn_risk": i % 2 == 0},
            "group": f"g{i // 3}",
            "created_at": (start + timedelta(hours=i)).isoformat(),
            "label_source": "human",
        }
        for i in range(n)
    ]


def test_group_split_never_shares_a_group_and_is_deterministic() -> None:
    rows = _rows(300)
    first, again = split(rows), split(rows)
    assert first == again
    groups = {name: {r["group"] for r in part} for name, part in first.items()}
    names = list(groups)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            assert not groups[a] & groups[b]
    assert sum(len(p) for p in first.values()) == 300
    assert 150 < len(first["train"]) < 250
    assert split(rows, seed="other") != first


def test_time_split_keeps_the_newest_decisions_for_test() -> None:
    parts = split(_rows(100), by="time")
    assert [len(parts[n]) for n in ("train", "validation", "calibration", "test")] == [
        70,
        10,
        10,
        10,
    ]
    assert max(r["created_at"] for r in parts["train"]) < min(
        r["created_at"] for r in parts["test"]
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"ratios": (0.5, 0.5)}, "ratios"),
        ({"ratios": (0.5, 0.5, 0.5, -0.5)}, "ratios"),
        ({"by": "random"}, "group' or 'time"),
    ],
)
def test_bad_splits_are_refused(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(DatasetBuildError, match=message):
        split(_rows(4), **kwargs)


def test_splits_are_written_with_a_manifest(tmp_path: Path) -> None:
    manifest = write_splits(split(_rows(40)), tmp_path, SPEC, by="group", seed="laya-platform")
    assert manifest["questions_sha256"] == SPEC.questions_sha256()
    for entry in manifest["splits"].values():
        assert (tmp_path / entry["file"]).is_file()
        assert len(read_jsonl(tmp_path / entry["file"])) == entry["rows"]
        assert entry["label_sources"] in ({"human": entry["rows"]}, {})
    saved = json.loads((tmp_path / "manifest.json").read_text())
    assert saved["splits"]["train"]["sha256"] == manifest["splits"]["train"]["sha256"]


# ------------------------------------------------------------------------------------- teacher
def _llm(*replies: Any) -> LLMGateway:
    settings = LLMSettings.model_validate(
        {
            "providers": {"local": {"kind": "openai_compatible", "local": True}},
            "tiers": {"teacher": {"provider": "local", "model": "model-a"}},
        }
    )
    return LLMGateway(settings, providers={"local": FakeProvider.answering(*replies)})


def test_the_teacher_votes_become_soft_targets() -> None:
    other = VALUES | {"department": "technical"}
    rows = [{"state": "x", "group": "g"}]
    labelled, summary = teacher_label(_llm(VALUES, VALUES, other), "teacher", SPEC, rows, samples=3)
    (row,) = labelled
    assert row["label_source"] == "teacher"
    assert row["expected"] == VALUES  # the majority
    assert row["gold"]["department"]["probabilities"] == pytest.approx(
        {"billing": 2 / 3, "technical": 1 / 3}
    )
    assert row["gold"]["urgency"]["probabilities"] == {"2": 1.0}  # a level index
    assert row["group"] == "g"
    assert summary == {"rows": 1, "failures": 0, "cost": 0.0, "tier": "teacher"}


def test_failed_votes_are_counted_and_a_row_without_votes_is_dropped() -> None:
    down = LLMProviderError("APIConnectionError: down")
    labelled, summary = teacher_label(
        _llm(down, down), "teacher", SPEC, [{"state": "x"}], samples=2
    )
    assert labelled == []
    assert summary["failures"] == 2
    with pytest.raises(DatasetBuildError, match="at least 1"):
        teacher_label(_llm(), "teacher", SPEC, [], samples=0)


# --------------------------------------------------------------------------- recipe rows/items
def test_gold_is_the_teacher_distribution_or_the_expected_one_hot() -> None:
    assert gold_for(SPEC, {"expected": VALUES}) == {
        "department": {"probabilities": {"billing": 1.0}},
        "urgency": {"probabilities": {"2": 1.0}},
        "churn_risk": {"probabilities": {"true": 1.0}},
    }
    soft = {"churn_risk": {"probabilities": {"true": 0.7, "false": 0.3}}}
    assert gold_for(SPEC, {"expected": VALUES, "gold": soft}) == soft


def test_training_rows_normalize_list_criteria_like_the_upstream_inference() -> None:
    (row,) = training_rows(QSPEC, [{"state": "s", "expected": {"team": 2, "churn": False}}])
    assert row["questions"]["team"]["criteria"] == {1: None, 2: None}
    assert row["gold"] == {
        "team": {"probabilities": {"2": 1.0}},
        "churn": {"probabilities": {"false": 1.0}},
    }
    assert training_rows(QSPEC, [{"state": "s", "expected": {}}]) == []


def test_upstream_rows_read_the_public_dataset_format() -> None:
    # One row as `Dataset.to_json` writes LocalLLaMA/typed-decisions: three JSON strings.
    row = {
        "state": json.dumps({"ticket": "x"}),
        "questions": json.dumps(
            {
                "team": {"type": "choice", "criteria": ["a", "b"]},
                "unlabelled": {"type": "noul"},
            }
        ),
        "gold": json.dumps({"team": {"probabilities": {"a": 0.8, "b": 0.2}}}),
    }
    (out,) = upstream_rows([row])
    assert out == {
        "state": {"ticket": "x"},
        "questions": {"team": {"type": "choice", "criteria": {"a": None, "b": None}}},
        "gold": {"team": {"probabilities": {"a": 0.8, "b": 0.2}}},
    }
    assert upstream_rows([row | {"gold": "{}"}]) == []


class Recipe:
    """A stand-in for the upstream recipe module: same functions, no torch, no weights."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.items: list[Any] = []
        self.trained: list[Any] = []
        self.torch = SimpleNamespace(
            save=lambda obj, path: Path(path).write_text(json.dumps(obj, default=str)),
            device=lambda name: f"device:{name}",
            cuda=SimpleNamespace(is_available=lambda: False),
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
        )
        self.AutoTokenizer = SimpleNamespace(from_pretrained=lambda path: f"tokenizer:{path}")

    def prepare_model(self, model_dir: str) -> str:
        return model_dir

    def build_training_item(self, tokenizer, cfg, state, question, gold_question):  # type: ignore[no-untyped-def]
        if question["type"] == "score":
            return None  # as the recipe does when the head budget hides an option
        item = {"q": question.get("instructions"), "target": gold_question["probabilities"]}
        self.items.append((tokenizer, cfg["max_len"], item))
        return item

    def train(self, args, model_dir, items_path, device):  # type: ignore[no-untyped-def]
        self.trained.append((vars(args).copy(), model_dir, Path(items_path).exists(), device))
        out = Path(args.output_dir)
        (out / "model.safetensors").write_bytes(b"weights")
        (out / "rl_agent_config.json").write_text("{}")
        (out / "tokenizer").mkdir(exist_ok=True)
        (out / "tokenizer" / "tokenizer.json").write_text("{}")
        (out / "checkpoint_latest").mkdir(exist_ok=True)
        (out / "checkpoint_latest" / "model.safetensors").write_bytes(b"older")


def test_items_use_the_recipe_and_its_own_option_keys(tmp_path: Path) -> None:
    recipe = Recipe(tmp_path)
    rows = training_rows(QSPEC, [{"state": "s", "expected": {"team": 2, "churn": True}}])
    items, skipped = build_items(recipe, "tok", {"max_len": 1024}, rows)  # type: ignore[arg-type]
    assert skipped == 0
    team, churn = items
    # int labels written to JSON as strings are looked up by the recipe's own key objects
    assert team["target"] == {1: 0.0, 2: 1.0}
    assert churn["target"] == {"false": 0.0, "true": 1.0}


def test_run_finetune_drives_the_recipe_and_describes_the_result(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    (base / "rl_agent_config.json").write_text(json.dumps({"head_max_len": 192}))
    recipe = Recipe(base)
    out = tmp_path / "out"
    rows = training_rows(SPEC, [{"state": "Cancelo", "expected": VALUES}])
    run = run_finetune(recipe, base_dir=base, rows=rows, out_dir=out, epochs=1)  # type: ignore[arg-type]
    assert (run["items"], run["skipped"], run["device"]) == (2, 1, "cpu")  # urgency is a score
    args, model_dir, items_existed, device = recipe.trained[0]
    assert (args["epochs"], args["no_checkpointing"], model_dir) == (1, False, str(base))
    assert items_existed
    assert device == "device:cpu"
    assert not (out / "train_items.pt").exists()  # removed after training
    assert recipe.items[0][1] == 1024  # max_len from the base config's default
    assert run["recipe"]["sha256"] == upstream_compat.FINETUNE_SCRIPT.sha256
    manifest = specialist_manifest(
        out,
        name="t.triage_pt",
        version="1",
        base="multilingual",
        spec=SPEC,
        languages=["pt"],
        dataset={"train": "train.jsonl"},
        training=run,
    )
    assert set(manifest.sha256) == {
        "model.safetensors",
        "rl_agent_config.json",
        "tokenizer/tokenizer.json",
    }  # the final checkpoint only, never checkpoint_latest
    assert manifest.decision_specs == (SPEC.id,)
    assert Path(manifest.source) == out.resolve()


def test_the_train_command_mixes_public_rows_in_and_records_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from laya_platform import cli
    from laya_platform.registry import load_manifest

    base = tmp_path / "base"
    base.mkdir()
    (base / "rl_agent_config.json").write_text("{}")
    recipe = Recipe(base)
    monkeypatch.setattr(upstream_compat, "load_script", lambda script, directory: recipe)
    train = tmp_path / "train.jsonl"
    train.write_text(json.dumps({"state": "Cancelo", "expected": VALUES}) + "\n")
    public = tmp_path / "typed_decisions.jsonl"
    public.write_text(
        json.dumps(
            {
                "state": json.dumps("ticket"),
                "questions": json.dumps({"q": {"type": "noul", "instructions": "public"}}),
                "gold": json.dumps({"q": {"probabilities": {"true": 1.0, "false": 0.0}}}),
            }
        )
        + "\n"
    )
    out = tmp_path / "out"
    code = cli.main(
        [
            "train",
            "--spec",
            str(EXAMPLE / "support_triage.yaml"),
            "--data",
            str(train),
            "--extra-upstream",
            str(public),
            "--base-dir",
            str(base),
            "--upstream-dir",
            str(tmp_path),
            "--out",
            str(out),
            "--name",
            "t.triage_pt",
            "--version",
            "1",
            "--epochs",
            "1",
        ]
    )
    assert code == 0, capsys.readouterr().err
    assert [item["q"] for _, _, item in recipe.items][-1] == "public"
    assert len(recipe.items) == 3  # two domain questions (urgency is skipped) + one public
    extra = load_manifest(out / "specialist.yaml").dataset["extra_upstream"]
    assert extra["sha256"] == hashlib.sha256(public.read_bytes()).hexdigest()


def test_no_item_no_training(tmp_path: Path) -> None:
    (tmp_path / "rl_agent_config.json").write_text("{}")
    with pytest.raises(ValueError, match="no training item"):
        run_finetune(Recipe(tmp_path), base_dir=tmp_path, rows=[], out_dir=tmp_path / "o")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- pinned recipe file
def test_the_recipe_file_must_be_the_pinned_one(tmp_path: Path) -> None:
    script = upstream_compat.FINETUNE_SCRIPT
    file = tmp_path / script.path
    file.parent.mkdir(parents=True)
    file.write_text("print('not the upstream recipe')\n")
    with pytest.raises(upstream_compat.UpstreamScriptMismatchError, match="not the pinned"):
        upstream_compat.script_file(script, tmp_path)
    assert script.url.endswith(f"/{script.commit}/{script.path}")


def test_loading_the_recipe_needs_torch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    content = b"import torch\n"
    (tmp_path / "recipe.py").write_bytes(content)
    script = dataclasses.replace(
        upstream_compat.FINETUNE_SCRIPT,
        path="recipe.py",
        sha256=hashlib.sha256(content).hexdigest(),
    )
    monkeypatch.setattr(upstream_compat, "torch_available", lambda: False)
    with pytest.raises(MissingRuntimeError, match="imports torch"):
        upstream_compat.load_script(script, tmp_path)
