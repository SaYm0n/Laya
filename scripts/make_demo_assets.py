"""Demo images for the README and the guides, produced by running the real platform.

The gateway runs in-process (FastAPI TestClient) and the CLI on a temporary database, both on the
synthetic example of ``examples/support_triage``. The System-1 is the simulated engine
(``FakeEngine``: scripted answers, no model) and the System-2 a simulated provider
(``FakeProvider``); everything else (policy, audit, review queue, PII redaction, metrics,
specialist registry) is the platform's own code. What they return is rendered as terminal-style
SVG cards, each labelled as coming from a simulated engine: the cards show the platform's
mechanics, never a model's accuracy.

    uv run python scripts/make_demo_assets.py              # writes docs/assets/demo/
    uv run python scripts/make_demo_assets.py --out DIR
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from xml.sax.saxutils import escape

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / "examples" / "support_triage"
DEFAULT_OUT = REPO_ROOT / "docs" / "assets" / "demo"
CAPTION = "Mars · motor simulado, dados sintéticos · scripts/make_demo_assets.py"
API_KEY = "demo-key"  # a throwaway key for the in-process gateway, never a real credential

# ------------------------------------------------------------------------------- terminal cards
FG, DIM, PROMPT = "#E2E8F0", "#64748B", "#86EFAC"
KEY, STRING, NUMBER, LITERAL, PUNCT = "#93C5FD", "#FDBA74", "#C4B5FD", "#86EFAC", "#94A3B8"
Segment = tuple[str, str]
JSON_TOKEN = re.compile(
    r'(?P<key>"(?:[^"\\]|\\.)*")(?=\s*:)|(?P<string>"(?:[^"\\]|\\.)*")'
    r"|(?P<number>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|(?P<literal>true|false|null)"
)


@dataclass
class Card:
    """A terminal window: a title bar, coloured lines and the provenance caption."""

    title: str
    lines: list[list[Segment]] = field(default_factory=list)

    def command(self, text: str) -> None:
        for i, part in enumerate(text.split("\n")):
            self.lines.append([("$ " if i == 0 else "  ", PROMPT), (part, FG)])

    def comment(self, text: str) -> None:
        self.lines.append([(f"# {text}", DIM)])

    def text(self, text: str, color: str = FG) -> None:
        self.lines.append([(text, color)])

    def blank(self) -> None:
        self.lines.append([])

    def json(self, value: Any, omitted: str | None = None) -> None:
        for line in json.dumps(value, indent=2, ensure_ascii=False).splitlines():
            self.lines.append(_highlight(line))
        if omitted:
            self.comment(omitted)

    def svg(self) -> str:
        char_w, line_h, pad, top = 8.6, 21, 24, 52
        longest = max([sum(len(t) for t, _ in line) for line in self.lines] + [60])
        width = max(int(longest * char_w + 2 * pad), 720)
        height = top + len(self.lines) * line_h + 46
        rows = []
        for i, line in enumerate(self.lines):
            y = top + 14 + i * line_h
            spans = "".join(
                f'<tspan fill="{color}">{escape(text)}</tspan>' for text, color in line if text
            )
            rows.append(f'<text x="{pad}" y="{y}" xml:space="preserve">{spans}</text>')
        dots = "".join(
            f'<circle cx="{22 + 20 * i}" cy="22" r="6" fill="{c}"/>'
            for i, c in enumerate(("#F87171", "#FBBF24", "#34D399"))
        )
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-label="{escape(self.title)}">\n'
            f"<title>{escape(self.title)}</title>\n"
            f'<rect width="{width}" height="{height}" rx="14" fill="#0B1020"/>\n'
            f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="14" '
            'fill="none" stroke="#1E293B"/>\n'
            f'<path d="M0 44 H{width}" stroke="#1E293B"/>{dots}\n'
            f'<text x="{width / 2}" y="27" text-anchor="middle" fill="#94A3B8" font-size="13" '
            'font-family="Inter, Segoe UI, Helvetica, Arial, sans-serif">'
            f"{escape(self.title)}</text>\n"
            "<g font-family=\"ui-monospace, SFMono-Regular, Menlo, Consolas, 'DejaVu Sans Mono', "
            'monospace" font-size="14">\n' + "\n".join(rows) + "\n</g>\n"
            f'<text x="{width - pad}" y="{height - 16}" text-anchor="end" fill="{DIM}" '
            'font-size="12" font-family="Inter, Segoe UI, Helvetica, Arial, sans-serif">'
            f"{escape(CAPTION)}</text>\n</svg>\n"
        )


def _highlight(line: str) -> list[Segment]:
    segments: list[Segment] = []
    cursor = 0
    for match in JSON_TOKEN.finditer(line):
        if match.start() > cursor:
            segments.append((line[cursor : match.start()], PUNCT))
        kind = match.lastgroup or "string"
        color = {"key": KEY, "string": STRING, "number": NUMBER, "literal": LITERAL}[kind]
        segments.append((match.group(), color))
        cursor = match.end()
    segments.append((line[cursor:], PUNCT))
    return segments


def pick(value: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    """The listed keys of ``value``, in that order (the rest is left out of the card)."""
    return {key: value[key] for key in keys if key in value}


# ------------------------------------------------------------------------------------ the demo
SPEC_ID = "examples.support_triage"
TRIAGE = "Fui cobrado duas vezes na fatura deste mês."
CHURN = "Vou cancelar o contrato se isso não for resolvido hoje."
UNSURE = "O app trava na segunda via. Meu e-mail: joana@exemplo.com"


class Scripted:
    """The simulated System-1 with answers changed between calls (each scene scripts its own)."""

    def __init__(self) -> None:
        from laya_platform.core.adapters import FakeEngine

        self.current = FakeEngine()

    def script(self, answers: Mapping[str, tuple[Any, float]]) -> None:
        from laya_platform.core.adapters import FakeAnswer, FakeEngine

        self.current = FakeEngine({q: FakeAnswer(v, p) for q, (v, p) in answers.items()})

    def __getattr__(self, name: str) -> Any:
        return getattr(self.current, name)


def demo_spec() -> dict[str, Any]:
    """The example spec in gated mode with an illustrative policy (not one fitted to data)."""
    spec = yaml.safe_load((EXAMPLE / "specs" / "support_triage.yaml").read_text(encoding="utf-8"))
    spec |= {
        "mode": "gated",
        "risk": "medium",
        "policy": {
            "calibration_ref": "eval-demo00000000",
            "bands": [
                {"min": 0.9, "outcome": "auto"},
                {"min": 0.6, "outcome": "review"},
                {"outcome": "escalate"},
            ],
            "never_auto_if": [{"question": "churn_risk", "equals": True}],
            "escalation_tier": "deep",
            "canary": 1.0,
        },
    }
    return cast(dict[str, Any], spec)


@contextlib.contextmanager
def gateway(workdir: Path) -> Iterator[tuple[Any, Scripted, Any, Any]]:
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from laya_platform.gateway import ApiKey, GatewaySettings, hash_key
    from laya_platform.gateway.app import create_app
    from laya_platform.llm import FakeProvider, LLMGateway, LLMSettings
    from laya_platform.storage import Database

    specs = workdir / "specs"
    specs.mkdir()
    (specs / "support_triage.yaml").write_text(
        yaml.safe_dump(demo_spec(), allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    url = f"sqlite:///{workdir / 'mars.db'}"
    database = Database(url)
    database.upgrade()
    llm_settings = LLMSettings.model_validate(
        {
            "allow_external": True,  # a simulated external provider, to show the PII redaction
            "providers": {
                "cloud": {"kind": "openai_compatible", "base_url": "https://llm.invalid"}
            },
            "tiers": {
                "deep": {
                    "provider": "cloud",
                    "model": "demo-llm",
                    "input_cost_per_mtok": 2.0,
                    "output_cost_per_mtok": 10.0,
                }
            },
        }
    )
    provider = FakeProvider()
    llm = LLMGateway(llm_settings, providers={"cloud": provider}, spend=database)
    engine = Scripted()
    settings = GatewaySettings(
        specs_dir=specs,
        database_url=url,
        hmac_key=SecretStr("demo-hmac-key-" + "x" * 32),
        api_keys=(
            ApiKey(
                name="ops",
                sha256=hash_key(API_KEY),
                scopes=("decide", "route", "admin", "metrics", "review"),
            ),
        ),
    )
    app = create_app(settings, engine=cast(Any, engine), database=database, llm=llm)
    try:
        with TestClient(app) as client:
            yield client, engine, provider, app
    finally:
        database.dispose()


def curl(method: str, path: str, body: Mapping[str, Any] | None = None) -> str:
    lines = [f"curl -s -X {method} localhost:8000{path} \\", '  -H "Authorization: Bearer $KEY"']
    if body is not None:
        lines[-1] += " \\"
        lines.append(f"  -d '{json.dumps(body, ensure_ascii=False)}'")
    return "\n".join(lines)


def call(client: Any, method: str, path: str, body: Mapping[str, Any] | None = None) -> Any:
    response = client.request(
        method, path, json=body, headers={"Authorization": f"Bearer {API_KEY}"}
    )
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path}: {response.status_code} {response.text}")
    if response.headers.get("content-type", "").startswith("application/json"):
        return response.json()
    return response.text


def gateway_cards(workdir: Path) -> dict[str, Card]:
    cards: dict[str, Card] = {}
    with gateway(workdir) as (client, engine, provider, app):
        # 1. a confident answer, automated in gated mode
        engine.script(
            {"department": ("billing", 0.96), "urgency": (2, 0.93), "churn_risk": (False, 0.97)}
        )
        body = {"spec": SPEC_ID, "state": TRIAGE}
        decision = call(client, "POST", "/api/v1/decide", body)
        card = Card("Decisão automatizada · POST /api/v1/decide")
        card.command(curl("POST", "/api/v1/decide", body))
        card.json(
            pick(decision, "trace_id", "mode", "outcome", "act", "reasons")
            | {"suggestion": pick(decision["suggestion"], "values", "bands", "calibration_ref")},
            omitted="suggestion.answers (probabilidades e answer_confidence) e routing omitidos",
        )
        cards["decide-auto"] = card

        # 2. a rule keeps a confident answer from being automated: the case goes to a person
        engine.script(
            {"department": ("billing", 0.94), "urgency": (3, 0.91), "churn_risk": (True, 0.95)}
        )
        body = {"spec": SPEC_ID, "state": CHURN}
        decision = call(client, "POST", "/api/v1/decide", body)
        reviews = call(client, "GET", "/api/v1/reviews")
        item = reviews[0] if isinstance(reviews, list) else reviews["items"][0]
        resolution = {"values": {"department": "account", "urgency": 3, "churn_risk": True}}
        resolved = call(client, "POST", f"/api/v1/reviews/{item['id']}/resolve", resolution)
        card = Card("Revisão humana · never_auto_if e fila de revisão")
        card.command(curl("POST", "/api/v1/decide", body))
        card.json(pick(decision, "outcome", "act", "reasons", "review_id"))
        card.blank()
        card.command(curl("GET", "/api/v1/reviews"))
        card.json([pick(item, "id", "trace_id", "spec_id", "reason", "status")])
        card.blank()
        card.command(curl("POST", f"/api/v1/reviews/{item['id']}/resolve", resolution))
        card.json(pick(resolved, "id", "status", "resolved_by", "resolution"))
        card.comment("a resolução vira rótulo 'human' para treinar especialistas")
        cards["review"] = card

        # 3. too unsure: escalated to the System-2 tier, PII masked before it leaves
        engine.script(
            {"department": ("technical", 0.55), "urgency": (2, 0.71), "churn_risk": (False, 0.9)}
        )
        provider.extend([{"department": "technical", "urgency": 2, "churn_risk": False}])
        body = {"spec": SPEC_ID, "state": UNSURE}
        decision = call(client, "POST", "/api/v1/decide", body)
        prompt = str(provider.calls[-1]["prompt"])
        sent = prompt.split("State (JSON data):\n", 1)[1].split("\n\n", 1)[0]
        card = Card("System-2 · escalonamento para um LLM com PII mascarada")
        card.command(curl("POST", "/api/v1/decide", body))
        system2 = decision.get("system2") or {}
        card.json(
            pick(decision, "outcome", "act", "reasons")
            | {"suggestion": pick(decision["suggestion"], "values", "source")}
            | {
                "system2": pick(
                    system2,
                    "tier",
                    "model",
                    "input_tokens",
                    "output_tokens",
                    "cost",
                    "redacted",
                    "agreement",
                )
            }
        )
        card.blank()
        card.comment("o que o provedor externo recebeu como estado:")
        card.text(sent, STRING)
        cards["system2"] = card

        # 4. what the operation watches
        metrics = call(client, "GET", "/metrics")
        wanted = (
            "laya_platform_decisions_total",
            "laya_platform_policy_outcomes_total",
            "laya_platform_acts_total",
            "laya_platform_reviews_resolved_total",
            "laya_platform_llm_cost_total",
            "laya_platform_system2_agreement_total",
            "laya_platform_reviews_open ",
        )
        card = Card("Operação · GET /metrics (Prometheus)")
        card.command(
            'curl -s localhost:8000/metrics -H "Authorization: Bearer $KEY" \\\n'
            "  | grep -E 'decisions|outcomes|acts|reviews|llm_cost|system2'"
        )
        for line in str(metrics).splitlines():
            if line.startswith(wanted):
                card.text(line)
        cards["metrics"] = card

        # 5. every endpoint the gateway adds (read from its own OpenAPI document)
        card = Card("API do gateway · rotas")
        for path, operations in app.openapi()["paths"].items():
            for method, operation in operations.items():
                card.lines.append(
                    [
                        (f"{method.upper():<6}", LITERAL),
                        (f"{path:<42}", FG),
                        (operation.get("summary", ""), DIM),
                    ]
                )
        card.blank()
        card.comment("com o Router do Laya, o app do upstream fica montado sem alteração:")
        card.comment("POST /v1/systemone · POST /v1/systemone/batch · GET /health")
        cards["api"] = card
    return cards


def specialist_card(workdir: Path) -> Card:
    from laya_platform import cli
    from laya_platform.core import DecisionSpec
    from laya_platform.registry import SpecialistManifest, SpecialistRegistry, engine_label
    from laya_platform.storage import ChallengerResult, Database

    url = f"sqlite:///{workdir / 'registry.db'}"
    database = Database(url)
    database.upgrade()
    spec = DecisionSpec.model_validate(demo_spec())
    registry = SpecialistRegistry(database)
    for version in ("1", "2"):
        manifest = SpecialistManifest(
            name="examples.triage_pt",
            version=version,
            base="multilingual",
            source=f"models/triage_pt-{version}",
            decision_specs=(SPEC_ID,),
            languages=("pt",),
        )
        report = {
            "id": f"eval-demo-v{version}",
            "identity": {
                "spec": SPEC_ID,
                "questions_sha256": spec.questions_sha256(),
                "engine": engine_label(manifest),
            },
            "overall": {"accuracy": 0.9},
        }
        registry.register(manifest, "ml")
        registry.to_shadow(manifest.name, version, spec, report, "ml")
        for i in range(200):  # shadow runs of the challenger, recorded by the gateway in real use
            database.record_challenger(
                ChallengerResult(
                    trace_id=f"v{version}-{i}",
                    spec_id=SPEC_ID,
                    name=manifest.name,
                    version=version,
                    agreement={"department": i % 10 != 0},
                )
            )
        registry.to_candidate(manifest.name, version, "ml")
        if version == "1":
            registry.to_production(manifest.name, version, "Márcio", "primeira versão")
    database.dispose()

    shown = "sqlite:///mars.db"
    card = Card("Especialistas · promoção com aprovador e rollback sem deploy")

    def run(args: Sequence[str], keep: str | None = None) -> None:
        card.command(wrap(["laya-platform", *(_quote(a).replace(url, shown) for a in args)]))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(args))
        if code != 0:
            raise RuntimeError(f"{args}: exit {code}")
        value = json.loads(out.getvalue())
        card.json(value[keep] if keep else value)
        card.blank()

    run(
        [
            "specialist",
            "promote",
            "--db-url",
            url,
            "--name",
            "examples.triage_pt",
            "--version",
            "2",
            "--actor",
            "Márcio",
            "--reason",
            "aprovado na revisão",
        ]
    )
    run(["specialist", "list", "--db-url", url], keep="pointers")
    run(
        [
            "specialist",
            "rollback",
            "--db-url",
            url,
            "--spec-id",
            SPEC_ID,
            "--actor",
            "plantão",
            "--reason",
            "regressão em produção",
        ]
    )
    card.lines.pop()
    return card


def _quote(arg: str) -> str:
    return f'"{arg}"' if " " in arg else arg


def wrap(words: Sequence[str], width: int = 78) -> str:
    """A shell command on several lines, broken between an option and the next one."""
    lines, current = [], ""
    for word in words:
        if current and word.startswith("--") and len(current) + len(word) + 12 > width:
            lines.append(current + " \\")
            current = "  " + word
        else:
            current = f"{current} {word}" if current else word
    return "\n".join([*lines, current])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    # ignore_cleanup_errors: on Windows an SQLite file the CLI still holds cannot be deleted yet
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        (root / "gateway").mkdir()
        (root / "registry").mkdir()
        cards = gateway_cards(root / "gateway")
        cards["specialists"] = specialist_card(root / "registry")
    for name, card in cards.items():
        target = out / f"{name}.svg"
        target.write_text(card.svg(), encoding="utf-8")
        print(target.relative_to(out.parent) if out.is_relative_to(REPO_ROOT) else target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
