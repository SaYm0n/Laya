# Desenvolvimento

Guia de desenvolvimento: instalação, comandos, categorias de teste, Decision Core (F2), CI, proteção da `main` e
atualização do upstream. Cobre apenas o que já existe no repositório.

## 1. Pré-requisitos

| ferramenta | versão | observação |
|---|---|---|
| Python | 3.11 ou 3.12 | 3.12 recomendado (arquivo `.python-version`); 3.10 e 3.13 não são suportados pelo projeto |
| [uv](https://docs.astral.sh/uv/) | 0.12.22 ou mais novo da série 0.12 | exigido por `[tool.uv] required-version`; o CI usa exatamente 0.12.22 |
| Git | qualquer recente | |

O `uv` instala o Python certo sozinho se ele não existir na máquina.

## 2. Instalação inicial

### Linux / macOS

```bash
git clone https://github.com/SaYm0n/Laya.git
cd Laya
uv sync --locked                  # instala o lock completo (inclui torch)
uv run laya-platform --version
uv run pre-commit install         # hooks locais
```

### Windows (PowerShell)

```powershell
# instalar o uv, se ainda não tiver:  winget install --id=astral-sh.uv -e
git clone https://github.com/SaYm0n/Laya.git
cd Laya
uv sync --locked
uv run laya-platform --version
uv run pre-commit install
```

Saída esperada de `--version`:

```text
laya-platform 0.1.0.dev0 (upstream laya 0.3.23)
```

### Perfil leve (sem torch)

Nenhum código da plataforma importa o torch, e `import laya` o carrega sob demanda. Para economizar alguns GB (o
torch para Linux do PyPI traz a pilha CUDA), é possível instalar tudo **menos** os pacotes que só o torch usa —
é o que o CI padrão faz:

```bash
uv sync --locked $(uv run --no-project python scripts/ci_install_args.py)
export UV_NO_SYNC=1               # senão o próximo `uv run` reinstala o torch
```

No perfil leve, os testes marcados `torch` são pulados com o motivo (§4); carregar um checkpoint
(`AgentEngine.from_checkpoint`) exige o perfil completo. No Windows o torch do PyPI já é só CPU e bem menor; ali
basta `uv sync --locked`.

## 3. Comandos do dia a dia

| tarefa | comando |
|---|---|
| lint | `uv run ruff check .` |
| formatação | `uv run ruff format .` (verificar: `--check`) |
| tipos (mypy estrito) | `uv run mypy` |
| contratos de arquitetura | `uv run lint-imports` |
| testes padrão (offline) | `uv run pytest` |
| só os contratos com o upstream | `uv run pytest -m compatibility` |
| testes com checkpoints reais (perfil completo + acesso ao Hugging Face) | `uv run pytest --run-weights` |
| gravar o baseline dos golden tests | ver §8 |
| build | `uv build` |
| regras do pacote construído | `uv run --no-project python scripts/check_dist.py dist` |
| política de licenças (consulta o PyPI) | `uv run --no-project python scripts/check_licenses.py` |
| novas versões do upstream (consulta o PyPI) | `uv run python scripts/check_upstream.py` |
| todos os hooks | `uv run pre-commit run --all-files` |

## 4. Categorias de teste

Cada teste pertence a **uma** categoria base, definida pelo diretório, e pode ter marcadores que exigem recursos
externos. As regras estão em `tests/conftest.py`.

| categoria | onde / como | roda por padrão? | habilitar |
|---|---|:---:|---|
| `unit` | `tests/unit/` | sim | — |
| `compatibility` | `tests/compatibility/` | sim | — |
| `network` | marcador `@pytest.mark.network` | não | `--run-network` |
| `weights` | marcador; checkpoints reais do Laya (implica rede e torch) | não | `--run-weights` |
| `gpu` | marcador; CUDA/XPU/MPS (implica torch) | não | `--run-gpu` |
| `llm` | marcador; provedor de LLM externo (implica rede) | não | `--run-llm` |
| `torch` | marcador; precisa do runtime torch, **sem** pesos | sim, quando o torch está instalado | automático (perfil completo) |

`torch` não é opt-in: o teste roda sempre que o torch está instalado (perfil completo, workflow **Full install**) e
é pulado, com o motivo, no perfil leve. `weights` e `gpu` também são pulados sem torch, mesmo habilitados.

Garantias da execução padrão:

- **offline**: qualquer conexão a host que não seja loopback, ou resolução DNS, falha na hora com uma mensagem
  pedindo o marcador adequado. A guarda vale para o **processo Python dos testes**; um executável externo
  chamado por subprocess não é coberto, então ela não é uma sandbox de rede absoluta;
- bibliotecas da Hugging Face em modo offline (`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`);
- teste fora de `tests/unit/` ou `tests/compatibility/` é recusado na coleta;
- `--strict-markers` e warnings tratados como erro.

Exemplos:

```bash
uv run pytest -m compatibility                 # só contratos com o upstream
uv run pytest --run-network                    # inclui os testes que consultam o PyPI
uv run pytest --run-weights --run-gpu          # numa máquina com acesso ao Hugging Face e GPU
```

Testes `weights`/`network` não rodam neste ambiente de nuvem (o proxy bloqueia `huggingface.co`); rode-os na sua
máquina, no Colab/Kaggle ou em um ambiente com esse acesso.

## 5. Decision Core (F2)

`laya_platform.core` é a camada mínima e tipada sobre o `laya==0.3.23`. Importá-la não importa torch, onnxruntime
nem frameworks web, e não acessa a rede.

### 5.1 `DecisionEngine` e adaptadores

Todos implementam o mesmo protocolo (`predict`, `predict_batch`, `route`) com a semântica do `laya.Router`, e
devolvem o payload do upstream **sem alteração**: nenhuma banda de confiança, nenhuma regra de negócio, nenhuma
mudança em `confidence` ou `answer_confidence`.

| adaptador | sobre o quê | `route` | observações |
|---|---|---|---|
| `UpstreamRouterEngine` | `laya.Router` | ✅ do upstream | `create(**opções do Router)` mantém os padrões do upstream; para pt-BR use `create(default="multilingual", lang_guess=...)` |
| `AgentEngine` | um checkpoint (`laya.Agent` ou objeto equivalente) | ❌ `UnsupportedOperationError` | `from_checkpoint(caminho_ou_repo, revision=..., expected_sha256=...)` usa `laya.load`; precisa de torch. Controles de roteamento (`model`, `task`, `lang_guess`) são recusados com `UnsupportedControlError` |
| `OnnxEngine` | `laya.onnx_agent.ONNXAgent` | ❌ | `from_export(...)` exige `onnxruntime` **e** torch (o `ONNXAgent` do 0.3.23 importa `laya.common`); nenhum dos dois é dependência padrão |
| `RemoteEngine` | um endpoint `/v1/systemone` | ❌ | cliente HTTP (stdlib) com transporte injetável; `predict_batch` agrupa por perguntas/controles e respeita o limite de 64 estados por chamada; sem retries (fases futuras) |
| `FakeEngine` | nada | fixo | determinístico, sem pesos: respostas roteirizadas por pergunta (`FakeAnswer`), gate de abstenção do próprio upstream, registro de chamadas e falha simulada |

Erros (`laya_platform.core.errors`) herdam também do builtin correspondente: `UnsupportedControlError` é um
`TypeError`, `MissingRuntimeError` é um `ImportError`, `UnsupportedOperationError` é um `NotImplementedError`;
`RemoteEngineError` traz `status` e `detail` do servidor.

### 5.2 `DecisionSpec`

Declaração versionada de uma decisão, carregada de `.yaml`/`.yml`/`.json` com `load_decision_spec(caminho)`:

```yaml
id: support.ticket_triage          # minúsculas, segmentos separados por ponto
version: 3                         # inteiro >= 1
schema:                            # JSON Schema (ou `questions:` no formato do Laya; exatamente um dos dois)
  type: object
  properties:
    department: {type: string, enum: [billing, technical], description: "Qual equipe deve tratar?"}
    urgency:    {type: integer, minimum: 0, maximum: 4, description: "Quão urgente é?"}
    churn_risk: {type: boolean, description: "O cliente ameaça cancelar?"}
languages: [pt, en]                # códigos de idioma; declarativo nesta fase (não altera o roteamento)
engine:
  checkpoint: multilingual         # opcional: checkpoint do Router (aliases aceitos); vira o controle `model`
```

- `spec.to_questions()` converte o schema pelo próprio `laya.structured.questions_from_json_schema` (nenhum
  conversor próprio); `spec.predict_controls()` devolve `{"model": ...}` quando há `engine.checkpoint`.
- Regras além do upstream, todas porque o upstream aceitaria algo que falha em silêncio: toda propriedade do
  schema precisa de `description`; perguntas em `questions:` usam texto não vazio em `instructions`, rótulos e
  níveis e respeitam os limites HTTP do `laya.serve` (64 perguntas, 100 opções por `choice`, 32 níveis por
  `score`, 512 opções no total); YAML lido como YAML 1.2 (`no` continua sendo a string "no") e chaves duplicadas
  recusadas em YAML e JSON.
- Chaves de fases futuras são **recusadas com o motivo**, não ignoradas: `policy` (F6), `calibration_ref` (F4),
  `risk` (F6), `mode` (F3), `engine.specialist` e `engine.fallback_checkpoint` (F7).

### 5.3 Contratos congelados

`tests/compatibility/` congela o contrato do `laya==0.3.23` (lista completa em `COMPATIBILITY_MATRIX.md` §11).
`tests/unit/test_contract_drift.py` prova que a suíte funciona: copia o `laya` instalado, aplica mudanças
incompatíveis (um limite, um alias, um estado do gate, uma assinatura, um interno de treino) e exige que cada uma
derrube o seu teste.

## 6. Regras de arquitetura verificadas automaticamente

| regra | verificação |
|---|---|
| O núcleo não importa runtimes de ML nem frameworks de IA (`torch`, `transformers`, `onnxruntime`, `anthropic`, `openai`, `google`, `langchain*`, `langgraph`, `llama_index`, `crewai`) | `lint-imports` (contrato em `pyproject.toml`) |
| Importar `laya_platform.core` não carrega torch, onnxruntime, transformers, FastAPI, Starlette, uvicorn nem mcp | `tests/unit/test_architecture_imports.py` (interpretador novo) |
| Internos do Laya (`laya.common`, `laya.agent`, `laya.calibrate`, `laya.fast`, `laya.tl_kernels`, `laya._*`, nomes privados) só via `laya_platform.core.upstream_compat`, inclusive por `importlib.import_module("...")` | `tests/unit/test_architecture_imports.py` |
| O código da plataforma nunca lê o campo `confidence` (entropia) de uma resposta | `tests/compatibility/test_confidence_semantics.py` |
| Nenhum ID concreto de modelo LLM em `src/`, `scripts/` ou `tests/` | `tests/unit/test_model_id_guard.py` |
| Dependências de runtime exatamente as declaradas (`laya==0.3.23`, `pydantic`, `pyyaml`); sem extras opcionais ainda | `tests/unit/test_package_metadata.py` + `scripts/check_dist.py` |
| Nada de segredos, pesos ou dados no git | `.gitignore` + `tests/unit/test_repo_hygiene.py` + gitleaks |

## 7. Dependências e lockfile

- `uv.lock` é a fonte da verdade: versões exatas e **hashes sha256** de cada artefato, para Linux, Windows e macOS.
- Runtime: `laya==0.3.23`, `pydantic` e `pyyaml` (os dois últimos desde a F2, importados diretamente pela
  `DecisionSpec`).
- Grupos: `dev` (padrão: pytest, ruff, mypy, import-linter, pre-commit, packaging, types-pyyaml e, **só para os
  testes de contrato**, `fastapi`, `httpx` e `mcp`, que exercitam o app HTTP e o servidor MCP do próprio upstream),
  `audit` (pip-audit), `package` (twine). Extras opcionais (`gateway`, `llm`, `rag`, `onnx`, ...) só entram nas fases
  que precisarem deles.
- Atualizar uma dependência: `uv lock --upgrade-package <nome>` → rodar a suíte → commit do `uv.lock`.
- O CI recusa um `uv.lock` desatualizado (`uv lock --check`).

## 8. Atualização do upstream (`laya`)

O pin fica em dois lugares que os testes mantêm coerentes: `pyproject.toml` (`laya==0.3.23`) e
`src/laya_platform/_upstream.py` (versão, nomes e hashes dos artefatos, commits auditados).

O workflow semanal **Upstream watch** só **detecta**: se houver versão nova no PyPI, abre uma issue com rótulo
`upstream` e o checklist abaixo. Ele também falha se o PyPI deixar de servir os artefatos auditados. Nada é
atualizado automaticamente.

Checklist de atualização:

1. Ler as notas de versão e o diff do upstream desde o commit auditado.
2. Atualizar `pyproject.toml` e `_upstream.py` (versão, hashes do wheel e do sdist), rodar `uv lock`.
3. Suíte padrão verde, incluindo `tests/compatibility/`; no perfil completo, também os testes `torch`. Uma falha
   num snapshot de contrato é uma mudança do upstream a avaliar, não um teste a "consertar" às cegas.
4. Suítes com pesos verdes (`--run-weights`), fora do CI, incluindo os golden tests contra o baseline anterior.
5. Gravar o novo baseline dos golden tests e revisar o diff antes do commit:

   ```bash
   LAYA_PLATFORM_RECORD_GOLDEN=1 uv run pytest --run-weights -k golden_decision tests/compatibility
   ```

   Sem baseline gravado, `test_golden_decisions` **falha** (não passa no vazio). Tolerâncias e justificativa estão
   no próprio módulo.
6. Benchmarks comparados com o pin anterior.
7. Revisão de segurança: `pip-audit`, política de licenças, changelog de correções de segurança.

## 9. CI (GitHub Actions)

| workflow | quando | o que faz | rede externa |
|---|---|---|---|
| `CI` | PR, push na `main`, manual | `lockfile` (lock atualizado), `lint` (ruff), `typecheck` (mypy + import-linter), `tests` (Ubuntu 3.11 e 3.12, Windows 3.12; perfil leve), `build` (build, regras do pacote, `twine check`, wheel isolado + CLI), `required` (agregador) | só para instalar dependências |
| `Security` | PR, push na `main`, semanal, manual | gitleaks no histórico completo, `pip-audit` do lock inteiro (runtime e dev), política de licenças | base de vulnerabilidades e metadados do PyPI |
| `Upstream watch` | semanal, manual | compara o pin com o PyPI, confere hashes auditados, abre issue se houver versão nova | PyPI |
| `Full install` | semanal, manual, PR que muda dependências, `src/laya_platform/core/`, `tests/compatibility/` ou `tests/conftest.py` | instala o lock completo (torch e CUDA no Linux), importa `torch` e `laya.Agent` sem pesos, roda a suíte — é onde os testes `torch` executam | só para instalar dependências |

Todas as actions de terceiros estão fixadas por SHA de commit (o comentário indica a tag); o Dependabot as mantém
atualizadas. Os testes padrão nunca acessam Hugging Face, GPU, chaves de LLM ou serviços externos.

O `gitleaks-action` é gratuito para contas pessoais; se o repositório for para uma organização, crie o segredo
`GITLEAKS_LICENSE`.

## 10. `main` protegida

O ruleset **"Proteção da main"** está **ativo** no repositório (configurado pelo mantenedor em Settings → Rules →
Rulesets, alvo `main`). Estado vigente:

- a `main` só muda por **pull request**; commits diretos não são aceitos;
- **checks obrigatórios** antes do merge: `required` (workflow `CI`, que agrega lockfile, lint, tipos, testes e
  build) e `secret scan (gitleaks)` (workflow `Security`);
- **force push** bloqueado e **exclusão** da branch restrita, conforme a configuração vigente do ruleset;
- `dependency audit (pip-audit)`, `license policy` e `Full install` rodam e ficam visíveis, mas **não** são
  obrigatórios: um alerta publicado hoje para uma dependência antiga não deve bloquear um PR não relacionado. Eles
  também rodam semanalmente na `main`.

Aprovações exigidas e as opções opcionais (histórico linear, commits assinados) seguem o que estiver configurado
no ruleset; com um único mantenedor, a recomendação é 0 aprovações até haver revisores.

Localmente, o hook `no-commit-to-branch` do pre-commit impede commits diretos na `main`.

## 11. Segredos e dados

- Nunca versionar: `.env`, chaves de API, tokens, senhas, credenciais do Hugging Face, datasets reais, pesos,
  arquivos de calibração com dados. O `.gitignore` cobre esses padrões e um teste garante que nenhum está rastreado.
- `.env.example` documenta variáveis sem nenhum valor secreto.
- Os casos dos golden tests (`tests/compatibility/golden/decisions.json`) são sintéticos e públicos.
- Dados reais só entram na plataforma depois do gate DG-1 (`ARCHITECTURE_PROPOSAL.md` §6.15).
