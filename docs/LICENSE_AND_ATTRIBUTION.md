# Licença e atribuição

> Fase 0. Levantamento técnico das obrigações de licença ao construir sobre o Laya. **Não é parecer jurídico**;
> para uso comercial relevante, valide com um advogado.

## 1. O que o upstream declara (verificado no código)

| item | evidência | situação |
|---|---|---|
| Licença do código | `LICENSE` (Apache License 2.0, texto padrão, sem apêndice de copyright); `pyproject.toml` `license = "Apache-2.0"`; wheel 0.3.23 inclui `licenses/LICENSE` | ✅ Apache-2.0 |
| Titular / autoria | `pyproject.toml`: `authors = Convai Innovations`; README: "Apache 2.0. Developed by Convai Innovations." | ✅ |
| Arquivo `NOTICE` | não existe no commit auditado (`4aa6761`) | ✅ não há NOTICE a reproduzir |
| Cabeçalhos de copyright por arquivo | nenhum em `laya/*.py` | — |
| Contribuições | `CONTRIBUTING.md`: contribuições licenciadas sob Apache-2.0 | ✅ |
| SDKs TypeScript (`laya-ts`, `laya-client`) | `package.json`: `Apache-2.0`; `sdk/typescript/LICENSE` | ✅ |
| Subpasta com outra licença | `research/benchmarks/feishu_zh/` é **MIT** (Copyright (c) 2026 Adkid-Zephyr) | ⚠️ não usaremos; se algum dia for copiada, manter a licença MIT dela |

## 2. O que não pôde ser verificado nesta auditoria

O proxy deste ambiente bloqueia `huggingface.co`, então os cartões dos modelos e datasets não foram lidos.

| artefato | o que verificar antes de redistribuir |
|---|---|
| Pesos `convaiinnovations/laya`, `laya-multilingual`, `laya-typed-decisions` | licença no cartão do modelo (o README do código afirma "Weights: Apache 2.0") |
| Encoders base (ModernBERT-large; mmBERT-base) | licença de cada encoder, que se propaga para checkpoints derivados |
| Dataset `LocalLLaMA/typed-decisions` (usado no notebook de fine-tuning) | licença e termos de uso do dataset |
| Serviço Jev (TypeSafe) | é um serviço fechado; implementamos apenas compatibilidade de protocolo. Não usar marca/logo deles além de declarações factuais de compatibilidade |
| Dependências transitivas (torch, transformers, fastapi, mcp, crewai, langchain, llama-index, onnxruntime...) | verificação automática de licenças no CI (Fase 1) |

## 3. Obrigações por forma de uso

| como usamos o Laya | obrigações Apache-2.0 aplicáveis |
|---|---|
| **Dependência via pip** (recomendado; nenhum código do upstream no nosso repositório) | Praticamente nenhuma no repositório. Boa prática: citar o upstream no README e no `NOTICE` |
| **Imagem Docker que inclui o pacote `laya`** (redistribuição em forma de objeto) | Entregar uma cópia da licença (§4(a)) — o wheel já inclui `LICENSE` em `dist-info`; manter os avisos existentes (§4(c)) |
| **Copiar/adaptar arquivos do upstream** (ex.: playground de `examples/server.py`, loop de treino do notebook) | Manter avisos de copyright/atribuição (§4(c)); **marcar cada arquivo modificado de forma proeminente** (§4(b)); incluir a licença (§4(a)); manter em diretório identificável (`third_party/laya/`) |
| **Redistribuir pesos** (originais ou fine-tuned derivados) | Seguir a licença do cartão do modelo e dos encoders base (§2); documentar a origem no manifesto do especialista |
| **Publicar o nosso projeto** | Podemos licenciar nossas contribuições como quisermos (§4, último parágrafo), inclusive Apache-2.0 ou proprietária, desde que o uso do Laya cumpra o acima |

### Modelo de cabeçalho para arquivos adaptados do upstream

```python
# Derived from Laya (https://github.com/NandhaKishorM/laya), commit 4aa6761be8173de4ce6d92c31b3e40b6eaf59a7c,
# file <caminho original>. Copyright Convai Innovations and Laya contributors. Licensed under the Apache License 2.0.
# Modified by <seu nome>, <data>: <o que mudou>.
```

## 4. Marca (§6 da Apache-2.0)

A licença **não** concede direito de usar nomes comerciais, marcas ou logotipos do licenciante. Portanto:

- Não usar os logotipos do Laya (`assets/logo-*`).
- Deixar explícito no README que o projeto é **independente e não afiliado** à Convai Innovations nem ao projeto Laya.
- Usar "Laya" de forma descritiva ("compatível com Laya", "construído sobre o Laya").
- Não publicar pacotes em registros públicos (PyPI/npm) com nomes que possam ser confundidos com os oficiais
  (`laya-*`) sem avaliar o risco. Para uso pessoal/privado o risco é baixo; para uso comercial, considere um nome
  próprio para o produto.

## 5. Recomendação

1. **Licença do nosso código:** Apache-2.0 (compatível, inclui concessão de patente, simples de cumprir).
   Proprietária também é possível — decisão sua.
2. **Nenhum código do upstream no repositório** enquanto possível; quando inevitável, usar `third_party/laya/` com
   o cabeçalho acima.
3. Adicionar na Fase 1 um `NOTICE` próprio, por exemplo:

```text
Laya Decision Platform (nome provisório)
Copyright 2026 <seu nome>

Este produto usa o Laya (https://github.com/NandhaKishorM/laya),
desenvolvido pela Convai Innovations e colaboradores, licenciado sob a Apache License 2.0.
Este projeto é independente e não é afiliado nem endossado pela Convai Innovations.

"Jev" e "TypeSafe" pertencem aos seus respectivos titulares; a compatibilidade de
protocolo aqui descrita não implica afiliação.
```

4. Verificar no CI as licenças de todas as dependências e falhar em licenças incompatíveis com a escolhida.
5. Antes de redistribuir qualquer peso, registrar no manifesto do especialista: origem, revisão, licença do
   checkpoint, licença do encoder base e licença/termos dos dados de treino.
