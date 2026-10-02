# Desenvolvimento

Guia do bootstrap da Fase 1: instalação, comandos, categorias de teste, CI, proteção da `main` e
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

Nenhum código da Fase 1 importa o torch, e `import laya` o carrega sob demanda. Para economizar alguns GB (o
torch para Linux do PyPI traz a pilha CUDA), é possível instalar tudo **menos** os pacotes que só o torch usa —
é o que o CI padrão faz:

```bash
uv sync --locked $(uv run --no-project python scripts/ci_install_args.py)
export UV_NO_SYNC=1               # senão o próximo `uv run` reinstala o torch
```

No Windows o torch do PyPI já é só CPU e bem menor; ali basta `uv sync --locked`.

## 3. Comandos do dia a dia

| tarefa | comando |
|---|---|
| lint | `uv run ruff check .` |
| formatação | `uv run ruff format .` (verificar: `--check`) |
| tipos (mypy estrito) | `uv run mypy` |
| contratos de arquitetura | `uv run lint-imports` |
| testes padrão (offline) | `uv run pytest` |
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
| `weights` | marcador; checkpoints reais do Laya (implica rede) | não | `--run-weights` |
| `gpu` | marcador; CUDA/XPU/MPS | não | `--run-gpu` |
| `llm` | marcador; provedor de LLM externo (implica rede) | não | `--run-llm` |

Garantias da execução padrão:

- **offline**: qualquer conexão a host que não seja loopback, ou resolução DNS, falha na hora com uma mensagem
  pedindo o marcador adequado;
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

## 5. Regras de arquitetura verificadas automaticamente

| regra | verificação |
|---|---|
| O núcleo não importa runtimes de ML nem frameworks de IA (`torch`, `transformers`, `onnxruntime`, `anthropic`, `openai`, `google`, `langchain*`, `langgraph`, `llama_index`, `crewai`) | `lint-imports` (contrato em `pyproject.toml`) |
| Internos do Laya (`laya.common`, `laya.agent`, `laya.calibrate`, `laya.fast`, `laya.tl_kernels`, `laya._*`) só via `laya_platform.core.upstream_compat` (a partir da F2) | `tests/unit/test_architecture_imports.py` |
| Nenhum ID concreto de modelo LLM em `src/`, `scripts/` ou `tests/` | `tests/unit/test_model_id_guard.py` |
| Núcleo depende só de `laya==0.3.23`; sem extras opcionais ainda | `tests/unit/test_package_metadata.py` |
| Nada de segredos, pesos ou dados no git | `.gitignore` + `tests/unit/test_repo_hygiene.py` + gitleaks |

## 6. Dependências e lockfile

- `uv.lock` é a fonte da verdade: versões exatas e **hashes sha256** de cada artefato, para Linux, Windows e macOS.
- Grupos: `dev` (padrão: pytest, ruff, mypy, import-linter, pre-commit, packaging), `audit` (pip-audit),
  `package` (twine). Extras opcionais (`gateway`, `llm`, `rag`, ...) só entram nas fases que precisarem deles.
- Atualizar uma dependência: `uv lock --upgrade-package <nome>` → rodar a suíte → commit do `uv.lock`.
- O CI recusa um `uv.lock` desatualizado (`uv lock --check`).

## 7. Atualização do upstream (`laya`)

O pin fica em dois lugares que os testes mantêm coerentes: `pyproject.toml` (`laya==0.3.23`) e
`src/laya_platform/_upstream.py` (versão, nomes e hashes dos artefatos, commits auditados).

O workflow semanal **Upstream watch** só **detecta**: se houver versão nova no PyPI, abre uma issue com rótulo
`upstream` e o checklist abaixo. Ele também falha se o PyPI deixar de servir os artefatos auditados. Nada é
atualizado automaticamente.

Checklist de atualização:

1. Ler as notas de versão e o diff do upstream desde o commit auditado.
2. Atualizar `pyproject.toml` e `_upstream.py` (versão, hashes do wheel e do sdist), rodar `uv lock`.
3. Suíte padrão verde, incluindo `tests/compatibility/`.
4. Suítes com pesos verdes (`--run-weights`), fora do CI.
5. Benchmarks comparados com o pin anterior.
6. Revisão de segurança: `pip-audit`, política de licenças, changelog de correções de segurança.

## 8. CI (GitHub Actions)

| workflow | quando | o que faz | rede externa |
|---|---|---|---|
| `CI` | PR, push na `main`, manual | `lockfile` (lock atualizado), `lint` (ruff), `typecheck` (mypy + import-linter), `tests` (Ubuntu 3.11 e 3.12, Windows 3.12; perfil leve), `build` (build, regras do pacote, `twine check`, wheel isolado + CLI), `required` (agregador) | só para instalar dependências |
| `Security` | PR, push na `main`, semanal, manual | gitleaks no histórico completo, `pip-audit` do lock inteiro (runtime e dev), política de licenças | base de vulnerabilidades e metadados do PyPI |
| `Upstream watch` | semanal, manual | compara o pin com o PyPI, confere hashes auditados, abre issue se houver versão nova | PyPI |
| `Full install` | semanal, manual, PR que muda dependências | instala o lock completo (torch e CUDA no Linux), importa `torch` e `laya.Agent` sem pesos, roda a suíte | só para instalar dependências |

Todas as actions de terceiros estão fixadas por SHA de commit (o comentário indica a tag); o Dependabot as mantém
atualizadas. Os testes padrão nunca acessam Hugging Face, GPU, chaves de LLM ou serviços externos.

O `gitleaks-action` é gratuito para contas pessoais; se o repositório for para uma organização, crie o segredo
`GITLEAKS_LICENSE`.

## 9. `main` protegida (configuração recomendada)

A proteção é uma configuração administrativa do GitHub e **não** foi alterada por esta fase. Recomendação para
quando o projeto passar a ter código revisado em PRs (Settings → Rules → Rulesets → New branch ruleset, alvo
`main`):

- **Restrict deletions** e **Block force pushes**.
- **Require a pull request before merging**, com resolução obrigatória das conversas (aprovações: 0 enquanto o
  projeto tiver um único mantenedor; 1 quando houver revisores).
- **Require status checks to pass**, com a branch atualizada antes do merge, exigindo:
  - `required` (workflow `CI`) — agrega lockfile, lint, tipos, testes e build num único check estável;
  - `secret scan (gitleaks)` (workflow `Security`).
- Deixar `dependency audit (pip-audit)` e `license policy` **visíveis mas não obrigatórios** no início: um alerta
  publicado hoje para uma dependência antiga não deve bloquear um PR não relacionado. Eles também rodam
  semanalmente na `main`.
- **Require linear history** e **Require signed commits** (ambos opcionais).

Localmente, o hook `no-commit-to-branch` do pre-commit impede commits diretos na `main`.

## 10. Segredos e dados

- Nunca versionar: `.env`, chaves de API, tokens, senhas, credenciais do Hugging Face, datasets reais, pesos,
  arquivos de calibração com dados. O `.gitignore` cobre esses padrões e um teste garante que nenhum está rastreado.
- `.env.example` documenta variáveis sem nenhum valor secreto.
- Dados reais só entram na plataforma depois do gate DG-1 (`ARCHITECTURE_PROPOSAL.md` §6.15).
