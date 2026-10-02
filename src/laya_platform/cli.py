"""Command-line entry point: ``laya-platform``.

``--version`` reads distribution metadata and never imports ``laya`` (and therefore never imports
torch or touches the network). Every subcommand imports what it needs when it runs:

==============  ============================================================================
``serve``       run the gateway (``gateway`` extra)
``db upgrade``  apply the audit-store migrations (``gateway`` extra)
``eval``        evaluate an engine on a labelled dataset of one DecisionSpec; JSON + Markdown
``bands``       choose the confidence bands of a report by cost; prints the ``policy`` block
``calibrate``   fit per-type temperatures for one checkpoint (torch and weights)
``hash-key``    the SHA-256 under which an API key is configured
==============  ============================================================================
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from collections.abc import Callable, Sequence
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import Any

from laya_platform import __version__
from laya_platform._upstream import UPSTREAM_DISTRIBUTION, UPSTREAM_VERSION
from laya_platform.cli_ops import add_commands


def installed_upstream_version() -> str | None:
    """Version of the installed upstream distribution, or None when it is not installed."""
    try:
        return distribution_version(UPSTREAM_DISTRIBUTION)
    except PackageNotFoundError:
        return None


def version_text() -> str:
    """One line naming this package's version and the state of the upstream pin."""
    installed = installed_upstream_version()
    if installed == UPSTREAM_VERSION:
        upstream = f"upstream {UPSTREAM_DISTRIBUTION} {UPSTREAM_VERSION}"
    elif installed is None:
        upstream = f"upstream {UPSTREAM_DISTRIBUTION}: not installed, expected {UPSTREAM_VERSION}"
    else:
        upstream = (
            f"upstream {UPSTREAM_DISTRIBUTION}: installed {installed}, expected {UPSTREAM_VERSION}"
        )
    return f"laya-platform {__version__} ({upstream})"


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from laya_platform.gateway.app import create_app
    from laya_platform.gateway.settings import GatewaySettings

    app = create_app(GatewaySettings.load(args.config))
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def _db_upgrade(args: argparse.Namespace) -> int:
    from laya_platform.storage import Database

    database = Database(args.url)
    try:
        database.upgrade()
    finally:
        database.dispose()
    print("database at head")
    return 0


def _engine_config(args: argparse.Namespace) -> Any:
    import yaml

    from laya_platform.gateway.settings import EngineConfig

    if args.remote_url:
        return EngineConfig(
            kind="remote", remote_url=args.remote_url, remote_api_key_env=args.remote_api_key_env
        )
    if args.engine_config:
        data = yaml.safe_load(Path(args.engine_config).read_text(encoding="utf-8-sig")) or {}
        return EngineConfig.model_validate(
            data.get("engine", {}) if isinstance(data, dict) else data
        )
    return EngineConfig()


def _eval(args: argparse.Namespace) -> int:
    from laya_platform.core.spec import load_decision_spec
    from laya_platform.evaluation import build_report, evaluate, load_examples, to_markdown

    spec = load_decision_spec(args.spec)
    examples = load_examples(args.data, spec)
    if args.specialist:
        from laya_platform.registry import engine_label, load_manifest, load_specialist

        manifest = load_manifest(args.specialist)
        engine, described, routed = load_specialist(manifest), engine_label(manifest), False
    else:
        config = _engine_config(args)
        engine, routed = config.build(), True
        described = (
            f"remote {config.remote_url}"
            if config.kind == "remote"
            else ("router " + json.dumps(config.router, sort_keys=True))
        )
    cases = evaluate(engine, spec, examples, batch_size=args.batch_size, routed=routed)
    report = build_report(spec, cases, dataset=args.data, engine=described)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (out / "report.md").write_text(to_markdown(report), encoding="utf-8")
    overall = report["overall"]
    print(f"{report['id']}: n={overall['n']} accuracy={overall['accuracy']:.4f} -> {out}")
    return 0


def _bands(args: argparse.Namespace) -> int:
    import yaml

    from laya_platform.evaluation import choose_threshold, policy_for

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    cases = [c for c in report["cases"] if args.question is None or c["qid"] == args.question]
    choice = choose_threshold(cases, error_cost=args.error_cost, review_cost=args.review_cost)
    print(
        f"# threshold={choice.threshold} coverage={choice.coverage:.4f} "
        f"auto_error_rate={choice.auto_error_rate:.4f} expected_cost={choice.expected_cost:.4f} "
        f"n={choice.n}"
    )
    print(yaml.safe_dump({"policy": policy_for(report, choice)}, sort_keys=False), end="")
    return 0


def _calibrate(args: argparse.Namespace) -> int:
    import laya

    from laya_platform.core.spec import load_decision_spec
    from laya_platform.evaluation import load_examples
    from laya_platform.evaluation.calibration import fit_temperatures

    spec = load_decision_spec(args.spec)
    examples = load_examples(args.data, spec)
    agent = laya.load(args.model, subfolder=args.subfolder, revision=args.revision)
    result = fit_temperatures(agent, spec, examples, args.out)
    print(json.dumps(result, indent=2, default=str))
    return 0


def _hash_key(args: argparse.Namespace) -> int:
    from laya_platform.gateway.settings import hash_key

    if args.generate:
        key = secrets.token_urlsafe(32)
        print(f"key:    {key}")
        print(f"sha256: {hash_key(key)}")
        return 0
    key = sys.stdin.readline().strip()  # stdin, so the key stays out of the shell history
    if not key:
        print("laya-platform: error: no key on stdin", file=sys.stderr)
        return 2
    print(hash_key(key))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="laya-platform",
        description=(
            "Laya Decision Platform (provisional name). Independent project built on top of "
            "Laya; not affiliated with Convai Innovations."
        ),
    )
    parser.add_argument("--version", action="version", version=version_text())
    commands = parser.add_subparsers(dest="command", metavar="command")

    serve = commands.add_parser("serve", help="run the gateway")
    serve.add_argument("--config", required=True, help="gateway settings (YAML)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(handler=_serve)

    db = commands.add_parser("db", help="audit store").add_subparsers(dest="db_command")
    upgrade = db.add_parser("upgrade", help="apply the migrations")
    upgrade.add_argument("--url", required=True, help="SQLAlchemy database URL")
    upgrade.set_defaults(handler=_db_upgrade)

    evaluate = commands.add_parser("eval", help="evaluate an engine on a labelled dataset")
    evaluate.add_argument("--spec", required=True, help="DecisionSpec (YAML/JSON)")
    evaluate.add_argument("--data", required=True, help="labelled dataset (JSONL)")
    evaluate.add_argument("--out", required=True, help="directory for report.json / report.md")
    evaluate.add_argument("--engine-config", help="YAML with an 'engine' block (default Router)")
    evaluate.add_argument("--remote-url", help="evaluate a remote /v1/systemone endpoint")
    evaluate.add_argument("--remote-api-key-env", help="env var holding the remote's API key")
    evaluate.add_argument("--specialist", help="evaluate a specialist (manifest YAML/JSON)")
    evaluate.add_argument("--batch-size", type=int, default=16)
    evaluate.set_defaults(handler=_eval)

    bands = commands.add_parser("bands", help="choose confidence bands by cost")
    bands.add_argument("--report", required=True, help="report.json from 'eval'")
    bands.add_argument(
        "--error-cost", type=float, required=True, help="cost of a wrong auto answer"
    )
    bands.add_argument("--review-cost", type=float, required=True, help="cost of a human review")
    bands.add_argument("--question", help="use only this question's cases")
    bands.set_defaults(handler=_bands)

    calibrate = commands.add_parser("calibrate", help="fit temperatures (torch and weights)")
    calibrate.add_argument("--spec", required=True)
    calibrate.add_argument("--data", required=True, help="calibration split (JSONL)")
    calibrate.add_argument("--model", required=True, help="checkpoint id or path for laya.load")
    calibrate.add_argument("--subfolder")
    calibrate.add_argument("--revision")
    calibrate.add_argument("--out", required=True, help="calibration file to write")
    calibrate.set_defaults(handler=_calibrate)

    keys = commands.add_parser("hash-key", help="SHA-256 of an API key read from stdin")
    keys.add_argument("--generate", action="store_true", help="make a new key and print both")
    keys.set_defaults(handler=_hash_key)
    add_commands(commands, db)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] | None = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    try:
        return handler(args)
    # Most errors here are ValueErrors; a missing optional runtime is a MissingRuntimeError, an
    # ImportError (importing laya_platform.core here would load the upstream for every command).
    except (ValueError, OSError, ImportError) as exc:
        print(f"laya-platform: error: {exc}", file=sys.stderr)
        return 1
