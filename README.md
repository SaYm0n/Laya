# Laya Decision Platform

> Nome provisório. Projeto **independente**, construído sobre o [Laya](https://github.com/NandhaKishorM/laya)
> (Apache-2.0, Convai Innovations). **Não é afiliado nem endossado** pela Convai Innovations ou pelo projeto Laya.

Plataforma que usa o Laya como **System-1** (decisões tipadas `choice` / `score` / `noul`, rápidas, locais e
calibradas) e decide quando escalar para um **LLM (System-2)** ou para um humano. Ela também transforma esse fluxo
num ciclo de dados para treinar **especialistas** do seu domínio.

## Estado atual

| fase | situação |
|---|---|
| F0 — auditoria do upstream e arquitetura | concluída |
| F1 — bootstrap, licença, organização e CI | concluída |
| F2 — Decision Core e compatibilidade | concluída |
| Bloco A — F3 gateway/auditoria/shadow + F4 avaliação/calibração | implementado (dados sintéticos), em revisão |
| Bloco B — F5 LLM Gateway + F6 System-1/System-2 | implementado (dados sintéticos), aguarda o merge do A |
| Bloco C — F7 Specialist Registry + F8 dados e treino | próximo (depois do DG-1) |

O pacote `laya_platform` tem hoje o **Decision Core** (`laya_platform.core`): o protocolo `DecisionEngine`, cinco
adaptadores (`UpstreamRouterEngine`, `AgentEngine`, `OnnxEngine`, `RemoteEngine`, `FakeEngine`), a `DecisionSpec`
(YAML/JSON validada, schema → perguntas pelo próprio `laya.structured`) e a suíte que congela o contrato do
`laya==0.3.23`. Com o extra `gateway` (Bloco A): o gateway HTTP que monta o app do upstream sem alteração e
acrescenta `/api/v1/decide` auditado em modo shadow/advisory, flags, kill switch e métricas; a avaliação com
relatório identificado, bandas por custo e calibração pelo próprio upstream (`laya-platform eval|bands|calibrate`).
Com o extra `llm` (Bloco B): System-2 por tiers sobre os SDKs oficiais `anthropic` e `openai` (este também para
Gemini, DeepSeek, OpenRouter, Ollama e vLLM), com orçamento, circuit breaker e provedor externo desligado por padrão;
a `DecisionPolicy` decide entre automatizar (modo `gated`, só na banda calibrada e dentro do canário), escalar para
um LLM ou mandar para a fila de revisão humana.

```python
from laya_platform.core import load_decision_spec
from laya_platform.core.adapters import UpstreamRouterEngine

spec = load_decision_spec("minha_decisao.yaml")                 # formato em docs/DEVELOPMENT.md §5
engine = UpstreamRouterEngine.create(default="multilingual")   # tráfego majoritariamente pt-BR
payload = engine.predict({"body": "Quero cancelar"}, spec.to_questions(), **spec.predict_controls())
```

## Começando

Requer [uv](https://docs.astral.sh/uv/) e Python 3.11 ou 3.12 (o uv instala o Python se necessário).

```bash
git clone https://github.com/SaYm0n/Laya.git
cd Laya
uv sync --locked --all-extras
uv run laya-platform --version     # laya-platform 0.1.0.dev0 (upstream laya 0.3.23)
uv run pytest                      # suíte padrão, offline
```

Instruções completas para Windows e Linux, categorias de teste e CI: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
Exemplo ponta a ponta (gateway e avaliação, dados sintéticos): [examples/support_triage](examples/support_triage/README.md).

## Documentação

| documento | conteúdo |
|---|---|
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | fluxo LEAN, instalação, comandos, categorias de teste, Decision Core, gateway e avaliação, CI, `main` protegida, atualização do upstream |
| [docs/UPSTREAM_ANALYSIS.md](docs/UPSTREAM_ANALYSIS.md) | o que o Laya 0.3.23 realmente oferece, limitações e riscos |
| [docs/ARCHITECTURE_PROPOSAL.md](docs/ARCHITECTURE_PROPOSAL.md) | arquitetura, gate de governança de dados (DG-1) e divergências do plano original |
| [docs/IMPLEMENTATION_ROADMAP.md](docs/IMPLEMENTATION_ROADMAP.md) | fases 0–20 em 4 blocos de entrega, marcos e bloqueios |
| [docs/VALUE_AND_GAPS.md](docs/VALUE_AND_GAPS.md) | dores que resolve, onde é mais eficiente, cenários reais, ganhos de operação e lacunas atuais (atualizado a cada bloco) |
| [docs/COMPATIBILITY_MATRIX.md](docs/COMPATIBILITY_MATRIX.md) | contrato de compatibilidade com o Laya e o protocolo Jev |
| [docs/LICENSE_AND_ATTRIBUTION.md](docs/LICENSE_AND_ATTRIBUTION.md) | obrigações da Apache-2.0, marca e pontos a verificar |

## Upstream de referência

| | |
|---|---|
| Pacote | `laya==0.3.23` (PyPI), fixado por versão e hash no `uv.lock` |
| Commit auditado | `NandhaKishorM/laya@4aa6761` |
| Licença | Apache-2.0 |

## Licença

Apache-2.0 — ver [LICENSE](LICENSE) e [NOTICE](NOTICE). Este pacote não é publicado em índices públicos.
