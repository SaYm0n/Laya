# Exemplo: triagem de chamados de suporte (sintético)

> **Público:** quem quer experimentar o Mars · **Status:** vigente · **Atualizado:** 2026-10-03

Um `DecisionSpec` genérico e um dataset **sintético** pequeno em pt-BR (texto curto, negação, ruído, mistura de
português e inglês) para exercitar o gateway e a avaliação de ponta a ponta. Nada aqui é dado real. O dataset é
pequeno demais para calibrar qualquer coisa e só mostra o formato.

| arquivo | o que é |
|---|---|
| [`specs/support_triage.yaml`](specs/support_triage.yaml) | três perguntas: equipe (`choice`), urgência de 0 a 3 (`score`) e risco de cancelamento (`noul`); começa em `shadow` |
| [`eval_ptbr.jsonl`](eval_ptbr.jsonl) | 24 casos rotulados, no formato `{"state", "expected", "language", "tags"}` |
| [`gateway.yaml`](gateway.yaml) | configuração local do gateway; o bloco `llm` (System-2) vem comentado |

## Sem pesos: a demonstração

Roda em qualquer máquina, sem acesso ao Hugging Face. Usa o gateway e a CLI reais, com um motor simulado:

```bash
uv run python scripts/make_demo_assets.py --out /tmp/mars-demo
```

O resultado são os cartões de [docs/assets/demo](../../docs/assets/demo), mostrados no [README](../../README.md#demonstração).

## Com pesos: gateway e avaliação

Os pesos do Laya são baixados no primeiro uso (é preciso acesso ao Hugging Face):

```bash
export LAYA_PLATFORM_HMAC_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uv run laya-platform hash-key --generate            # copie o sha256 para gateway.yaml
uv run laya-platform serve --config examples/support_triage/gateway.yaml

uv run laya-platform eval --spec examples/support_triage/specs/support_triage.yaml \
  --data examples/support_triage/eval_ptbr.jsonl --out reports/support_triage
uv run laya-platform bands --report reports/support_triage/report.json --error-cost 5 --review-cost 1
```

- O `eval` também pode avaliar um gateway no ar ou um `laya serve`, com `--remote-url`.
- O `bands` imprime o bloco `policy` para colar no spec. Ele cita o relatório de origem em `calibration_ref`.

Próximos passos: [fluxos de trabalho](../../docs/WORKFLOWS.md), do shadow ao especialista em produção.
