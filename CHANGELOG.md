# Changelog

Todas as mudanças relevantes do Mars Decision Platform. O formato segue o
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). O pacote ainda não tem versão publicada
(`0.1.0.dev0`), por isso as entradas são agrupadas por entrega.

## [Não lançado]

### Adicionado

- Marca **Mars**: o símbolo com Marte, Fobos (System-1) e Deimos (System-2), o banner e a imagem de prévia para o
  GitHub ([docs/assets/brand](docs/assets/brand/README.md)).
- README com visão geral, diagramas, demonstração e estado do projeto.
- [docs/WORKFLOWS.md](docs/WORKFLOWS.md), com cinco fluxos ponta a ponta, e o índice [docs/README.md](docs/README.md).
- `scripts/make_demo_assets.py`: gera as imagens da demonstração rodando o gateway e a CLI de verdade, com motor
  simulado e dados sintéticos; um teste garante que o script continua funcionando.
- `CHANGELOG.md` e `SECURITY.md`.

### Alterado

- Nome do produto: de "Laya Decision Platform (nome provisório)" para **Mars Decision Platform**. A CLI
  `laya-platform`, o pacote `laya_platform`, as métricas e as variáveis de ambiente continuam com os nomes atuais.
- `NOTICE`: autoria de Márcio.

## Bloco C: especialistas, dados e treino ([#4](https://github.com/SaYm0n/Laya/pull/4)), 2026-10-02

### Adicionado

- **Specialist Registry:**
  - manifesto com sha256 dos pesos;
  - ciclo de vida `experimental → shadow → candidate → production → deprecated`, com critérios objetivos;
  - challenger em shadow;
  - rollback sem deploy (CLI e `POST /api/v1/specialists/rollback`).
- **Dados:** `dataset candidates`, `build` (proveniência por HMAC, PII mascarada), `split` (por grupo ou tempo) e
  `label` (professor LLM).
- **Treino:** `train` pela receita de fine-tune do upstream, fixada por commit e sha256 e importada sem cópia. O
  checkpoint base é baixado no commit revisado. Também `eval --specialist`.
- **Governança:**
  - redação de PII (e-mail, CPF/CNPJ, cartão, telefone);
  - `db purge` (retenção) e `db forget` (exclusão do titular);
  - rascunho do gate DG-1.
- **Operação:** autor das mudanças de flag, métricas da fila de revisão, concordância System-1 × System-2 e
  orçamento de LLM no banco.

## Blocos A e B: gateway, avaliação e System-2 ([#3](https://github.com/SaYm0n/Laya/pull/3)), 2026-10-02

### Adicionado

- **Gateway:**
  - o app do Laya montado sem alteração;
  - `/api/v1/decide` auditado (HMAC da entrada, sem o texto);
  - modos shadow e advisory, flags, kill switch, `/ready` e `/metrics`.
- **Avaliação e calibração:** relatório identificado por hash, bandas por custo e calibração pelo próprio upstream.
- **System-2:** tiers sobre os SDKs oficiais `anthropic` e `openai`, provedor externo desligado por padrão,
  orçamento e circuit breaker.
- **Política de confiança:** modo `gated` com canário determinístico, escalonamento para LLM e fila de revisão
  humana.
- `docs/VALUE_AND_GAPS.md`, o documento vivo de valor, cenários e lacunas.

## Fase 2: Decision Core ([#2](https://github.com/SaYm0n/Laya/pull/2)), 2026-10-02

### Adicionado

- Protocolo `DecisionEngine` e cinco adaptadores (Router, checkpoint, ONNX, remoto, simulado).
- `DecisionSpec` em YAML/JSON, que aceita exatamente o que o upstream aceita.
- Suíte de compatibilidade que congela o contrato do `laya==0.3.23`.

## Fases 0 e 1: auditoria e fundação ([#1](https://github.com/SaYm0n/Laya/pull/1)), 2026-10-02

### Adicionado

- Auditoria do upstream e proposta de arquitetura.
- Pacote, lockfile com hashes, categorias de teste com guarda offline, CI (testes, tipos, arquitetura, segurança,
  licenças) e higiene do repositório.
