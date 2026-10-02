"""Subcommands of Block C (``laya-platform dataset|train|specialist``, ``db purge``).

Kept apart from ``laya_platform.cli`` so the entry point stays small; like it, every handler
imports what it needs when it runs, so ``--version`` never loads the platform.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

HMAC_KEY_ENV = "LAYA_PLATFORM_HMAC_KEY"


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _database(url: str) -> Any:
    from laya_platform.storage import Database

    database = Database(url)
    database.upgrade()
    return database


# ------------------------------------------------------------------------------------- db purge
def db_purge(args: argparse.Namespace) -> int:
    if args.older_than_days < 1:
        raise ValueError("--older-than-days must be at least 1")
    database = _database(args.url)
    try:
        before = datetime.now(UTC) - timedelta(days=args.older_than_days)
        _print({"before": before.isoformat(timespec="seconds"), "deleted": database.purge(before)})
    finally:
        database.dispose()
    return 0


def db_forget(args: argparse.Namespace) -> int:
    hmacs = list(args.hmac or [])
    if args.hmac_file:
        lines = Path(args.hmac_file).read_text(encoding="utf-8").splitlines()
        hmacs += [line.strip() for line in lines if line.strip()]
    if not hmacs:
        raise ValueError("pass --hmac and/or --hmac-file")
    database = _database(args.url)
    try:
        _print({"inputs": len(hmacs), "deleted": database.forget(hmacs)})
    finally:
        database.dispose()
    return 0


# -------------------------------------------------------------------------------------- dataset
def dataset_candidates(args: argparse.Namespace) -> int:
    from laya_platform.training import candidates

    database = _database(args.db_url)
    try:
        for row in candidates(database, args.spec_id, args.limit):
            print(json.dumps(row))
    finally:
        database.dispose()
    return 0


def dataset_build(args: argparse.Namespace) -> int:
    from laya_platform.core.spec import load_decision_spec
    from laya_platform.training import build_dataset, read_jsonl, write_jsonl

    key = os.environ.get(HMAC_KEY_ENV, "")
    if len(key) < 32:
        raise ValueError(f"set ${HMAC_KEY_ENV} to the gateway's HMAC key (it verifies the inputs)")
    spec = load_decision_spec(args.spec)
    database = _database(args.db_url)
    try:
        rows, stats = build_dataset(
            database,
            spec,
            read_jsonl(args.inputs),
            hmac_key=key.encode("utf-8"),
            sources=tuple(args.sources.split(",")),
            redact_pii=not args.no_redact,
        )
    finally:
        database.dispose()
    digest = write_jsonl(rows, args.out)
    _print({"rows": len(rows), "sha256": digest, "stats": stats, "out": args.out})
    return 0


def dataset_split(args: argparse.Namespace) -> int:
    from laya_platform.core.spec import load_decision_spec
    from laya_platform.training import read_jsonl, split, write_splits

    ratios = tuple(float(r) for r in args.ratios.split(","))
    splits = split(read_jsonl(args.data), by=args.by, ratios=ratios, seed=args.seed)
    manifest = write_splits(
        splits, args.out, load_decision_spec(args.spec), by=args.by, seed=args.seed
    )
    _print({name: s["rows"] for name, s in manifest["splits"].items()})
    return 0


def dataset_label(args: argparse.Namespace) -> int:
    import yaml

    from laya_platform.core.spec import load_decision_spec
    from laya_platform.llm import LLMGateway, LLMSettings
    from laya_platform.training import read_jsonl, teacher_label, write_jsonl

    data = yaml.safe_load(Path(args.config).read_text(encoding="utf-8-sig")) or {}
    settings = LLMSettings.model_validate(data.get("llm") or {})
    rows, summary = teacher_label(
        LLMGateway(settings),
        args.tier,
        load_decision_spec(args.spec),
        read_jsonl(args.data),
        samples=args.samples,
    )
    write_jsonl(rows, args.out)
    _print(summary)
    return 0


# ---------------------------------------------------------------------------------------- train
def train(args: argparse.Namespace) -> int:
    from laya_platform.core import upstream_compat
    from laya_platform.core.spec import load_decision_spec
    from laya_platform.registry import write_manifest
    from laya_platform.training import (
        read_jsonl,
        run_finetune,
        specialist_manifest,
        training_rows,
        upstream_rows,
    )

    script = upstream_compat.FINETUNE_SCRIPT
    if args.fetch_upstream:
        upstream_compat.fetch_script(script, args.upstream_dir)
    recipe = upstream_compat.load_script(script, args.upstream_dir)
    spec = load_decision_spec(args.spec)
    rows = training_rows(spec, read_jsonl(args.data))
    if args.extra_upstream:  # public rows mixed in; their license is checked before (DG-1 §8)
        rows += upstream_rows(read_jsonl(args.extra_upstream))
    run = run_finetune(
        recipe,
        base_dir=args.base_dir,
        rows=rows,
        out_dir=args.out,
        device=args.device,
        epochs=args.epochs,
        micro_batch=args.micro_batch,
        grad_accum=args.grad_accum,
        calib_max=args.calib_max,
        checkpointing=not args.no_checkpointing,
    )
    dataset: dict[str, Any] = {"train": args.data}
    if args.extra_upstream:
        extra = Path(args.extra_upstream)
        dataset["extra_upstream"] = {
            "path": str(extra),
            "sha256": hashlib.sha256(extra.read_bytes()).hexdigest(),
        }
    if args.dataset_manifest:
        dataset["manifest"] = json.loads(Path(args.dataset_manifest).read_text(encoding="utf-8"))
    manifest = specialist_manifest(
        args.out,
        name=args.name,
        version=args.version,
        base=args.base,
        spec=spec,
        languages=list(spec.languages),
        dataset=dataset,
        training=run,
        owner=args.owner,
    )
    write_manifest(manifest, Path(args.out) / "specialist.yaml")
    _print({"manifest": str(Path(args.out) / "specialist.yaml"), **run})
    return 0


# ----------------------------------------------------------------------------------- specialist
def _registry(url: str) -> Any:
    from laya_platform.registry import SpecialistRegistry

    return SpecialistRegistry(_database(url))


def specialist_register(args: argparse.Namespace) -> int:
    from laya_platform.registry import load_manifest

    manifest = load_manifest(args.manifest)
    _registry(args.db_url).register(manifest, args.actor)
    _print({"registered": manifest.key, "status": "experimental"})
    return 0


def specialist_list(args: argparse.Namespace) -> int:
    registry = _registry(args.db_url)
    pointers = {
        spec: {role: p.key for role, p in roles.items()}
        for spec, roles in registry.pointers().items()
    }
    _print({"versions": registry.versions(), "pointers": pointers})
    return 0


def _gates(args: argparse.Namespace) -> Any:
    from laya_platform.registry import Gates

    return Gates(
        min_accuracy=args.min_accuracy,
        max_ece=args.max_ece,
        max_regression=args.max_regression,
        min_shadow_samples=args.min_samples,
        min_shadow_agreement=args.min_agreement,
        max_shadow_failure_rate=args.max_failure_rate,
    )


def specialist_shadow(args: argparse.Namespace) -> int:
    from laya_platform.core.spec import load_decision_spec

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    baseline = (
        json.loads(Path(args.baseline).read_text(encoding="utf-8")) if args.baseline else None
    )
    _registry(args.db_url).to_shadow(
        args.name,
        args.version,
        load_decision_spec(args.spec),
        report,
        args.actor,
        baseline=baseline,
        gates=_gates(args),
    )
    _print({"shadow": f"{args.name}@{args.version}"})
    return 0


def specialist_candidate(args: argparse.Namespace) -> int:
    evidence = _registry(args.db_url).to_candidate(
        args.name, args.version, args.actor, gates=_gates(args)
    )
    _print({"candidate": f"{args.name}@{args.version}", "evidence": evidence})
    return 0


def specialist_promote(args: argparse.Namespace) -> int:
    _registry(args.db_url).to_production(args.name, args.version, args.actor, args.reason)
    _print({"production": f"{args.name}@{args.version}"})
    return 0


def specialist_rollback(args: argparse.Namespace) -> int:
    restored = _registry(args.db_url).rollback(args.spec_id, args.actor, args.reason)
    _print({"spec": args.spec_id, "production": restored.key if restored else "router"})
    return 0


def specialist_deprecate(args: argparse.Namespace) -> int:
    _registry(args.db_url).deprecate(args.name, args.version, args.actor, args.reason)
    _print({"deprecated": f"{args.name}@{args.version}"})
    return 0


# --------------------------------------------------------------------------------------- parser
def add_commands(commands: Any, db: Any) -> None:
    """Add Block C's subcommands to ``laya-platform`` (``db`` is the ``db`` sub-parser group)."""
    purge = db.add_parser("purge", help="delete what the retention policy expires")
    purge.add_argument("--url", required=True, help="SQLAlchemy database URL")
    purge.add_argument("--older-than-days", type=int, required=True)
    purge.set_defaults(handler=db_purge)

    forget = db.add_parser("forget", help="erase the decisions on given inputs (by HMAC)")
    forget.add_argument("--url", required=True, help="SQLAlchemy database URL")
    forget.add_argument("--hmac", action="append", help="input HMAC (repeatable)")
    forget.add_argument("--hmac-file", help="one input HMAC per line")
    forget.set_defaults(handler=db_forget)

    dataset = commands.add_parser("dataset", help="datasets for specialists").add_subparsers(
        dest="dataset_command"
    )
    cand = dataset.add_parser("candidates", help="decisions most worth a label")
    cand.add_argument("--db-url", required=True)
    cand.add_argument("--spec-id", required=True)
    cand.add_argument("--limit", type=int, default=100)
    cand.set_defaults(handler=dataset_candidates)

    build = dataset.add_parser("build", help="join exported inputs with the platform's labels")
    build.add_argument("--db-url", required=True)
    build.add_argument("--spec", required=True, help="DecisionSpec (YAML/JSON)")
    build.add_argument("--inputs", required=True, help='JSONL: {"trace_id", "state", ...}')
    build.add_argument("--out", required=True, help="labelled dataset (JSONL)")
    build.add_argument("--sources", default="human,incumbent,teacher")
    build.add_argument("--no-redact", action="store_true", help="keep PII (needs DG-1)")
    build.set_defaults(handler=dataset_build)

    splitter = dataset.add_parser("split", help="train / validation / calibration / test")
    splitter.add_argument("--spec", required=True)
    splitter.add_argument("--data", required=True)
    splitter.add_argument("--out", required=True, help="directory for the splits + manifest")
    splitter.add_argument("--by", choices=["group", "time"], default="group")
    splitter.add_argument("--ratios", default="0.7,0.1,0.1,0.1")
    splitter.add_argument("--seed", default="laya-platform")
    splitter.set_defaults(handler=dataset_split)

    label = dataset.add_parser("label", help="soft labels from an LLM tier (votes)")
    label.add_argument("--spec", required=True)
    label.add_argument("--data", required=True)
    label.add_argument("--config", required=True, help="YAML with an 'llm' block")
    label.add_argument("--tier", required=True)
    label.add_argument("--samples", type=int, default=3)
    label.add_argument("--out", required=True)
    label.set_defaults(handler=dataset_label)

    trainer = commands.add_parser("train", help="fine-tune with the upstream recipe (GPU)")
    trainer.add_argument("--spec", required=True)
    trainer.add_argument("--data", required=True, help="train split (JSONL)")
    trainer.add_argument("--base-dir", required=True, help="base checkpoint directory")
    trainer.add_argument("--base", default="english", help="name of the base checkpoint")
    trainer.add_argument("--upstream-dir", required=True, help="upstream repository files")
    trainer.add_argument("--fetch-upstream", action="store_true", help="download the recipe")
    trainer.add_argument("--out", required=True, help="output checkpoint directory")
    trainer.add_argument("--name", required=True)
    trainer.add_argument("--version", required=True)
    trainer.add_argument("--owner")
    trainer.add_argument("--dataset-manifest", help="manifest.json from 'dataset split'")
    trainer.add_argument(
        "--extra-upstream", help="JSONL in the upstream format to mix in (typed-decisions export)"
    )
    trainer.add_argument("--device", default="auto", help="auto, cuda, mps or cpu")
    trainer.add_argument("--epochs", type=int, default=4)
    trainer.add_argument("--micro-batch", type=int, default=2)
    trainer.add_argument("--grad-accum", type=int, default=16)
    trainer.add_argument("--calib-max", type=int, default=400)
    trainer.add_argument("--no-checkpointing", action="store_true")
    trainer.set_defaults(handler=train)

    specialist = commands.add_parser("specialist", help="specialist registry").add_subparsers(
        dest="specialist_command"
    )
    commands_by_name = {
        "register": (specialist_register, "register a manifest (experimental)"),
        "list": (specialist_list, "versions and pointers"),
        "shadow": (specialist_shadow, "experimental -> shadow, on an evaluation report"),
        "candidate": (specialist_candidate, "shadow -> candidate, on shadow evidence"),
        "promote": (specialist_promote, "candidate -> production, with a named approver"),
        "rollback": (specialist_rollback, "return a spec to the previous version"),
        "deprecate": (specialist_deprecate, "retire a version"),
    }
    for name, (handler, help_text) in commands_by_name.items():
        command = specialist.add_parser(name, help=help_text)
        command.add_argument("--db-url", required=True)
        command.set_defaults(handler=handler)
        if name == "register":
            command.add_argument("--manifest", required=True)
        if name in ("shadow", "candidate", "promote", "deprecate"):
            command.add_argument("--name", required=True)
            command.add_argument("--version", required=True)
        if name != "list":
            command.add_argument("--actor", required=True, help="who does it (recorded)")
        if name in ("promote", "rollback", "deprecate"):
            command.add_argument("--reason", required=True)
        if name == "rollback":
            command.add_argument("--spec-id", required=True)
        if name == "shadow":
            command.add_argument("--spec", required=True)
            command.add_argument("--report", required=True, help="report.json of this specialist")
            command.add_argument("--baseline", help="report.json of what it replaces")
        if name in ("shadow", "candidate"):
            command.add_argument("--min-accuracy", type=float, default=0.0)
            command.add_argument("--max-ece", type=float)
            command.add_argument("--max-regression", type=float, default=0.0)
            command.add_argument("--min-samples", type=int, default=200)
            command.add_argument("--min-agreement", type=float, default=0.0)
            command.add_argument("--max-failure-rate", type=float, default=0.01)
