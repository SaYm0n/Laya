# Análise do upstream — Laya (`NandhaKishorM/laya`)

> **Público:** quem desenvolve · **Status:** referência da Fase 0 (`laya==0.3.23`) · **Atualizado:** 2026-10-02

> Fase 0 — auditoria. Este documento descreve o que o projeto oficial **realmente** entrega hoje,
> com base no código-fonte, e não apenas na documentação. Nada do upstream foi modificado.

## 0. Escopo e método

| item | valor |
|---|---|
| Repositório auditado | `https://github.com/NandhaKishorM/laya` |
| Commit auditado (`main`) | `4aa6761be8173de4ce6d92c31b3e40b6eaf59a7c` (2026-10-01 23:48 +0530) |
| Release de referência | tag `v0.3.23` → `d8a2e59781ca135169a36095056132e273cd9938` (2026-10-01 22:52 +0530) |
| Pacote PyPI | `laya==0.3.23`, publicado em 2026-10-01T17:30Z (é a versão mais recente no PyPI) |
| Hash do wheel | `laya-0.3.23-py3-none-any.whl` sha256 `30247fd93dec16b131d8483b1621db198600e90c777ad7d9992e48fc118db677` |
| Hash do sdist | `laya-0.3.23.tar.gz` sha256 `5812dfd7bc27032a0b969b7ee2de655460e4d925ad2d55f97a869cf15ad0deba` |
| Diferença `main` × `v0.3.23` | 32 commits, tocando `laya/agent.py`, `laya/router.py` e `laya/serve.py` (+75/−6 linhas) |

**Como foi feito**

1. Clone do repositório e leitura do código de `laya/` (≈16,3 mil linhas Python), `laya-ts/`, `sdk/typescript/`,
   `docs/`, `tests/` (86 arquivos, ≈35,4 mil linhas), workflows de CI, Dockerfile e notebooks.
2. Instalação isolada de `laya==0.3.23` a partir do PyPI e execução das partes **sem pesos**
   (roteamento, schema → perguntas, gate de abstenção) para confirmar comportamento real.
3. Cruzamento com as afirmações do plano elaborado pelo ChatGPT (ver `ARCHITECTURE_PROPOSAL.md`, seção 12).

**Limitações desta auditoria**

- O ambiente de execução desta sessão **bloqueia `huggingface.co`** (HTTP 403 no proxy) e `download.pytorch.org`.
  Por isso **nenhum checkpoint foi baixado** e nenhuma inferência real foi executada. Todos os números de
  acurácia/latência abaixo são os **publicados pelo upstream**, não medidos aqui.
- As licenças dos pesos no Hugging Face, dos encoders base e do dataset de treino **não puderam ser verificadas**
  diretamente (ver `LICENSE_AND_ATTRIBUTION.md`).
- O serviço Jev (TypeSafe) é fechado; toda informação sobre ele vem da documentação do upstream.

---

## 1. O que o Laya é (confirmado no código)

Um **modelo de decisão não autorregressivo**: encoder bidirecional + cabeça de decisão tipada. Ele não gera
texto; pontua opções fechadas.

```
sequência por pergunta:
[CLS] <tipo> instruções [SEP] [MASK] opção0 [MASK] opção1 ... [SEP] estado [SEP]

encoder (ModernBERT-large | mmBERT-base)
  → + embedding do tipo (choice/score/noul)
  → cabeça Transformer (2 camadas)
  → "scorer" aplicado nas posições [MASK]   → 1 logit por opção
  → softmax / temperatura (por tipo e por nº de opções) → probabilidades
  → act_head (sinal "agir?" — documentado como SEM sinal útil, issue #185)
```

- **"Um único forward pass"** significa: cada pergunta vira **uma linha** do batch; todas as perguntas de uma
  requisição são avaliadas no mesmo passe. Por isso `usage.input_tokens` cresce com o número de perguntas.
- Treinado com **RLCD**: gradiente de política com recompensas de *regras de pontuação estritamente próprias*
  (log score + esférica + RPS para `score`) mais cross-entropy contra **distribuições-alvo** (soft targets).
  `laya.common.proper_reward` aceita alvo one-hot **ou** distribuição.

### 1.1 Checkpoints publicados

| nome no Router | repo HF | encoder | parâmetros | `max_len` padrão | `head_max_len` |
|---|---|---|---|---|---|
| `english` | `convaiinnovations/laya` | ModernBERT-large | 421M | 512 | 192 |
| `multilingual` | `convaiinnovations/laya` (subpasta `multilingual`) / `laya-multilingual` | mmBERT-base | 322M | 1024 (até 8192) | 256 |
| `typed-decisions` | subpasta `typed-decisions` / `laya-typed-decisions` | ModernBERT-large | 421M | 1024 | 256 |

Revisões revisadas (opt-in) em `laya.PINNED_REVISIONS`:
`laya` `55cf4c4e…`, `laya-multilingual` `e4e9ddf2…`, `laya-typed-decisions` `1a793eb5…`.

### 1.2 Primitivos de decisão

| primitivo | entrada | saída principal | observações do código |
|---|---|---|---|
| `choice` | `criteria` dict `rótulo → descrição` ou lista de rótulos | `choice` (argmax) + `probabilities` | rótulo nulo, duplicado ou não-escalar → `ValueError` (422 no HTTP) |
| `score` | `criteria` lista ordinal (índice 0 primeiro) | `score` = **valor esperado** (pode cair entre níveis) + `legend` + `probabilities` | nível nulo rejeitado |
| `noul` | `criteria` opcional com chaves **apenas** `true`/`false`; `labels` opcional | `noul` = P(true) | chaves diferentes de true/false são rejeitadas (#156) |

Todos aceitam `option_order` (permuta de apresentação) para mitigar viés de posição.

### 1.3 Duas medidas de confiança (crítico)

| campo | definição | uso correto |
|---|---|---|
| `answer_confidence` | `max(p)` | **o número a usar em thresholds**; é o que a temperatura ajusta e o ECE mede |
| `confidence` | `choice`/`score`: `1 − H(p)/log k` (entropia normalizada); `noul`: `max(p, 1−p)` | **não** calibrado; varia com o nº de opções; não comparar com o mesmo threshold |
| Jev (referência) | `(n·p_max − 1)/(n − 1)` | thresholds do Jev **não** transferem para o Laya |

Os checkpoints base são **super-confiantes** tal como publicados e o `laya-multilingual` **não traz temperaturas
ajustadas**. Temperaturas carregadas são limitadas a `[0,5; 5,0]`.

---

## 2. Mapa de módulos (`laya/`)

| módulo | linhas | responsabilidade | depende de torch? | veredito para nosso projeto |
|---|---:|---|:---:|---|
| `agent.py` | 1916 | `Agent`/`load`: carregamento HF/local, validação de perguntas, `predict`/`system_one`, `predict_batch`, `predict_long`, `decide(_batch)`, AMP, fallback OOM→CPU, calibração | sim | **reusar** via adaptador |
| `router.py` | 1379 | `Router`: escolhe checkpoint (idioma/script/tarefa), LRU de checkpoints, pins de revisão e SHA-256, `predict_batch` heterogêneo | não (lazy) | **reusar**; estender **por fora** (ver §4.7) |
| `common.py` | 817 | formato de sequência, `DecisionModel`, regras de pontuação, ECE, temperaturas | sim | reusar no treino; **API interna** — fixar versão |
| `serve.py` | 1052 | FastAPI `/v1/systemone`, `/v1/systemone/batch`, `/health`; limites; bearer | não (lazy) | **montar** dentro do nosso gateway |
| `structured.py` | 399 | JSON Schema/Pydantic → perguntas; `decide`, `decide_batch` | não | reusar |
| `confidence.py` | 153 | `min_confidence`, `abstention` (`passed`/`abstained`/`unevaluated`) | não | reusar |
| `hooks.py` | 535 | `PredictContext`, hooks start/end/route/load/evict/error, `AsyncHook`, timeouts | não | **ponto de extensão principal** (auditoria, métricas, redação, cache, shadow) |
| `calibrate.py` | 433 | ajuste de temperatura por tipo/bucket, persistência JSON | sim | reusar; estender relatórios |
| `evals.py`, `evals_cli.py`, `_eval_policy.py`, `evals_shortlist.py` | 1595 | `laya-evals`: dataset JSONL, métricas, fatias, baseline, gates por fatia, atribuição de erro do shortlist | não | reusar; **estender métricas** |
| `shortlist.py` | 391 | top-k por embedding (encoder do próprio agente) + cache LRU | parcial | reusar |
| `onnx_agent.py` | 817 | `ONNXAgent` com a mesma API (batch, long, decide) | não (onnxruntime) | reusar |
| `presets.py` | 210 | triage, email, guard, moderation, router (LLM) | não | reusar como ponto de partida |
| `lang.py`, `email.py` | 993 | detecção de script/idioma heurística; limpeza de e-mail (EN/PT/ES/FR) | não | reusar |
| `revisions.py` | 141 | `PINNED_REVISIONS`, `LAYA_REVISION`, verificação SHA-256 | não | reusar |
| `cli.py` | 415 | CLI `laya` | — | reusar |
| `mcp/` | 1870 | servidor MCP **stdio** com 8 ferramentas | — | reusar; admin separado |
| `integrations/` | 2502 | LangChain/LangGraph, LlamaIndex, CrewAI | — | reusar |
| `fast.py`, `tl_kernels.py`, `_compile.py` | 543 | caminho rápido TileLang (CUDA), `torch.compile` | sim | opcional |

**API pública:** `laya.__all__` exporta 51 nomes (confirmado no wheel 0.3.23). `import laya` não importa torch
(atributos pesados são resolvidos sob demanda).

---

## 3. Auditoria por componente

### 3.1 README, `pyproject.toml`, LICENSE, SECURITY
- `pyproject.toml`: `requires-python >=3.10`; classificadores 3.10–3.13; dependências núcleo `torch>=2.0`,
  `transformers>=4.48`, `safetensors>=0.4`, `huggingface_hub>=0.20`, `numpy>=1.20`.
  Extras: `serve`, `fast`, `mcp` (`mcp>=2.2.0`), `structured`, `onnx`, `langchain`, `langgraph`, `llamaindex`, `crewai`.
  Scripts: `laya`, `laya-serve`, `laya-evals`, `laya-mcp-server`.
- LICENSE: Apache-2.0 (texto padrão, sem apêndice de copyright). **Não há arquivo `NOTICE`.**
- SECURITY.md: reporte privado via GitHub Advisories; suporte 0.3.x (ativo) e 0.2.x (só crítico).
- O README é extenso (≈1.560 linhas) e honesto sobre limitações ("Honest limits").

### 3.2 Agent
- `predict = system_one`. Parâmetros por chamada: `lang`, `max_len`, `head_max_len`, `min_confidence`, hooks.
- `predict_batch(states, questions, batch_size, sort_by_length, ...)` — mesmas perguntas para vários estados.
- `predict_long` — janelas sobrepostas; `noul` usa a janela mais forte, `choice`/`score` a mais confiante.
  **A probabilidade retornada é da janela vencedora, não é calibrada para o documento inteiro.**
- Integridade: `revision`, `expected_sha256` (verificado antes de desserializar), apenas `safetensors`.
- Concorrência: leitura concorrente; somente o rebaixamento de dispositivo após OOM é exclusivo.
- `fit_temperatures`, `save_calibration`, `load_calibration`, parâmetro `calibration=` no carregamento.

### 3.3 Router
- Precedência: `model` > `task` > workflow detectado (opt-in) > `lang` > `lang_guess` > detecção > `default`.
- `max_loaded=2` (LRU); `preload=True`; `attach()`; `unload()`.
- **Conjunto fechado de nomes**: `normalise_name` só aceita `english`, `multilingual`, `typed-decisions` (+ aliases).
  `Router(models=...)` apenas **remapeia** o repositório desses três nomes. Não é possível registrar
  um quarto checkpoint (ex.: `laya-pharma-v1`) no `Router` do upstream.
- `lang_guess` aceita código ou callable — é o ponto para plugar um detector de idioma real (fastText/CLD3).

### 3.4 ONNXAgent
- Mesma superfície (`system_one`, `predict_batch`, `predict_long`, `decide`, `decide_batch`).
- Exportação por `scripts/export_onnx.py` (**não** faz parte do wheel); `--quantize` INT8 per-tensor
  (per-channel derrubou a concordância para 32% — corrigido na 0.3.23).

### 3.5 Serving HTTP (`laya-serve`)
- Endpoints: **somente** `GET /health`, `POST /v1/systemone`, `POST /v1/systemone/batch` (máx. 64 estados).
  Não existem `/ready`, `/metrics`, `/v1/decide`, `/v1/route`.
- Limites: corpo 2 MiB; estado 50.000 caracteres; 64 perguntas; 100 opções por `choice`; 32 níveis por `score`;
  512 opções no total; `LAYA_MAX_TOKEN_BUDGET` 8192; `LAYA_MAX_CONCURRENT` 16 (excedente → 503 + `Retry-After`).
- Autenticação opcional por `LAYA_API_KEY` (comparação em tempo constante). Sem chave, o servidor é aberto e
  escuta em `0.0.0.0` por padrão.
- **Concorrência: uma inferência por vez** (executor de 1 worker + `asyncio.Lock`). Escalar exige réplicas
  ou micro-batching na frente.
- Erros: 400/401/413/422/500 (texto fixo, sem vazar detalhes)/503.
- `create_app(router)` aceita um `Router` injetado → **pode ser montado dentro de outra aplicação FastAPI**.
- `examples/server.py` (≈3.800 linhas, fora do wheel) traz um **playground web** completo e `/predict`.

### 3.6 MCP
- Transporte **stdio** apenas. Ferramentas: `laya_predict`, `laya_predict_batch`, `laya_route`, `laya_route_batch`,
  `laya_decide`, `laya_shortlist`, `laya_preset`, `laya_status`. Não há ferramentas administrativas.

### 3.7 SDKs TypeScript
- `sdk/typescript` → pacote npm **`laya-client`**: cliente HTTP tipado para `laya-serve` (ESM/CJS, presets, cancelamento).
- `laya-ts/` → pacote npm **`laya-ts`**: runtime **local** via ONNX (Node/navegador), com router, tokenizer, hooks,
  shortlist e structured em TS. Testes de paridade com o Python.

### 3.8 Hooks
- Eventos: `on_predict_start`, `on_predict_end`, `on_route`, `on_load`, `on_evict`, `on_error`.
- `PredictContext`: `states`, `questions`, `run_id`, `results`, `decision`, `model`, `agent`, `router`,
  `max_len`, `head_max_len`, `usage`, `started_at`, `elapsed_ms`, `error`; `ctx.skip()` para cache.
- Hooks podem reescrever estado (redação de PII **antes** da inferência) e resultado.
- Exemplos prontos: `audit.py`, `cache.py`, `redact.py`, `otel.py` (OTel apenas comentado).

### 3.9 Structured decisions
- Subconjunto de JSON Schema: objeto plano com `enum`/`const` (→ `choice`), `boolean` (→ `noul`), inteiro limitado
  (→ `score`), `Optional[...]`. Strings livres, arrays, objetos aninhados e `$ref` são rejeitados.
- Limites: 32 propriedades, 32 opções, 10 níveis.
- **Sem `description`, as instruções geradas são genéricas** (ex.: `"What is `dept`?"`) — confirmado no teste
  empírico. Recomendação de qualidade (não é exigência do upstream nem da `DecisionSpec`): em produção, dar uma
  `description` a toda propriedade.

### 3.10 Avaliação (`laya-evals`)
- Dataset JSONL (`state`, `questions`, `expected`, `tags`, `language`, `model`).
- Métricas: `choice_accuracy`, `noul_accuracy`, `score_mae`, `ScoreWithin`, `ece`, `mean_confidence`, latência p50/p95.
- Fatias por idioma/modelo/pergunta/tag; baseline com tolerâncias; política por fatia; identidade do run
  (sha256 do dataset e das perguntas, revisões); avaliação ONNX; atribuição retrieval × decisão no shortlist.
- **Ausentes**: precisão/recall/F1 por classe, matriz de confusão, Brier, NLL, FPR/FNR, cobertura × risco, p99.

### 3.11 Fine-tuning
- Código de treino **não é um módulo do pacote**: está num notebook Kaggle 2×T4 (o script `train_ddp.py` é escrito
  por `%%writefile` dentro de uma célula) e num script MPS/CPU (`notebooks/laya_finetune_typed_decisions_mps.py`).
- O notebook instala `laya>=0.1.6` (sem pin) e usa APIs internas (`laya.common.build_model`, `build_sequence`,
  `proper_reward`, `laya.agent._fix_tokenizer_config`).
- Dados esperados: `state`, `questions` e **`gold` = distribuição do professor por pergunta** (soft targets).
- Receita: 4 épocas, LR encoder 2,5e-5 / cabeça 1e-4, fp16, gradient checkpointing, `max_len` 1024.
  ≈4–5 h para ~30k perguntas em 2×T4.

### 3.12 Calibração
- Uma temperatura por tipo (e opcionalmente por bucket de nº de opções `2`, `3-5`, `6-10`, `11+`) e por idioma
  (`lang_temperatures`). Persistência em JSON separado dos pesos.
- O notebook ajusta temperaturas numa fatia retirada **antes** do treino; o upstream alerta que isso não substitui
  avaliação em dados separados.

### 3.13 Benchmarks (publicados pelo upstream; não reproduzidos aqui)
| | valor publicado |
|---|---|
| typed-decisions: `laya-typed-decisions` / `laya` / `laya-multilingual` | 0,766 / 0,362 / 0,352 (majoritária 0,461; aleatório 0,318) |
| Latência 1 pergunta, T4 GPU (EN / ML) | 39,5 ms / 32,8 ms |
| Latência com preload, **CPU** | **193–464 ms** |
| ECE antes → depois de ajustar temperatura (EN / ML) | 0,466 → 0,081 / 0,314 → 0,106 |
| Banking77 (muitas opções): Laya × Jev | 0,425 × 0,870 |
| Idiomas "utilizáveis" (>3× aleatório) no ML | 48 de 51 |

Os números do Jev são de terceiros, com amostras e prompts diferentes (aviso do próprio upstream).

### 3.14 Docker
- `Dockerfile` multi-stage, Python 3.11 slim, torch 2.14 fixado (CPU por padrão; cu128/cu130 por build-arg),
  usuário não-root `laya` (UID 10001), `TORCH_DISABLE_NATIVE_JIT=1`, segredo por arquivo (`LAYA_API_KEY_FILE`).
- Composes: `compose.yaml` (quickstart CPU), `compose.http.yaml` (servidor), `compose.cuda.yaml`,
  `compose.spark.yaml` (ARM64), `compose.modelscope.yaml` (pré-carga via ModelScope). Também Nix/NixOS.

### 3.15 Integrações
- LangChain/LangGraph: `LayaRouter`, `LayaGuardrail`, `LayaTriage`, `LayaEvaluator`, `LayaDecision` (com `batch`/`abatch`).
- LlamaIndex: seletores/roteador de consultas. CrewAI: `LayaCrewRouter`, `LayaTaskGuard`.
- Todas aceitam modo local ou remoto (`base_url`) e `confidence_threshold` sobre `answer_confidence`.

### 3.16 Testes e CI/CD
- 86 arquivos de teste; muitos são scripts com `assert` executáveis diretamente; `tests/test_hooks_api.py` é o
  **contrato da API pública**.
- CI (`ci.yml`): Python 3.10–3.13 no Linux, Python 3.11 no Windows, TypeScript, lint mínimo (`ruff --select=E9,F63,F7,F82,F401,F811`),
  `compileall`, build + `twine check`, exportação ONNX, evals sem pesos.
- `evals.yml`: avaliação com checkpoint real **semanal** e em release (não bloqueia PR).
- `security.yml`: gitleaks, `pip-audit`, CodeQL, verificação "nada de pickle / nada de exec".
- `docker.yml`: build CPU e CUDA ARM64, testes sem download de modelo.
- Actions fixadas por SHA; Dependabot ativo.

---

## 4. Validação empírica (wheel 0.3.23, sem pesos)

```text
Router().route({"body": ...})
"Hi, we were billed twice for March..."            → english       | English Latin text
"Fui cobrado duas vezes em março, quero o reembolso" → multilingual | Latin script but language looks like 'pt'
"Quero cancelar"                                    → english       | language not identified ... using default (english)
"मुझसे दो बार शुल्क लिया गया"                         → multilingual | non-Latin script (devanagari)
```

**Consequência direta para um público brasileiro:** textos curtos em português ("Quero cancelar",
"Esqueci minha senha") caem no checkpoint **inglês**, que colapsa fora do inglês. Para tráfego majoritariamente
em pt-BR é obrigatório usar `Router(default="multilingual")` e/ou um `lang_guess` com detector real.

`apply_confidence_gate(..., 0.9)` confirmou: `abstention` = `passed`/`abstained` + `abstention_threshold`;
sem `min_confidence` o payload não muda.

---

## 5. Limitações conhecidas (consolidadas)

1. **Base zero-shot fraca**: nos checkpoints base o typed-decisions fica abaixo da classe majoritária.
   O Laya é uma base rápida para **especializar**, não um decisor genérico pronto.
2. **Muitas opções**: o orçamento de tokens da cabeça (`head_max_len`) é dividido entre as opções; acima de ~20
   opções os rótulos ficam indistinguíveis. Mitigação: `predict_shortlist`, aumentar `head_max_len`, hierarquizar.
3. **Negação**: casos como "não quero cancelar" podem escolher "cancelar" com alta confiança (#377).
4. **`noul` segue os rótulos** no checkpoint inglês (#156); `noul` sem `criteria` responde "não" para tudo no `english`.
5. **Viés de posição** (#131), principalmente `score` no `multilingual`.
6. **`act_probability` sem sinal** (#185) — não usar.
7. **Super-confiança** como publicado; `multilingual` sem temperaturas.
8. **`predict_long`**: probabilidade da janela, não do documento; acurácia varia acima de ~4.000 tokens.
9. **Servidor serializa inferência**; sem métricas Prometheus; MCP só stdio.
10. **CPU**: ~0,2–0,5 s por chamada — a promessa de "33 ms" é GPU.
11. **Rótulos booleanos em `choice`** (`yes`/`no`, `true`/`false`) contaminam a decisão.
12. **Classificação pode ser influenciada por texto injetado** no estado. O Laya não "vaza" texto, mas uma
    instrução maliciosa dentro do estado pode deslocar a decisão. Ele **não** é, sozinho, um controle de segurança.

---

## 6. Riscos de depender do upstream

| risco | evidência | mitigação |
|---|---|---|
| Alta velocidade de mudança | 64 PRs na 0.3.23; 32 commits entre a tag `v0.3.23` e o `HEAD` de `main`, criado menos de 1 h depois | fixar release + hash; atualização só via testes de contrato/regressão |
| Uso de APIs internas no treino | notebook usa `laya.common.*`, `laya.agent._fix_tokenizer_config` | encapsular em um único adaptador + testes de contrato dessas funções |
| Router fechado em 3 nomes | `normalise_name` | registry/seleção de especialistas **acima** do Router |
| Pesos baixados do Hub em tempo de execução | default = branch padrão | `LAYA_REVISION=reviewed` + `LAYA_SHA256_DIGESTS`; espelho interno de pesos |
| Conflito de dependências entre extras | `crewai`, `langchain`, `llama-index` puxam muitas dependências | extras separados por aplicação + lockfile (`uv.lock`) |

---

## 7. Conclusão: reutilizar × estender × construir

| reutilizar como está | estender (por fora, sem fork) | construir (não existe no upstream) |
|---|---|---|
| Agent, Router, ONNXAgent, structured, confidence, shortlist, presets, lang/email, revisions, `laya-serve` (montado), MCP stdio, integrações de frameworks, Dockerfile como referência | avaliação (métricas e relatórios), calibração (relatórios, seleção de threshold), treino (extrair do notebook para módulo testável), hooks (auditoria persistente, OTel, Prometheus), Router (seleção de especialistas) | Specialist Registry, LLM Gateway, roteamento System-1/System-2, pipeline de dados e rotulagem por LLM professor, guardrails em camadas, RAG, A2A, MCP administrativo, gateway com modos 0–4/feature flags/kill switch, dashboard, micro-batching |

**Recomendação central:** depender do `laya` publicado (versão + hash fixados) e construir a plataforma como um
pacote separado que o **envolve**. Não fazer fork nem copiar código do upstream, exceto quando inevitável — e nesse
caso marcar as modificações conforme a Apache-2.0.
