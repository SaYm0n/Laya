# Laya Decision Platform

> Nome provisório. Projeto **independente**, construído sobre o [Laya](https://github.com/NandhaKishorM/laya)
> (Apache-2.0, Convai Innovations). **Não é afiliado nem endossado** pela Convai Innovations ou pelo projeto Laya.

Plataforma que usa o Laya como **System-1** (decisões tipadas `choice` / `score` / `noul`, rápidas, locais e
calibradas) e decide quando escalar para um **LLM (System-2)** ou para um humano. Ela também transforma esse fluxo
num ciclo de dados para treinar **especialistas** do seu domínio.

## Estado atual

**Fase 0 concluída — auditoria e arquitetura.** Ainda não há código da plataforma.

| documento | conteúdo |
|---|---|
| [docs/UPSTREAM_ANALYSIS.md](docs/UPSTREAM_ANALYSIS.md) | o que o Laya 0.3.23 realmente oferece, limitações e riscos |
| [docs/ARCHITECTURE_PROPOSAL.md](docs/ARCHITECTURE_PROPOSAL.md) | arquitetura proposta e divergências em relação ao plano original |
| [docs/IMPLEMENTATION_ROADMAP.md](docs/IMPLEMENTATION_ROADMAP.md) | fases 0–20 reordenadas, marcos e bloqueios |
| [docs/COMPATIBILITY_MATRIX.md](docs/COMPATIBILITY_MATRIX.md) | compatibilidade com o Laya e o protocolo Jev, e testes de contrato |
| [docs/LICENSE_AND_ATTRIBUTION.md](docs/LICENSE_AND_ATTRIBUTION.md) | obrigações da Apache-2.0, marca e pontos a verificar |

## Upstream de referência

| | |
|---|---|
| Pacote | `laya==0.3.23` (PyPI) |
| Commit auditado | `NandhaKishorM/laya@4aa6761` |
| Licença | Apache-2.0 |
