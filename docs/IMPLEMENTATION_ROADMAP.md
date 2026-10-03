# Roadmap de implementação

> **Público:** quem planeja e acompanha o projeto · **Status:** vigente · **Atualizado:** 2026-10-03
>
> Criado na Fase 0 e atualizado nas Fases 1 e 2 e nos Blocos A, B e C. Mantém as 21 fases (0–20) do plano original, mas **reordenadas** para que cada fase entregue algo usável
> e produza os insumos da seguinte. A seção 3 mapeia a numeração original para a nova. Desde a F2 as fases são
> entregues em **4 blocos** (modo LEAN, `DEVELOPMENT.md` §0), sem criar etapas novas.

## 1. Marcos

| marco | ao final da fase | o que você consegue fazer |
|---|---|---|
| **M1 — Shadow pronto** | F3 + DG-1 | ligar a plataforma a um sistema existente em modo shadow, sem risco; com dados reais somente após o gate DG-1 |
| **M2 — Advisory calibrado em pt-BR** | F4 | mostrar sugestões calibradas a humanos, com thresholds justificados por relatório |
| **M3 — System-1/System-2** | F6 | automatizar a banda segura e escalar o resto para LLM ou humano |
| **M4 — Primeiro especialista** | F8 | treinar, calibrar, avaliar e promover um especialista próprio a partir dos dados coletados |
| **M5 — Ecossistema de agentes** | F13 | guardrails, MCP admin, RAG, A2A e frameworks integrados |
| **M6 — Release candidate** | F20 | operação em produção, auditada e com rollback |

### 1.1 Blocos de entrega (modo LEAN)

| bloco | fases | situação |
|---|---|---|
| **A** | F3 + F4 — gateway, auditoria, shadow/advisory, avaliação e calibração | ✅ integrado (PR #3, dados sintéticos) |
| **B** | F5 + F6 — LLM Gateway, System-1/System-2 e política de confiança | ✅ integrado (PR #3, dados sintéticos) |
| **C** | F7 + F8 — Specialist Registry, dados e treino, rascunho do DG-1 | ✅ integrado (PR #4; código e testes com stubs); treino real 🔒 GPU e DG-1 |
| **D** | F9–F20 selecionadas — guardrails, MCP, RAG, observabilidade, Docker/implantação, benchmarks, segurança, RC | por necessidade |

Cada bloco segue o fluxo implementar → testes focados → validação única → Draft PR → CI → revisão → merge, e
reaproveita o upstream e bibliotecas maduras antes de escrever código próprio.

### 1.2 Visão única

O objetivo de cada fase, o que já existe para reaproveitar (upstream `laya==0.3.23`, bibliotecas maduras ou blocos
anteriores) e o que sobra como código próprio. Nenhuma fase cria o que o upstream já oferece.

| fase | objetivo | reaproveita | código próprio (gap real) | bloco |
|---|---|---|---|---|
| F0 Auditoria | entender o upstream: arquitetura, limites, contratos, licença, riscos | — | — | ✅ |
| F1 Fundação | projeto, dependências, licença, CI, testes, segurança, `main` protegida | uv, ruff, mypy, import-linter, gitleaks, pip-audit | scripts de verificação | ✅ |
| F2 Decision Core | `DecisionEngine`, adaptadores, `DecisionSpec`, contratos do upstream | `laya.Router`, `laya.structured`, `laya.confidence`, `laya.serve` | protocolo, adaptadores finos, envelope do spec | ✅ |
| F3 Gateway + Shadow | entrada operacional; decidir em paralelo sem afetar o sistema real; registrar tudo | app HTTP do upstream montado sem alteração; SQLAlchemy/Alembic; Prometheus | `/api/v1/*`, auditoria com HMAC, modos, flags | A |
| F4 Avaliação + Calibração | medir (P/R/F1, ECE, Brier, abstenção, latência) e calibrar antes de automatizar | `laya.evals`, `fit_temperatures`/`records_from_labeled` | métricas extras, relatório identificado, bandas por custo | A |
| F5 LLM Gateway | uma abstração para GPT, Claude, Gemini, DeepSeek, OpenRouter, Ollama, vLLM | SDKs oficiais `anthropic` e `openai` (este com `base_url` para todos os compatíveis), com o retry/backoff deles | tiers, privacidade, orçamento, circuit breaker, validação da saída | B |
| F6 System-1/System-2 | Laya decide quando confiante; LLM quando exige raciocínio; humano quando risco/incerteza | gate de abstenção, `answer_confidence`, `usage`/`routing` do upstream | `DecisionPolicy`, modo `gated` com canário, fila de revisão humana | B |
| F7 Specialist Registry | vários especialistas por domínio/versão/idioma, com promoção, rollback e histórico | `laya.Router.attach`, `laya.revisions` | registro, estados de promoção, ponteiro de rollback | C |
| F8 Dados + Treino | dados e feedback → datasets → treino/calibração → comparar → promover só o melhor | notebook RLCD e `laya.calibrate` do upstream; fila de revisão (F6) como fonte de rótulos | pipeline de dados, splits, treino testável | C (**marco central**) |
| F9 Guardrails | proteger entrada, decisão, ação e saída (PII/LGPD, limites, regras, revisão humana) | limites do `laya.serve` (já aplicados); `never_auto_if`/`risk` (F6) | detectores de PII, catálogo de ações | D |
| F10 MCP | integrar a plataforma a agentes | servidor MCP do upstream (8 ferramentas) | só ferramentas administrativas | D |
| F11 A2A | comunicação estruturada entre agentes, com permissões | — | só com caso de uso concreto | D (opcional) |
| F12 RAG | conhecimento externo sem retreinar | — | só com caso de uso concreto | D (opcional) |
| F13 Ecossistema | LangChain, LangGraph, CrewAI, LlamaIndex onde trouxerem benefício | `laya.integrations` (LangChain/LangGraph, LlamaIndex, CrewAI) | documentação e exemplos | D |
| F14 Observabilidade | métricas, logs, tracing, custo, versões | Prometheus e auditoria (A), custo/tokens de LLM (B) | tracing/logs com bibliotecas prontas | D |
| F15 Dashboard / Playground | testar decisões, modelos, confiança, métricas | — | interface | D |
| F16 Deploy / Docker | CPU/GPU, Docker, configuração por ambiente | — | imagens, compose com PostgreSQL | D |
| F17 Produção real | shadow → advisory → gated → produção, com flags, kill switch e rollback | modos, flags e kill switch (A), `gated` (B) | processo de rollout; modo `production` | D |
| F18 Benchmarks | comparar versões, especialistas e checkpoints | `laya.evals` + `laya-platform eval` | comparação de relatórios | D |
| F19 Segurança final | dependências, autenticação, autorização, segredos, isolamento, LGPD | pip-audit, gitleaks, licenças (F1) | auditoria final | D |
| F20 Release Candidate | revisão final e primeira versão candidata | — | — | D |

Valor por cenário, lacunas atuais e próximos ganhos priorizados: `VALUE_AND_GAPS.md` (atualizado a cada bloco).

Ordem antes da produção: **F9 (guardrails), F16 (deploy) e F19 (segurança) antes de qualquer `gated` ou produção
com dados reais** (F17). F13, F17 e F18 são quase só configuração e processo: itens de checklist do Bloco D.

## 2. Fases

Regra de saída para **todas** as fases: testes verdes (sem reduzir testes), documentação atualizada, riscos e
pendências listados, nenhuma funcionalidade do upstream removida.

### F0 — Auditoria do upstream e arquitetura ✅
Entregue: `UPSTREAM_ANALYSIS.md`, `ARCHITECTURE_PROPOSAL.md`, `IMPLEMENTATION_ROADMAP.md`,
`COMPATIBILITY_MATRIX.md`, `LICENSE_AND_ATTRIBUTION.md`.

### F1 — Bootstrap, licença, organização e CI ✅
Entregue (detalhes em `DEVELOPMENT.md`):
- `pyproject.toml` (uv, `uv_build`), pacote `src/laya_platform` com a CLI `laya-platform --version`;
  `laya==0.3.23` fixado e `uv.lock` com hashes sha256 (resolução universal, Python 3.11 e 3.12). O hash do wheel e
  do sdist do `laya` no lock é verificado contra o registrado na auditoria.
- Núcleo mínimo: única dependência `laya`; grupos `dev`, `audit` e `package`; nenhum extra ainda. Classificador
  `Private :: Do Not Upload` impede publicação acidental.
- ruff, mypy estrito, import-linter (núcleo sem runtimes de ML/frameworks), pre-commit com hooks fixados por SHA
  (inclui gitleaks e `no-commit-to-branch` para a `main`).
- Testes em `tests/unit/` e `tests/compatibility/`; marcadores `network`, `weights`, `gpu`, `llm` desligados por
  padrão; execução padrão offline com bloqueio de rede verificado. Guardas automáticas: internos do Laya só via
  adaptador, nenhum ID concreto de modelo LLM no código, nada de segredos/pesos/dados no git.
- `LICENSE` (Apache-2.0), `NOTICE` com aviso de independência, `.gitignore`, `.env.example` sem segredos,
  `.gitattributes` (LF em Windows e Linux).
- GitHub Actions com actions fixadas por SHA: `CI` (lock, lint, tipos, testes em Ubuntu 3.11/3.12 e Windows 3.12,
  build + verificação do pacote + `twine check`, job agregador `required`), `Security` (gitleaks, pip-audit,
  política de licenças), `Upstream watch` (semanal, só detecta e abre issue), `Full install` (lock completo, semanal
  e quando as dependências mudam); Dependabot para as actions.
- Configuração recomendada de `main` protegida documentada. Aplicada pelo mantenedor depois da F1: ruleset
  "Proteção da main" ativo (ver `DEVELOPMENT.md` §10).

### F2 — Decision Core e testes de compatibilidade ✅
Entregue (detalhes em `DEVELOPMENT.md` §5 e `COMPATIBILITY_MATRIX.md` §11):
- `laya_platform.core`: protocolo `DecisionEngine` (`predict`, `predict_batch`, `route`, semântica do
  `laya.Router`) com tipos `TypedDict` que descrevem o payload do upstream sem reconstruí-lo.
- Adaptadores: `UpstreamRouterEngine` (fino sobre `laya.Router`, padrões do upstream preservados),
  `AgentEngine` (um checkpoint, `laya.load`), `OnnxEngine` (`ONNXAgent`, runtime opcional importado só no uso),
  `RemoteEngine` (cliente `/v1/systemone`, transporte injetável, sem servidor) e `FakeEngine` (determinístico, sem
  pesos). Operações e controles sem sentido para um adaptador geram erro explícito.
- `DecisionSpec` (Pydantic, YAML/JSON): `id`, `version`, `schema` **ou** `questions`, `languages`,
  `engine.checkpoint`; schema → perguntas pelo próprio `laya.structured`; aceita exatamente os schemas e as
  perguntas que o upstream aceita (sem regras próprias sobre o conteúdo); chaves de fases futuras recusadas com o
  motivo; YAML 1.2 e chaves duplicadas recusadas.
- `core/upstream_compat.py` como único ponto de acesso a internos do Laya (registro dos internos auditados e
  acessores preguiçosos); a guarda de arquitetura também detecta imports dinâmicos.
- `tests/compatibility/`: contratos sem pesos em todo PR (API Python, roteamento, schema, abstenção, semântica de
  confiança, wire HTTP do app do upstream, ferramentas MCP, internos de treino), testes `torch` no Full install e
  testes com pesos preparados (`--run-weights`), incluindo golden tests com tolerâncias justificadas.
- Novas dependências de runtime: `pydantic` e `pyyaml`. `fastapi`, `httpx` e `mcp` entram só no grupo `dev`
  (testes de contrato).
- **Saída alcançada:** o contrato do upstream está congelado; `tests/unit/test_contract_drift.py` demonstra que
  mudanças incompatíveis numa cópia do `laya` derrubam a suíte. Os testes com pesos não foram executados no
  ambiente de nuvem (Hugging Face bloqueado): precisam de uma execução local para gravar o baseline dos golden
  tests.

### F3 — Gateway, auditoria e modo shadow  *(M1)* — Bloco A ✅
- FastAPI montando `laya.serve.create_app(router)` para `/v1/systemone*`, com **contrato wire/API compatível** com o
  upstream (schema, campos, valores, status codes e semântica validados sobre o JSON interpretado; sem igualdade binária).
- `/health`, `/ready`, `/metrics`, `/api/v1/decide`, `/api/v1/route` (rota ainda só System-1), autenticação por chave
  com escopos, limites, micro-batching.
- Hooks de auditoria (HMAC, redação), persistência SQLite/PostgreSQL com Alembic.
- Modos 0–2 por DecisionSpec, *feature flags*, kill switch; cliente mínimo para o sistema existente com chamada
  shadow assíncrona (outbox).
- `docker-compose` de desenvolvimento (gateway + PostgreSQL).
- Implementada e testada **somente com dados sintéticos**; o gateway recusa iniciar com dados reais sem referência a
  uma aprovação DG-1 válida.
- **Saída:** um sistema real pode chamar a plataforma em shadow sem nenhum efeito colateral; os registros de
  auditoria são suficientes para reconstruir a comparação.
- **Entregue no Bloco A** (`DEVELOPMENT.md` §5.4): app do upstream montado sem alteração; `/ready`, `/metrics`,
  `/api/v1/decide`, `/api/v1/route`; chaves por SHA-256 com escopos; limites do `laya.serve`; auditoria com HMAC
  da entrada (nunca o texto) em SQLite via SQLAlchemy 2 + Alembic (pronto para PostgreSQL); modos offline/shadow/
  advisory, flags e kill switch; recusa de dados reais sem DG-1; cliente com `shadow()` não bloqueante.
  **Ajustes de escopo:** micro-batching próprio não foi feito (o `/v1/systemone/batch` do upstream já está
  montado); o *outbox* virou uma fila limitada em background no cliente; `docker-compose` com PostgreSQL ficou para
  o Bloco D (F16).

### Gate DG-1 — Data Governance Gate  *(bloqueante antes de qualquer dado real)*
Não é uma fase de implementação extensa; é um portão. Antes de shadow com dados reais, avaliação sobre exportações
reais (F4), criação de datasets (F8) ou envio de dados a LLMs (F5/F6/F8), precisam estar definidos e aprovados:
classificação de dados; PII/LGPD; retenção; criptografia; controle de acesso; audit logs; datasets de treino; envio
ou proibição de envio a LLMs externos (proibido por padrão); sanitização/redação; exclusão/expurgo.
Detalhes em `ARCHITECTURE_PROPOSAL.md` §6.15. Evidência: `docs/DATA_GOVERNANCE.md` aprovado, configuração
versionada e testes de redação e expurgo.

### F4 — Avaliação e calibração  *(M2)* — Bloco A ✅ (com dados sintéticos)
- Métricas além do `laya.evals`: precisão/recall/F1, matriz de confusão, Brier, NLL, ECE + diagrama de
  confiabilidade, FPR/FNR, cobertura × risco, abstenção, p50/p95/p99, custo, tudo por fatia.
- Relatórios JSON/Markdown com identidade (dataset sha256, revisão, versão do pacote).
- Ajuste de temperaturas (`fit_temperatures`) por decisão e idioma; seleção de bandas por custo de erro.
- Conjunto de avaliação pt-BR inicial (curto, negação, ruído, mistura pt/en) — **precisa de dados do seu domínio**;
  com dados reais, só após o gate DG-1.
- **Saída:** cada DecisionSpec tem uma política com `calibration_ref`; modo advisory habilitado.
- **Entregue no Bloco A:** CLI `eval` (relatório JSON/Markdown com id derivado da identidade e dos resultados,
  métricas por pergunta, classe e fatia), `bands` (limiar por custo → bloco `policy`) e `calibrate` (ajuste do
  próprio upstream); `policy` e `mode` na DecisionSpec; spec genérico e dataset **sintético** pt-BR em `examples/`.
  **Pendente:** calibração e bandas com dados reais do domínio, só após o gate DG-1.

### F5 — LLM Gateway — Bloco B ✅
- `LLMProvider` + Anthropic, OpenAI, Gemini, DeepSeek, OpenRouter, Ollama/vLLM, genérico OpenAI-compatível.
- Configuração por tier, custo, latência e privacidade; retries, circuit breaker, orçamento, métricas de tokens/custo.
- Saída estruturada com o mesmo schema da DecisionSpec.
- Testes com provedores falsos; testes reais só com marcador `llm` e chave presente.
- **Saída:** trocar o modelo de um tier é só configuração.
- **Entregue no Bloco B** (`DEVELOPMENT.md` §5.5): dois adaptadores sobre os SDKs oficiais — `anthropic` e `openai`
  (este com `base_url` para OpenAI, Gemini, DeepSeek, OpenRouter, Ollama e vLLM); tiers por configuração; provedor
  externo recusado sem `allow_external`; orçamento diário e circuit breaker por provedor; retries dos próprios SDKs;
  saída validada localmente e projetada como a do System-1; tokens, custo e latência em métricas e na auditoria.
  **Ajuste de escopo:** sem SDK do Gemini (o endpoint compatível com OpenAI basta) e sem LiteLLM (dependência pesada,
  versões 1.82.7/1.82.8 comprometidas no PyPI em 24/03/2026).

### F6 — System-1/System-2 e política de confiança  *(M3)* — Bloco B ✅
- `DecisionPolicy` (bandas, bloqueios por truncamento/colapso/idioma, risco) e `EscalationRouter`
  (preset `router_questions` → tier) + fila de revisão humana.
- `/api/v1/route` completo; modo 3 (gated) com canário por DecisionSpec.
- **Saída:** automação apenas na banda calibrada; todo o resto vai para LLM ou humano e fica registrado.
- **Entregue no Bloco B:** `DecisionPolicy` (bandas sobre `answer_confidence`; bloqueios por abstenção, truncamento,
  opções colapsadas, idioma, `risk: high` e `never_auto_if`); modo `gated` com canário determinístico por spec;
  escalonamento para o tier do spec ou para a fila de revisão humana (sem o texto de entrada; a resolução vira rótulo
  para a F8); `/api/v1/route` completo. **Ajuste de escopo:** a escolha automática do tier pelo preset
  `router_questions` do upstream ficou para quando houver mais de um tier em uso; hoje o tier é fixo por spec.

### F7 — Specialist Registry — Bloco C ✅
- Manifestos, armazenamento de artefatos com sha256, máquina de estados de promoção com portões objetivos,
  `SpecialistSelector`, rollback por ponteiro.
- **Saída:** um checkpoint externo pode ser registrado, colocado em shadow e revertido sem deploy.
- **Entregue no Bloco C** (`DEVELOPMENT.md` §5.6): manifesto do especialista (fonte, revisão e sha256 por arquivo
  passados ao `laya.load` do upstream, que verifica os pesos); estados `experimental → shadow → candidate →
  production → deprecated` no banco, com eventos e autor; portões objetivos (relatório de avaliação deste spec e
  deste especialista, mínimos de acurácia/ECE, sem regressão contra o baseline no mesmo dataset; amostras, falhas e
  concordância do shadow); ponteiros `production`/`shadow` por spec; rollback sem deploy (CLI e
  `POST /api/v1/specialists/rollback`). O gateway serve o especialista de produção e roda o de shadow como
  challenger **depois** da resposta, registrando a concordância; falha ao carregar nunca bloqueia (volta ao Router
  e aparece em `/ready`). **Ajuste de escopo:** o armazenamento de artefatos é o do próprio upstream (Hub privado
  ou diretório com sha256 no manifesto); nada de storage próprio.

### F8 — Pipeline de dados e treino  *(M4)*
- Pré-requisito: gate DG-1 aprovado (dados reais e envio a LLM professor).
- Ingestão CSV/XLSX/JSON/JSONL/Parquet/PostgreSQL; validação de schema; sanitização de PII; deduplicação.
- Amostragem ativa a partir da auditoria; rotulagem por LLM professor (distribuições) + revisão humana (ouro);
  medição de qualidade do professor.
- Splits train/validation/calibration/test por grupo e por tempo; datasets versionados.
- Loop RLCD extraído do notebook do upstream (1 GPU e DDP), com testes em modelo minúsculo; notebooks finos para
  Colab, Kaggle e local.
- Calibração → avaliação held-out → registro como `experimental`.
- **Saída:** um especialista treinado a partir dos dados de shadow, comparado com o genérico nas mesmas métricas.
- **Entregue no Bloco C** (código; o treino real 🔒 GPU e DG-1): `dataset candidates` (amostragem ativa pela
  auditoria: incertos, discordantes, escalados), `dataset build` (junta a exportação do sistema de origem aos
  rótulos da plataforma pelo HMAC, deduplica, mascara PII, marca `label_source`), `dataset split` (por grupo ou por
  tempo, com `manifest.json` e sha256), `dataset label` (professor LLM com votos e distribuição), `train` (a
  **receita do próprio upstream**, fixada por commit e sha256 e importada sem cópia; nós só montamos os itens e o
  manifesto) e `eval --specialist`. **Ajustes de escopo:** a ingestão é JSONL exportado pelo sistema de origem
  (CSV/XLSX/Parquet/PostgreSQL ficam para quando houver fonte concreta); sem DDP nem notebooks próprios, porque a
  receita do upstream já roda em CUDA/MPS/CPU; o dataset público `LocalLLaMA/typed-decisions` pode ser misturado
  (`train --extra-upstream`) depois de confirmada a licença.

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
| Gate DG-1 (governança de dados) aprovado | qualquer uso de dados reais: shadow real, F4 com dados reais, F8, envio a LLM | a definir antes do fim da F3 |
| Dados reais rotulados ou rotuláveis | F4, F8 | depende do domínio |
| Chaves de LLM e orçamento | F5 (testes reais), F8 (rotulagem) | decisão sua |
| GPU para treino | F8 | Kaggle 2×T4 / Colab / local |
| Política LGPD para envio de dados a LLMs externos | F8 | decisão sua |

## 5. Próximo passo

Os Blocos A, B e C estão integrados. Decidir os itens ☐ de `docs/DATA_GOVERNANCE.md` e aprovar o DG-1: só então entram dados reais, o primeiro dataset do domínio e o primeiro treino numa GPU
(`DEVELOPMENT.md` §5.6). Rodar uma vez a suíte com pesos numa máquina com acesso ao Hugging Face e gravar o
baseline dos golden tests (`DEVELOPMENT.md` §8). O próximo bloco é o **D**, escolhido pelas lacunas de
`docs/VALUE_AND_GAPS.md` §6. O produto se chama Mars; o pacote `laya_platform` mantém o nome até uma troca própria.

Melhoria registrada para o procedimento de atualização do upstream: um portão que detecte mudança no corpo/AST de
`Agent._check_question` e obrigue a revisar o espelho `validate_question` da `DecisionSpec`.
