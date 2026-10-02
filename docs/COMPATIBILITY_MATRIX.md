# Matriz de compatibilidade

> Fase 0. Define **o que a plataforma promete manter compatível** com o Laya upstream (`laya==0.3.23`) e com o
> protocolo Jev, e como cada promessa será verificada em `tests/compatibility/`.
>
> Legenda de status: **✅ upstream** = já existe no upstream e será reutilizado sem alteração ·
> **🧩 envolver** = reutilizado através de adaptador · **🆕 novo** = construído pela plataforma ·
> **⚠️ diferença** = existe divergência conhecida · **❓ não verificado** = depende de algo inacessível nesta auditoria.

## 1. Política de compatibilidade

1. A plataforma **não reimplementa** os primitivos do Laya. Toda decisão passa pelo pacote `laya` publicado.
2. O namespace `/v1/systemone*` é servido **pelo próprio app do upstream** (`laya.serve.create_app(router)`), montado
   dentro do gateway. Assim a resposta é idêntica à do `laya-serve`, byte a byte.
3. Tudo que é novo fica em **`/api/v1/*`** (HTTP), `laya_platform.*` (Python) e ferramentas MCP com prefixo
   `platform_*`. Nada novo entra em `/v1/*`, para não colidir com evoluções futuras do upstream ou do Jev.
4. Toda promessa desta matriz tem um teste de contrato. Testes sem pesos rodam em todo PR; testes com pesos
   rodam localmente/agendados (marcador `weights`).
5. Atualizar a versão do `laya` só é aceito com toda a suíte de compatibilidade verde.

## 2. Primitivos e campos de resposta

| item | upstream 0.3.23 | Jev (segundo docs do upstream) | plataforma | teste de contrato |
|---|---|---|---|---|
| `choice` → `choice`, `probabilities` | ✅ | ✅ | ✅ upstream | `test_primitives_shape.py` |
| `score` → `score` (valor esperado), `probabilities` `"0".."k-1"`, `legend` | ✅ | ⚠️ Jev ecoa nível `null`; Laya rejeita (422) | ✅ upstream | idem |
| `noul` → `noul` = P(true) | ✅ | ✅ | ✅ upstream | idem |
| `labels` (somente `noul`) | ✅ | ❓ | ✅ upstream | idem |
| `option_order` | ✅ | ❓ | ✅ upstream | `test_option_order.py` |
| `confidence` | entropia normalizada (`choice`/`score`); `max(p)` (`noul`) | `(n·p_max − 1)/(n − 1)` | ⚠️ **nunca** reutilizar threshold Jev | `test_confidence_semantics.py` |
| `answer_confidence` | `max(p)` | — | ✅ upstream; **único campo usado em gates** | idem |
| `action.act_probability` | presente, sem sinal útil (#185) | — | ignorado pela plataforma | — |
| `low_confidence`, `abstention`, `abstention_threshold` | só quando `min_confidence` é enviado | — | ✅ upstream | `test_abstention.py` |
| `usage.input_tokens`, `usage.output_tokens` (=0) | ✅ | ✅ | ✅ upstream | `test_usage.py` |
| `usage.state_tokens`, `state_tokens_dropped`, `truncated`, `truncated_questions`, `options` | ✅ | ❓ | ✅ upstream; **auditados** | idem |
| `routing` (`model`, `repo`, `reason`, `detection`, `workflow`) | ✅ | ausente (clientes Jev ignoram) | ✅ upstream + bloco `platform` só em `/api/v1/*` | `test_routing_block.py` |
| `model` no corpo da resposta | `"laya-rl-agent"` (constante) | id do modelo Jev | ✅ upstream | idem |

## 3. HTTP

### 3.1 Endpoints

| endpoint | upstream `laya-serve` | Jev | plataforma | observação |
|---|---|---|---|---|
| `POST /v1/systemone` | ✅ | ✅ | ✅ montado do upstream | resposta idêntica |
| `POST /v1/systemone/batch` | ✅ (≤ 64 estados) | ❓ | ✅ montado do upstream | |
| `GET /health` | ✅ (detalhes só com bearer quando há chave) | ❓ | ✅ montado do upstream | liveness |
| `GET /ready` | ❌ | ❓ | 🆕 | prontidão: checkpoints carregados, banco, flags |
| `GET /metrics` | ❌ | ❓ | 🆕 | formato Prometheus |
| `POST /api/v1/decide` | ❌ | ❌ | 🆕 | JSON Schema/Pydantic → valores tipados + política |
| `POST /api/v1/route` | ❌ | ❌ | 🆕 | System-1/System-2: decide quem responde (Laya, especialista, LLM, humano) |
| `/api/v1/models`, `/api/v1/specialists`, `/api/v1/evaluations`, `/api/v1/router`, `/api/v1/audit` | ❌ | ❌ | 🆕 | administração |

> O plano original listava `/v1/decide` e `/v1/route` como "endpoints base". Eles **não existem** no upstream
> nem no Jev; por isso ficam em `/api/v1/` (ver `ARCHITECTURE_PROPOSAL.md` §12).

### 3.2 Campos de requisição de `/v1/systemone`

| campo | upstream | Jev | plataforma |
|---|---|---|---|
| `state`, `questions` | obrigatórios | obrigatórios | ✅ |
| `model` | nome/alias/id HF do Laya; qualquer outro valor (ex.: `jev-1`) = roteamento automático | id Jev | ✅ upstream |
| `task`, `lang`, `lang_guess`, `max_len`, `head_max_len`, `min_confidence` | ✅ | ❓ | ✅ upstream |
| `hooks`, `on_predict_start`, `on_predict_end`, `hooks_raise`, `hooks_timeout` | recusados (422) | — | ✅ upstream |
| `batch_size`, `sort_by_length` (só batch) | ✅ | ❓ | ✅ upstream |

### 3.3 Limites

| limite | upstream HTTP | Jev | plataforma |
|---|---|---|---|
| opções por `choice` | 100 (413) e orçamento de tokens da cabeça (422) | 255 | ⚠️ mantém 100; acima de 20 usa shortlist automático em `/api/v1/*` |
| níveis por `score` | 32 | ❓ | 32 |
| perguntas por requisição | 64 | ❓ | 64 |
| opções no total | 512 | ❓ | 512 |
| estado | 50.000 caracteres | ❓ | configurável, ≤ upstream |
| corpo | 2 MiB | ❓ | 2 MiB |
| orçamento de tokens por requisição | 8192 (`LAYA_MAX_TOKEN_BUDGET`) | — | idem |
| concorrência admitida | 16 (503 + `Retry-After`) | ❓ | idem + fila de micro-batching |

### 3.4 Erros

| status | upstream | plataforma |
|---|---|---|
| 400 corpo inválido / surrogate isolado | ✅ | ✅ |
| 401 bearer ausente/errado | ✅ | ✅ (+ escopos por endpoint em `/api/v1/*`) |
| 413 limite | ✅ | ✅ |
| 422 pergunta inválida / controle inválido | ✅ | ✅ |
| 500 `inference failed` (texto fixo) | ✅ | ✅ |
| 503 ocupado | ✅ | ✅ |

### 3.5 Autenticação

| | upstream | Jev | plataforma |
|---|---|---|---|
| esquema | `Authorization: Bearer <LAYA_API_KEY>` opcional | ❓ (não verificado) | bearer obrigatório fora de `localhost`; chaves por cliente com escopos |

## 4. API Python

A plataforma **reexporta** e nunca sombreia nomes do `laya`. Contratos verificados (`test_python_api_contract.py`):

| superfície | assinatura/contrato fixado | status |
|---|---|---|
| `laya.load(...)`, `laya.Agent(...)` | `model_id_or_path, device, token, subfolder, fast, compile, revision, expected_sha256, lang_temperatures, hooks..., calibration` | ✅ upstream |
| `Agent.predict` ≡ `system_one` | `state, questions, lang, hooks..., max_len, head_max_len, min_confidence` | ✅ upstream |
| `Agent.predict_batch` | `+ batch_size, sort_by_length` | ✅ upstream |
| `Agent.predict_long` | janelas, `usage.windows`, `answer.window` | ✅ upstream |
| `Agent.decide` / `decide_batch` | `schema` **ou** `questions`, `return_details`, `min_confidence` | ✅ upstream |
| `Router(...)` | `models, device, token, revision, revisions, max_loaded, default, auto_task_detection, standalone_repos, preload, lang_guess, hooks..., agent_kwargs, sha256_digests` | ✅ upstream |
| `Router.route` / `route_batch` | `RouteDecision` (`model`, `reason`, `repo`, `detection`, `workflow`) | ✅ upstream |
| `Router.predict` / `predict_batch` / `predict_long` / `decide` / `decide_batch` | + bloco `routing` | ✅ upstream |
| `laya.predict_shortlist`, `shortlist_choice`, `embed_fn_from_agent`, `cached_embed_fn` | `k=20` padrão | ✅ upstream |
| `laya.decide`, `decide_batch`, `DecisionResult` | inclui `answer_confidence` | ✅ upstream |
| `laya.apply_confidence_gate`, `GATE_STATES` | `passed`/`abstained`/`unevaluated` | ✅ upstream |
| `laya.fit_temperatures`, `Agent.save_calibration`/`load_calibration` | JSON versionado | ✅ upstream |
| hooks: `PredictContext`, `BaseHook`, `AsyncHook`, eventos | 6 eventos, `run_id`, `ctx.skip()` | ✅ upstream |
| presets: `triage_questions`, `email_questions`, `guard_questions`, `moderation_questions`, `router_questions` | ids e tipos das perguntas | ✅ upstream |
| `laya.__all__` | 51 nomes em 0.3.23 | snapshot congelado no teste |
| **APIs internas usadas no treino** (`laya.common.build_model`, `build_sequence`, `render_options`, `proper_reward`, `collate_items`, `QTYPES`; `laya.agent._fix_tokenizer_config`) | não são API pública | 🧩 isoladas em `laya_platform.training.upstream_compat` com teste próprio |

## 5. CLI

| comando | upstream | plataforma |
|---|---|---|
| `laya` (rotear, `--predict`, `--preset`, `--batch`, `--questions`, `--min-confidence`, `--lang`, `--lang-guess`, `--model`) | ✅ | ✅ reutilizado como está |
| `laya-serve` | ✅ | substituído pelo gateway em produção; continua disponível |
| `laya-evals validate/run/compare` | ✅ | ✅ + `laya-platform eval` com métricas extras |
| `laya-mcp-server` | ✅ | ✅ + `laya-platform mcp-admin` |
| `laya-platform ...` | — | 🆕 (registry, dataset, treino, calibração, promoção) |

## 6. MCP

| ferramenta | upstream | plataforma |
|---|---|---|
| `laya_predict`, `laya_predict_batch`, `laya_route`, `laya_route_batch`, `laya_decide`, `laya_shortlist`, `laya_preset`, `laya_status` | ✅ (stdio) | ✅ reutilizadas sem alteração |
| `platform_health`, `platform_metrics`, `platform_specialists_list`, `platform_specialist_status`, `platform_calibration_report`, `platform_model_compare`, `platform_dataset_validate`, `platform_audit_query` | — | 🆕 **somente leitura** |
| `platform_evaluate`, `platform_train`, `platform_promote`, `platform_rollback` | — | 🆕 **desligadas por padrão**; exigem confirmação explícita e escopo administrativo |
| transporte HTTP (streamable) | ❌ | 🆕 opcional, sempre autenticado |

## 7. Integrações

| integração | upstream | plataforma |
|---|---|---|
| LangChain/LangGraph (`LayaRouter`, `LayaGuardrail`, `LayaTriage`, `LayaEvaluator`, `LayaDecision`) | ✅ | ✅ reutilizadas; 🆕 nós `EscalationNode`, `HumanReviewNode` |
| LlamaIndex (seletores/roteador) | ✅ | ✅ |
| CrewAI (`LayaCrewRouter`, `LayaTaskGuard`) | ✅ | ✅ + roteamento via gateway |
| TypeScript HTTP (`laya-client`) | ✅ | ✅ compatível com `/v1/systemone` |
| TypeScript local ONNX (`laya-ts`) | ✅ | fora do escopo inicial |
| A2A | ❌ | 🆕 |
| RAG (`KnowledgeProvider`) | ❌ | 🆕 |

## 8. Runtime e plataforma

| alvo | upstream | plataforma | nota |
|---|---|---|---|
| Python 3.10 / 3.11 / 3.12 / 3.13 | ✅ CI | 3.11 e 3.12 no CI | 3.12 recomendado para desenvolvimento |
| Linux x86_64 | ✅ | ✅ produção | |
| Windows | ✅ CI (3.11) | ✅ desenvolvimento | `HF_HUB_DISABLE_SYMLINKS` tratado pelo upstream |
| macOS / MPS | ✅ | best-effort | |
| CPU | ✅ | ✅ | ~0,2–0,5 s por chamada (publicado) |
| CUDA (cu128 / cu130) | ✅ | ✅ | |
| Intel XPU | ✅ | best-effort | |
| ONNX Runtime fp32 / INT8 per-tensor | ✅ | ✅ | INT8 per-channel é **proibido** (32% de concordância) |
| TileLang fast path | ✅ opcional | opcional | |
| `torch.compile` | ✅ opcional | desligado por padrão | recompilação por shape |

## 9. Checkpoints e especialistas

| checkpoint | carregável hoje | seleção automática no upstream | plataforma |
|---|---|---|---|
| `english`, `multilingual`, `typed-decisions` | ✅ | ✅ (Router) | ✅ via Router |
| checkpoint próprio (HF privado ou diretório local) | ✅ `laya.load(path)` | ❌ (Router aceita só 3 nomes) | 🆕 `SpecialistRegistry` + `SpecialistSelector` |
| calibração externa (`calibration=path`) | ✅ | — | ✅ versionada no registry |

## 10. Provedores LLM (System-2) — sem equivalente no upstream

Nenhuma regra de negócio referencia nome de modelo. A configuração mapeia **tiers** (`small`, `medium`, `frontier`)
para modelos concretos.

| provedor | adaptador | observação |
|---|---|---|
| Anthropic | 🆕 `AnthropicProvider` (SDK oficial `anthropic`) | IDs atuais confirmados: `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-4-5` |
| OpenAI | 🆕 `OpenAIProvider` | IDs de modelo ❓ (não verificados nesta auditoria) |
| Google Gemini | 🆕 `GeminiProvider` | ❓ |
| DeepSeek | 🆕 `DeepSeekProvider` (API compatível OpenAI) | ❓ |
| OpenRouter | 🆕 `OpenRouterProvider` | ❓ |
| Ollama / vLLM / OpenAI-compatível | 🆕 `OpenAICompatibleProvider` | modelos locais |

## 11. Suíte de testes de compatibilidade planejada (`tests/compatibility/`)

| arquivo | precisa de pesos? | o que garante |
|---|:---:|---|
| `test_upstream_pin.py` | não | versão instalada = versão fixada; hash do wheel confere |
| `test_python_api_contract.py` | não | assinaturas e `__all__` (snapshot) |
| `test_routing_contract.py` | não | precedência e motivos de roteamento (`Router.route`) |
| `test_schema_contract.py` | não | JSON Schema → perguntas; erros de schema |
| `test_abstention.py`, `test_confidence_semantics.py` | não | gate e semântica das confianças |
| `test_http_wire_contract.py` | não (engine falso injetado em `create_app`) | campos, códigos de erro, limites, headers `Server-Timing` |
| `test_mcp_tools_contract.py` | não | nomes e esquemas das 8 ferramentas |
| `test_training_internals_contract.py` | não | funções internas usadas pelo treino ainda existem e têm a mesma assinatura |
| `test_primitives_shape.py`, `test_usage.py`, `test_routing_block.py` | **sim** | forma real das respostas com checkpoint |
| `test_golden_decisions.py` | **sim** | decisões de referência (com tolerância) não regridem após atualização |
