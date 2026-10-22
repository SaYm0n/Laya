# Revisão preliminar de upgrade upstream — Laya 0.3.23 → 0.4.13

> **22/10/2026 — Em avaliação. NÃO aprovado para atualizar o pin ou usar dados reais.**
> Esta revisão é um complemento à auditoria original da F0, que continua válida **somente para 0.3.23**.
> Não representa auditoria de código completa nem resultado de testes.

## Situação verificada

- Versão em uso: `laya==0.3.23` em `pyproject.toml`, `src/laya_platform/_upstream.py` e `uv.lock`.
- Versão candidata: `v0.4.13`, commit publicado `6f0496f`, lançada em 21/10/2026.
- Comparação GitHub [v0.3.23...v0.4.13](https://github.com/NandhaKishorM/laya/compare/v0.3.23...v0.4.13): **1.342 commits à frente**; a resposta de comparação retornou 300 arquivos (limite da API; **não é inventário completo**).
- Comparação por blob confirmou alterações em `laya/agent.py`, `laya/router.py`, `laya/serve.py`, `laya/confidence.py`, `laya/structured.py`, `laya/common.py`, `laya/__init__.py` e na receita `notebooks/laya_finetune_typed_decisions_mps.py`.
- O arquivo `LICENSE` permanece idêntico entre as duas tags e `pyproject.toml` do upstream v0.4.13 ainda declara Apache-2.0. **Isso não valida as licenças dos novos artefatos/dependências/datasets.**

## Mudanças e riscos a investigar

| Área | Fato publicado / verificado | Gate antes de upgrade |
|---|---|---|
| API Python e internos | Vários módulos e receita de fine-tuning foram modificados; nossa matriz congela assinaturas, `__all__`, `Agent._check_question` e contrato de treino | Diff de código/API; adaptar contratos *sem mascarar regressões*; executar `tests/compatibility/` |
| Confiança e esquema | v0.4.13 rejeita confiança booleana em `_answer_confidence` e limites booleanos de score | Verificar `answer_confidence`, abstenção, bandas de risco, `DecisionSpec` e casos inválidos |
| Roteamento e HTTP | `laya/router.py` e `laya/serve.py` mudaram; SDK Python agora suporta batch HTTP | Comparar tipos, limites, status codes, controles e comportamento de `/v1/systemone*` |
| Hooks, replay, inferência | Ajustes de precisão na estabilidade/replay; otimização em `_decode_answers` | Testes de equivalência de probabilidades, golden baseline e regressão CPU/ONNX/pesos |
| LLM remoto | Versão nova corrige rejeição de resposta remota não-objeto em ferramentas | Falhar fechado nas integrações externas; não alterar permissões de `act`/produção |
| Fine-tuning | Script upstream referenciado por hash na plataforma tem conteúdo modificado | Revisar API de treino e pin commit/SHA256; não copiar nem migrar automaticamente a receita |
| Licenças | LICENSE do upstream sem alteração | Checar wheel/sdist, NOTICE, dependências e dataset `LocalLLaMA/typed-decisions` (licença pendente); nada de treino real |
| Governança | DG-1, pesos reais e golden baseline permanecem bloqueadores independentes | Manter uso real, `gated` e promoção em produção desabilitados |

**Fontes:** [release v0.4.13](https://github.com/NandhaKishorM/laya/releases/tag/v0.4.13), [comparação completa no GitHub](https://github.com/NandhaKishorM/laya/compare/v0.3.23...v0.4.13), `docs/UPSTREAM_ANALYSIS.md`, `docs/COMPATIBILITY_MATRIX.md`, `docs/LICENSE_AND_ATTRIBUTION.md`.

## Critérios objetivos de aprovação (todos obrigatórios)

- [ ] Inspecionar o diff relevante 0.3.23 → 0.4.13, inclusive alterações acumuladas da série 0.4; registrar incompatibilidades intencionais e alterações de comportamento.
- [ ] Validar a versão publicada (PyPI wheel/sdist, hashes, metadados, segurança e licença), e compatibilidade de dependências.
- [ ] Atualizar `pyproject.toml`, `src/laya_platform/_upstream.py`, `uv.lock` **num PR separado de atualização do pin** e revisar os snapshots com evidência.
- [ ] Executar `uv lock --check`, CI, Security, Full install e todos os contratos em Linux/Windows suportados.
- [ ] Executar os testes com checkpoints reais (`--run-weights`) e comparar golden baseline, métricas, calibração, inferência e benchmarks com a versão antiga.
- [ ] Revisar imports internos, receita de fine-tuning, política `answer_confidence`, HTTP e invariantes de `shadow` / `gated` / `act`.
- [ ] Confirmar DG-1 antes de qualquer validação com dados reais e não utilizar `typed-decisions` sem prova de licença.

## Decisão

**NO-GO para trocar o pin neste momento**: mudança acumulada grande, contratos ainda não comparados integralmente, testes com pesos reais não registrados. A release é **candidata avaliada preliminarmente, não dependência atualizada**. Manter `laya==0.3.23` até aprovação explícita dos gates; nenhuma implementação de funcionalidade antecipada.

Esta revisão não altera a conclusão cronológica da F0: a auditoria inicial precedeu F1–F8, conforme o histórico do repositório.
