# Example: support ticket triage (synthetic)

A generic DecisionSpec and a small synthetic pt-BR dataset (short text, negation, noise, pt/en
mix) to exercise the gateway and the evaluation end to end. Nothing here is real data; the
dataset is too small to calibrate anything and only shows the format.

```bash
export LAYA_PLATFORM_HMAC_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
laya-platform hash-key --generate            # copy the sha256 into gateway.yaml
laya-platform serve --config examples/support_triage/gateway.yaml

laya-platform eval --spec examples/support_triage/specs/support_triage.yaml \
  --data examples/support_triage/eval_ptbr.jsonl --out reports/support_triage
laya-platform bands --report reports/support_triage/report.json --error-cost 5 --review-cost 1
```

`eval` downloads the checkpoint on first use (or point it at a running gateway or `laya serve`
with `--remote-url`). `bands` prints the `policy` block to paste into the spec; it names the
report it came from in `calibration_ref`.
