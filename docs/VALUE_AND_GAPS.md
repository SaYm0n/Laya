# Valor, cenários e lacunas — documento vivo

> **Última atualização:** 2026-10-02 — Blocos A + B (PR #3, commit `404d827`).
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

## 2. Dores que a plataforma resolve

| dor | como a plataforma resolve | onde | status |
|---|---|---|---|
| Custo e latência de LLM em decisões repetitivas de alto volume | o Laya decide localmente; só o que a política escala vai para um LLM, com orçamento diário | `core`, `llm` (B) | ✅ |
| Confiança "de enfeite": modelos sempre seguros de si | `answer_confidence` (nunca a entropia), calibração de temperatura do upstream, bandas escolhidas por custo de erro | `evaluation` (A), `core/policy` (B) | ✅ código · 🔒 calibração real |
| Medo de automatizar e não ter volta | shadow → advisory → gated com canário determinístico, flags por spec e kill switch, sem deploy | `gateway` (A/B) | ✅ |
| Saída de LLM em texto livre, parsing frágil | `DecisionSpec` tipado; o System-2 responde num schema fechado, validado localmente e projetado como o System-1 | `core/spec`, `llm/decisions` | ✅ |
| Dependência de um único fornecedor de LLM | tiers por configuração; dois SDKs oficiais cobrem Claude, OpenAI, Gemini, DeepSeek, OpenRouter, Ollama e vLLM | `llm` (B) | ✅ |
| Dados sensíveis saindo da empresa (LGPD) | Laya local; provedor externo desligado por padrão; auditoria só com HMAC; recusa de dados reais sem DG-1 | `gateway/settings`, `llm/config` | ✅ controles · ⏳ redação de PII (F9) |
| "Por que o sistema decidiu isso?" | auditoria com trace id, banda, motivos (`reasons`), modelo, System-2 (tier, tokens, custo) e concordância com o sistema atual | `storage` (A/B) | ✅ |
| Fila humana sem critério | só vai para humano o que a política manda (banda, truncamento, idioma, risco, regra); cada item com o motivo | `/api/v1/reviews` (B) | ✅ · ⏳ SLA e interface |
| Custo de LLM imprevisível | orçamento diário, circuit breaker, métricas de tokens e custo por tier | `llm/gateway` (B) | 🟡 por processo (§6) |
| Integração arriscada com o sistema legado | `/v1/systemone` do upstream inalterado; `PlatformClient.shadow()` não bloqueia nem lança exceção | `gateway`, `client` (A) | ✅ |
| Falta de dados rotulados do domínio | resoluções da fila humana e comparação com o sistema atual viram rótulos | F8 | 🟡 coleta pronta · ⏳ pipeline · 🔒 DG-1 |
| Modelo genérico fraco no domínio (o upstream admite base zero-shot fraca) | especialistas próprios treinados, calibrados e promovidos com portões | F7 + F8 | ⏳ (Bloco C) |
| Ataques por texto injetado no estado | o estado vai ao LLM como dado JSON com instrução de ignorar ordens; o Laya decide, não executa | `llm/decisions` | 🟡 · ⏳ guardrails (F9) |

## 3. Onde a plataforma é mais eficiente

**Encaixe forte:**

- **Decisões fechadas, de alto volume e repetitivas**:
  - triagem, classificação, priorização, roteamento, sinalização (sim/não);
  - poucas opções por pergunta (até ~20; acima disso o upstream perde distinção, então use shortlist ou hierarquia).
- **Ambientes com restrição de dados** (financeiro, saúde, setor público, jurídico): o System-1 roda na própria
  infraestrutura e o System-2 externo fica desligado até o DG-1.
- **GPU disponível para volume.** O upstream publica ~33–40 ms por pergunta em T4. Em **CPU** a referência é
  ~0,2–0,5 s por chamada, viável para baixo volume ou para shadow.
- **pt-BR e en**: checkpoint `multilingual` (48 de 51 idiomas "utilizáveis" segundo o upstream). Textos pt-BR
  curtos podem não ser detectados; o roteamento cai no `default` e a política bloqueia a automação se o idioma
  detectado estiver fora do spec.
- **Quando já existe um sistema decidindo** (regras, pessoas ou um LLM caro): o shadow mede a concordância antes de
  qualquer mudança.

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

**O que medir em todo cenário:**

- **cobertura:** parcela das decisões na banda `auto`;
- **erro automatizado:** taxa de erro dentro da banda `auto`;
- **concordância com o sistema atual** (`incumbent`);
- **custo de LLM por decisão;**
- **latência p95;**
- **tamanho e idade da fila humana.**

As métricas do gateway (`/metrics`) e o relatório de avaliação já cobrem tudo isso, exceto a idade da fila (§6).

## 5. Melhorias na operação

| antes | com a plataforma | status |
|---|---|---|
| Decisão automatizada "tudo ou nada" | canário por spec (fração do tráfego), modo por spec via flag, kill switch global | ✅ |
| Mudar regra exige deploy | flags em tempo de execução; política declarada no spec (versionado) | ✅ flags · ⏳ recarga de spec sem reinício |
| Limiares escolhidos "no olho" | `laya-platform bands` escolhe o limiar de menor custo esperado e grava o `calibration_ref` | ✅ |
| Sem rastreabilidade de quem decidiu | trace id, modelo, banda, motivos, System-2 e resolvedor humano | ✅ · ⏳ autoria das flags |
| Custo de LLM descoberto na fatura | métricas `laya_platform_llm_*` (tokens, custo, latência, falhas) e orçamento | ✅ · 🟡 orçamento por processo |
| Revisão humana sem retorno | resolução registrada vira rótulo (F8) | ✅ coleta · ⏳ uso no treino |
| Troca de fornecedor de LLM custosa | troca de tier é configuração | ✅ |
| Comparar com o sistema atual exige planilha | concordância por pergunta em métricas e na auditoria | ✅ |

## 6. Lacunas atuais (análise sobre o código do PR #3)

| # | lacuna | impacto | onde resolver | prioridade |
|---|---|---|---|---|
| 1 | Sem execução com **pesos reais** nem baseline dos golden tests (Hugging Face bloqueado no ambiente de nuvem) | não há medida real de acurácia/latência dos checkpoints aqui | máquina local ou CI com acesso ao HF (`DEVELOPMENT.md` §8) | 🔴 alta |
| 2 | **DG-1 não escrito** (`docs/DATA_GOVERNANCE.md`) — retenção, expurgo, PII, envio a LLM | bloqueia dados reais, calibração real e F8 | documento + configuração + testes de expurgo | 🔴 alta |
| 3 | **Sem redação de PII** antes do System-2 | `allow_external` com dados reais seria arriscado | F9 (detectores BR: CPF, CNPJ, e-mail, telefone) | 🔴 alta antes de dados reais |
| 4 | **Orçamento e circuit breaker em memória, por processo** | com vários workers o teto real multiplica | contador no banco (tabela simples) ou Redis | 🟠 média |
| 5 | **SQLite** e um único processo (`uvicorn.run`) | concorrência de escrita limitada | PostgreSQL + vários workers (Bloco D, F16) | 🟠 média |
| 6 | **System-2 síncrono** dentro de `/api/v1/decide` | a latência do LLM soma à resposta | escalonamento assíncrono com callback/webhook ou modo "responde já, completa depois" | 🟠 média |
| 7 | **Sem `/api/v1/decide/batch`** (o upstream tem `predict_batch`) | volume alto paga overhead por requisição | endpoint em lote reaproveitando `predict_batch` | 🟠 média (ganho de eficiência) |
| 8 | **Fila humana sem atribuição, SLA nem expiração**; sem interface | revisões podem envelhecer sem alarme | campos `assignee`/`due_at` + métrica de idade; UI na F15 | 🟠 média |
| 9 | **Sem limite de taxa por chave** em `/api/v1/*` | uma integração defeituosa pode saturar o gateway | limitador simples por chave (token bucket) | 🟠 média |
| 10 | **Mudança de flag não registra quem mudou** | auditoria incompleta de operações | gravar autor e evento de flag | 🟡 baixa/média (rápido) |
| 11 | **Concordância System-1 × System-2 não vira métrica** | perde-se o sinal de quando o Laya já poderia decidir sozinho | contador por pergunta no escalonamento | 🟡 baixa (rápido) |
| 12 | **Avaliação só do System-1** (`eval` usa `DecisionEngine`) | não se mede a qualidade/custo do tier de LLM no mesmo dataset | adaptar o `LLMGateway` como engine de avaliação | 🟡 média |
| 13 | **Sem alertas de deriva** (métricas existem, regras não) | queda de confiança/concordância passa despercebida | regras Prometheus de exemplo (F14) | 🟡 média |
| 14 | **Tier fixo por spec** (sem `router_questions`) | custo maior que o necessário quando há vários tiers | seleção automática de tier com o preset do upstream | 🟡 baixa até haver 2+ tiers |
| 15 | **Spec carregado só na inicialização** | mudar política exige reinício | recarga controlada por endpoint admin | 🟡 baixa |
| 16 | **ONNX ainda exige torch** (limitação do upstream 0.3.23) | imagens "CPU leves" não ficam leves | reavaliar a cada versão do upstream | ⚪ externa |
| 17 | **Sem imagem Docker própria** | implantação manual | partir do Dockerfile/compose do upstream (não recriar) | 🟠 média (F16) |
| 18 | **Especialistas e treino** | o principal ganho de qualidade no domínio | Bloco C (F7 + F8) | 🔴 alta (marco central) |

## 7. Próximos ganhos priorizados (agilidade, eficiência, qualidade)

**Rápidos** (menos de um dia cada; podem entrar no início do Bloco C):

1. Métrica de concordância System-1 × System-2 (lacuna 11). Mostra quando um especialista ou nova calibração
   dispensaria o LLM.
2. Autoria e evento de flags na auditoria (lacuna 10).
3. Métrica de idade da fila e contagem de itens abertos (lacuna 8, parte).
4. Orçamento e circuito persistidos no banco já existente (lacuna 4), sem infraestrutura nova.

**Eficiência** (Bloco C ou D):

5. `/api/v1/decide/batch` sobre `predict_batch` do upstream (lacuna 7).
6. Escalonamento assíncrono (lacuna 6): o sistema de origem recebe a decisão do System-1 na hora e o System-2 por
   callback.
7. Prompt caching do System-2: avaliar com prompts reais. O prefixo mínimo cacheável é de 512–4096 tokens conforme
   o modelo, e o nosso prompt de sistema é curto; só vale com specs grandes.

**Qualidade** (Bloco C):

8. Avaliação do System-2 no mesmo dataset e relatório (lacuna 12): decide quando o LLM compensa o custo.
9. Pipeline F8: resoluções humanas + concordância → dataset versionado → especialista → calibração → promoção com
   portões (cobertura e erro automatizado melhores que o atual).
10. Calibração por idioma (`lang_temperatures` do upstream) para pt-BR.

**Pré-requisitos de dados reais** (bloqueantes):

11. Escrever e aprovar o DG-1 (lacuna 2) e implementar a redação de PII (lacuna 3) antes de qualquer
    `allow_external` com dados reais.
12. Rodar a suíte com pesos e gravar o baseline (lacuna 1) numa máquina com acesso ao Hugging Face.

## 8. Fora de escopo

O que fica de fora ou só entra com caso de uso concreto:

- **A2A e RAG** (F11/F12): só com um cenário que precise. O RAG cobre o "encaixe fraco" de dados atualizados; o
  A2A só faz sentido com vários agentes próprios.
- **Integrações LangChain/LangGraph/LlamaIndex/CrewAI** (F13): já existem no upstream (`laya.integrations`); o
  trabalho é documentar e exemplificar.
- **Dashboard** (F15): até lá, a operação usa `/metrics` (Prometheus/Grafana) e os endpoints admin.

## 9. Histórico

| data | marco | o que mudou neste documento |
|---|---|---|
| 2026-10-02 | Blocos A + B (PR #3) | criação: dores, ambientes, cenários, operação, lacunas (18), próximos ganhos |
