# Proposta de arquitetura — Mars Decision Platform

> **Público:** quem desenvolve · **Status:** referência da Fase 0 · **Atualizado:** 2026-10-03

> Fase 0. Arquitetura-alvo baseada no que o upstream `laya==0.3.23` realmente oferece
> (ver `UPSTREAM_ANALYSIS.md`). Onde esta proposta diverge do plano original, a seção 12 explica o motivo.
> O produto se chama **Mars Decision Platform** (decidido em 2026-10-03; veja a [marca](assets/brand/README.md)).
> O pacote `laya_platform` e a CLI `laya-platform` mantêm os nomes atuais até uma troca própria.

## 1. Objetivo

Uma plataforma que coloca o Laya como **System-1** (decisões fechadas, rápidas, calibradas, locais) dentro de um
fluxo de produção que também sabe quando **não** decidir sozinho: escalar para um LLM (**System-2**), para um
humano, ou apenas observar (shadow). E que usa esse próprio fluxo para **gerar dados e treinar especialistas**,
porque o upstream deixa claro que é a especialização que entrega acurácia.

## 2. Princípios

1. **Depender, não bifurcar.** O pacote `laya` publicado (versão + hash fixados) é a única implementação dos
   primitivos. A plataforma o envolve. Nenhum arquivo do upstream é copiado sem necessidade.
2. **O Laya decide; a aplicação age.** Uma decisão é evidência, não permissão. Ações têm sua própria política de risco.
3. **Shadow primeiro.** Instalar a plataforma nunca altera o comportamento de um sistema existente.
4. **Thresholds vêm da calibração**, por decisão e por domínio, sempre sobre `answer_confidence`, com proveniência
   (qual relatório de avaliação justificou o número).
5. **Agnóstico de modelo.** Regras de negócio referenciam *tiers* (`small`, `medium`, `frontier`) e capacidades,
   nunca nomes de modelos.
6. **Português como cidadão de primeira classe.** Roteamento, avaliação e calibração com dados pt-BR desde o início.
7. **CI sem pesos.** Tudo que pode ser testado sem baixar checkpoints é testado em todo PR.
8. **Nenhum segredo, nenhum dado bruto em log por padrão.** Hash com chave (HMAC) e redação.

## 3. Visão de contexto

```
                         ┌──────────────────────────────────────────┐
  Sistemas existentes ──►│            Decision Gateway              │◄── Operadores (dashboard, CLI)
  Agentes (A2A, MCP)  ──►│  /v1/systemone (contrato do upstream)    │◄── Clientes MCP (Claude Code, Cursor)
  Frameworks (LangGraph, │  /api/v1/decide | route | admin          │
  CrewAI, LlamaIndex) ──►└───────────────┬──────────────────────────┘
                                         │
        ┌────────────────┬───────────────┼────────────────┬─────────────────┐
        ▼                ▼               ▼                ▼                 ▼
   Guardrails      Knowledge (RAG)  System-1 Engine   Decision Policy   Escalation Router
   input/decision/ KnowledgeProvider (laya Router +   (gate calibrado,  ─► LLM Gateway (System-2)
   action/output   → montagem do    especialistas)    risco, modo 0–4)  ─► Fila de revisão humana
                   estado                                                ─► Agent Dispatcher (A2A)
        │                │               │                │                 │
        └────────────────┴───────┬───────┴────────────────┴─────────────────┘
                                 ▼
               Observabilidade + Auditoria (hooks, OTel, Prometheus, audit trail)
                                 │
                                 ▼
          Data & Training Loop: amostragem ativa → rotulagem (LLM professor + humano)
          → dataset versionado → fine-tune RLCD → calibração → avaliação → Specialist Registry
          → shadow → candidate → production
```

## 4. Vocabulário (evita a confusão de "router")

O plano original usa "router" para quatro coisas diferentes. Aqui cada uma tem nome próprio:

| nome | o que escolhe | com base em | onde mora |
|---|---|---|---|
| **CheckpointRouter** | `english` × `multilingual` × `typed-decisions` | script/idioma/tarefa | `laya.Router` (upstream, sem alteração) |
| **SpecialistSelector** | qual especialista fine-tuned responde uma decisão | tipo de decisão, domínio, idioma, status no registry, modo | plataforma |
| **DecisionPolicy** (confidence gate) | `auto` × `review` × `escalate` × `reject` | `answer_confidence` calibrada, risco da ação, truncamento | plataforma |
| **EscalationRouter** (System-1 → System-2) | tier de LLM ou humano | política + preset `router_questions` (dificuldade, domínio, ferramentas, sensibilidade) | plataforma |
| **AgentDispatcher** | qual agente recebe uma tarefa (A2A/CrewAI) | capacidades declaradas dos agentes | plataforma |

## 5. Estrutura do repositório

Um único pacote Python com subpacotes e *extras* opcionais. Para um desenvolvedor, 11 pacotes versionados
separadamente (como no plano original) custam mais do que entregam; a separação lógica é mantida por fronteiras de
import verificadas no CI (`import-linter`). Se um subpacote precisar de ciclo de vida próprio, ele vira membro de
um *workspace* `uv` sem mudar imports.

```
SaYm0n/Laya
├── pyproject.toml              # projeto laya-platform; extras: gateway, llm, rag, mcp, a2a, agents, training, onnx, dev
├── uv.lock                     # lock reprodutível (laya fixado por versão e hash)
├── src/laya_platform/
│   ├── core/                   # DecisionEngine (Protocol), modelos de requisição/resposta, DecisionSpec
│   │   ├── adapters/           # upstream_router, agent, onnx, remote_http, fake (testes)
│   │   └── upstream_compat.py  # ÚNICO lugar que toca APIs internas do laya
│   ├── registry/               # SpecialistRegistry, manifestos, máquina de estados de promoção
│   ├── selection/              # SpecialistSelector
│   ├── policy/                 # DecisionPolicy, bandas de confiança, risco, proveniência
│   ├── escalation/             # EscalationRouter (System-1 → System-2 → humano)
│   ├── llm/                    # LLMProvider + provedores, tiers, custo, saídas estruturadas
│   ├── guardrails/             # input / decision / action / output
│   ├── rag/                    # KnowledgeProvider + provedores, montagem de contexto
│   ├── agents/                 # A2A, AgentDispatcher, integrações (LangGraph, CrewAI, LlamaIndex)
│   ├── mcp/                    # servidor MCP administrativo (leitura por padrão)
│   ├── evaluation/             # métricas além de laya.evals, relatórios
│   ├── calibration/            # relatórios, seleção de threshold por custo
│   ├── data/                   # ingestão, validação, sanitização (PII), splits, rotulagem
│   ├── training/               # loop RLCD extraído do notebook (1 GPU e DDP), exportação
│   ├── observability/          # hooks de auditoria, OTel, Prometheus
│   ├── storage/                # SQLAlchemy + Alembic
│   ├── gateway/                # FastAPI, modos 0–4, feature flags, kill switch, micro-batching
│   └── cli.py                  # laya-platform ...
├── configs/                    # decisions/*.yaml, policies, providers.yaml (sem segredos)
├── specialists/                # manifestos (sem pesos)
├── notebooks/                  # Colab / Kaggle / local — finos, chamam laya_platform.training
├── deploy/                     # docker (cpu, cuda, onnx), compose
├── tests/                      # unit, integration, compatibility, regression, security, evaluation
└── docs/
```

## 6. Componentes

### 6.1 System-1 Engine (`core`)

```python
class DecisionEngine(Protocol):
    def predict(self, state, questions, **controls) -> dict: ...          # payload do upstream
    def predict_batch(self, requests, **controls) -> list[dict]: ...
    def route(self, state, questions=None, **hints) -> dict: ...
```

Adaptadores: `UpstreamRouterEngine` (`laya.Router`), `AgentEngine` (`laya.load(path)` — especialistas),
`OnnxEngine` (`ONNXAgent`), `RemoteEngine` (cliente de `/v1/systemone`) e `FakeEngine` (determinístico, para testes).
O payload devolvido é **sempre** o do upstream; a plataforma só acrescenta um bloco `platform` nas respostas de
`/api/v1/*`.

**Contrato de `/v1/systemone*`.** A compatibilidade prometida é de **contrato wire/API** — schema, campos, tipos,
valores (com tolerância numérica), status codes e semântica — e não de igualdade binária de headers ou corpo.
Middleware, serialização e versões de FastAPI/Starlette podem mudar bytes sem mudar o contrato; os testes comparam o
JSON interpretado (ver `COMPATIBILITY_MATRIX.md` §1).

Configuração padrão recomendada para tráfego brasileiro:
`Router(default="multilingual", lang_guess=<detector real>, revision via LAYA_REVISION=reviewed, sha256 fixados)`.

**Micro-batching.** O `laya-serve` executa uma inferência por vez. O gateway agrega requisições concorrentes por até
*N* itens ou *T* ms e chama `Router.predict_batch` (que já agrupa por checkpoint e por schema). É o ganho de
throughput mais barato disponível; escala horizontal vem depois, com réplicas.

### 6.2 DecisionSpec — a unidade de configuração

Toda decisão de negócio é declarada e versionada:

```yaml
# configs/decisions/support.ticket_triage.yaml   (valores ilustrativos)
id: support.ticket_triage
version: 3
schema:                     # JSON Schema (ou `questions:` no formato Laya)
  type: object
  properties:
    department: {type: string, enum: [billing, technical, commercial], description: "Qual equipe deve tratar?"}
    urgency:    {type: integer, minimum: 0, maximum: 4, description: "Quão urgente é?"}
    churn_risk: {type: boolean, description: "O cliente ameaça cancelar?"}
languages: [pt, en]
engine:
  specialist: laya-support          # opcional; resolvido pelo SpecialistSelector
  fallback_checkpoint: multilingual
policy:
  calibration_ref: eval-run-2026-11-03-7f3a   # relatório que justificou as bandas
  bands:                                       # sobre answer_confidence; NÚMEROS ILUSTRATIVOS
    - {min: 0.93, outcome: auto}
    - {min: 0.70, outcome: review}
    - {outcome: escalate}
  never_auto_if: [truncated, options_collapsed, language_mismatch]
risk: low                      # low | medium | high | critical
mode: shadow                   # offline | shadow | advisory | gated | production
```

### 6.3 Specialist Registry e SpecialistSelector

Como o `laya.Router` aceita apenas três nomes, especialistas são carregados com `laya.load(<repo ou diretório>)`
e selecionados **acima** do Router.

Manifesto (`specialists/<id>/<versão>.yaml`): `id`, `name`, `domain`, `version`, `base_model` (+ revisão),
`checkpoint_uri`, `sha256` (por arquivo), `calibration_uri`, `dataset_version`, `training_run`, `training_date`,
`metrics` (refs para relatórios), `confidence_thresholds` (por DecisionSpec), `languages`, `decision_specs`,
`status`, `license`, `owner`.

Pesos **nunca** vão para o git: repositório privado no Hugging Face, armazenamento de objetos ou diretório local,
sempre verificados por SHA-256 no carregamento (mecanismo nativo `expected_sha256`).

Máquina de estados com portões objetivos:

```
experimental ──(avaliação held-out ≥ mínimos, sem regressão por fatia)──► shadow
shadow ──(N amostras reais, concordância e calibração em produção dentro da tolerância)──► candidate
candidate ──(aprovação humana registrada + rollback testado)──► production
qualquer ──► deprecated            production ──(kill switch / regressão)──► versão anterior (rollback imediato)
```

### 6.4 DecisionPolicy (confidence gate)

- Usa **somente** `answer_confidence` (a `confidence` de entropia não é calibrada).
- Bandas por DecisionSpec, derivadas de um relatório de calibração, escolhidas por **custo de erro**
  (ex.: custo de um falso positivo × custo de revisão humana), não por um número fixo.
- Bloqueios independentes da confiança: `usage.truncated`, opções colapsadas (`usage.options`), estado em pt roteado
  para o checkpoint inglês, decisão sem `answer_confidence` (`abstention = unevaluated`).
- Ações irreversíveis ou de risco `high`/`critical` exigem humano **sempre**, qualquer que seja a confiança.

### 6.5 LLM Gateway (System-2)

```python
class LLMProvider(Protocol):
    name: str
    capabilities: set[str]           # structured_output, tools, vision, logprobs, batch, ...
    def complete(self, request: LLMRequest) -> LLMResponse: ...
    def structured(self, request: LLMRequest, schema: dict) -> StructuredResponse: ...
```

Configuração (`configs/providers.yaml`, segredos só por variável de ambiente/gerenciador):
`provider`, `model`, `base_url`, `api_key_env`, `capabilities`, `cost_profile` (preço por 1M tokens),
`latency_profile`, `privacy` (`external` | `local`), `enabled`, `tier`.

Provedores: Anthropic (SDK oficial), OpenAI, Gemini, DeepSeek, OpenRouter, Ollama/vLLM e genérico
OpenAI-compatível. Retentativas com backoff, *circuit breaker*, orçamento por decisão, métricas de tokens/custo.
Quando a escalada precisa de uma decisão tipada, o LLM recebe **o mesmo schema** da DecisionSpec via saída
estruturada, para que o resultado seja comparável com o do Laya.

**Regra de dependência de modelos.** O núcleo (políticas, interfaces, roteamento, testes centrais) depende apenas de
`provider`, `tier` e `capabilities`. IDs concretos de modelos (`claude-*`, `gpt-*`, `gemini-*`, `deepseek-*` e
equivalentes) são **referências temporais**: vivem só em configuração e em documentação datada. Um teste de guarda
no CI (`tests/unit/test_model_id_guard.py`) falha se um desses padrões aparecer em `src/`, `scripts/` ou `tests/`.

### 6.6 EscalationRouter (System-1 → System-2 → humano)

```
pedido ─► Laya (especialista ou genérico) ─► DecisionPolicy
             │ auto ──────────────► decisão estruturada (respeitando o modo)
             │ review ────────────► fila humana (com sugestão do Laya)
             │ escalate ──► preset router_questions (dificuldade, domínio, precisa de ferramentas, sensível)
             │                 └─► tier small | medium | frontier  ─► resposta estruturada
             │                         └─ discordância Laya × LLM em risco ≥ medium ─► humano
             └ reject ────────────► recusa explicada
```

Tudo que é escalado vira **dado de treino rotulado** (seção 7).

### 6.7 Guardrails em quatro camadas

| camada | determinístico (sempre) | sinais do Laya (nunca sozinhos) |
|---|---|---|
| **Input** | tamanho, encoding, segredos (regex de chaves/tokens), PII brasileira (CPF, CNPJ, e-mail, telefone) com redação antes de qualquer LLM externo | `guard_questions`: jailbreak, injeção, dados sensíveis, severidade |
| **Decision** | truncamento, opções colapsadas, incoerência de idioma, schema | política de confiança calibrada |
| **Action** | catálogo de ações com risco e reversibilidade, idempotência, limites de taxa, dry-run, aprovação humana para irreversíveis | classificação de risco (especialista) |
| **Output** | validação de schema da saída do LLM, vazamento de PII, citações obrigatórias em RAG | `moderation_questions` |

O preset de guarda do upstream é zero-shot (≈0,70 em `prompt-injections` no checkpoint inglês, publicado) e uma
classificação **pode ser influenciada por texto injetado no estado**. Ele é um sinal; o controle é a camada
determinística + a política de ação. Um especialista de guarda treinado e avaliado pode ganhar mais peso depois.

### 6.8 Conhecimento (RAG)

`KnowledgeProvider.search(query, filters, k) -> list[Chunk]` e um `ContextAssembler` que monta o estado do Laya
respeitando o espaço real de tokens (o upstream informa `usage.truncated`; a montagem usa `state_room`). Recuperação
fica separada de decisão e de geração: fluxos `RAG → Laya`, `RAG → LLM`, `RAG → agente`.
Primeiro provedor: **pgvector**, porque reaproveita o PostgreSQL de produção. Qdrant, Chroma, Milvus,
Elasticsearch e Neo4j/GraphRAG entram por demanda.

### 6.9 Agentes: A2A e frameworks

- `AgentRegistry` com *agent cards* (nome, capacidades, endpoint, autenticação) e envelope de tarefa
  (`task`, `context`, `requested_capability`, `priority`, `trace_id`).
- `AgentDispatcher` transforma as capacidades em uma pergunta `choice` (mesma técnica do `LayaCrewRouter`) e aplica a
  `DecisionPolicy`; baixa confiança → orquestrador LLM ou humano.
- LangChain/LangGraph, CrewAI e LlamaIndex: **reusar** as integrações do upstream; acrescentar só nós que conhecem o
  gateway (`EscalationNode`, `HumanReviewNode`, `PolicyGateNode`).

### 6.10 MCP

- O servidor MCP do upstream (stdio, 8 ferramentas) é usado sem alteração.
- Um servidor administrativo separado expõe ferramentas `platform_*` **somente leitura** por padrão.
- Ferramentas que alteram estado (avaliar, treinar, promover, reverter) ficam **desligadas** e, quando ligadas,
  exigem escopo administrativo e confirmação explícita registrada em auditoria. Promoção para `production` nunca é
  feita só por MCP.

### 6.11 Observabilidade e auditoria

Implementadas como **hooks do upstream** (`on_route`, `on_predict_start/end`, `on_error`, `on_load`, `on_evict`) +
instrumentação do gateway e do LLM Gateway:

- **Métricas (Prometheus `/metrics`)**: requisições, latência por estágio, checkpoint/especialista, distribuição de
  `answer_confidence`, abstenção, fallbacks, escaladas, tokens e custo estimado de LLM, dispositivo, carga/evicção de
  modelos, fallbacks OOM→CPU.
- **Traces (OpenTelemetry)**: um span por estágio, correlacionado por `trace_id` (plataforma) e `run_id` (upstream).
- **Audit trail** por decisão: `trace_id`, `run_id`, `timestamp`, `decision_spec@versão`, `mode`, engine
  (checkpoint/especialista, versão, revisão, sha256), `input_hash`, idioma detectado, respostas (rótulo +
  `answer_confidence`), resultado da política (+ versão), guardrails, escalada (provedor, tier, modelo, tokens, custo,
  latência), ação final e ator (automático ou id do revisor), latências, erros.
- `input_hash` = **HMAC-SHA256 com chave secreta**: um SHA-256 simples de textos curtos ("quero cancelar") é
  revertível por dicionário.
- Estado bruto **não** é armazenado por padrão; quando necessário para treino, armazenar apenas a versão redigida,
  com retenção configurável (LGPD).

### 6.12 Persistência

SQLAlchemy 2 + Alembic. SQLite em desenvolvimento, PostgreSQL em produção. Tabelas: `decision_specs`,
`specialists`, `specialist_versions`, `eval_runs`, `calibration_runs`, `policies`, `audit_events` (particionada por
mês no PostgreSQL), `review_queue`, `datasets`, `dataset_versions`, `labels`, `experiments`, `feature_flags`,
`api_keys` (somente hash). Pesos e datasets ficam em armazenamento de artefatos; o banco guarda URI + sha256.

### 6.13 Integração com sistemas existentes

| modo | nome | comportamento |
|---|---|---|
| 0 | offline | avaliação sobre logs exportados; nenhuma chamada em produção |
| 1 | shadow (**padrão**) | o sistema atual continua decidindo; o Laya decide em paralelo, de forma assíncrona e com timeout, e só registra. Com dados reais, só após o gate DG-1 (§6.15) |
| 2 | advisory | a sugestão aparece para o humano; o humano decide e a escolha é registrada como rótulo |
| 3 | gated | automação apenas na banda `auto` de uma DecisionSpec aprovada e numa fatia limitada (canário) |
| 4 | production | automação na DecisionSpec inteira, com amostragem contínua para auditoria |

- Modo **por DecisionSpec**, alterável por *feature flag* sem deploy.
- **Kill switch** global e por DecisionSpec: volta tudo para shadow imediatamente.
- **Rollback**: o registry mantém o ponteiro para a versão anterior em `production`.
- A chamada de shadow usa *outbox*/fila: falha ou lentidão da plataforma nunca afeta o sistema atual.
- SDK cliente mínimo: `decide(spec_id, state, incumbent=...) -> {suggestion, outcome, act}` — o sistema existente
  sempre recebe `act=False` até o modo permitir.

### 6.14 Implantação

- Imagens: `cpu`, `cuda`, `onnx-cpu`, seguindo as boas práticas do Dockerfile do upstream (multi-stage, torch
  fixado, usuário não-root, `TORCH_DISABLE_NATIVE_JIT=1`, segredo por arquivo). O `laya` entra pelo wheel do PyPI
  com hash verificado, não por cópia do código.
- Pesos: volume de cache do Hugging Face ou pré-carga na imagem; `LAYA_REVISION=reviewed` + `LAYA_SHA256_DIGESTS`.
- `docker-compose`: gateway + PostgreSQL (com pgvector) + Prometheus/Grafana opcionais.
- Desenvolvimento no Windows com `uv` e Python 3.12; produção em Linux.
- Segredos só por variável de ambiente ou gerenciador; `.env` nunca versionado (`.env.example` sim).
- Capacidade: em CPU contar com ~0,2–0,5 s por chamada (publicado). Para latência de dezenas de ms é preciso GPU,
  ONNX INT8 ou micro-batching.

### 6.15 Data Governance Gate (DG-1) — requisito bloqueante

O modo shadow não altera a decisão do sistema existente, mas **processa dados reais**. Por isso nenhum dado real
entra na plataforma — shadow, avaliação offline sobre exportações reais, criação de datasets ou envio a qualquer LLM —
antes de o gate **DG-1** estar aprovado. Até lá, desenvolvimento e testes usam apenas dados sintéticos ou públicos.

| item | o que precisa estar definido e aprovado |
|---|---|
| Classificação de dados | níveis (público, interno, confidencial, pessoal, pessoal sensível) e a classificação de cada fonte e de cada DecisionSpec |
| PII / LGPD | base legal e finalidade por fonte, minimização, necessidade de RIPD, atendimento a direitos do titular |
| Retenção | prazos por tipo (metadados de auditoria, estado redigido, rótulos, datasets, relatórios) e o padrão mínimo |
| Criptografia | TLS em trânsito; criptografia em repouso para banco, backups e artefatos; gestão e rotação das chaves (incluindo a chave do HMAC) |
| Controle de acesso | papéis com menor privilégio; leitura de auditoria e de datasets separadas da operação; acesso a esses dados também auditado |
| Audit logs | o que é registrado e o que nunca é; armazenamento append-only; quem pode consultar |
| Datasets de treino | proveniência, base legal, aprovação antes do uso, versionamento, proibição de dados sensíveis sem aprovação explícita |
| LLM externo | **proibido por padrão**; liberação explícita por DecisionSpec e por classificação; redação obrigatória; provedor com termos de não retenção e de não treinamento; dados sensíveis só com professor local |
| Sanitização / redação | regras pt-BR (CPF, CNPJ, RG, e-mail, telefone, cartão, endereço) testadas; redação antes de persistir e antes de qualquer LLM |
| Exclusão / expurgo | expurgo automático por retenção; atendimento a pedido de exclusão do titular (localização via HMAC do identificador); propagação para datasets derivados e política de re-treino dos especialistas afetados |

Evidência exigida: `docs/DATA_GOVERNANCE.md` aprovado por você (responsável pelo tratamento), configuração versionada
e testes das regras de redação e expurgo. A partir da F3, o gateway **recusa** iniciar qualquer modo com dados reais
se a configuração de governança não referenciar uma aprovação DG-1 válida.

## 7. O ciclo de dados e destilação (a principal adição ao upstream)

O upstream mostra que o checkpoint base fica perto do acaso no typed-decisions (0,36) e que o especializado chega a
0,766. O treino RLCD usa **distribuições-alvo** (`gold` por pergunta). A plataforma fecha esse ciclo usando o próprio
System-2 como professor:

```
produção (shadow/advisory) ─► audit trail (estado redigido, decisões, resultado real quando houver)
   ─► amostragem ativa: baixa confiança, discordância Laya × sistema atual × LLM, fatias raras, idiomas
   ─► rotulagem
        • LLM professor com saída estruturada no MESMO schema → distribuição por pergunta
          (log-probabilidades quando o provedor expõe; senão votação de N amostras ou distribuição declarada)
        • revisão humana nos casos de risco e de discordância → subconjunto "ouro"
        • qualidade do professor medida contra o ouro (teto de concordância, como o upstream fez)
   ─► dataset versionado (sha256), splits train / validation / calibration / test
        por grupo (cliente, conversa) e por tempo — nunca avaliar no que foi treinado
   ─► fine-tune RLCD (soft targets) ─► calibração (fatia própria) ─► avaliação held-out com métricas completas
   ─► registry: experimental → shadow → candidate → production
```

Cuidados:

- **LGPD e privacidade**: nenhuma etapa deste ciclo roda com dados reais antes do gate DG-1 (§6.15). Depois dele,
  dados reais só vão para um LLM externo se a DecisionSpec permitir, após redação e com base legal; para dados
  sensíveis, usar professor local (Ollama/vLLM).
- **Distribuições do professor precisam ser validadas.** A API Messages da Anthropic oferece saída estruturada e
  processamento em lote (Message Batches, útil para rotular offline), mas não documenta log-probabilidades; em
  Claude Opus 5.5 e Sonnet 5.5 também não é possível ajustar `temperature`. Para esse provedor, distribuições
  vêm de votação ou declaração, e a variância entre amostras precisa ser medida antes de usar.
- O professor define o teto: se ele erra num tipo de caso, o especialista aprende o erro. Por isso o subconjunto
  ouro humano é obrigatório.

## 8. Avaliação e calibração

Estende `laya.evals` (sem substituí-lo) com: precisão, recall e F1 por classe, matriz de confusão, Brier, NLL,
ECE com diagrama de confiabilidade, MAE de `score`, FPR/FNR no threshold escolhido, curva cobertura × risco,
taxa de abstenção, latência p50/p95/p99, custo por decisão (incluindo escaladas) e todas as métricas **por fatia**
(idioma, DecisionSpec, especialista, canal). Nenhum modelo é promovido só por acurácia. Seleção de threshold por
custo esperado, registrada como artefato com proveniência.

Conjuntos mínimos para pt-BR: frases curtas (roteamento), **negação** ("não quero cancelar"), ironia, ruído de
digitação e mistura pt/en.

## 9. Segurança (resumo do modelo de ameaças)

| ameaça | controle |
|---|---|
| Texto no estado tenta mudar a decisão | guardrails determinísticos; política de ação independente da confiança; testes adversariais em `tests/security/` |
| Servidor exposto (o upstream escuta em `0.0.0.0` e não exige chave por padrão) | bind em `127.0.0.1` em dev; bearer obrigatório fora de localhost; proxy reverso com TLS |
| Cadeia de suprimentos (pesos, pacotes) | `LAYA_REVISION`, SHA-256 dos artefatos, `uv.lock` com hashes, `pip-audit`, Dependabot, actions fixadas por SHA |
| Vazamento para provedores de LLM | redação de PII antes da chamada; classificação `privacy: local` para dados sensíveis |
| Agência excessiva via MCP/A2A | ferramentas administrativas desligadas por padrão; humano no loop para ações irreversíveis |
| Audit log como alvo | HMAC em vez de hash simples; sem estado bruto por padrão; retenção; acesso por escopo |

## 10. Requisitos não funcionais (metas iniciais, a revisar após medições)

| requisito | meta inicial |
|---|---|
| Overhead do gateway sobre a inferência | p95 < 15 ms |
| Shadow | 0 impacto em latência e disponibilidade do sistema atual |
| Rollback de especialista | < 1 min, sem deploy |
| Reprodutibilidade | todo modelo em `production` rastreável até dataset, código, revisão do `laya` e relatório |
| CI sem pesos | < 10 min |

## 11. O que NÃO entra no núcleo

Regras de domínio fictícias, nomes de modelos LLM fixos no código, cópias do código do upstream, pesos ou dados
reais no git, e qualquer automação que altere um sistema existente sem passar pelos modos 1–3.

## 12. Onde esta proposta diverge do plano original (e por quê)

| plano original | recomendação | motivo |
|---|---|---|
| Criar o projeto a partir do upstream (clone/derivado) | **Depender** do `laya` publicado, fixado por versão e hash | o upstream muda muito rápido (64 PRs numa release); um fork viraria dívida de merge permanente |
| 11 pacotes em `packages/` + `apps/` | 1 pacote com subpacotes e extras; `workspace` só se necessário | mesmo isolamento lógico com uma fração da manutenção |
| Integração em shadow só na Fase 17 | Shadow + auditoria na **Fase 3** | é o shadow que gera os dados reais para calibração e treino |
| LLM Gateway na Fase 7, depois do treino | LLM Gateway **antes** do pipeline de treino | o LLM é o professor que rotula os dados de treino |
| "O Router deverá escolher genérico e especialistas" | `SpecialistSelector` acima do `laya.Router` | o Router do upstream só aceita 3 nomes fixos |
| `/v1/decide` e `/v1/route` como endpoints base | `/api/v1/decide` e `/api/v1/route` | não existem no upstream nem no Jev; `/v1/*` fica reservado à compatibilidade |
| "Laya System-1: 20–100 ms" | 20–100 ms só em GPU; CPU ~0,2–0,5 s | números publicados pelo upstream; afeta capacidade e custo |
| Instalar todos os extras de uma vez | extras por aplicação + lockfile | evita conflitos entre CrewAI, LangChain e LlamaIndex |
| Exemplo 0,95 / 0,75 de thresholds | bandas por DecisionSpec, escolhidas por custo a partir da calibração, só em `answer_confidence` | `confidence` (entropia) não é calibrada; os checkpoints base são super-confiantes |
| GPT-6, Gemini 3.8, DeepSeek V4 etc. no plano | IDs são referências datadas, só em configuração; o núcleo depende de provider + tier + capabilities | nomes mudam; nenhuma regra, interface ou teste central pode depender de `claude-*`, `gpt-*`, `gemini-*`, `deepseek-*` ou equivalentes (guarda automática no CI) |
| Playground do zero | reutilizar primeiro o playground de `examples/server.py` do upstream | já existe (≈3.800 linhas) e mostra distribuição completa e confiança |
| Fine-tuning por notebooks | módulo `training` testável; notebooks apenas o chamam | o treino do upstream vive dentro de uma célula de notebook e usa APIs internas |
| "Labeling" genérico | rotulagem com **distribuições** (LLM professor + ouro humano) | o RLCD treina contra distribuições; é onde o System-2 gera mais valor |
| "noul = 0,97 → BLOCK" | Laya como sinal; bloqueio por regra determinística + política de ação | o preset de guarda é zero-shot e a classificação pode ser manipulada |
| — (não mencionado) | pt-BR como prioridade: `default="multilingual"`, `lang_guess`, calibrar o multilingual, testes de negação | texto curto em pt é roteado para o checkpoint inglês por padrão (verificado) |
| `input_hash` simples | HMAC-SHA256 com chave | hash simples de texto curto é revertível |
| MCP com `laya.train`, `laya.calibrate` etc. | leitura por padrão; mutações desligadas e confirmadas | agência excessiva é o principal risco de ferramentas administrativas |
| Vários bancos vetoriais | começar por pgvector | reaproveita o PostgreSQL de produção |
| Nome `laya-personal` / `laya-decision-platform` | manter aviso de não afiliação; cautela com a marca "Laya" se houver uso comercial | a Apache-2.0 não concede direito de marca (§6) |

## 13. Decisões em aberto (precisam de você)

1. **Primeiro domínio e primeiro sistema a integrar.** Qual sistema existente e qual decisão (ex.: triagem de
   tickets, classificação de e-mail, risco de ação de agente)? Isso define a primeira DecisionSpec, o primeiro
   dataset e o primeiro especialista.
2. **Nome e licença do projeto.** Sugestão: código sob Apache-2.0; nome do pacote `laya_platform` (provisório).
3. **Onde ficam pesos e datasets** (Hugging Face privado, armazenamento de objetos, disco local).
4. **Provedores de LLM disponíveis** (chaves e orçamento) e se dados reais podem sair para provedores externos (LGPD).
5. **GPU para treino** (Kaggle, Colab, máquina local) e **alvo de produção** (VPS, nuvem, on-premise; com ou sem GPU).
