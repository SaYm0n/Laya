# Governança de dados — Gate DG-1

> **Status: RASCUNHO, aguardando aprovação.** Enquanto este documento não estiver aprovado pelo responsável
> pelo tratamento (§12), **nenhum dado real** entra na plataforma: nem shadow, nem avaliação sobre exportações
> reais, nem datasets, nem envio a LLM. O gateway já recusa iniciar com `data_classification: real` sem
> `dg1_approval_ref` (`ARCHITECTURE_PROPOSAL.md` §6.15).
>
> Este rascunho separa, item a item, **o que o código já garante** do que **precisa da sua decisão**. As decisões
> estão marcadas com ☐ e um valor sugerido.

## 1. Escopo

Tudo o que a plataforma recebe, guarda ou envia: o estado (texto/JSON da decisão), as respostas, os valores do
sistema atual (`incumbent`), as respostas do System-2, os rótulos humanos, os datasets e os especialistas treinados
a partir deles.

## 2. Classificação de dados

| nível | exemplos | pode entrar? |
|---|---|---|
| público | dados abertos, textos sintéticos | sim |
| interno | metadados operacionais | sim |
| confidencial | contratos, dados comerciais | após DG-1 |
| pessoal | nome, CPF, e-mail, telefone, conversa de cliente | após DG-1, com base legal |
| pessoal sensível | saúde, religião, biometria, dados de crianças | ☐ **sugerido: proibido** até decisão específica |

- **Já garantido:** `data_classification` na configuração do gateway (`synthetic`, `public`, `real`). Com
  `real`, a inicialização exige `dg1_approval_ref`.
- **Decisão ☐:** a classificação de cada fonte e de cada DecisionSpec, numa tabela neste documento.

## 3. PII e LGPD

- **Decisão ☐:** a base legal por fonte e finalidade. Sugestão: legítimo interesse para triagem operacional, com
  teste de balanceamento documentado; contrato quando a decisão faz parte do serviço.
- **Decisão ☐:** a necessidade de RIPD (relatório de impacto). Sugestão: sim, se houver decisão automatizada que
  afete titulares (`gated`/produção).
- **Decisão ☐:** como atender aos direitos do titular (acesso, correção, exclusão, revisão de decisão
  automatizada).
- **Minimização (já garantido):** a auditoria guarda o **HMAC** da entrada, nunca o texto. A fila de revisão não
  guarda a entrada. Os relatórios de avaliação não contêm a entrada.

## 4. Retenção

| dado | onde | prazo sugerido ☐ | mecanismo |
|---|---|---|---|
| eventos de auditoria (HMAC, respostas, motivos, custo) | `audit_events` | 180 dias | `laya-platform db purge --older-than-days N` |
| resultados de challenger | `challenger_results` | 180 dias | idem |
| itens de revisão resolvidos | `review_items` | 180 dias (os abertos nunca expiram) | idem |
| eventos de flags e do ciclo de especialistas | `flag_events`, `specialist_events` | permanente (trilha de operações) | — |
| datasets e splits | fora do banco (arquivos) | ☐ 12 meses ou até re-treino | exclusão manual registrada |
| relatórios de avaliação | fora do banco (`reports/`) | ☐ enquanto houver especialista que os cita | — |
| pesos de especialistas | fora do git (Hub privado, objeto ou diretório) | ☐ enquanto em uso + 1 versão de rollback | — |

**Já garantido:** o expurgo por idade é testado (`tests/unit/test_storage.py`) e não toca itens abertos nem as
trilhas de operação.

## 5. Criptografia

- **Decisão ☐:** TLS na frente do gateway (proxy reverso ou balanceador). O gateway não termina TLS.
- **Decisão ☐:** criptografia em repouso para o banco (PostgreSQL com disco criptografado), os backups e os
  artefatos (datasets, pesos).
- **Chave HMAC (já garantido):** vem de `LAYA_PLATFORM_HMAC_KEY`, nunca de arquivo; tem no mínimo 32 caracteres.
- **Decisão ☐:** a rotação da chave HMAC. Trocar a chave quebra a junção de datasets com a auditoria antiga;
  sugestão: rotação anual, com expurgo da auditoria anterior.

## 6. Controle de acesso

- **Já garantido:** as chaves de API são guardadas só como SHA-256. Os escopos `decide`, `route`, `review`,
  `metrics` e `admin` são separados.
- **Já garantido:** a revisão humana exige o escopo `review`, e o resolvedor fica registrado.
- **Já garantido:** mudanças de flag e do ciclo de especialistas registram quem as fez.
- **Decisão ☐:** quem recebe cada escopo. Sugestão: a integração recebe só `decide`; os revisores, `review`; a
  operação, `admin`; o monitoramento, `metrics`.
- **Pendente (Bloco D):** o acesso de leitura à auditoria ainda não é registrado, e não há limite de requisições
  por chave.

## 7. Logs de auditoria

- **Registrado:**
  - trace id, spec e versão, modo e engine (Router ou especialista);
  - HMAC da entrada, respostas resumidas (valor, `answer_confidence`, banda), veredito da política e motivos;
  - System-2 (tier, modelo, tokens, custo, quantidade de PII mascarada), concordâncias e erro.
- **Nunca registrado:** o texto da entrada, chaves de API em claro e a chave HMAC.
- **Decisão ☐:** armazenamento append-only. Sugestão: um usuário de banco sem `UPDATE`/`DELETE` para o gateway,
  com o expurgo rodando com outro usuário.

## 8. Datasets de treino

- **Proveniência (já garantido):** `dataset build` só aceita entradas exportadas pelo sistema de origem cujo HMAC
  bate com o da decisão auditada. Cada linha guarda `label_source` (`human`, `incumbent`, `teacher`).
- **Versionamento (já garantido):** `dataset split` grava `manifest.json` com o sha256 de cada arquivo. O manifesto
  do especialista referencia o dataset e a receita de treino (commit e sha256 do script do upstream).
- **Decisão ☐:** a aprovação antes de usar um dataset em treino. Sugestão: registro no PR, ou no manifesto, de quem
  aprovou.
- **Decisão ☐:** a proibição de dados pessoais sensíveis em datasets sem aprovação explícita.
- **Decisão ☐:** a licença de datasets públicos, a confirmar antes do uso. O `LocalLLaMA/typed-decisions` (o
  dataset da receita do upstream) só entra no treino por `train --extra-upstream`, que grava caminho e sha256 no
  manifesto do especialista. A licença não pôde ser conferida no ambiente de desenvolvimento em nuvem (Hugging Face
  bloqueado): confira na página do dataset e registre aqui (licença, data, quem conferiu).

## 9. Envio a LLM externo

- **Já garantido:** proibido por padrão (`llm.allow_external: false`). Um tier externo é recusado na inicialização.
- **Já garantido:** quando habilitado, a PII é mascarada antes do envio (`llm.redact_pii: true`, padrão), e a
  contagem fica na auditoria.
- **Decisão ☐:** provedores aceitos, com termos de não retenção e de não uso para treino, e a região.
- **Decisão ☐:** a liberação **por DecisionSpec**. Hoje a liberação é global; a liberação por spec está pendente.
- **Regra sugerida:** dados pessoais sensíveis só com professor local (`local: true`, por exemplo Ollama/vLLM).

## 10. Sanitização e redação

**Já garantido** (`laya_platform.privacy`, testado):

- e-mail;
- CPF e CNPJ (numérico e alfanumérico), com dígitos verificadores;
- cartão de pagamento (Luhn);
- telefone brasileiro.

O texto mascarado mantém a forma (`[CPF]`, `[EMAIL]`...). É aplicado antes de qualquer LLM externo e na construção
de datasets (`dataset build`, salvo `--no-redact`).

**Pendente (F9):** RG, endereço, nome próprio e detalhes livres de saúde ou finanças. Por isso dados reais ainda
dependem desta aprovação e da classificação de cada spec.

## 11. Exclusão e expurgo

- **Expurgo por retenção (já garantido):** `laya-platform db purge`.
- **Pedido de exclusão do titular (já garantido):** a entrada não fica guardada; para localizar decisões, o
  sistema de origem calcula o HMAC das entradas do titular, e `laya-platform db forget --hmac ...` (ou
  `--hmac-file`) apaga os eventos, os challengers e as revisões dessas decisões.
- **Propagação (decisão ☐):** os datasets que contêm as linhas excluídas são regenerados. Os especialistas
  treinados com elas são re-treinados ou mantidos conforme a política. Sugestão: re-treino no próximo ciclo.

## 12. Aprovação

| papel | nome | data | referência (`dg1_approval_ref`) |
|---|---|---|---|
| responsável pelo tratamento | ☐ | ☐ | ☐ |
| encarregado (DPO) | ☐ | ☐ | ☐ |

Depois de aprovado, configure no gateway:

- `data_classification: real`;
- `dg1_approval_ref: <referência>`.

Mantenha este documento e a configuração versionados juntos.
