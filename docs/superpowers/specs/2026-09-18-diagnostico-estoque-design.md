# Diagnóstico do estoque atual — design

Data: 2026-09-18. Público: time de compras da Elevato, com um bloco de visão geral
para diretoria. Entrega: página `/diagnostico` no app.

## Objetivo

Uma leitura do estoque **de hoje** (ou de uma data `ate` congelada), classificando
cada SKU em uma faixa de saúde e agregando em reais por fornecedor, comprador e
família. O plano de compra responde "o que comprar"; o diagnóstico responde "o
que está parado, o que está em risco e quanto isso custa". Nada da política do
modelo é recalculado: ROP, estoque máximo, cobertura, capital e risco vêm de
`res_plano_compra`.

## Dimensionamento que motivou o escopo

Posição mais recente do DW ao custo médio do ERP (2026-09-18): CD Gravataí
(26/124) R$ 33,2 mi em 5.520 SKUs; todos os outros 169 locais R$ 7,5 mi, dos
quais ~R$ 4,3 mi em SKUs que o CD movimenta e ~R$ 1,7 mi nas empresas 41/42/43,
fora do universo do CD. Decisão: trazer **uma foto** da posição atual por SKU e
local das lojas, não o histórico diário.

## Dados novos

### Extrator (`scripts/extrair_dw.py`)

Nova tabela `raw_estoque_posicao_lojas`: posição mais recente por
`idempresa, idlocalestoque, idsubproduto` em `db2.estoque_sintetico`, restrita ao
universo do CD, com `dtmovimento, qtdatualestoque, valcustomedio`. Exclui o
próprio CD (26/124) e o trânsito fiscal do e-commerce (33/185). Uma consulta,
`distinct on` pela última data nos últimos 120 dias. Registrada em
`_extracao.txt` como as demais. Documentar em `dbt_elevato/models/sources.yml`.

### Staging

`stg_catalogo.sql` passa a expor `fornecedor` (`IDCLIFOR_FORNECEDOR`, texto) e
`comprador` (`COMPRADOROFICIAL`) na base `real`/`dw`; nas bases `sintetica` e
`exports` ambos saem `null`. Novo `stg_estoque_posicao_lojas.sql` (nulo/vazio
fora da base dw: `where false` sobre um `select` tipado).

### Mart `mart_estoque_posicao`

Um registro por SKU do catálogo, tudo na última data do `stg_estoque_diario`:

| coluna | origem |
|---|---|
| `data_posicao` | max(data) de `stg_estoque_diario` |
| `saldo_cd` | `saldo_final` nessa data |
| `custo_medio_erp` | `valcustomedio` do raw na última linha com custo > 0 (via staging) |
| `saldo_lojas`, `lojas_com_saldo`, `valor_lojas_erp` | soma de `stg_estoque_posicao_lojas` |
| `ultima_venda` | max(data) em `stg_vendas` com peças > 0 |
| `ultima_entrada` | max(`dt_entrada_estoque`) em `raw_ciclo_pagamento` |
| `idade_fifo_dias` | ver abaixo |
| `entrada_mais_antiga_em_estoque` | idem |
| `entradas_cobrem_saldo` | booleano: as entradas históricas somam pelo menos o saldo |

**Idade FIFO**: ordena as entradas de `raw_ciclo_pagamento` (`dt_entrada_estoque`,
`qtdatendida`) da mais recente para trás e acumula até cobrir `saldo_cd`. A
idade é a média das idades das entradas usadas, ponderada pela quantidade
consumida de cada uma (a última, parcialmente), medida em dias até
`data_posicao`. Se as entradas não cobrem o saldo, a fração descoberta recebe a
idade da entrada mais antiga disponível e `entradas_cobrem_saldo = false`. Sem
nenhuma entrada, idade nula. Implementado em SQL com soma acumulada em janela.

O `custo_medio_erp` vem de uma view nova `stg_custo_medio_erp` (sku, data,
custo; só dias com custo > 0 e saldo > 0), vazia fora da base real. As
entradas para o FIFO vêm de `stg_entradas` (sku, data, pecas), também por base.
Assim o mart não lê `source()` e a grade diária não muda.

## Classificação (`backend/diagnostico.py`)

Sobre `res_plano_compra` ⨝ `mart_estoque_posicao`. Posição = `posicao_estoque`
(físico + trânsito). Uma faixa por SKU, avaliada nesta ordem:

1. **Zerado com demanda**: `estoque_fisico == 0` e `demanda_media_dia > 0`.
2. **Risco**: `posicao_estoque <= ponto_de_pedido`.
3. **Sem giro**: `estoque_fisico > 0` e (`ultima_venda` nula ou mais antiga que
   `dias_sem_giro`, padrão 180, query param).
4. **Excesso**: `estoque_fisico > estoque_maximo`. `excesso_pecas =
   estoque_fisico - estoque_maximo`.
5. **Saudável**: o resto.

Colunas derivadas por SKU: `faixa`, `capital_modelo = estoque_fisico *
custo_unitario`, `capital_erp = estoque_fisico * custo_medio_erp` (nulo se sem
custo ERP), `excesso_valor`, `faixa_idade` (`até 3m`, `3-6m`, `6-12m`, `+12m`,
`sem entrada`), `capital_otimo = estoque_medio * custo_unitario`,
`acao` (texto): zerado/risco → "comprar" se `quantidade_a_comprar > 0`, senão
"transferir" se `saldo_lojas >= mu_periodo`, senão "revisar"; sem giro →
"liquidar" se `+12m`, senão "segurar"; excesso → "segurar"; saudável → "manter".

Funções puras sobre DataFrames, sem SQL, para `revisao.py` recompor:
`classificar_faixas(df, dias_sem_giro)`, `resumo_geral(df)`,
`agregar(df, por)` com `por ∈ {fornecedor, comprador, familia}`,
`rede(df)` (candidatos a transferência e excesso concentrado em loja),
`itens(df, filtros)`. Uma função `carregar(wh, ate=None, dias_sem_giro=180)`
monta o DataFrame base; com `ate`, chama `modelo.executar(ate=...)` em memória
(como `validacao` faz) em vez de ler `res_plano_compra`, e a foto de estoque é
recomputada de `mart_estoque_diario` na data; idade FIFO e saldo de lojas ficam
indisponíveis nesse modo (nulos) e a página avisa.

## Página

Rotas: `GET /diagnostico` (template) e `GET /api/diagnostico/{recurso}` com
`recurso ∈ {geral, idade, agregado?por=, rede, itens}`, todos aceitando
`ate` e `dias_sem_giro`. Cache em `_cache_diagnostico` (chave: ate,
dias_sem_giro), limpo em `invalidar_caches()`.

Blocos, de cima para baixo:

1. **Visão geral**: KPIs (capital modelo, capital ERP, capital ótimo, diferença
   real − ótimo, giro e cobertura ponderados real vs. ótimo); barra empilhada
   de reais por faixa; contagem de itens por faixa.
2. **Idade**: barras de capital por `faixa_idade`, empilhadas por `faixa`.
3. **Agregado**: abas fornecedor / comprador / família. Colunas: itens, capital
   modelo, capital ERP, por faixa em R$, excesso R$, sem giro R$, itens em risco,
   `lucro_perdido_ruptura`. Clicar filtra o bloco Itens.
4. **Rede**: tabela de risco/zerado no CD com `saldo_lojas >= mu_periodo`; e
   tabela de SKUs com `valor_lojas_erp` acima do p90 e faixa excesso/sem giro.
5. **Itens**: `N.tabela` com busca, colunas da classificação, link para o
   dossiê do item. Botão CSV → `GET /diagnostico.csv` (mesmo padrão de
   `conferencia.csv`).
6. **Confiança**: badge por bloco. Fonte: `qualidade.verificar()` (níveis dos
   grupos de estoque/custo/compras) e contagem de itens sinalizados em
   `outliers` dentro do recorte.

Frontend em `static/diagnostico.js` com `N.grafico`/`N.tabela`/`N.buscar`.
Entrada no menu de `base.html`. Verbete em `static/glossario.js` para
"faixa", "idade FIFO", "capital ótimo".

## Testes

Bloco 9 em `scripts/revisao.py`:

- recompõe as faixas em numpy a partir de `res_plano_compra` +
  `mart_estoque_posicao` e confere 100% de igualdade com
  `diagnostico.classificar_faixas`;
- recompõe a idade FIFO em Python puro para uma amostra de 300 SKUs e confere
  o mart (tolerância 0,5 dia);
- somas por fornecedor, comprador e família batem com o total geral;
- todo SKU do catálogo tem exatamente uma linha no mart;
- `saldo_cd` do mart é igual a `estoque_fisico` de `res_plano_compra`.

`scripts/conferir.py` não muda. Rodar `revisao.py` e `conferir.py` ao final.

## Fora de escopo

Histórico diário das lojas; estoque das empresas 41/42/43; qualquer alteração
em `modelo.py` além de nenhuma (o diagnóstico só lê); PowerPoint.
