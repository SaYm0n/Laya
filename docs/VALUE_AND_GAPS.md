# Valor, cenários e lacunas — documento vivo

> **Última atualização:** 2026-10-02 — Bloco C (F7 + F8, rascunho do DG-1).
> **Regra:** atualizar a cada bloco implementado (`DEVELOPMENT.md` §0): estado das dores (§2), cenários (§4),
> lacunas (§6), próximos ganhos (§7) e o histórico (§9). Status: ✅ entregue · 🟡 parcial · ⏳ planejado ·
> 🔒 depende do gate DG-1 ou de ambiente com pesos/GPU.

Números do upstream citados aqui são **os publicados pelo upstream e não reproduzidos** neste projeto
(`UPSTREAM_ANALYSIS.md` §3.13). Os benchmarks próprios são a F18.

## 1. O que a plataforma é hoje

Uma camada de decisão sobre o Laya (`laya==0.3.23`):

- **O System-1** é o Laya. Ele responde perguntas fechadas (`choice`, `score`, `noul`) num único passe, localmente,
  com uma confiança calibrável (`answer_confidence`).
- **A plataforma** decide o que fazer com cada resposta: automatizar, escalar para um LLM (**System-2**) ou mandar
  para um humano.
- **Registro:** tudo fica auditado, sem guardar o texto de entrada.
- **Evolução:** a introdução no sistema real é gradual (shadow → advisory → gated) e pode ser desligada na hora
  (kill switch).
- **Aprendizado (Bloco C):** as decisões auditadas e os rótulos (revisão humana, sistema atual, professor LLM)
  viram datasets; um **especialista** treinado pela receita do próprio upstream entra como challenger em shadow,
  passa por portões objetivos e pode ser revertido sem deploy.

## 2. Dores que a plataforma resolve

| dor | como a plataforma resolve | onde | status |
|---|---|---|---|
| Custo e latência de LLM em decisões repetitivas de alto volume | o Laya decide localmente; só o que a política escala vai para um LLM, com orçamento diário | `core`, `llm` (B) | ✅ |
| Confiança "de enfeite": modelos sempre seguros de si | `answer_confidence` (nunca a entropia), calibração de temperatura do upstream, bandas escolhidas por custo de erro | `evaluation` (A), `core/policy` (B) | ✅ código · 🔒 calibração real |
| Medo de automatizar e não ter volta | shadow → advisory → gated com canário determinístico, flags por spec e kill switch, sem deploy | `gateway` (A/B) | ✅ |
| Saída de LLM em texto livre, parsing frágil | `DecisionSpec` tipado; o System-2 responde num schema fechado, validado localmente e projetado como o System-1 | `core/spec`, `llm/decisions` | ✅ |
| Dependência de um único fornecedor de LLM | tiers por configuração; dois SDKs oficiais cobrem Claude, OpenAI, Gemini, DeepSeek, OpenRouter, Ollama e vLLM | `llm` (B) | ✅ |
| Dados sensíveis saindo da empresa (LGPD) | Laya local; provedor externo desligado por padrão; auditoria só com HMAC; recusa de dados reais sem DG-1; **PII mascarada** antes de LLM externo e nos datasets (CPF, CNPJ, e-mail, telefone, cartão) | `gateway/settings`, `llm`, `privacy` (C) | ✅ controles e redação básica · ⏳ nomes, endereços, RG (F9) · 🔒 aprovação do DG-1 |
| Pedido de exclusão de um titular (LGPD) | a entrada nunca é guardada; `db forget` apaga as decisões pelo HMAC da entrada; `db purge` aplica a retenção | `storage`, CLI (C) | ✅ · ⏳ propagação para datasets e especialistas (processo) |
| "Por que o sistema decidiu isso?" | auditoria com trace id, banda, motivos (`reasons`), modelo, **engine que respondeu** (Router ou especialista), System-2 (tier, tokens, custo, PII mascarada) e concordância com o sistema atual | `storage` (A/B/C) | ✅ |
| Fila humana sem critério | só vai para humano o que a política manda (banda, truncamento, idioma, risco, regra); cada item com o motivo; tamanho e idade da fila em métricas | `/api/v1/reviews` (B), métricas (C) | ✅ · ⏳ SLA, atribuição e interface |
| Custo de LLM imprevisível | orçamento diário **somado no banco** (vale para vários processos), circuit breaker, métricas de tokens e custo por tier | `llm/gateway` (B/C) | ✅ |
| Integração arriscada com o sistema legado | `/v1/systemone` do upstream inalterado; `PlatformClient.shadow()` não bloqueia nem lança exceção | `gateway`, `client` (A) | ✅ |
| Falta de dados rotulados do domínio | amostragem ativa pela auditoria; resoluções humanas, sistema atual e professor LLM (votos) viram rótulos; datasets com proveniência (HMAC), splits por grupo/tempo e manifesto | `training/data` (C) | ✅ código · 🔒 DG-1 para dados reais |
| Modelo genérico fraco no domínio (o upstream admite base zero-shot fraca) | especialistas próprios treinados pela receita do upstream, avaliados contra o baseline no mesmo dataset e promovidos com portões | `training`, `registry` (C) | ✅ código · 🔒 GPU + DG-1 para o primeiro especialista |
| Modelo novo entra sem comparação justa | o especialista roda como **challenger** no tráfego real, depois da resposta, e só passa a `candidate` com amostras, falhas e concordância suficientes | `registry`, `gateway` (C) | ✅ |
| Trocar de modelo exige deploy e não tem volta | ponteiros por spec no banco; `promote` exige aprovador nomeado; rollback por CLI ou API, sem deploy | `registry` (C) | ✅ |
| Ataques por texto injetado no estado | o estado vai ao LLM como dado JSON com instrução de ignorar ordens; o Laya decide, não executa | `llm/decisions` | 🟡 · ⏳ guardrails (F9) |

## 3. Onde a plataforma é mais eficiente

**Encaixe forte:**

- **Decisões fechadas, de alto volume e repetitivas**:
  - triagem, classificação, priorização, roteamento, sinalização (sim/não);
  - poucas opções por pergunta (até ~20; acima disso o upstream perde distinção, então use shortlist ou hierarquia).
- **Ambientes com restrição de dados** (financeiro, saúde, setor público, jurídico): o System-1 roda na própria
  infraestrutura e o System-2 externo fica desligado até o DG-1. Quando um LLM é necessário, um professor local
  (Ollama/vLLM) rotula sem dados saírem da empresa.
- **GPU disponível para volume.** O upstream publica ~33–40 ms por pergunta em T4. Em **CPU** a referência é
  ~0,2–0,5 s por chamada, viável para baixo volume ou para shadow. Em shadow com challenger, cada decisão roda dois
  modelos (§6, lacuna 19).
- **Treino:** a receita do upstream roda em CUDA, MPS (Apple) ou CPU; para um especialista, uma GPU T4 (Kaggle ou
  Colab) basta como ponto de partida.
- **pt-BR e en**: checkpoint `multilingual` (48 de 51 idiomas "utilizáveis" segundo o upstream). Textos pt-BR
  curtos podem não ser detectados; o roteamento cai no `default` e a política bloqueia a automação se o idioma
  detectado estiver fora do spec. Um especialista pt-BR elimina essa dependência (não recebe controles de
  roteamento).
- **Quando já existe um sistema decidindo** (regras, pessoas ou um LLM caro): o shadow mede a concordância antes de
  qualquer mudança, e as decisões do sistema atual viram rótulos (`incumbent`) para o primeiro especialista.

**Encaixe fraco** (vai para System-2, RAG ou fica fora):

- geração de texto, respostas abertas e raciocínio longo em várias etapas;
- extração de valores livres (datas, números, nomes): o Laya pontua opções, não extrai;
- documentos muito longos: o `predict_long` decide por janela e a acurácia varia acima de ~4.000 tokens;
- muitas opções numa só pergunta (ex.: dezenas de intenções): exige shortlist ou especialista;
- decisões que dependem de busca, ferramentas ou dados privados em tempo real (preset `router_questions` →
  `needs_tools`).

## 4. Cenários reais

Cada cenário indica as perguntas, o modo inicial, o que automatiza, o que escala e o que medir. Os presets citados
existem no upstream (`laya.presets`) e entram como `questions` de um `DecisionSpec`.

| cenário | perguntas (tipo) | começa em | automatiza (gated) | escala / humano | já suportado |
|---|---|---|---|---|---|
| **Atendimento / SAC** — triagem de chamados (exemplo em `examples/support_triage`, preset `triage_questions`) | equipe (choice), urgência (score), risco de cancelamento (noul) | shadow, contra a triagem atual | roteamento para fila com banda `auto` | `never_auto_if: churn_risk=true` → retenção humana; baixa confiança → LLM | ✅ |
| **E-mail corporativo** — categoria e ameaça (preset `email_questions`; o upstream limpa citações e assinaturas em pt/es/fr) | categoria (choice), phishing (noul) | shadow | encaminhamento por categoria | suspeita de phishing → segurança (`risk: high`) | ✅ (limpeza via `laya.email`) |
| **Roteador de LLM** — qual modelo atende (preset `router_questions`) | dificuldade (score), domínio (choice), precisa de ferramentas (noul), sensível (noul) | advisory | pedidos triviais e fáceis num tier barato | difícil ou sensível → tier forte ou humano | 🟡 tier fixo por spec; seleção automática ⏳ |
| **Guardrail de agentes** — antes de executar um pedido (preset `guard_questions`) | jailbreak, injeção, dado sensível (noul), dano (score) | shadow | liberar pedidos limpos | qualquer sinal → bloqueio e revisão | 🟡 decisão pronta; ação de bloqueio é do chamador (F9) |
| **Moderação de conteúdo** (preset `moderation_questions`) | tóxico, assédio, ameaça, spam (noul) | advisory | ocultar spam com banda `auto` | ameaça/assédio → humano (`never_auto_if`) | ✅ |
| **Financeiro / back-office** — despesas, notas, cobrança duplicada | categoria contábil (choice), duplicidade (noul), prioridade (score) | shadow | classificação contábil | valores altos ou duplicidade → revisão | ✅ (limiares por spec) |
| **Cobrança e retenção** | propensão a pagar (score), risco de saída (noul) | advisory | ordem da fila de contato | ofertas e exceções → humano | ✅ |
| **Saúde (administrativo, não clínico)** — agendamento, encaminhamento de mensagens | tipo de pedido (choice), urgência (score) | shadow | encaminhamento administrativo | qualquer sinal clínico → humano (`risk: high`) | ✅ · 🔒 DG-1 obrigatório |
| **Jurídico** — tipo de documento/petição, prazo | tipo (choice), urgência (score) | shadow | distribuição interna | documentos longos → System-2 ou janela | 🟡 documentos longos |
| **Setor público / ouvidoria** — classificação de manifestações | órgão (choice), tipo (choice), prioridade (score) | shadow | distribuição | denúncias → humano | ✅ · 🔒 DG-1 |
| **E-commerce / logística** — devoluções, ocorrências | motivo (choice), fraude suspeita (noul) | advisory | devoluções simples | fraude → revisão (`never_auto_if`) | ✅ |
| **RH** — triagem de mensagens internas (**nunca** reprovar candidatos automaticamente) | assunto (choice), urgência (score) | advisory | encaminhamento | qualquer decisão sobre pessoas → humano (`risk: high`) | ✅ · ⚠️ viés e LGPD |

**Caminho para um especialista em qualquer cenário** (Bloco C, depois do DG-1):

1. shadow com o Router genérico, comparando com o sistema atual;
2. `dataset candidates` aponta o que mais vale rotular; a fila humana e o sistema atual rotulam;
3. `dataset build` + `split`;
4. `train` numa GPU e `eval --specialist` contra o relatório do Router no mesmo dataset;
5. `specialist shadow` (challenger no tráfego real), `candidate` com evidência e `promote` com aprovador;
6. rollback a qualquer momento.

**O que medir em todo cenário:**

- **cobertura:** parcela das decisões na banda `auto`;
- **erro automatizado:** taxa de erro dentro da banda `auto`;
- **concordância com o sistema atual** (`incumbent`) e, com especialista em shadow, **do challenger**;
- **concordância System-1 × System-2:** quando alta, o LLM pode deixar de ser chamado para aquela pergunta;
- **custo de LLM por decisão;**
- **latência p95;**
- **tamanho e idade da fila humana.**

As métricas do gateway (`/metrics`) e o relatório de avaliação cobrem tudo isso.

## 5. Melhorias na operação

| antes | com a plataforma | status |
|---|---|---|
| Decisão automatizada "tudo ou nada" | canário por spec (fração do tráfego), modo por spec via flag, kill switch global | ✅ |
| Mudar regra exige deploy | flags em tempo de execução; política declarada no spec (versionado) | ✅ flags · ⏳ recarga de spec sem reinício |
| Trocar de modelo exige deploy | ponteiros de especialista no banco, rollback por API/CLI | ✅ |
| Limiares escolhidos "no olho" | `laya-platform bands` escolhe o limiar de menor custo esperado e grava o `calibration_ref` | ✅ |
| Promoção de modelo "porque parece melhor" | portões objetivos: relatório do mesmo spec, sem regressão contra o baseline no mesmo dataset, evidência de shadow, aprovador nomeado | ✅ |
| Sem rastreabilidade de quem decidiu | trace id, engine, banda, motivos, System-2 e resolvedor humano; autor de cada mudança de flag e de cada transição de especialista | ✅ |
| Custo de LLM descoberto na fatura | métricas `laya_platform_llm_*` (tokens, custo, latência, falhas) e orçamento compartilhado no banco | ✅ |
| Não se sabe quando o LLM ainda é necessário | métrica de concordância System-1 × System-2 por pergunta | ✅ |
| Revisão humana sem retorno | resolução registrada vira rótulo `human`, com prioridade no dataset | ✅ · 🔒 DG-1 para uso real |
| Fila humana envelhece sem ninguém ver | `laya_platform_reviews_open` e `laya_platform_reviews_oldest_age_seconds` | ✅ métricas · ⏳ alerta (F14) |
| Retenção e exclusão feitas à mão | `db purge` (retenção) e `db forget` (titular) | ✅ |
| Troca de fornecedor de LLM custosa | troca de tier é configuração | ✅ |
| Comparar com o sistema atual exige planilha | concordância por pergunta em métricas e na auditoria | ✅ |

## 6. Lacunas atuais (análise sobre o código do Bloco C)

| # | lacuna | impacto | onde resolver | prioridade |
|---|---|---|---|---|
| 1 | Sem execução com **pesos reais** nem baseline dos golden tests (Hugging Face bloqueado no ambiente de nuvem) | não há medida real de acurácia/latência dos checkpoints aqui | máquina local ou CI com acesso ao HF (`DEVELOPMENT.md` §8) | 🔴 alta |
| 2 | **DG-1 em rascunho** (`docs/DATA_GOVERNANCE.md`): decisões ☐ e aprovação pendentes | bloqueia dados reais, calibração real e o primeiro especialista | suas decisões + aprovação do responsável e do DPO | 🔴 alta |
| 3 | Redação de PII **básica** (sem nome, endereço, RG, saúde) | dados reais com texto livre ainda exigem cautela | F9 (detectores adicionais ou NER local) | 🟠 média |
| 5 | **SQLite** e um único processo (`uvicorn.run`) | concorrência de escrita limitada | PostgreSQL + vários workers (Bloco D, F16) | 🟠 média |
| 6 | **System-2 síncrono** dentro de `/api/v1/decide` | a latência do LLM soma à resposta | escalonamento assíncrono com callback/webhook ou modo "responde já, completa depois" | 🟠 média |
| 7 | **Sem `/api/v1/decide/batch`** (o upstream tem `predict_batch`) | volume alto paga overhead por requisição | endpoint em lote reaproveitando `predict_batch` | 🟠 média (ganho de eficiência) |
| 8 | **Fila humana sem atribuição, SLA nem expiração**; sem interface (as métricas já existem) | revisões podem envelhecer; ninguém é dono | campos `assignee`/`due_at`; regra de alerta; UI na F15 | 🟠 média |
| 9 | **Sem limite de taxa por chave** em `/api/v1/*` | uma integração defeituosa pode saturar o gateway | limitador simples por chave (token bucket) | 🟠 média |
| 12 | **Avaliação só do System-1** (`eval` usa `DecisionEngine`) | não se mede a qualidade/custo do tier de LLM no mesmo dataset | adaptar o `LLMGateway` como engine de avaliação | 🟡 média |
| 13 | **Sem alertas de deriva** (métricas existem, regras não) | queda de confiança ou concordância (inclusive do challenger) passa despercebida | regras Prometheus de exemplo (F14) | 🟡 média |
| 14 | **Tier fixo por spec** (sem `router_questions`) | custo maior que o necessário quando há vários tiers | seleção automática de tier com o preset do upstream | 🟡 baixa até haver 2+ tiers |
| 15 | **Spec carregado só na inicialização** | mudar política exige reinício | recarga controlada por endpoint admin | 🟡 baixa |
| 16 | **ONNX ainda exige torch** (limitação do upstream 0.3.23) | imagens "CPU leves" não ficam leves | reavaliar a cada versão do upstream | ⚪ externa |
| 17 | **Sem imagem Docker própria** | implantação manual | partir do Dockerfile/compose do upstream (não recriar) | 🟠 média (F16) |
| 18 | **Primeiro especialista real** não treinado (o código está pronto) | o ganho de qualidade no domínio ainda não foi medido | GPU + dados do domínio depois do DG-1 | 🔴 alta (marco M4) |
| 19 | **Challenger em toda decisão**, no mesmo processo (tarefa em background) | em shadow, o custo de inferência dobra | fração de amostragem por spec; challenger em lote | 🟠 média |
| 20 | **Licença do `LocalLLaMA/typed-decisions` não confirmada** (Hugging Face bloqueado aqui) | `train --extra-upstream` não deve ser usado antes | conferir a página do dataset numa máquina com acesso e registrar no DG-1 §8 | 🟠 média (antes do 1º treino) |
| 21 | **Liberação de LLM externo é global**, não por spec | um spec sensível herda a liberação dos outros | `allow_external` por DecisionSpec (DG-1 §9) | 🟡 média |
| 22 | **Ingestão só por JSONL** exportado pelo sistema de origem | cada integração escreve seu exportador | leitores CSV/Parquet/PostgreSQL quando houver fonte concreta | 🟡 baixa |
| 23 | **Retenção de datasets e pesos** só documentada (ficam fora do banco) | arquivos podem ficar além do prazo | inventário a partir dos manifestos + rotina de expurgo | 🟡 baixa |

Resolvidas no Bloco C: 4 (orçamento no banco; o circuit breaker continua por processo, o que é aceitável), 10
(autoria das flags), 11 (concordância System-1 × System-2), a parte de métricas da 8, a primeira versão da 3 e o
código da 18.

## 7. Próximos ganhos priorizados (agilidade, eficiência, qualidade)

Entregues do ciclo anterior: os quatro ganhos rápidos (concordância System-1 × System-2, autoria das flags, métricas
da fila, orçamento no banco) e o pipeline F8.

**Rápidos** (menos de um dia cada):

1. Fração de amostragem do challenger por spec (lacuna 19).
2. `allow_external` por DecisionSpec (lacuna 21).
3. `assignee`/`due_at` na fila de revisão e um exemplo de regra de alerta para a idade da fila (lacunas 8 e 13).
4. Limite de taxa por chave de API (lacuna 9).

**Eficiência** (Bloco D):

5. `/api/v1/decide/batch` sobre `predict_batch` do upstream (lacuna 7).
6. Escalonamento assíncrono (lacuna 6): o sistema de origem recebe a decisão do System-1 na hora e o System-2 por
   callback.
7. Prompt caching do System-2: avaliar com prompts reais. O prefixo mínimo cacheável é de 512–4096 tokens conforme
   o modelo, e o nosso prompt de sistema é curto; só vale com specs grandes.

**Qualidade:**

8. Avaliação do System-2 no mesmo dataset e relatório (lacuna 12): decide quando o LLM compensa o custo e mede o
   professor antes de usá-lo para rotular.
9. Primeiro especialista do domínio (lacuna 18), seguindo o caminho do §4.
10. Calibração por idioma (`lang_temperatures` do upstream) para pt-BR.
11. Regras de deriva (lacuna 13): queda de `answer_confidence`, de concordância com o sistema atual e do challenger.

**Pré-requisitos de dados reais** (bloqueantes):

12. Decidir os itens ☐ do `docs/DATA_GOVERNANCE.md` e aprovar o DG-1 (lacuna 2).
13. Rodar a suíte com pesos e gravar o baseline (lacuna 1) numa máquina com acesso ao Hugging Face.
14. Confirmar a licença do dataset público antes de misturá-lo no treino (lacuna 20).

## 8. Fora de escopo

O que fica de fora ou só entra com caso de uso concreto:

- **A2A e RAG** (F11/F12): só com um cenário que precise. O RAG cobre o "encaixe fraco" de dados atualizados; o
  A2A só faz sentido com vários agentes próprios.
- **Integrações LangChain/LangGraph/LlamaIndex/CrewAI** (F13): já existem no upstream (`laya.integrations`); o
  trabalho é documentar e exemplificar.
- **Dashboard** (F15): até lá, a operação usa `/metrics` (Prometheus/Grafana) e os endpoints admin.
- **Loop de treino próprio, DDP e notebooks próprios:** a receita do upstream já cobre CUDA/MPS/CPU; só
  reavaliar se ela deixar de atender.

## 9. Histórico

| data | marco | o que mudou neste documento |
|---|---|---|
| 2026-10-02 | Blocos A + B (PR #3) | criação: dores, ambientes, cenários, operação, lacunas (18), próximos ganhos |
| 2026-10-02 | Bloco C (F7 + F8, rascunho do DG-1) | dores de LGPD, dados rotulados, especialistas e troca de modelo atualizadas; caminho para especialista por cenário; lacunas 4, 10 e 11 resolvidas, 2, 3, 8 e 18 revistas, 19–23 novas; próximos ganhos refeitos |
