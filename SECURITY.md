# Segurança

## Como reportar uma vulnerabilidade

**Não abra uma issue pública** para relatar uma falha de segurança. Use o
[relato privado de vulnerabilidades do GitHub](https://github.com/SaYm0n/Laya/security/advisories/new) e inclua:

- o que é afetado (endpoint, comando ou módulo) e a versão ou o commit;
- os passos para reproduzir, com dados sintéticos;
- o impacto esperado.

Nunca envie dados reais, chaves ou segredos no relato.

## Versões cobertas

O projeto ainda não tem versão publicada. Correções entram na `main` e valem a partir do commit seguinte.

## O que o projeto já garante

| área | garantia | onde |
|---|---|---|
| Segredos | nada de `.env`, chaves, tokens, senhas, credenciais do Hugging Face, datasets reais nem pesos no git; gitleaks em todo PR | `.gitignore`, `tests/unit/test_repo_hygiene.py`, CI |
| Chaves de API | guardadas só como SHA-256, com escopos (`decide`, `route`, `review`, `metrics`, `admin`) | `docs/DEVELOPMENT.md` §5.4 |
| Chave HMAC | só por variável de ambiente (`LAYA_PLATFORM_HMAC_KEY`, 32+ caracteres), nunca em arquivo | idem |
| Dados de entrada | a auditoria guarda o HMAC, nunca o texto; a fila de revisão não guarda a entrada | `docs/DATA_GOVERNANCE.md` |
| LLM externo | desligado por padrão; quando ligado, a PII é mascarada antes do envio | `llm.allow_external`, `llm.redact_pii` |
| Dados reais | recusados na inicialização sem a referência de aprovação do DG-1 | `data_classification`, `dg1_approval_ref` |
| Pesos de terceiros | carregados com sha256 por arquivo e revisão fixada | manifesto do especialista |
| Código de terceiros | a receita de treino do upstream é importada só se o sha256 bater com o fixado | `upstream_compat.FINETUNE_SCRIPT` |
| Dependências | lockfile com hashes, `pip-audit` e política de licenças no CI; Dependabot | `.github/workflows/security.yml` |

## Limites conhecidos

- O gateway não termina TLS: use um proxy reverso ou um balanceador.
- Não há limite de requisições por chave nem registro de leituras da auditoria (planejados para o Bloco D).
- A redação de PII cobre identificadores brasileiros comuns, mas não nomes, endereços ou detalhes livres de saúde
  (F9). Por isso, dados reais dependem da aprovação do DG-1.

A lista completa de lacunas, com prioridade, está em [docs/VALUE_AND_GAPS.md](docs/VALUE_AND_GAPS.md) §6.
