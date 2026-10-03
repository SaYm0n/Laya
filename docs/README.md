<p align="center"><img src="assets/brand/mars-mark.svg" width="96" alt="Mars"></p>

# Documentação do Mars

Todos os documentos do projeto, por público. Comece pelo [README](../README.md) para ter a visão geral.

## Para quem avalia o produto

| documento | o que responde |
|---|---|
| [Valor, cenários e lacunas](VALUE_AND_GAPS.md) | que dores o Mars resolve, onde é mais eficiente, 12 cenários reais e o que ainda falta (atualizado a cada entrega) |
| [Fluxos de trabalho](WORKFLOWS.md) | como o sistema é usado na prática, do shadow ao especialista em produção |
| [Roadmap](IMPLEMENTATION_ROADMAP.md) | as fases 0–20 em quatro blocos de entrega, marcos e bloqueios |
| [Changelog](../CHANGELOG.md) | o que foi entregue em cada etapa |

## Para quem opera

| documento | o que responde |
|---|---|
| [Fluxos de trabalho](WORKFLOWS.md) | comandos, métricas e "como desfazer" de cada operação |
| [Desenvolvimento §5](DEVELOPMENT.md#5-decision-core-f2) | configuração do gateway, da política, do System-2, dos especialistas e do treino |
| [Exemplo: triagem de chamados](../examples/support_triage/README.md) | um spec e um dataset sintéticos para experimentar |

## Para quem desenvolve

| documento | o que responde |
|---|---|
| [Desenvolvimento](DEVELOPMENT.md) | fluxo de trabalho, instalação, comandos, categorias de teste, regras de arquitetura, CI e atualização do upstream |
| [Matriz de compatibilidade](COMPATIBILITY_MATRIX.md) | o contrato com o Laya e o protocolo Jev, e os testes que o garantem |
| [Análise do upstream](UPSTREAM_ANALYSIS.md) | o que o Laya 0.3.23 oferece de fato, suas limitações e riscos |
| [Proposta de arquitetura](ARCHITECTURE_PROPOSAL.md) | a arquitetura decidida na Fase 0 e as divergências do plano original |

## Governança, segurança e licença

| documento | o que responde |
|---|---|
| [Governança de dados (DG-1)](DATA_GOVERNANCE.md) | o que o código já garante e as decisões pendentes antes de dados reais |
| [Segurança](../SECURITY.md) | como reportar uma vulnerabilidade e as garantias atuais |
| [Licença e atribuição](LICENSE_AND_ATTRIBUTION.md) | obrigações da Apache-2.0, uso da marca do upstream e pontos a verificar |
| [Marca Mars](assets/brand/README.md) | o símbolo, as cores, a tipografia e como usar |

## Convenções

- Cada documento abre com o público, o status e a data da última atualização.
- Documentação em português do Brasil. Código, comentários e mensagens de commit em inglês.
- Imagens de demonstração em `assets/demo/`, geradas por `scripts/make_demo_assets.py`; não as edite à mão.
