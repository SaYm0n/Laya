# Roadmap de implementação

> Fase 0. Mantém as 21 fases (0–20) do plano original, mas **reordenadas** para que cada fase entregue algo usável
> e produza os insumos da seguinte. A seção 3 mapeia a numeração original para a nova.

## 1. Marcos

| marco | ao final da fase | o que você consegue fazer |
|---|---|---|
| **M1 — Shadow pronto** | F3 | ligar a plataforma a um sistema existente em modo shadow, sem risco, coletando dados reais |
| **M2 — Advisory calibrado em pt-BR** | F4 | mostrar sugestões calibradas a humanos, com thresholds justificados por relatório |
| **M3 — System-1/System-2** | F6 | automatizar a banda segura e escalar o resto para LLM ou humano |
| **M4 — Primeiro especialista** | F8 | treinar, calibrar, avaliar e promover um especialista próprio a partir dos dados coletados |
| **M5 — Ecossistema de agentes** | F13 | guardrails, MCP admin, RAG, A2A e frameworks integrados |
| **M6 — Release candidate** | F20 | operação em produção, auditada e com rollback |

## 2. Fases

Regra de saída para **todas** as fases: testes verdes (sem reduzir testes), documentação atualizada, riscos e
pendências listados, nenhuma funcionalidade do upstream removida.

### F0 — Auditoria do upstream e arquitetura ✅
Entregue: `UPSTREAM_ANALYSIS.md`, `ARCHITECTURE_PROPOSAL.md`, `IMPLEMENTATION_ROADMAP.md`,
`COMPATIBILITY_MATRIX.md`, `LICENSE_AND_ATTRIBUTION.md`.

### F1 — Bootstrap, licença, organização e CI
- `pyproject.toml` (uv), pacote `src/laya_platform`, `laya==0.3.23` fixado com hash no `uv.lock`; extras por área.
- Ferramentas: ruff, mypy (estrito nos módulos novos), pytest com marcadores `weights`, `gpu`, `network`, `llm`;
  `import-linter` com as fronteiras entre subpacotes; pre-commit.
- `LICENSE`, `NOTICE`, aviso de não afiliação no README, `.gitignore` (inclui `.env`, pesos, datasets), `.env.example`.
- CI (GitHub Actions, actions fixadas por SHA): lint, tipos, testes sem pesos em 3.11 e 3.12 (Linux) + 3.12 (Windows),
  gitleaks, pip-audit, verificação de licenças das dependências.
- Workflow semanal que **detecta** nova versão do `laya` e abre uma issue com o changelog — nunca atualiza sozinho.
- **Saída:** CI verde num esqueleto vazio; `laya_platform --version` funciona.

### F2 — Decision Core e testes de compatibilidade
- `DecisionEngine` + adaptadores `UpstreamRouterEngine`, `AgentEngine`, `OnnxEngine`, `RemoteEngine`, `FakeEngine`.
- `DecisionSpec` (YAML/JSON validado por Pydantic) e conversão schema → perguntas via `laya.structured`.
- `upstream_compat.py` como único ponto de contato com APIs internas.
- `tests/compatibility/` conforme `COMPATIBILITY_MATRIX.md` §11 (sem pesos em todo PR; com pesos por marcador).
- **Saída:** contrato do upstream congelado em testes; trocar a versão do `laya` quebra o CI se algo mudar.

### F3 — Gateway, auditoria e modo shadow  *(M1)*
- FastAPI montando `laya.serve.create_app(router)` para `/v1/systemone*` (idêntico ao upstream).
- `/health`, `/ready`, `/metrics`, `/api/v1/decide`, `/api/v1/route` (rota ainda só System-1), autenticação por chave
  com escopos, limites, micro-batching.
- Hooks de auditoria (HMAC, redação), persistência SQLite/PostgreSQL com Alembic.
- Modos 0–2 por DecisionSpec, *feature flags*, kill switch; cliente mínimo para o sistema existente com chamada
  shadow assíncrona (outbox).
- `docker-compose` de desenvolvimento (gateway + PostgreSQL).
- **Saída:** um sistema real pode chamar a plataforma em shadow sem nenhum efeito colateral; os registros de
  auditoria são suficientes para reconstruir a comparação.

### F4 — Avaliação e calibração  *(M2)*
- Métricas além do `laya.evals`: precisão/recall/F1, matriz de confusão, Brier, NLL, ECE + diagrama de
  confiabilidade, FPR/FNR, cobertura × risco, abstenção, p50/p95/p99, custo, tudo por fatia.
- Relatórios JSON/Markdown com identidade (dataset sha256, revisão, versão do pacote).
- Ajuste de temperaturas (`fit_temperatures`) por decisão e idioma; seleção de bandas por custo de erro.
- Conjunto de avaliação pt-BR inicial (curto, negação, ruído, mistura pt/en) — **precisa de dados do seu domínio**.
- **Saída:** cada DecisionSpec tem uma política com `calibration_ref`; modo advisory habilitado.

### F5 — LLM Gateway
- `LLMProvider` + Anthropic, OpenAI, Gemini, DeepSeek, OpenRouter, Ollama/vLLM, genérico OpenAI-compatível.
- Configuração por tier, custo, latência e privacidade; retries, circuit breaker, orçamento, métricas de tokens/custo.
- Saída estruturada com o mesmo schema da DecisionSpec.
- Testes com provedores falsos; testes reais só com marcador `llm` e chave presente.
- **Saída:** trocar o modelo de um tier é só configuração.

### F6 — System-1/System-2 e política de confiança  *(M3)*
- `DecisionPolicy` (bandas, bloqueios por truncamento/colapso/idioma, risco) e `EscalationRouter`
  (preset `router_questions` → tier) + fila de revisão humana.
- `/api/v1/route` completo; modo 3 (gated) com canário por DecisionSpec.
- **Saída:** automação apenas na banda calibrada; todo o resto vai para LLM ou humano e fica registrado.

### F7 — Specialist Registry
- Manifestos, armazenamento de artefatos com sha256, máquina de estados de promoção com portões objetivos,
  `SpecialistSelector`, rollback por ponteiro.
- **Saída:** um checkpoint externo pode ser registrado, colocado em shadow e revertido sem deploy.

### F8 — Pipeline de dados e treino  *(M4)*
- Ingestão CSV/XLSX/JSON/JSONL/Parquet/PostgreSQL; validação de schema; sanitização de PII; deduplicação.
- Amostragem ativa a partir da auditoria; rotulagem por LLM professor (distribuições) + revisão humana (ouro);
  medição de qualidade do professor.
- Splits train/validation/calibration/test por grupo e por tempo; datasets versionados.
- Loop RLCD extraído do notebook do upstream (1 GPU e DDP), com testes em modelo minúsculo; notebooks finos para
  Colab, Kaggle e local.
- Calibração → avaliação held-out → registro como `experimental`.
- **Saída:** um especialista treinado a partir dos dados de shadow, comparado com o genérico nas mesmas métricas.

### F9 — Guardrails
- Input/Decision/Action/Output conforme `ARCHITECTURE_PROPOSAL.md` §6.7; detectores de PII brasileira; catálogo de
  ações com risco e reversibilidade; `tests/security/` com casos adversariais (injeção dentro do estado).

### F10 — MCP
- Servidor upstream reutilizado; servidor administrativo `platform_*` somente leitura; ferramentas mutáveis
  desligadas por padrão, com confirmação e auditoria.

### F11 — A2A
- Agent cards, envelope de tarefa, `AgentDispatcher` com política e fallback.

### F12 — RAG
- `KnowledgeProvider`, `ContextAssembler` ciente do orçamento de tokens, provedor pgvector; demais por demanda.

### F13 — Frameworks  *(M5)*
- Reutilizar integrações do upstream; nós de gateway para LangGraph (`EscalationNode`, `HumanReviewNode`,
  `PolicyGateNode`), CrewAI e LlamaIndex.

### F14 — Observabilidade completa
- Traces OpenTelemetry ponta a ponta, dashboards Grafana, alertas (taxa de abstenção, deriva de confiança,
  custo de LLM, fallbacks OOM).

### F15 — Dashboard e playground
- Playground: começar pelo `examples/server.py` do upstream; dashboard administrativo com modelos, especialistas,
  versões, métricas completas por fatia, comparação de modelos, rotas, escaladas e custos.

### F16 — Docker e implantação
- Imagens CPU, CUDA e ONNX-CPU; compose de produção; guia Windows (dev) e Linux (prod); segredos por arquivo.

### F17 — Integração em produção
- Do shadow ao gated e ao production numa fatia real, com critérios de promoção e rollback ensaiado.

### F18 — Benchmarks
- Laya genérico × especialista × LLM (por tier) × Jev (se houver acesso), no mesmo dataset, com custo e latência.

### F19 — Auditoria de segurança
- Revisão do modelo de ameaças, testes adversariais, dependências, configuração de produção.

### F20 — Release candidate  *(M6)*

## 3. Mapeamento com o plano original

| plano original | nova fase | mudança |
|---|---|---|
| F0 Auditoria | F0 | — |
| F1 Bootstrap/licença/CI | F1 | + detecção de nova versão do upstream, licenças das dependências |
| F2 Decision Core + compatibilidade | F2 | — |
| F3 API Jev-compatível | F3 | API **montada do upstream** + auditoria + shadow juntos |
| F4 Avaliação + calibração | F4 | + conjunto pt-BR, seleção de threshold por custo |
| F5 Specialist Registry | F7 | depois do gateway de LLM e da política |
| F6 Pipeline de treino | F8 | depois do LLM (professor) e do registry |
| F7 LLM Gateway | F5 | **antecipado** |
| F8 Router System-1/System-2 | F6 | antecipado |
| F9 Guardrails | F9 | versão mínima (limites, segredos, PII) já entra na F3 |
| F10 MCP | F10 | — |
| F11 A2A | F11 | — |
| F12 RAG | F12 | — |
| F13 Frameworks | F13 | reutiliza integrações do upstream |
| F14 Observabilidade/auditoria | F14 | auditoria e métricas básicas **antecipadas para a F3** |
| F15 Dashboard/playground | F15 | reutiliza o playground do upstream |
| F16 Docker | F16 | compose de desenvolvimento já na F3 |
| F17 Integração shadow | F3 + F17 | shadow **antecipado para a F3**; a F17 vira a ida a produção |
| F18 Benchmark | F18 | — |
| F19 Segurança | F19 | — |
| F20 Release candidate | F20 | — |

## 4. Bloqueios e dependências externas

| item | bloqueia | situação |
|---|---|---|
| Acesso ao Hugging Face neste ambiente de nuvem | testes com pesos e qualquer inferência real aqui | **bloqueado** (proxy retorna 403 para `huggingface.co`); liberar o domínio nas configurações de rede do ambiente ou rodar testes com pesos localmente |
| `download.pytorch.org` | wheels de torch só-CPU neste ambiente | bloqueado; o torch do PyPI funciona, mas é bem maior |
| Domínio e sistema da primeira integração | F3 (cliente), F4 (dataset pt-BR), F8 | decisão sua |
| Dados reais rotulados ou rotuláveis | F4, F8 | depende do domínio |
| Chaves de LLM e orçamento | F5 (testes reais), F8 (rotulagem) | decisão sua |
| GPU para treino | F8 | Kaggle 2×T4 / Colab / local |
| Política LGPD para envio de dados a LLMs externos | F8 | decisão sua |

## 5. Próximo passo

Executar a **F1** (bootstrap). Ela não depende de nenhuma das decisões em aberto, exceto nome e licença — que podem
ficar provisórios (`laya_platform`, Apache-2.0) e ser trocados antes da primeira release sem custo.
