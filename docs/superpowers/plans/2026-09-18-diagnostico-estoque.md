# Diagnóstico do estoque atual — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Uma página `/diagnostico` que classifica cada SKU do CD em uma faixa de saúde (zerado / risco / sem giro / excesso / saudável), valoriza o estoque de hoje nas duas leituras de custo (modelo e contábil), mede a idade FIFO do saldo, agrega por fornecedor, comprador e família, e aponta o que há na rede de lojas.

**Architecture:** Dados novos entram pelo caminho padrão do repo: extrator → `raw_*` → staging (`stg_*`, um ramo por base) → mart (`mart_estoque_posicao`, um registro por SKU, format-agnóstico). A leitura é um módulo novo `backend/diagnostico.py` com funções puras sobre DataFrames (para `scripts/revisao.py` recompor) e uma `carregar()` que cruza `res_plano_compra` (política do modelo, não recalculada) com o mart. Rotas em `backend/main.py` seguem o par `GET /pagina` + `GET /api/pagina/recurso`; frontend em `templates/diagnostico.html` + `static/diagnostico.js` com os primitivos de `static/nucleo.js`.

**Tech Stack:** Python 3.11, pandas/numpy, DuckDB, dbt-duckdb, FastAPI + Jinja2, ECharts 5.5.1 (já carregado por `base.html`), psycopg (extrator, Postgres `dwanalitico`).

**Spec:** `docs/superpowers/specs/2026-09-18-diagnostico-estoque-design.md`

## Global Constraints

- **Outra sessão do Claude está editando a árvore principal** (prazo de pagamento/recebimento por item): `backend/main.py`, `backend/analitico.py`, `scripts/revisao.py`, `scripts/extrair_dw.py`, `scripts/rodar_pipeline.py`, `dbt_elevato/models/staging/stg_catalogo.sql`, `sources.yml`, `schema.yml`, `static/item.js`, `static/glossario.js` têm alterações não commitadas dela. **Todo o trabalho deste plano acontece num worktree** (Task 0) sobre a branch `diagnostico-estoque`, com cópia própria do DuckDB. Nunca `git add` na árvore principal. Edições nos arquivos compartilhados devem ser **aditivas e localizadas** (uma entrada nova numa lista, um bloco novo no fim) para o merge posterior ser trivial.
- `backend/modelo.py` **não muda**. O diagnóstico só lê `res_plano_compra`.
- Nenhum `mart_*` lê `source()` diretamente: toda leitura de `raw_*` fica em `stg_*`, com um ramo por base (`real`, `exports`, `sintetica`) que devolve **as mesmas colunas tipadas** (vazio via `where false` quando a base não tem o dado).
- Toda consulta multi-passo é **uma** string SQL com CTEs (cada `wh.query()` abre conexão nova). Nomes de tabela sempre via `ref('nome')`.
- dbt roda com `cwd=dbt_elevato`, `DBT_PROFILES_DIR=dbt_elevato`, `DUCKDB_PATH=../data/elevato.duckdb`. Vars: `--vars '{"base": "real", "posicao_lojas": true}'`.
- Comentários e textos de tela em português, sem acento nos comentários de SQL/Python (convenção do repo), com acento nos textos exibidos.
- Não há pytest. Verificação = `python scripts/revisao.py` (bloco 9 novo) e `python scripts/conferir.py --linhas 800`, mais checagens `python -c` nos passos.
- Commits terminam com `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

### Task 0: Worktree isolado com cópia do banco

**Files:**
- Create: worktree em `../inventory_control-diagnostico` (branch `diagnostico-estoque`, a partir de `main` = `b1e017e`)
- Create (não versionados): `../inventory_control-diagnostico/data/elevato.duckdb` (cópia), `../inventory_control-diagnostico/data/fonte_dw` (symlink), `../inventory_control-diagnostico/.env` (cópia)

**Interfaces:**
- Produces: caminho `WT=/Users/dougzec/Documents/GitHub/inventory_control-diagnostico` usado por todas as tasks seguintes. Todo comando abaixo assume `cd $WT`.

- [ ] **Step 1: Criar worktree e branch**

```bash
cd /Users/dougzec/Documents/GitHub/inventory_control
git worktree add -b diagnostico-estoque ../inventory_control-diagnostico main
```

- [ ] **Step 2: Copiar banco, .env e apontar a pasta do extrato**

```bash
WT=/Users/dougzec/Documents/GitHub/inventory_control-diagnostico
mkdir -p $WT/data
cp data/elevato.duckdb $WT/data/elevato.duckdb
cp .env $WT/.env
cp data/parametros.json $WT/data/parametros.json
cp data/ajustes_sku.json $WT/data/ajustes_sku.json
ln -s /Users/dougzec/Documents/GitHub/inventory_control/data/fonte_dw $WT/data/fonte_dw
```

- [ ] **Step 3: Verificar que a cópia abre e tem os resultados**

```bash
cd $WT && python -c "
from backend.warehouse import abrir; w=abrir()
print(w.caminho); print(w.existe('res_plano_compra'), w.query('select count(*) n from res_plano_compra').iloc[0].n)"
```
Expected: caminho termina em `inventory_control-diagnostico/data/elevato.duckdb`, `True 19141`.

- [ ] **Step 4: Confirmar que o dbt compila no worktree**

```bash
cd $WT/dbt_elevato && DBT_PROFILES_DIR=. DUCKDB_PATH=../data/elevato.duckdb dbt compile --vars '{"base": "real"}' | tail -3
```
Expected: `Done.` sem erro.

---

### Task 1: Extrator — foto da posição das lojas

**Files:**
- Modify: `scripts/extrair_dw.py` (nova constante `SQL_POSICAO_LOJAS` antes de `TABELAS`; nova entrada em `TABELAS`)
- Modify: `scripts/rodar_pipeline.py` (`FONTES["dw"]` ganha `("raw_estoque_posicao_lojas", "raw_estoque_posicao_lojas")`; `rodar_dbt` passa a var `posicao_lojas`)
- Modify: `dbt_elevato/models/sources.yml` (documenta `raw_estoque_posicao_lojas`)

**Interfaces:**
- Produces: tabela `raw_estoque_posicao_lojas` no DuckDB com colunas `idempresa int, idlocalestoque int, idsubproduto bigint, dtmovimento date, qtdatualestoque double, valcustomedio double`. Uma linha por (empresa, local, subproduto); exclui CD 26/124 e trânsito 33/185; só SKUs do universo do CD; só saldo diferente de zero.

- [ ] **Step 1: Adicionar a consulta e a entrada em `TABELAS`**

Em `scripts/extrair_dw.py`, logo antes de `TABELAS = {`:

```python
# Foto da posicao ATUAL nas lojas, um registro por (empresa, local, SKU).
# Nao e grade diaria de proposito: as lojas somam ~R$ 7,5 mi contra R$ 33 mi
# do CD (medido 2026-09-18), pulverizados em 169 locais - o historico
# multiplicaria os 21 milhoes de linhas do estoque do CD por pouco ganho. O
# diagnostico so precisa saber QUANTO ha na rede hoje, para apontar
# transferencia em vez de compra e excesso parado em loja. A ultima linha de
# cada local nos ultimos 120 dias e a posicao; local sem movimento ha mais de
# 120 dias fica fora (saldo velho demais para valer como posicao).
SQL_POSICAO_LOJAS = f"""
with {UNIVERSO},
ult as (
    select distinct on (e.idempresa, e.idlocalestoque, e.idsubproduto)
           e.idempresa, e.idlocalestoque, e.idsubproduto,
           e.dtmovimento, e.qtdatualestoque, e.valcustomedio
    from db2.estoque_sintetico e
    join universo u on u.idsubproduto = e.idsubproduto
    where e.dtmovimento >= %(dt_fim)s::date - interval '120 days'
      and not (e.idempresa = {EMPRESA_CD} and e.idlocalestoque = {LOCAL_CD})
      and not (e.idempresa = {EMPRESA_ECOMMERCE} and e.idlocalestoque = 185)
    order by e.idempresa, e.idlocalestoque, e.idsubproduto, e.dtmovimento desc
)
select idempresa, idlocalestoque, idsubproduto,
       dtmovimento::date as dtmovimento,
       qtdatualestoque, valcustomedio
from ult
where qtdatualestoque <> 0
order by idempresa, idlocalestoque, idsubproduto
"""
```

E em `TABELAS`, última linha:

```python
    "raw_estoque_posicao_lojas": ("simples", SQL_POSICAO_LOJAS),
```

- [ ] **Step 2: Registrar a tabela na base `dw` e a var do dbt em `rodar_pipeline.py`**

Em `FONTES["dw"]`, acrescentar ao fim da lista:

```python
        ("raw_estoque_posicao_lojas", "raw_estoque_posicao_lojas"),
```

Em `rodar_dbt`, trocar a linha do `subprocess.run` para passar as duas vars (a foto das lojas so existe na extracao direta do DW):

```python
    import json
    vars_dbt = {"base": STAGING[base], "posicao_lojas": base == "dw"}
    r = subprocess.run(["dbt", "build", "--vars", json.dumps(vars_dbt)],
                       cwd=dbt_dir, env=env, capture_output=True, text=True)
```

- [ ] **Step 3: Documentar em `sources.yml`**

Depois do bloco de `raw_ciclo_recebimento`, dentro de `tables:`:

```yaml
      - name: raw_estoque_posicao_lojas
        description: >
          Foto da posicao ATUAL de estoque fora do CD: um registro por
          (empresa, local, SKU) com a ultima linha de db2.estoque_sintetico
          nos 120 dias anteriores a extracao, so para SKUs do universo do CD e
          so com saldo diferente de zero. Exclui o proprio CD (26/124) e o
          transito fiscal do e-commerce (33/185). Nao e serie diaria: alimenta
          o diagnostico do estoque (saldo na rede por SKU). So existe na base
          `dw` (var posicao_lojas=true).
```

- [ ] **Step 4: Extrair e carregar no banco do worktree**

```bash
cd $WT && python scripts/extrair_dw.py --so raw_estoque_posicao_lojas
python -c "
from backend.warehouse import abrir; from pathlib import Path
w=abrir(); n=w.carregar_csv(Path('data/fonte_dw/raw_estoque_posicao_lojas'), 'raw_estoque_posicao_lojas'); print(n)
print(w.query('select count(distinct idlocalestoque) locais, sum(qtdatualestoque*valcustomedio) valor from raw_estoque_posicao_lojas'))"
```
Expected: algumas dezenas de milhares de linhas; ~150-170 locais; valor na casa de R$ 5-8 mi.

- [ ] **Step 5: Commit**

```bash
cd $WT && git add scripts/extrair_dw.py scripts/rodar_pipeline.py dbt_elevato/models/sources.yml
git commit -m "extrator: foto da posicao de estoque das lojas (raw_estoque_posicao_lojas)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Staging — fornecedor/comprador, posição das lojas, custo médio ERP, entradas

**Files:**
- Modify: `dbt_elevato/models/staging/stg_catalogo.sql` (colunas `fornecedor`, `comprador` nos três ramos)
- Create: `dbt_elevato/models/staging/stg_estoque_posicao_lojas.sql`
- Create: `dbt_elevato/models/staging/stg_custo_medio_erp.sql`
- Create: `dbt_elevato/models/staging/stg_entradas.sql`

**Interfaces:**
- Produces (views):
  - `stg_catalogo` + `fornecedor varchar` (nome do fornecedor pelo pedido de compra mais recente do id; fallback `'Fornecedor ' || id`; `'Nao informado'` sem id) + `comprador varchar` (`COMPRADOROFICIAL`; `'Nao informado'` se nulo).
  - `stg_estoque_posicao_lojas(sku varchar, empresa int, local int, data date, saldo double, custo_medio double)`
  - `stg_custo_medio_erp(sku varchar, data date, custo double)` — só dias com custo > 0 e saldo > 0
  - `stg_entradas(sku varchar, data date, pecas double)` — entradas de mercadoria no CD (`raw_ciclo_pagamento`), `pecas > 0`

- [ ] **Step 1: `stg_catalogo` — ramo `real`**

Acrescentar uma CTE depois de `cad`:

```sql
fornecedor_nome as (
    -- o cadastro so tem o ID do fornecedor; o nome vem do pedido de compra
    -- mais recente daquele id (332 ids, 343 grafias - fica a ultima)
    select idclifor, fornecedor
    from (
        select cast(idclifor as bigint) as idclifor, cast(fornecedor as varchar) as fornecedor,
               row_number() over (partition by idclifor order by dtmovimento desc) as rn
        from {{ source('raw', 'raw_compras') }}
        where idclifor is not null and fornecedor is not null
    ) where rn = 1
),
```

Alterar a CTE `cad` para trazer os dois campos:

```sql
cad as (
    select cast(IDSUBPRODUTO as varchar) as sku,
           cast(DESCRCOMPRODUTO as varchar) as item,
           cast(DESCRSECAO as varchar)      as familia,
           cast(UNMEDIDA as varchar)        as unidade,
           cast(FABRICANTE as varchar)      as origem,
           try_cast(IDCLIFOR_FORNECEDOR as bigint) as id_fornecedor,
           cast(COMPRADOROFICIAL as varchar) as comprador
    from {{ source('raw', 'raw_produtos') }}
),
```

No `select` final, logo depois de `coalesce(cad.origem, 'Nao informado') as origem,`:

```sql
    -- dimensoes do time de compras (diagnostico do estoque): quem vende e
    -- quem compra. 1.773 dos 19.140 itens nao tem fornecedor no cadastro.
    coalesce(fn.fornecedor,
             case when cad.id_fornecedor is not null
                  then 'Fornecedor ' || cast(cad.id_fornecedor as varchar) end,
             'Nao informado')                        as fornecedor,
    coalesce(cad.comprador, 'Nao informado')         as comprador,
```

E o join, depois de `left join cad on cad.sku = u.sku`:

```sql
left join fornecedor_nome fn on fn.idclifor = cad.id_fornecedor
```

- [ ] **Step 2: `stg_catalogo` — ramos `exports` e `sintetica`**

No ramo `exports`, depois de `coalesce(a.origem, 'Nao informado') as origem,`:

```sql
    cast(null as varchar)                            as fornecedor,
    cast(null as varchar)                            as comprador,
```

No ramo `sintetica`, depois de `cast(origem as varchar) as origem,`:

```sql
    cast(null as varchar)               as fornecedor,
    cast(null as varchar)               as comprador,
```

- [ ] **Step 3: Criar `stg_estoque_posicao_lojas.sql`**

```sql
{#
  Foto da posicao de estoque FORA do CD, um registro por (empresa, local, SKU).
  So existe na extracao direta do DW (var posicao_lojas=true); nas outras
  bases a view sai vazia com as mesmas colunas, e o mart soma zero.
#}

{% if var('base', 'sintetica') == 'real' and var('posicao_lojas', false) %}

select
    cast(idsubproduto as varchar)       as sku,
    cast(idempresa as integer)          as empresa,
    cast(idlocalestoque as integer)     as local,
    cast(dtmovimento as date)           as data,
    cast(qtdatualestoque as double)     as saldo,
    cast(valcustomedio as double)       as custo_medio
from {{ source('raw', 'raw_estoque_posicao_lojas') }}

{% else %}

select
    cast(null as varchar) as sku,
    cast(null as integer) as empresa,
    cast(null as integer) as local,
    cast(null as date)    as data,
    cast(null as double)  as saldo,
    cast(null as double)  as custo_medio
where false

{% endif %}
```

- [ ] **Step 4: Criar `stg_custo_medio_erp.sql`**

```sql
{#
  Custo medio CONTABIL do ERP por SKU e dia, so nos dias em que havia estoque
  (quando o saldo zera o ERP zera o custo junto e lanca R$ 1,00 - ver
  stg_catalogo). E a segunda valorizacao do diagnostico: o custo que a
  contabilidade ve, ao lado do custo que o modelo usa.
#}

{% if var('base', 'sintetica') == 'real' %}

select
    cast(idsubproduto as varchar) as sku,
    cast(dtmovimento as date)     as data,
    cast(valcustomedio as double) as custo
from {{ source('raw', 'raw_estoque_diario_erp') }}
where valcustomedio > 0
  and cast(qtdatualestoque as double) > 0

{% else %}

select cast(null as varchar) as sku, cast(null as date) as data, cast(null as double) as custo
where false

{% endif %}
```

- [ ] **Step 5: Criar `stg_entradas.sql`**

```sql
{#
  Entradas de mercadoria no CD: quando e quantas pecas. Alimenta a idade FIFO
  do saldo atual (mart_estoque_posicao). Vem do ciclo de pagamento porque e a
  unica fonte com a DATA DE ENTRADA NO ESTOQUE por nota.
#}

{% if var('base', 'sintetica') == 'real' %}

select
    cast(idsubproduto as varchar)        as sku,
    cast(dt_entrada_estoque as date)     as data,
    cast(qtdatendida as double)          as pecas
from {{ source('raw', 'raw_ciclo_pagamento') }}
where dt_entrada_estoque is not null
  and cast(qtdatendida as double) > 0

{% else %}

select cast(null as varchar) as sku, cast(null as date) as data, cast(null as double) as pecas
where false

{% endif %}
```

- [ ] **Step 6: Construir as views e conferir**

```bash
cd $WT/dbt_elevato && DBT_PROFILES_DIR=. DUCKDB_PATH=../data/elevato.duckdb \
  dbt build --vars '{"base": "real", "posicao_lojas": true}' \
  --select stg_catalogo stg_estoque_posicao_lojas stg_custo_medio_erp stg_entradas | tail -8
cd $WT && python -c "
from backend.warehouse import abrir; w=abrir()
print(w.query('select count(distinct fornecedor) f, count(distinct comprador) c, count(*) filter (where fornecedor=\'Nao informado\') sem from stg_catalogo'))
print(w.query('select count(*) n, count(distinct sku) skus from stg_estoque_posicao_lojas'))
print(w.query('select count(*) n from stg_entradas'), w.query('select count(*) n from stg_custo_medio_erp'))"
```
Expected: ~200 fornecedores, ~8 compradores, ~1.700 sem fornecedor; posição das lojas com milhares de SKUs; entradas ~106 mil; custo ERP na casa de milhões de linhas.

- [ ] **Step 7: Commit**

```bash
cd $WT && git add dbt_elevato/models/staging/
git commit -m "staging: fornecedor/comprador no catalogo; posicao das lojas, custo medio ERP e entradas como views por base

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `mart_estoque_posicao` com idade FIFO

**Files:**
- Create: `dbt_elevato/models/marts/mart_estoque_posicao.sql`
- Modify: `dbt_elevato/models/marts/schema.yml` (entrada nova com testes)

**Interfaces:**
- Produces: tabela `mart_estoque_posicao`, uma linha por SKU de `stg_catalogo`:

| coluna | tipo | significado |
|---|---|---|
| `sku` | varchar | |
| `fornecedor`, `comprador` | varchar | de `stg_catalogo` |
| `data_posicao` | date | `max(data)` de `stg_estoque_diario` |
| `saldo_cd` | double | `saldo_final` em `data_posicao` (0 se sem linha) |
| `custo_medio_erp` | double / null | último custo de `stg_custo_medio_erp` até `data_posicao` |
| `saldo_lojas` | double | soma de `saldo` positivo em `stg_estoque_posicao_lojas` |
| `lojas_com_saldo` | int | locais com saldo > 0 |
| `valor_lojas_erp` | double | soma de `saldo × custo_medio` (saldo > 0) |
| `ultima_venda` | date / null | `max(data)` em `stg_vendas` com `pecas_vendidas > 0` e `data <= data_posicao` |
| `ultima_entrada` | date / null | `max(data)` em `stg_entradas` até `data_posicao` |
| `idade_fifo_dias` | double / null | ver spec: média ponderada das idades das entradas que cobrem o saldo |
| `entrada_mais_antiga_em_estoque` | date / null | data da entrada mais antiga ainda consumida pelo FIFO |
| `entradas_cobrem_saldo` | boolean | soma das entradas ≥ saldo |

- [ ] **Step 1: Escrever o mart**

```sql
{#
  Foto do estoque de HOJE por item, para o diagnostico: quanto ha no CD e na
  rede, a que custo (modelo x contabil), ha quanto tempo esta parado.

  IDADE FIFO. O ERP nao guarda lote. A aproximacao: as pecas que estao na
  prateleira sao as das ULTIMAS entradas - percorre-se as entradas da mais
  recente para tras ate cobrir o saldo. A idade e a media das idades das
  entradas usadas, ponderada pela quantidade que cada uma contribui (a ultima
  entra parcialmente). Quando as entradas registradas nao cobrem o saldo
  (item que ja estava em estoque antes da janela do extrato), a fracao
  descoberta recebe a idade da entrada mais antiga disponivel e
  `entradas_cobrem_saldo` denuncia. E aproximacao: separa "parado ha 3 meses"
  de "parado ha 2 anos", que e o que o diagnostico precisa.
#}

with pos as (
    select max(data) as data_posicao from {{ ref('stg_estoque_diario') }}
),

saldo as (
    select e.sku, e.saldo_final as saldo_cd
    from {{ ref('stg_estoque_diario') }} e
    cross join pos
    where e.data = pos.data_posicao
),

custo as (
    select sku, custo as custo_medio_erp
    from (
        select c.sku, c.custo,
               row_number() over (partition by c.sku order by c.data desc) as rn
        from {{ ref('stg_custo_medio_erp') }} c
        cross join pos
        where c.data <= pos.data_posicao
    ) where rn = 1
),

lojas as (
    select sku,
           sum(saldo)                                         as saldo_lojas,
           count(distinct empresa * 100000 + local)           as lojas_com_saldo,
           sum(saldo * coalesce(custo_medio, 0))              as valor_lojas_erp
    from {{ ref('stg_estoque_posicao_lojas') }}
    where saldo > 0
    group by sku
),

venda as (
    select v.sku, max(v.data) as ultima_venda
    from {{ ref('stg_vendas') }} v
    cross join pos
    where v.pecas_vendidas > 0 and v.data <= pos.data_posicao
    group by v.sku
),

entradas as (
    select e.sku, e.data, e.pecas
    from {{ ref('stg_entradas') }} e
    cross join pos
    where e.data <= pos.data_posicao
),

ult_entrada as (
    select sku, max(data) as ultima_entrada, min(data) as primeira_entrada,
           sum(pecas) as pecas_entradas
    from entradas
    group by sku
),

-- FIFO: acumula da entrada mais recente para tras
acum as (
    select e.sku, e.data, e.pecas,
           sum(e.pecas) over (partition by e.sku order by e.data desc, e.pecas
                              rows between unbounded preceding and current row) as acumulado
    from entradas e
),

consumo as (
    select a.sku, a.data, a.pecas,
           least(a.pecas, greatest(s.saldo_cd - (a.acumulado - a.pecas), 0)) as consumido
    from acum a
    join saldo s on s.sku = a.sku
    where s.saldo_cd > 0
      and a.acumulado - a.pecas < s.saldo_cd
),

fifo as (
    select c.sku,
           sum(c.consumido)                                                    as coberto,
           sum(c.consumido * datediff('day', c.data, pos.data_posicao))        as soma_idade,
           min(c.data)                                                         as entrada_mais_antiga_em_estoque
    from consumo c
    cross join pos
    group by c.sku
)

select
    c.sku,
    c.fornecedor,
    c.comprador,
    pos.data_posicao,
    coalesce(s.saldo_cd, 0)                     as saldo_cd,
    cu.custo_medio_erp,
    coalesce(l.saldo_lojas, 0)                  as saldo_lojas,
    coalesce(l.lojas_com_saldo, 0)              as lojas_com_saldo,
    coalesce(l.valor_lojas_erp, 0)              as valor_lojas_erp,
    v.ultima_venda,
    ue.ultima_entrada,
    case
        when coalesce(s.saldo_cd, 0) <= 0 or f.sku is null then null
        when f.coberto >= s.saldo_cd - 1e-9 then f.soma_idade / f.coberto
        -- fracao descoberta herda a idade da entrada mais antiga disponivel
        else (f.soma_idade
              + (s.saldo_cd - f.coberto) * datediff('day', ue.primeira_entrada, pos.data_posicao))
             / s.saldo_cd
    end                                         as idade_fifo_dias,
    f.entrada_mais_antiga_em_estoque,
    case when coalesce(s.saldo_cd, 0) <= 0 then true
         when f.sku is null then false
         else f.coberto >= s.saldo_cd - 1e-9 end as entradas_cobrem_saldo
from {{ ref('stg_catalogo') }} c
cross join pos
left join saldo s        on s.sku = c.sku
left join custo cu       on cu.sku = c.sku
left join lojas l        on l.sku = c.sku
left join venda v        on v.sku = c.sku
left join ult_entrada ue on ue.sku = c.sku
left join fifo f         on f.sku = c.sku
```

- [ ] **Step 2: Testes no `schema.yml`**

Acrescentar ao fim da lista `models:`:

```yaml
  - name: mart_estoque_posicao
    description: >
      Foto do estoque de hoje por item para o diagnostico: saldo no CD e na
      rede, custo medio contabil, ultima venda, ultima entrada e idade FIFO do
      saldo (aproximacao pelas ultimas entradas).
    columns:
      - name: sku
        tests: [unique, not_null]
      - name: saldo_cd
        tests: [not_null]
      - name: data_posicao
        tests: [not_null]
```

- [ ] **Step 3: Construir e verificar propriedades**

```bash
cd $WT/dbt_elevato && DBT_PROFILES_DIR=. DUCKDB_PATH=../data/elevato.duckdb \
  dbt build --vars '{"base": "real", "posicao_lojas": true}' --select mart_estoque_posicao | tail -6
cd $WT && python -c "
from backend.warehouse import abrir; w=abrir()
print(w.query('''select count(*) n, sum(saldo_cd>0) com_saldo, sum(idade_fifo_dias is null and saldo_cd>0) sem_idade,
  sum(not entradas_cobrem_saldo) descobertos, median(idade_fifo_dias) idade_mediana, sum(saldo_lojas>0) na_rede
  from mart_estoque_posicao'''))
# saldo do mart == estoque_fisico do plano
print(w.query('''select sum(abs(m.saldo_cd - p.estoque_fisico) > 1e-6) dif from mart_estoque_posicao m join res_plano_compra p using(sku)'''))
# idade nunca negativa e nunca maior que a janela
print(w.query('select min(idade_fifo_dias) mn, max(idade_fifo_dias) mx from mart_estoque_posicao'))"
```
Expected: `n = 19141`, `dif = 0`, `mn >= 0`, `mx <= ~1100`, idade mediana plausível (dezenas a centenas de dias).

- [ ] **Step 4: Commit**

```bash
cd $WT && git add dbt_elevato/models/marts/mart_estoque_posicao.sql dbt_elevato/models/marts/schema.yml
git commit -m "mart_estoque_posicao: foto do estoque por item com idade FIFO, saldo na rede e custo contabil

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `backend/diagnostico.py` — classificação pura

**Files:**
- Create: `backend/diagnostico.py`

**Interfaces:**
- Produces:
  - `FAIXAS = ["Zerado com demanda", "Risco", "Sem giro", "Excesso", "Saudável"]`
  - `FAIXAS_IDADE = ["até 3 meses", "3 a 6 meses", "6 a 12 meses", "mais de 12 meses", "sem entrada"]`
  - `ACOES = {"comprar", "transferir", "revisar", "liquidar", "segurar", "manter"}`
  - `classificar_faixas(df: pd.DataFrame, dias_sem_giro: int = 180) -> pd.DataFrame` — recebe um DataFrame com as colunas de entrada listadas abaixo e devolve uma cópia com as colunas derivadas. Puro (sem SQL).
  - Entrada mínima: `sku, estoque_fisico, posicao_estoque, demanda_media_dia, ponto_de_pedido, estoque_maximo, estoque_medio, custo_unitario, quantidade_a_comprar, mu_periodo, ultima_venda, data_posicao, idade_fifo_dias, custo_medio_erp, saldo_lojas`.
  - Saída acrescenta: `faixa, dias_sem_venda (float, inf se nunca vendeu), capital_modelo, capital_erp (NaN sem custo ERP), excesso_pecas, excesso_valor, faixa_idade, capital_otimo, acao`.

- [ ] **Step 1: Escrever a checagem sintética (falha antes da implementação)**

Salvar em `$WT/../scratch_diag_check.py` (fora do repo; é o teste de mesa da função) — ou rodar inline:

```bash
cd $WT && cat > /tmp/diag_check.py <<'EOF'
import sys; sys.path.insert(0, ".")
import numpy as np, pandas as pd
from backend.diagnostico import classificar_faixas, FAIXAS, FAIXAS_IDADE

hoje = pd.Timestamp("2026-09-17")
df = pd.DataFrame({
    "sku": ["zerado", "risco", "semgiro", "excesso", "saudavel", "semdemanda_zero", "risco_transf"],
    "estoque_fisico":    [0,   2,  10,  50,  10,  0,   1],
    "posicao_estoque":   [0,   2,  10,  50,  10,  0,   1],
    "demanda_media_dia": [1.0, 1.0, 0.1, 1.0, 1.0, 0.0, 1.0],
    "ponto_de_pedido":   [5,   5,  1,   5,   5,   0,   5],
    "estoque_maximo":    [20,  20, 20,  20,  20,  0,   20],
    "estoque_medio":     [12,  12, 5,   12,  12,  0,   12],
    "custo_unitario":    [10., 10., 10., 10., 10., 10., 10.],
    "quantidade_a_comprar": [3, 0, 0, 0, 0, 0, 0],
    "mu_periodo":        [20., 20., 2., 20., 20., 0., 20.],
    "ultima_venda":      [hoje, hoje, hoje - pd.Timedelta(days=400), hoje, hoje, pd.NaT, hoje],
    "data_posicao":      [hoje] * 7,
    "idade_fifo_dias":   [np.nan, 10., 500., 100., 200., np.nan, 10.],
    "custo_medio_erp":   [12., np.nan, 12., 12., 12., 12., 12.],
    "saldo_lojas":       [0., 0., 0., 0., 0., 0., 40.],
})
r = classificar_faixas(df, dias_sem_giro=180).set_index("sku")
assert list(r.loc[["zerado","risco","semgiro","excesso","saudavel"], "faixa"]) == FAIXAS, r.faixa.tolist()
assert r.loc["semdemanda_zero", "faixa"] == "Saudável"           # zerado sem demanda nao e ruptura
assert r.loc["risco_transf", "faixa"] == "Risco"
assert r.loc["excesso", "excesso_pecas"] == 30 and r.loc["excesso", "excesso_valor"] == 300.0
assert r.loc["semgiro", "excesso_pecas"] == 0                     # so a faixa Excesso tem excesso
assert r.loc["saudavel", "capital_modelo"] == 100.0 and r.loc["saudavel", "capital_erp"] == 120.0
assert np.isnan(r.loc["risco", "capital_erp"])
assert r.loc["saudavel", "capital_otimo"] == 120.0
assert list(r.loc[["risco","excesso","saudavel","semgiro","zerado"], "faixa_idade"]) == \
       ["até 3 meses", "3 a 6 meses", "6 a 12 meses", "mais de 12 meses", "sem entrada"]
assert np.isinf(r.loc["semdemanda_zero", "dias_sem_venda"]) and r.loc["semgiro", "dias_sem_venda"] == 400
assert dict(r.acao) == {"zerado": "comprar", "risco": "revisar", "semgiro": "liquidar",
                        "excesso": "segurar", "saudavel": "manter", "semdemanda_zero": "manter",
                        "risco_transf": "transferir"}, dict(r.acao)
print("OK classificar_faixas")
EOF
python /tmp/diag_check.py
```
Expected: `ModuleNotFoundError: No module named 'backend.diagnostico'`.

- [ ] **Step 2: Implementar `backend/diagnostico.py` (parte pura)**

```python
# -*- coding: utf-8 -*-
"""
Diagnostico do estoque ATUAL: em que estado cada item esta hoje e quanto
dinheiro ha em cada estado.

O plano de compra responde "o que comprar"; aqui a pergunta e "o que esta
parado, o que esta em risco e quanto isso custa". Nada da politica e
recalculado: ponto de pedido, estoque maximo, estoque medio, cobertura e
risco vem de res_plano_compra. Este modulo so LE e classifica.

Funcoes puras sobre DataFrame (classificar_faixas, resumo_geral, agregar,
por_idade, rede, itens) para scripts/revisao.py poder recompor cada numero
por fora; `carregar()` e o unico ponto que fala com o warehouse.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .warehouse import Warehouse, ref

# ordem = prioridade: um item que esta zerado E acima do maximo (demanda alta,
# maximo pequeno) e ruptura, nao excesso
FAIXAS = ["Zerado com demanda", "Risco", "Sem giro", "Excesso", "Saudável"]
FAIXAS_IDADE = ["até 3 meses", "3 a 6 meses", "6 a 12 meses", "mais de 12 meses", "sem entrada"]
ACOES = {"comprar", "transferir", "revisar", "liquidar", "segurar", "manter"}
DIAS_SEM_GIRO_PADRAO = 180

COLUNAS_ENTRADA = [
    "sku", "estoque_fisico", "posicao_estoque", "demanda_media_dia", "ponto_de_pedido",
    "estoque_maximo", "estoque_medio", "custo_unitario", "quantidade_a_comprar",
    "mu_periodo", "ultima_venda", "data_posicao", "idade_fifo_dias", "custo_medio_erp",
    "saldo_lojas",
]


def _faixa_idade(idade: pd.Series) -> pd.Series:
    i = pd.to_numeric(idade, errors="coerce")
    return pd.Series(
        np.select(
            [i.isna(), i <= 90, i <= 180, i <= 365],
            [FAIXAS_IDADE[4], FAIXAS_IDADE[0], FAIXAS_IDADE[1], FAIXAS_IDADE[2]],
            default=FAIXAS_IDADE[3]),
        index=idade.index)


def classificar_faixas(df: pd.DataFrame, dias_sem_giro: int = DIAS_SEM_GIRO_PADRAO) -> pd.DataFrame:
    """Uma faixa por item, avaliada nesta ordem:

    1. Zerado com demanda  fisico = 0 e demanda corrigida > 0 (ruptura hoje)
    2. Risco               posicao (fisico + transito) <= ponto de pedido
    3. Sem giro            fisico > 0 e sem venda ha mais de `dias_sem_giro`
    4. Excesso             fisico > estoque maximo do modelo
    5. Saudavel            o resto

    Tudo sobre o estoque do CD, que e onde a politica se aplica. `saldo_lojas`
    so entra na acao sugerida (transferir em vez de comprar).
    """
    faltam = [c for c in COLUNAS_ENTRADA if c not in df.columns]
    if faltam:
        raise KeyError(f"classificar_faixas: faltam colunas {faltam}")
    d = df.copy()
    fis = d.estoque_fisico.fillna(0).astype(float)
    pos = d.posicao_estoque.fillna(fis).astype(float)
    dem = d.demanda_media_dia.fillna(0).astype(float)
    rop = d.ponto_de_pedido.fillna(0).astype(float)
    mx = d.estoque_maximo.fillna(0).astype(float)
    custo = d.custo_unitario.fillna(0).astype(float)

    data_pos = pd.to_datetime(d.data_posicao)
    ult = pd.to_datetime(d.ultima_venda, errors="coerce")
    dias = (data_pos - ult).dt.days.astype(float)
    d["dias_sem_venda"] = dias.where(ult.notna(), np.inf)

    zerado = (fis <= 0) & (dem > 0)
    risco = ~zerado & (pos <= rop)
    sem_giro = ~zerado & ~risco & (fis > 0) & (d.dias_sem_venda > dias_sem_giro)
    excesso = ~zerado & ~risco & ~sem_giro & (fis > mx)
    d["faixa"] = np.select([zerado, risco, sem_giro, excesso],
                           FAIXAS[:4], default=FAIXAS[4])

    d["capital_modelo"] = fis * custo
    d["capital_erp"] = fis * pd.to_numeric(d.custo_medio_erp, errors="coerce")
    d["excesso_pecas"] = np.where(excesso, fis - mx, 0.0)
    d["excesso_valor"] = d.excesso_pecas * custo
    d["faixa_idade"] = _faixa_idade(d.idade_fifo_dias)
    d["capital_otimo"] = d.estoque_medio.fillna(0).astype(float) * custo

    comprar = d.quantidade_a_comprar.fillna(0) > 0
    tem_na_rede = d.saldo_lojas.fillna(0) >= d.mu_periodo.fillna(0)
    velho = d.faixa_idade == FAIXAS_IDADE[3]
    d["acao"] = np.select(
        [(zerado | risco) & comprar,
         (zerado | risco) & tem_na_rede,
         (zerado | risco),
         sem_giro & velho,
         sem_giro | excesso],
        ["comprar", "transferir", "revisar", "liquidar", "segurar"],
        default="manter")
    return d
```

- [ ] **Step 3: Rodar a checagem**

```bash
cd $WT && python /tmp/diag_check.py
```
Expected: `OK classificar_faixas`.

- [ ] **Step 4: Commit**

```bash
cd $WT && git add backend/diagnostico.py
git commit -m "diagnostico: classificacao pura dos itens em faixas de saude do estoque

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `backend/diagnostico.py` — carregar, resumo, agregações, rede, itens

**Files:**
- Modify: `backend/diagnostico.py` (acrescentar ao fim)

**Interfaces:**
- Produces:
  - `carregar(wh, dias_sem_giro=180) -> pd.DataFrame` — `res_plano_compra` ⨝ `mart_estoque_posicao` já classificado. (O modo `ate` entra na Task 9.)
  - `resumo_geral(df) -> dict` com chaves: `data_posicao (str)`, `itens`, `capital_modelo`, `capital_erp`, `itens_sem_custo_erp`, `capital_otimo`, `diferenca_real_otimo`, `cobertura_real_dias`, `cobertura_otima_dias`, `giro_real`, `giro_otimo`, `faixas: [{faixa, itens, capital_modelo, capital_erp, excesso_valor}]` (na ordem de `FAIXAS`), `lucro_perdido_ruptura`.
  - `por_idade(df) -> list[dict]`: para cada `faixa_idade` (ordem `FAIXAS_IDADE`), `{faixa_idade, itens, capital_modelo, por_faixa: {faixa: capital_modelo}}`.
  - `agregar(df, por: str) -> list[dict]` com `por ∈ {"fornecedor","comprador","familia"}`: `{chave, itens, capital_modelo, capital_erp, capital_otimo, excesso_valor, sem_giro_valor, itens_risco, itens_zerados, lucro_perdido_ruptura, por_faixa: {faixa: capital_modelo}}`, ordenado por `capital_modelo` desc.
  - `rede(df) -> dict`: `{"transferir": [...itens], "parado_em_loja": [...itens]}` — o primeiro: faixa em {Zerado, Risco} e `saldo_lojas >= mu_periodo`, ordenado por `lucro_perdido_ruptura` desc, até 60; o segundo: `valor_lojas_erp` ≥ p90 (entre os > 0) e faixa em {Excesso, Sem giro}, ordenado por `valor_lojas_erp` desc, até 60.
  - `itens(df, faixa="", por="", chave="", busca="", limite=5000) -> list[dict]` — filtra e devolve as colunas de `COLUNAS_ITEM`.
  - `COLUNAS_ITEM` (lista) — colunas da tabela de itens e do CSV.

- [ ] **Step 1: Escrever a checagem (falha antes)**

```bash
cd $WT && cat > /tmp/diag_check2.py <<'EOF'
import sys; sys.path.insert(0, ".")
import numpy as np, pandas as pd
from backend import diagnostico as dg
from backend.warehouse import abrir
w = abrir()
df = dg.carregar(w)
assert len(df) == 19141, len(df)
assert set(df.faixa.unique()) <= set(dg.FAIXAS)
assert {"fornecedor", "comprador", "familia", "idade_fifo_dias", "saldo_lojas"} <= set(df.columns)
g = dg.resumo_geral(df)
assert abs(sum(f["capital_modelo"] for f in g["faixas"]) - g["capital_modelo"]) < 1e-6
assert [f["faixa"] for f in g["faixas"]] == dg.FAIXAS
for por in ("fornecedor", "comprador", "familia"):
    a = dg.agregar(df, por)
    assert abs(sum(x["capital_modelo"] for x in a) - g["capital_modelo"]) < 1e-6, por
    assert sum(x["itens"] for x in a) == len(df), por
idade = dg.por_idade(df)
assert [x["faixa_idade"] for x in idade] == dg.FAIXAS_IDADE
assert abs(sum(x["capital_modelo"] for x in idade) - g["capital_modelo"]) < 1e-6
r = dg.rede(df)
assert all(x["faixa"] in dg.FAIXAS[:2] for x in r["transferir"])
it = dg.itens(df, faixa="Excesso")
assert all(x["faixa"] == "Excesso" for x in it) and len(it) == int((df.faixa == "Excesso").sum())
it2 = dg.itens(df, por="comprador", chave=dg.agregar(df, "comprador")[0]["chave"])
assert len(it2) == dg.agregar(df, "comprador")[0]["itens"]
print("OK carregar/resumo/agregar/idade/rede/itens", {k: g[k] for k in ("itens","capital_modelo","capital_erp","capital_otimo")})
EOF
python /tmp/diag_check2.py
```
Expected: `AttributeError: module 'backend.diagnostico' has no attribute 'carregar'`.

- [ ] **Step 2: Implementar**

Acrescentar ao fim de `backend/diagnostico.py`:

```python
# ----------------------------------------------------------------------
# leitura
# ----------------------------------------------------------------------
COLUNAS_PLANO = [
    "sku", "item", "familia", "curva_abc", "classe_xyz", "regime", "custo_unitario",
    "demanda_media_dia", "mu_periodo", "ponto_de_pedido", "estoque_maximo", "estoque_medio",
    "estoque_fisico", "em_transito", "posicao_estoque", "quantidade_a_comprar",
    "cobertura_dias", "risco_de_faltar", "lucro_perdido_ruptura", "lucro_por_peca",
    "lead_time_dias", "giro_ano",
]
COLUNAS_POSICAO = [
    "sku", "fornecedor", "comprador", "data_posicao", "saldo_cd", "custo_medio_erp",
    "saldo_lojas", "lojas_com_saldo", "valor_lojas_erp", "ultima_venda", "ultima_entrada",
    "idade_fifo_dias", "entrada_mais_antiga_em_estoque", "entradas_cobrem_saldo",
]
COLUNAS_ITEM = [
    "sku", "item", "familia", "fornecedor", "comprador", "curva_abc", "classe_xyz", "faixa",
    "acao", "estoque_fisico", "em_transito", "saldo_lojas", "lojas_com_saldo",
    "ponto_de_pedido", "estoque_maximo", "cobertura_dias", "risco_de_faltar",
    "demanda_media_dia", "dias_sem_venda", "ultima_venda", "idade_fifo_dias", "faixa_idade",
    "entradas_cobrem_saldo", "custo_unitario", "custo_medio_erp", "capital_modelo",
    "capital_erp", "excesso_pecas", "excesso_valor", "lucro_perdido_ruptura",
    "quantidade_a_comprar",
]


def carregar(wh: Warehouse, dias_sem_giro: int = DIAS_SEM_GIRO_PADRAO) -> pd.DataFrame:
    """res_plano_compra (politica) x mart_estoque_posicao (foto), classificado."""
    plano = wh.query(f"select {', '.join(COLUNAS_PLANO)} from {ref('res_plano_compra')}")
    posicao = wh.query(f"select {', '.join(COLUNAS_POSICAO)} from {ref('mart_estoque_posicao')}")
    df = plano.merge(posicao, on="sku", how="left")
    df["data_posicao"] = df.data_posicao.fillna(pd.Timestamp(posicao.data_posicao.max()))
    for c in ("saldo_lojas", "lojas_com_saldo", "valor_lojas_erp"):
        df[c] = df[c].fillna(0)
    df["fornecedor"] = df.fornecedor.fillna("Nao informado")
    df["comprador"] = df.comprador.fillna("Nao informado")
    return classificar_faixas(df, dias_sem_giro)


# ----------------------------------------------------------------------
# leituras agregadas (puras)
# ----------------------------------------------------------------------
def _f(v) -> float:
    v = float(v)
    return v if np.isfinite(v) else 0.0


def _pond(valores: pd.Series, pesos: pd.Series, teto: float = 400.0) -> float:
    p = pesos.clip(lower=0).to_numpy(dtype=float)
    v = valores.clip(0, teto).fillna(0).to_numpy(dtype=float)
    return float(np.average(v, weights=p)) if p.sum() > 0 else 0.0


def resumo_geral(df: pd.DataFrame) -> dict:
    cap = df.capital_modelo
    dem = df.demanda_media_dia.replace(0, np.nan)
    cobertura_real = df.estoque_fisico / dem          # dias de estoque a taxa corrigida
    faixas = []
    for f in FAIXAS:
        s = df[df.faixa == f]
        faixas.append({"faixa": f, "itens": int(len(s)),
                       "capital_modelo": _f(s.capital_modelo.sum()),
                       "capital_erp": _f(s.capital_erp.sum(skipna=True)),
                       "excesso_valor": _f(s.excesso_valor.sum())})
    cob_real = _pond(cobertura_real, cap)
    cob_otima = _pond(df.cobertura_dias, df.capital_otimo)
    return {
        "data_posicao": str(pd.Timestamp(df.data_posicao.max()).date()),
        "itens": int(len(df)),
        "capital_modelo": _f(cap.sum()),
        "capital_erp": _f(df.capital_erp.sum(skipna=True)),
        "itens_sem_custo_erp": int((df.capital_erp.isna() & (df.estoque_fisico > 0)).sum()),
        "capital_otimo": _f(df.capital_otimo.sum()),
        "diferenca_real_otimo": _f(cap.sum() - df.capital_otimo.sum()),
        "cobertura_real_dias": cob_real,
        "cobertura_otima_dias": cob_otima,
        "giro_real": 365.0 / cob_real if cob_real > 0 else 0.0,
        "giro_otimo": 365.0 / cob_otima if cob_otima > 0 else 0.0,
        "lucro_perdido_ruptura": _f(df.lucro_perdido_ruptura.sum()),
        "faixas": faixas,
    }


def _por_faixa(s: pd.DataFrame) -> dict:
    g = s.groupby("faixa").capital_modelo.sum()
    return {f: _f(g.get(f, 0.0)) for f in FAIXAS}


def por_idade(df: pd.DataFrame) -> list[dict]:
    out = []
    for fi in FAIXAS_IDADE:
        s = df[df.faixa_idade == fi]
        out.append({"faixa_idade": fi, "itens": int(len(s)),
                    "capital_modelo": _f(s.capital_modelo.sum()),
                    "por_faixa": _por_faixa(s)})
    return out


DIMENSOES = ("fornecedor", "comprador", "familia")


def agregar(df: pd.DataFrame, por: str) -> list[dict]:
    if por not in DIMENSOES:
        raise ValueError(f"agregar: `por` deve ser um de {DIMENSOES}, veio {por!r}")
    out = []
    for chave, s in df.groupby(df[por].fillna("Nao informado"), sort=False):
        out.append({
            "chave": str(chave), "itens": int(len(s)),
            "capital_modelo": _f(s.capital_modelo.sum()),
            "capital_erp": _f(s.capital_erp.sum(skipna=True)),
            "capital_otimo": _f(s.capital_otimo.sum()),
            "excesso_valor": _f(s.excesso_valor.sum()),
            "sem_giro_valor": _f(s.loc[s.faixa == "Sem giro", "capital_modelo"].sum()),
            "itens_risco": int((s.faixa == "Risco").sum()),
            "itens_zerados": int((s.faixa == "Zerado com demanda").sum()),
            "lucro_perdido_ruptura": _f(s.lucro_perdido_ruptura.sum()),
            "por_faixa": _por_faixa(s),
        })
    out.sort(key=lambda x: -x["capital_modelo"])
    return out


def _registros(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    from .analitico import registros
    cols = [c for c in cols if c in df.columns]
    d = df[cols].copy()
    if "dias_sem_venda" in d:
        d["dias_sem_venda"] = d.dias_sem_venda.replace(np.inf, np.nan)
    return registros(d)


def rede(df: pd.DataFrame, limite: int = 60) -> dict:
    em_falta = df[df.faixa.isin(FAIXAS[:2]) & (df.saldo_lojas >= df.mu_periodo) & (df.saldo_lojas > 0)]
    em_falta = em_falta.sort_values("lucro_perdido_ruptura", ascending=False).head(limite)
    positivos = df.loc[df.valor_lojas_erp > 0, "valor_lojas_erp"]
    p90 = float(positivos.quantile(0.9)) if len(positivos) else np.inf
    parado = df[(df.valor_lojas_erp >= p90) & df.faixa.isin(["Excesso", "Sem giro"])]
    parado = parado.sort_values("valor_lojas_erp", ascending=False).head(limite)
    cols = COLUNAS_ITEM + ["valor_lojas_erp", "mu_periodo"]
    return {"transferir": _registros(em_falta, cols), "parado_em_loja": _registros(parado, cols),
            "p90_valor_loja": (None if not np.isfinite(p90) else p90)}


def itens(df: pd.DataFrame, faixa: str = "", por: str = "", chave: str = "",
          busca: str = "", limite: int = 5000) -> list[dict]:
    s = df
    if faixa:
        s = s[s.faixa == faixa]
    if por and chave:
        if por not in DIMENSOES:
            raise ValueError(f"itens: `por` deve ser um de {DIMENSOES}")
        s = s[s[por].fillna("Nao informado").astype(str) == chave]
    if busca:
        q = busca.lower()
        s = s[s.sku.astype(str).str.lower().str.contains(q, regex=False)
              | s.item.astype(str).str.lower().str.contains(q, regex=False)]
    s = s.sort_values("capital_modelo", ascending=False).head(limite)
    return _registros(s, COLUNAS_ITEM)
```

- [ ] **Step 3: Rodar a checagem**

```bash
cd $WT && python /tmp/diag_check2.py
```
Expected: `OK carregar/...` e o dicionário com itens = 19141 e capitais positivos. Se `capital_erp` sair muito distante de `capital_modelo` (mais de 2×), anotar no relatório final: é achado, não bug, mas vale conferir 3 SKUs à mão com `select sku, estoque_fisico, custo_unitario, custo_medio_erp from ...`.

- [ ] **Step 4: Commit**

```bash
cd $WT && git add backend/diagnostico.py
git commit -m "diagnostico: leitura do warehouse, resumo geral, idade, agregacoes por fornecedor/comprador/familia, rede e itens

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Rotas, cache e CSV em `backend/main.py`

**Files:**
- Modify: `backend/main.py` — import (linha do `from backend import acompanhamento, ...`), cache novo junto dos três existentes e em `invalidar_caches()`, bloco novo de rotas antes de `# DADOS`.

**Interfaces:**
- Consumes: `diagnostico.carregar/resumo_geral/por_idade/agregar/rede/itens/COLUNAS_ITEM/DIAS_SEM_GIRO_PADRAO`.
- Produces rotas:
  - `GET /diagnostico` → template `diagnostico.html`, contexto com `dias_sem_giro`.
  - `GET /api/diagnostico/geral?dias_sem_giro=` → `resumo_geral`
  - `GET /api/diagnostico/idade?dias_sem_giro=` → `{"faixas": por_idade}`
  - `GET /api/diagnostico/agregado?por=fornecedor|comprador|familia&dias_sem_giro=` → `{"por", "grupos": agregar}`
  - `GET /api/diagnostico/rede?dias_sem_giro=` → `rede`
  - `GET /api/diagnostico/itens?faixa=&por=&chave=&q=&dias_sem_giro=` → `{"itens": [...], "total": n}`
  - `GET /api/diagnostico/confianca` → ver Step 3
  - `GET /diagnostico.csv?faixa=&por=&chave=&q=&dias_sem_giro=` → CSV `;` com BOM, mesmo gerador de `conferencia.csv`.
- Cache: `_cache_diagnostico: dict` chaveado por `dias_sem_giro` (Task 9 acrescenta `ate`), limpo em `invalidar_caches()`.

- [ ] **Step 1: Import e cache**

Linha de import: trocar
```python
from backend import acompanhamento, ajustes, analitico, outliers, qualidade  # noqa: E402
```
por
```python
from backend import acompanhamento, ajustes, analitico, diagnostico, outliers, qualidade  # noqa: E402
```

Depois de `_cache_ranking: dict = {}`:

```python
# o diagnostico cruza 19 mil itens do plano com a foto do estoque e classifica:
# ~1s. Fica em cache por limiar de "sem giro"; cai com os demais.
_cache_diagnostico: dict = {}
```

Em `invalidar_caches()`, depois de `_cache_ranking.clear()`:

```python
    _cache_diagnostico.clear()
```

- [ ] **Step 2: Rotas**

Inserir antes do bloco `# DADOS` (antes de `FAMILIAS_TABELA = {`):

```python
# ======================================================================
# DIAGNOSTICO DO ESTOQUE ATUAL
# ======================================================================
def _diag(dias_sem_giro: int) -> pd.DataFrame:
    chave = int(dias_sem_giro)
    if chave not in _cache_diagnostico:
        _cache_diagnostico[chave] = diagnostico.carregar(wh(), chave)
    return _cache_diagnostico[chave]


@app.get("/diagnostico")
def diagnostico_pagina(request: Request,
                       dias_sem_giro: int = diagnostico.DIAS_SEM_GIRO_PADRAO):
    w = wh()
    if not pronto(w) or not w.existe("mart_estoque_posicao"):
        return sem_dados(request)
    return tpl.TemplateResponse(request, "diagnostico.html", contexto(
        request, "diagnostico", dias_sem_giro=int(dias_sem_giro),
        faixas=diagnostico.FAIXAS, faixas_idade=diagnostico.FAIXAS_IDADE))


@app.get("/api/diagnostico/geral")
def api_diag_geral(dias_sem_giro: int = diagnostico.DIAS_SEM_GIRO_PADRAO):
    return JSONResponse(diagnostico.resumo_geral(_diag(dias_sem_giro)))


@app.get("/api/diagnostico/idade")
def api_diag_idade(dias_sem_giro: int = diagnostico.DIAS_SEM_GIRO_PADRAO):
    return JSONResponse({"faixas": diagnostico.por_idade(_diag(dias_sem_giro))})


@app.get("/api/diagnostico/agregado")
def api_diag_agregado(por: str = "fornecedor",
                      dias_sem_giro: int = diagnostico.DIAS_SEM_GIRO_PADRAO):
    if por not in diagnostico.DIMENSOES:
        return JSONResponse({"erro": f"por deve ser um de {list(diagnostico.DIMENSOES)}"},
                            status_code=400)
    return JSONResponse({"por": por, "grupos": diagnostico.agregar(_diag(dias_sem_giro), por)})


@app.get("/api/diagnostico/rede")
def api_diag_rede(dias_sem_giro: int = diagnostico.DIAS_SEM_GIRO_PADRAO):
    return JSONResponse(diagnostico.rede(_diag(dias_sem_giro)))


@app.get("/api/diagnostico/itens")
def api_diag_itens(faixa: str = "", por: str = "", chave: str = "", q: str = "",
                   dias_sem_giro: int = diagnostico.DIAS_SEM_GIRO_PADRAO):
    df = _diag(dias_sem_giro)
    lista = diagnostico.itens(df, faixa=faixa, por=por, chave=chave, busca=q)
    return JSONResponse({"itens": lista, "total": len(lista)})


@app.get("/api/diagnostico/confianca")
def api_diag_confianca(dias_sem_giro: int = diagnostico.DIAS_SEM_GIRO_PADRAO):
    """Grau de confianca por bloco da pagina, a partir da bateria de qualidade
    (se o cache estiver quente - nunca roda os 13s aqui) e dos outliers."""
    q = _cache_qualidade.get("d")
    df = _diag(dias_sem_giro)
    return JSONResponse(diagnostico.confianca(q, outliers.sinalizar(outliers.carregar(wh())), df))


@app.get("/diagnostico.csv")
def diagnostico_csv(faixa: str = "", por: str = "", chave: str = "", q: str = "",
                    dias_sem_giro: int = diagnostico.DIAS_SEM_GIRO_PADRAO):
    from fastapi.responses import StreamingResponse

    df = pd.DataFrame(diagnostico.itens(_diag(dias_sem_giro), faixa=faixa, por=por,
                                        chave=chave, busca=q, limite=1_000_000))

    def gerar():
        yield "﻿"
        yield ";".join(df.columns) + "\n"
        for linha in df.itertuples(index=False):
            campos = []
            for v in linha:
                if v is None:
                    campos.append("")
                elif isinstance(v, bool):
                    campos.append("sim" if v else "nao")
                elif isinstance(v, (int, float)):
                    campos.append(str(v).replace(".", ","))
                else:
                    campos.append('"' + str(v).replace('"', '""') + '"')
            yield ";".join(campos) + "\n"

    sufixo = "-" + faixa.lower().replace(" ", "-") if faixa else ""
    nome = f"diagnostico-estoque{sufixo}.csv"
    return StreamingResponse(gerar(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{nome}"'})
```

- [ ] **Step 3: `diagnostico.confianca` em `backend/diagnostico.py`**

Acrescentar ao fim do módulo:

```python
# ----------------------------------------------------------------------
# confianca por bloco
# ----------------------------------------------------------------------
# que grupos da bateria de qualidade sustentam cada bloco da pagina
BLOCOS_QUALIDADE = {
    "geral":    ("estoque", "cadastro"),
    "idade":    ("compra", "estoque"),
    "agregado": ("cadastro",),
    "rede":     ("estoque",),
    "itens":    ("venda", "estoque", "cadastro"),
}
_PESO = {"ok": 0, "aviso": 1, "erro": 2}


def confianca(qualidade: dict | None, sinalizados: pd.DataFrame, df: pd.DataFrame) -> dict:
    """Por bloco: o pior nivel entre as checagens dos grupos que o sustentam,
    e quantos itens com sinal de outlier estao no recorte. `qualidade` e o
    JSON de qualidade.verificar() (ou None se o cache esta frio)."""
    out = {"qualidade_disponivel": qualidade is not None, "blocos": {}}
    por_grupo: dict[str, str] = {}
    if qualidade:
        for c in qualidade.get("checagens", []):
            g = c["grupo"]
            if _PESO.get(c["nivel"], 0) >= _PESO.get(por_grupo.get(g, "ok"), 0):
                por_grupo[g] = c["nivel"]
    n_out = 0
    if sinalizados is not None and len(sinalizados) and "n_motivos" in sinalizados:
        marcados = set(sinalizados.loc[sinalizados.n_motivos > 0, "sku"].astype(str))
        n_out = int(df.sku.astype(str).isin(marcados).sum())
    for bloco, grupos in BLOCOS_QUALIDADE.items():
        niveis = [por_grupo.get(g, "ok") for g in grupos] if qualidade else []
        pior = max(niveis, key=lambda n: _PESO[n]) if niveis else None
        out["blocos"][bloco] = {"nivel": pior, "grupos": list(grupos), "outliers": n_out}
    return out
```

- [ ] **Step 4: Verificar as rotas com o TestClient**

```bash
cd $WT && python -c "
from fastapi.testclient import TestClient
from backend.main import app
c = TestClient(app)
for u in ['/diagnostico', '/api/diagnostico/geral', '/api/diagnostico/idade',
          '/api/diagnostico/agregado?por=comprador', '/api/diagnostico/rede',
          '/api/diagnostico/itens?faixa=Excesso', '/api/diagnostico/confianca',
          '/diagnostico.csv?faixa=Sem%20giro', '/api/diagnostico/agregado?por=xpto']:
    r = c.get(u); print(r.status_code, u, len(r.content))
g = c.get('/api/diagnostico/geral').json(); print({k: g[k] for k in ('itens','capital_modelo','capital_otimo')})
"
```
Expected: `/diagnostico` pode dar 500 até a Task 7 criar o template (TemplateNotFound) — todas as `/api/...` e o CSV devem responder 200, `por=xpto` 400.

- [ ] **Step 5: Commit**

```bash
cd $WT && git add backend/main.py backend/diagnostico.py
git commit -m "diagnostico: rotas /diagnostico e /api/diagnostico/*, cache e CSV

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Página — template, JS, menu e glossário

**Files:**
- Create: `templates/diagnostico.html`
- Create: `static/diagnostico.js`
- Modify: `templates/base.html` (link no grupo "Decidir", depois de "Retorno do dinheiro")
- Modify: `static/glossario.js` (verbetes novos antes do fechamento de `var G = {`)

**Interfaces:**
- Consumes: as rotas da Task 6; primitivos `N.buscar, N.grafico, N.tabela, N.espera, N.curto, N.num, N.pct, N.data, N.esc, N.eixoX, N.eixoY, N.grade, N.dica, N.dicaTit, N.dicaLin, N.sombra, N.seloClasse, N.COR`; `abrirItem(sku)` de `static/item.js` (abre o dossiê do item na gaveta).
- Template usa o contexto `dias_sem_giro`, `faixas`, `faixas_idade`, `exec` (de `contexto()`).

- [ ] **Step 1: Template**

`templates/diagnostico.html`:

```html
{% extends "base.html" %}
{% block titulo %}Diagnóstico do estoque{% endblock %}
{% block cabecalho %}Diagnóstico do estoque{% endblock %}
{% block subcabecalho %}o estoque de hoje, item a item: o que está parado, o que está em risco e quanto isso custa · posição de {{ exec.posicao if exec else '–' }}{% endblock %}
{% block classe_corpo %}largo{% endblock %}
{% block acoes %}
  <label class="pequeno t4" style="display:inline-flex;align-items:center;gap:8px">
    sem giro a partir de
    <input type="number" id="dias-sem-giro" value="{{ dias_sem_giro }}" min="30" max="1095" step="30"
           style="width:70px"> dias
  </label>
  <a class="btn btn-p" id="bt-csv" href="/diagnostico.csv?dias_sem_giro={{ dias_sem_giro }}">Baixar CSV</a>
{% endblock %}

{% block cabeca %}
<style>
  .faixa-selo { display:inline-block; padding:2px 8px; border-radius:6px; font-size:10.5px; font-weight:600; white-space:nowrap; }
  .fx-zerado   { background: var(--coral); color:#111; }
  .fx-risco    { background: var(--ambar); color:#111; }
  .fx-semgiro  { background: var(--violeta); color:#fff; }
  .fx-excesso  { background: var(--ceu); color:#111; }
  .fx-saudavel { background: var(--menta); color:#111; }
  .abas { display:flex; gap:6px; }
  .abas button { font-size:11.5px; padding:4px 10px; }
  .abas button.on { border-color: var(--tinta-3); background: var(--painel-3); color: var(--tinta); }
  .conf { display:inline-flex; align-items:center; gap:6px; font-size:10.5px; color: var(--tinta-3); }
  .conf .pt { width:8px; height:8px; border-radius:50%; background: var(--tinta-4); }
  .conf.ok .pt { background: var(--menta); } .conf.aviso .pt { background: var(--ambar); } .conf.erro .pt { background: var(--coral); }
  .kpi-par { display:flex; gap:10px; align-items:baseline; }
  .kpi-par .vs { font-size:10.5px; color: var(--tinta-4); }
  tr.clicavel { cursor:pointer; }
  tr.clicavel:hover td { background: var(--painel-3); }
  tr.sel td { background: var(--ambar-lav); }
</style>
{% endblock %}

{% block conteudo %}

<!-- ============================================ visão geral -->
<div class="gr gr-5 secao" id="kpis">
  <div class="painel metrica">
    <div class="rotulo">Capital em estoque (custo do modelo)</div>
    <div class="valor md" id="k-cap-modelo">–</div>
    <div class="nota">ao custo contábil do ERP: <b id="k-cap-erp">–</b> <span id="k-sem-erp" class="t4"></span></div>
  </div>
  <div class="painel metrica am">
    <div class="rotulo">Real menos ótimo</div>
    <div class="valor md" id="k-dif">–</div>
    <div class="nota">a política do modelo manteria <b id="k-cap-otimo">–</b> em estoque médio</div>
  </div>
  <div class="painel metrica">
    <div class="rotulo">Cobertura real · ótima</div>
    <div class="valor md kpi-par"><span id="k-cob-real">–</span><span class="vs">vs</span><span id="k-cob-otima" class="t3">–</span></div>
    <div class="nota">dias, ponderados por capital · giro <b id="k-giro">–</b></div>
  </div>
  <div class="painel metrica cr">
    <div class="rotulo">Em risco ou zerado</div>
    <div class="valor md" id="k-risco">–</div>
    <div class="nota"><span id="k-risco-n">–</span> itens · lucro perdido por ruptura no histórico <b id="k-perdido">–</b></div>
  </div>
  <div class="painel metrica">
    <div class="rotulo">Parado (sem giro + excesso)</div>
    <div class="valor md" id="k-parado">–</div>
    <div class="nota"><span id="k-parado-n">–</span> itens · excesso acima do máximo <b id="k-excesso">–</b></div>
  </div>
</div>

<div class="gr gr-2 secao">
  <div class="painel">
    <div class="painel-cab"><h2>Reais por faixa</h2><span class="conf" data-bloco="geral"><i class="pt"></i><span></span></span></div>
    <div class="painel-int"><div id="g-faixas" style="height:260px"></div></div>
  </div>
  <div class="painel">
    <div class="painel-cab"><h2>Idade do estoque</h2><span class="dica">idade FIFO do saldo atual, empilhada por faixa</span><span class="conf" data-bloco="idade"><i class="pt"></i><span></span></span></div>
    <div class="painel-int"><div id="g-idade" style="height:260px"></div></div>
  </div>
</div>

<!-- ============================================ agregado -->
<div class="secao">
  <div class="secao-cab">
    <h2>Por fornecedor, comprador e família</h2>
    <span class="dica">clique numa linha para filtrar a tabela de itens</span>
    <div class="dir abas" id="abas-por">
      <button class="btn btn-p on" data-por="fornecedor">Fornecedor</button>
      <button class="btn btn-p" data-por="comprador">Comprador</button>
      <button class="btn btn-p" data-por="familia">Família</button>
    </div>
  </div>
  <div class="painel"><div class="painel-int rente"><div class="rolo rolo-medio" id="agregado"></div></div></div>
</div>

<!-- ============================================ rede -->
<div class="gr gr-2 secao">
  <div class="painel">
    <div class="painel-cab"><h2>Falta no CD, tem na rede</h2><span class="dica">zerado ou em risco no CD com saldo em loja que cobre o período de proteção: transferir antes de comprar</span><span class="conf" data-bloco="rede"><i class="pt"></i><span></span></span></div>
    <div class="painel-int rente"><div class="rolo rolo-medio" id="rede-transferir"></div></div>
  </div>
  <div class="painel">
    <div class="painel-cab"><h2>Parado em loja</h2><span class="dica">excesso ou sem giro no CD e valor em loja acima do p90 da rede</span></div>
    <div class="painel-int rente"><div class="rolo rolo-medio" id="rede-parado"></div></div>
  </div>
</div>

<!-- ============================================ itens -->
<div class="secao">
  <div class="secao-cab">
    <h2>Itens</h2>
    <span class="dica" id="itens-filtro">todas as faixas</span>
    <div class="dir" style="display:flex;gap:8px;align-items:center">
      <select id="sel-faixa"><option value="">Todas as faixas</option>
        {% for f in faixas %}<option value="{{ f }}">{{ f }}</option>{% endfor %}</select>
      <input type="search" id="busca-itens" placeholder="buscar SKU ou nome" style="width:220px">
      <button class="btn btn-p" id="bt-limpar">Limpar filtro</button>
      <span class="conf" data-bloco="itens"><i class="pt"></i><span></span></span>
    </div>
  </div>
  <div class="painel"><div class="painel-int rente"><div class="rolo rolo-alto" id="itens"></div></div></div>
</div>

{% endblock %}

{% block rodape %}
<script src="{{ est('item.js') }}"></script>
<script src="{{ est('diagnostico.js') }}"></script>
{% endblock %}
```

Os blocos de `base.html` são `titulo`, `cabeca`, `cabecalho`, `subcabecalho`, `acoes`, `classe_corpo`, `conteudo` e `rodape` (scripts vão em `rodape`, como em `outliers.html`).

- [ ] **Step 2: JavaScript**

`static/diagnostico.js`:

```javascript
/* Diagnostico do estoque atual: le /api/diagnostico/* e monta KPIs, os dois
   graficos, o agregado por dimensao, a rede e a tabela de itens. Todo numero
   vem do backend; aqui so se formata. */
(function () {
  "use strict";
  var C = N.COR;
  var COR_FAIXA = {
    "Zerado com demanda": C.coral, "Risco": C.ambar, "Sem giro": C.violeta,
    "Excesso": C.ceu, "Saudável": C.menta
  };
  var CLS_FAIXA = {
    "Zerado com demanda": "fx-zerado", "Risco": "fx-risco", "Sem giro": "fx-semgiro",
    "Excesso": "fx-excesso", "Saudável": "fx-saudavel"
  };
  var estado = { dias: parseInt(document.getElementById("dias-sem-giro").value, 10) || 180,
                 por: "fornecedor", chave: "", faixa: "", q: "" };

  function qs(extra) {
    var p = ["dias_sem_giro=" + estado.dias];
    for (var k in (extra || {})) if (extra[k]) p.push(k + "=" + encodeURIComponent(extra[k]));
    return "?" + p.join("&");
  }
  function selo(f) { return '<span class="faixa-selo ' + (CLS_FAIXA[f] || "") + '">' + N.esc(f) + "</span>"; }
  function rs(v) { return "R$ " + N.curto(v); }

  /* ------------------------------------------------------------ geral */
  function geral() {
    N.buscar("/api/diagnostico/geral" + qs(), true).then(function (g) {
      var byF = {}; g.faixas.forEach(function (f) { byF[f.faixa] = f; });
      var risco = byF["Risco"].capital_modelo + byF["Zerado com demanda"].capital_modelo;
      var parado = byF["Sem giro"].capital_modelo + byF["Excesso"].capital_modelo;
      var t = function (id, v) { document.getElementById(id).innerHTML = v; };
      t("k-cap-modelo", rs(g.capital_modelo));
      t("k-cap-erp", rs(g.capital_erp));
      t("k-sem-erp", g.itens_sem_custo_erp ? "(" + N.num(g.itens_sem_custo_erp) + " itens sem custo contábil)" : "");
      t("k-dif", (g.diferenca_real_otimo >= 0 ? "+" : "−") + rs(Math.abs(g.diferenca_real_otimo)));
      t("k-cap-otimo", rs(g.capital_otimo));
      t("k-cob-real", N.num(g.cobertura_real_dias, 0));
      t("k-cob-otima", N.num(g.cobertura_otima_dias, 0));
      t("k-giro", N.num(g.giro_real, 1) + "× vs " + N.num(g.giro_otimo, 1) + "×");
      t("k-risco", rs(risco));
      t("k-risco-n", N.num(byF["Risco"].itens + byF["Zerado com demanda"].itens));
      t("k-perdido", rs(g.lucro_perdido_ruptura));
      t("k-parado", rs(parado));
      t("k-parado-n", N.num(byF["Sem giro"].itens + byF["Excesso"].itens));
      t("k-excesso", rs(byF["Excesso"].excesso_valor));

      N.grafico("g-faixas", {
        grid: N.grade({ left: 8, right: 60, top: 8, bottom: 8 }),
        tooltip: N.dica(function (ps) {
          var f = g.faixas[ps[0].dataIndex];
          return N.dicaTit(f.faixa) +
            N.dicaLin(COR_FAIXA[f.faixa], "custo do modelo", rs(f.capital_modelo)) +
            N.dicaLin(C.tinta4, "custo contábil", rs(f.capital_erp)) +
            N.dicaLin(C.tinta4, "itens", N.num(f.itens));
        }),
        xAxis: N.eixoY({ axisLabel: { formatter: function (v) { return N.curto(v); } } }),
        yAxis: N.eixoX({ data: g.faixas.map(function (f) { return f.faixa; }),
          axisLabel: { color: C.tinta2, fontSize: 10.5 }, axisLine: { show: false } }),
        series: [{ type: "bar", barWidth: "58%",
          data: g.faixas.map(function (f) { return { value: f.capital_modelo,
            itemStyle: { color: N.sombra(COR_FAIXA[f.faixa], 0.85), borderRadius: [0, 3, 3, 0] } }; }),
          label: { show: true, position: "right", color: C.tinta2, fontSize: 10.5,
            fontFamily: '"JetBrains Mono", monospace',
            formatter: function (p) { return N.num(g.faixas[p.dataIndex].itens) + " itens"; } } }]
      });
    });
  }

  /* ------------------------------------------------------------ idade */
  function idade() {
    N.buscar("/api/diagnostico/idade" + qs(), true).then(function (d) {
      var faixas = Object.keys(COR_FAIXA);
      N.grafico("g-idade", {
        grid: N.grade({ left: 8, right: 14, top: 32, bottom: 6 }),
        legend: { top: 0, left: 0, itemWidth: 9, itemHeight: 9, itemGap: 12, icon: "roundRect",
          textStyle: { color: C.tinta3, fontSize: 10.5 } },
        tooltip: N.dica(function (ps) {
          var f = d.faixas[ps[0].dataIndex], s = N.dicaTit(f.faixa_idade);
          ps.forEach(function (x) { if (x.value) s += N.dicaLin(x.color, x.seriesName, rs(x.value)); });
          s += N.dicaLin(C.tinta, "total", rs(f.capital_modelo) + " · " + N.num(f.itens) + " itens");
          return s;
        }),
        xAxis: N.eixoX({ data: d.faixas.map(function (f) { return f.faixa_idade; }),
          axisLabel: { color: C.tinta2, interval: 0 } }),
        yAxis: N.eixoY({ min: 0, axisLabel: { formatter: function (v) { return N.curto(v); } } }),
        series: faixas.map(function (f, i) {
          return { name: f, type: "bar", stack: "c", barWidth: "52%",
            data: d.faixas.map(function (x) { return x.por_faixa[f] || 0; }),
            itemStyle: { color: N.sombra(COR_FAIXA[f], 0.85),
              borderRadius: i === faixas.length - 1 ? [3, 3, 0, 0] : 0 } };
        })
      });
    });
  }

  /* --------------------------------------------------------- agregado */
  function agregado() {
    var el = document.getElementById("agregado"); N.espera(el);
    N.buscar("/api/diagnostico/agregado" + qs({ por: estado.por }), true).then(function (d) {
      var h = '<table class="tb" id="tb-agregado"><thead><tr>' +
        "<th>" + N.esc(estado.por === "familia" ? "Família" : estado.por.charAt(0).toUpperCase() + estado.por.slice(1)) + "</th>" +
        '<th class="n">Itens</th><th class="n">Capital</th><th class="n">Capital contábil</th>' +
        '<th class="n">Ótimo</th><th class="n">Zerado + risco</th><th class="n">Sem giro</th>' +
        '<th class="n">Excesso</th><th class="n">Itens em risco</th><th class="n">Lucro perdido</th>' +
        "</tr></thead><tbody>";
      d.grupos.forEach(function (g) {
        var risco = g.por_faixa["Zerado com demanda"] + g.por_faixa["Risco"];
        h += '<tr class="clicavel' + (g.chave === estado.chave ? " sel" : "") + '" data-chave="' + N.esc(g.chave) + '">' +
          "<td>" + N.esc(g.chave) + "</td>" +
          '<td class="n">' + N.num(g.itens) + "</td>" +
          '<td class="n" data-v="' + g.capital_modelo + '">' + rs(g.capital_modelo) + "</td>" +
          '<td class="n" data-v="' + g.capital_erp + '">' + rs(g.capital_erp) + "</td>" +
          '<td class="n" data-v="' + g.capital_otimo + '">' + rs(g.capital_otimo) + "</td>" +
          '<td class="n" data-v="' + risco + '">' + rs(risco) + "</td>" +
          '<td class="n" data-v="' + g.sem_giro_valor + '">' + rs(g.sem_giro_valor) + "</td>" +
          '<td class="n" data-v="' + g.excesso_valor + '">' + rs(g.excesso_valor) + "</td>" +
          '<td class="n">' + N.num(g.itens_risco + g.itens_zerados) + "</td>" +
          '<td class="n" data-v="' + g.lucro_perdido_ruptura + '">' + rs(g.lucro_perdido_ruptura) + "</td>" +
          "</tr>";
      });
      el.innerHTML = h + "</tbody></table>";
      N.tabela("#tb-agregado", { col: 2, dir: "desc" });
      el.querySelectorAll("tr.clicavel").forEach(function (tr) {
        tr.addEventListener("click", function () {
          estado.chave = (estado.chave === tr.dataset.chave) ? "" : tr.dataset.chave;
          el.querySelectorAll("tr.sel").forEach(function (x) { x.classList.remove("sel"); });
          if (estado.chave) tr.classList.add("sel");
          itens();
        });
      });
    });
  }

  /* -------------------------------------------------------------- rede */
  function tabelaRede(el, lista, colExtra) {
    if (!lista.length) { el.innerHTML = '<div class="vazio">nada a apontar</div>'; return; }
    var h = '<table class="tb"><thead><tr><th>Item</th><th>Faixa</th><th class="n">CD</th>' +
      '<th class="n">Rede</th><th class="n">Lojas</th><th class="n">' + colExtra.rotulo + "</th></tr></thead><tbody>";
    lista.forEach(function (r) {
      h += '<tr class="clicavel" data-sku="' + N.esc(r.sku) + '"><td>' + N.esc(r.item) +
        '<div class="t4 pequeno">' + N.esc(r.sku) + " · " + N.esc(r.fornecedor) + "</div></td>" +
        "<td>" + selo(r.faixa) + "</td>" +
        '<td class="n">' + N.num(r.estoque_fisico) + "</td>" +
        '<td class="n">' + N.num(r.saldo_lojas) + "</td>" +
        '<td class="n">' + N.num(r.lojas_com_saldo) + "</td>" +
        '<td class="n">' + colExtra.valor(r) + "</td></tr>";
    });
    el.innerHTML = h + "</tbody></table>";
    el.querySelectorAll("tr.clicavel").forEach(function (tr) {
      tr.addEventListener("click", function () { if (window.abrirItem) abrirItem(tr.dataset.sku); });
    });
  }
  function rede() {
    N.buscar("/api/diagnostico/rede" + qs(), true).then(function (d) {
      tabelaRede(document.getElementById("rede-transferir"), d.transferir,
        { rotulo: "Precisa no período", valor: function (r) { return N.num(r.mu_periodo, 1); } });
      tabelaRede(document.getElementById("rede-parado"), d.parado_em_loja,
        { rotulo: "Valor em loja", valor: function (r) { return rs(r.valor_lojas_erp); } });
    });
  }

  /* ------------------------------------------------------------- itens */
  function itens() {
    var el = document.getElementById("itens"); N.espera(el);
    var filtros = { faixa: estado.faixa, por: estado.chave ? estado.por : "", chave: estado.chave, q: estado.q };
    document.getElementById("itens-filtro").textContent =
      (estado.faixa || "todas as faixas") + (estado.chave ? " · " + estado.por + " = " + estado.chave : "") +
      (estado.q ? ' · "' + estado.q + '"' : "");
    document.getElementById("bt-csv").href = "/diagnostico.csv" + qs(filtros);
    N.buscar("/api/diagnostico/itens" + qs(filtros), true).then(function (d) {
      var h = '<table class="tb" id="tb-itens"><thead><tr>' +
        "<th>Item</th><th>Fornecedor</th><th>Comprador</th><th>Classe</th><th>Faixa</th><th>Ação</th>" +
        '<th class="n">Físico</th><th class="n">Trânsito</th><th class="n">Rede</th>' +
        '<th class="n">Ponto de pedido</th><th class="n">Máximo</th><th class="n">Cobertura</th>' +
        '<th class="n">Dias sem venda</th><th class="n">Idade FIFO</th>' +
        '<th class="n">Custo unitário</th><th class="n">Custo contábil</th>' +
        '<th class="n">Capital</th><th class="n">Excesso</th><th class="n">Lucro perdido</th>' +
        "</tr></thead><tbody>";
      d.itens.forEach(function (r) {
        h += '<tr class="clicavel" data-sku="' + N.esc(r.sku) + '">' +
          "<td>" + N.esc(r.item) + '<div class="t4 pequeno">' + N.esc(r.sku) + " · " + N.esc(r.familia) + "</div></td>" +
          "<td>" + N.esc(r.fornecedor) + "</td><td>" + N.esc(r.comprador) + "</td>" +
          "<td>" + N.seloClasse((r.curva_abc || "") + (r.classe_xyz || "")) + "</td>" +
          "<td>" + selo(r.faixa) + "</td><td>" + N.esc(r.acao) + "</td>" +
          '<td class="n">' + N.num(r.estoque_fisico) + "</td>" +
          '<td class="n">' + N.num(r.em_transito) + "</td>" +
          '<td class="n">' + N.num(r.saldo_lojas) + "</td>" +
          '<td class="n">' + N.num(r.ponto_de_pedido) + "</td>" +
          '<td class="n">' + N.num(r.estoque_maximo) + "</td>" +
          '<td class="n">' + N.num(r.cobertura_dias, 0) + "</td>" +
          '<td class="n" data-v="' + (r.dias_sem_venda === null ? 99999 : r.dias_sem_venda) + '">' +
            (r.dias_sem_venda === null ? "nunca" : N.num(r.dias_sem_venda)) + "</td>" +
          '<td class="n" data-v="' + (r.idade_fifo_dias === null ? -1 : r.idade_fifo_dias) + '">' +
            (r.idade_fifo_dias === null ? "–" : N.num(r.idade_fifo_dias, 0) + (r.entradas_cobrem_saldo ? "" : "*")) + "</td>" +
          '<td class="n">' + N.num(r.custo_unitario, 2) + "</td>" +
          '<td class="n">' + (r.custo_medio_erp === null ? "–" : N.num(r.custo_medio_erp, 2)) + "</td>" +
          '<td class="n" data-v="' + r.capital_modelo + '">' + rs(r.capital_modelo) + "</td>" +
          '<td class="n" data-v="' + r.excesso_valor + '">' + (r.excesso_valor ? rs(r.excesso_valor) : "–") + "</td>" +
          '<td class="n" data-v="' + r.lucro_perdido_ruptura + '">' + rs(r.lucro_perdido_ruptura) + "</td>" +
          "</tr>";
      });
      el.innerHTML = h + "</tbody></table>" +
        '<div class="t4 pequeno" style="padding:8px 12px">' + N.num(d.total) + " itens · * idade estimada: as entradas registradas não cobrem o saldo</div>";
      N.tabela("#tb-itens", { col: 16, dir: "desc" });
      el.querySelectorAll("tr.clicavel").forEach(function (tr) {
        tr.addEventListener("click", function () { if (window.abrirItem) abrirItem(tr.dataset.sku); });
      });
    });
  }

  /* --------------------------------------------------------- confianca */
  function confianca() {
    N.buscar("/api/diagnostico/confianca" + qs(), true).then(function (d) {
      document.querySelectorAll(".conf").forEach(function (el) {
        var b = d.blocos[el.dataset.bloco]; if (!b) return;
        el.classList.remove("ok", "aviso", "erro");
        if (b.nivel) el.classList.add(b.nivel);
        el.querySelector("span").textContent =
          (b.nivel ? "qualidade: " + b.nivel : "qualidade: abra /qualidade para medir") +
          (b.outliers ? " · " + N.num(b.outliers) + " outliers" : "");
      });
    });
  }

  /* ----------------------------------------------------------- eventos */
  document.getElementById("abas-por").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-por]"); if (!b) return;
    document.querySelectorAll("#abas-por button").forEach(function (x) { x.classList.remove("on"); });
    b.classList.add("on"); estado.por = b.dataset.por; estado.chave = "";
    agregado(); itens();
  });
  document.getElementById("sel-faixa").addEventListener("change", function (e) { estado.faixa = e.target.value; itens(); });
  var tmr;
  document.getElementById("busca-itens").addEventListener("input", function (e) {
    clearTimeout(tmr); tmr = setTimeout(function () { estado.q = e.target.value.trim(); itens(); }, 250);
  });
  document.getElementById("bt-limpar").addEventListener("click", function () {
    estado.faixa = ""; estado.chave = ""; estado.q = "";
    document.getElementById("sel-faixa").value = ""; document.getElementById("busca-itens").value = "";
    agregado(); itens();
  });
  document.getElementById("dias-sem-giro").addEventListener("change", function (e) {
    var v = parseInt(e.target.value, 10); if (!v || v < 30) return;
    estado.dias = v; tudo();
  });

  function tudo() { geral(); idade(); agregado(); rede(); itens(); confianca(); }
  tudo();
})();
```

- [ ] **Step 3: Menu em `base.html`**

Depois do `</a>` de "Retorno do dinheiro" (dentro do grupo "Decidir"):

```html
        <a href="/diagnostico" class="{{ 'on' if pagina == 'diagnostico' }}">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 21h18"/><path d="M5 21V10l7-5 7 5v11"/><path d="M9 21v-6h6v6"/></svg>
          Diagnóstico do estoque
        </a>
```

- [ ] **Step 4: Glossário**

Em `static/glossario.js`, dentro de `var G = {`, acrescentar uma seção antes do fechamento `};`:

```javascript
  /* ------------------------------------------------------ diagnostico */
  "faixa": "Estado do item hoje, avaliado nesta ordem: <b>Zerado com demanda</b> (sem peça e com demanda estimada), <b>Risco</b> (posição igual ou abaixo do ponto de pedido), <b>Sem giro</b> (há peça, mas sem venda há mais dias que o limiar da página), <b>Excesso</b> (acima do estoque máximo do modelo), <b>Saudável</b> (o resto). Um item cai numa faixa só.",
  "acao": "O que o diagnóstico sugere para o item: comprar (está na fila do plano), transferir (falta no CD e a rede tem o suficiente para o período de proteção), revisar (falta e nem o plano nem a rede resolvem), liquidar (sem giro há mais de um ano), segurar (excesso ou sem giro recente), manter.",
  "idade fifo": "Há quantos dias, em média, as peças do saldo atual estão no CD. Aproximação: percorre as entradas de mercadoria da mais recente para trás até cobrir o saldo e pondera a idade de cada entrada pela quantidade usada. O ERP não guarda lote; quando as entradas registradas não cobrem o saldo, a parte descoberta recebe a idade da entrada mais antiga disponível e o valor sai marcado com *.",
  "capital contabil": "Estoque físico vezes o custo médio contábil do ERP no último dia com estoque. É o valor que a contabilidade vê; o capital do modelo usa o custo corrigido (ver Custo unitário). A diferença entre os dois é um achado em si.",
  "otimo": "Capital que a política do modelo manteria em estoque médio para o item (estoque médio vezes custo). Real menos ótimo é quanto a operação carrega a mais ou a menos do que a política recomenda.",
  "real menos otimo": "Capital em estoque hoje menos o capital que a política do modelo manteria em estoque médio. Positivo: a empresa carrega mais do que a política recomenda; negativo: menos.",
  "excesso": "Peças acima do estoque máximo do modelo vezes o custo unitário. Só existe na faixa Excesso.",
  "sem giro": "Capital em itens com peça no CD e sem venda há mais dias que o limiar escolhido na página (padrão 180).",
  "dias sem venda": "Dias desde a última venda registrada em qualquer loja até a data da posição. 'nunca' quando não há venda no histórico.",
  "rede": "Saldo somado nas lojas e demais locais fora do CD, na foto mais recente extraída do DW. Não entra na política; serve para apontar transferência em vez de compra.",
  "lojas": "Quantos locais fora do CD têm saldo positivo do item.",
  "precisa no periodo": "Demanda esperada no período de proteção (prazo de entrega + revisão), μ do período. Se a rede tem pelo menos isso, a transferência cobre a falta do CD.",
  "valor em loja": "Saldo em loja vezes o custo médio contábil de cada local, somado.",
  "comprador": "Comprador oficial do item no cadastro do ERP.",
```

- [ ] **Step 5: Verificar no navegador**

```bash
cd $WT && (uvicorn backend.main:app --port 8011 >/tmp/diag_uvicorn.log 2>&1 &) && sleep 3 && \
python -c "
from fastapi.testclient import TestClient; from backend.main import app
r = TestClient(app).get('/diagnostico'); print(r.status_code); assert 'g-faixas' in r.text and 'diagnostico.js' in r.text"
```
Expected: `200`. Depois abrir `http://localhost:8011/diagnostico` (ou usar a skill `webapp-testing` com Playwright para uma captura) e conferir: cinco KPIs preenchidos, dois gráficos, tabela agregada com abas funcionando, clique numa linha filtra itens, botão CSV baixa arquivo, mudar "sem giro" recarrega, clicar num item abre a gaveta do dossiê. Matar o servidor ao final: `pkill -f "uvicorn backend.main:app --port 8011"`.

- [ ] **Step 6: Commit**

```bash
cd $WT && git add templates/diagnostico.html static/diagnostico.js templates/base.html static/glossario.js
git commit -m "diagnostico: pagina com visao geral, idade, agregado por dimensao, rede e itens

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Bloco 9 em `scripts/revisao.py`

**Files:**
- Modify: `scripts/revisao.py` — nova função `bloco9` antes de `main()`, registro na lista `blocos`, `--so` aceita 9, import de `diagnostico`.

**Interfaces:**
- Consumes: `diagnostico.classificar_faixas`, `diagnostico.carregar`, `diagnostico.agregar`, `diagnostico.resumo_geral`, `FAIXAS`; tabelas `mart_estoque_posicao`, `stg_entradas`, `res_plano_compra`.

- [ ] **Step 1: Escrever o bloco**

Import, junto dos demais `from backend...`:

```python
from backend import diagnostico  # noqa: E402
```

Antes de `# ----------------------------------------------------------------------\ndef main()`:

```python
# ======================================================================
# BLOCO 9 - diagnostico do estoque atual
# ======================================================================
def _fifo_python(entradas: pd.DataFrame, saldo: float, data_pos) -> tuple[float | None, bool]:
    """A mesma regra do mart, em Python puro: da entrada mais recente para
    tras ate cobrir o saldo; a fracao descoberta herda a idade da mais antiga."""
    if saldo <= 0 or entradas.empty:
        return None, saldo <= 0
    e = entradas.sort_values(["data", "pecas"], ascending=[False, True])
    resta, soma, coberto = float(saldo), 0.0, 0.0
    for d, q in zip(e.data, e.pecas):
        if resta <= 0:
            break
        usa = min(float(q), resta)
        soma += usa * (data_pos - d).days
        coberto += usa
        resta -= usa
    if resta > 1e-9:
        soma += resta * (data_pos - e.data.min()).days
        return soma / saldo, False
    return soma / coberto, True


def bloco9(r: Relatorio, wh, p, ctx) -> None:
    B = "9. diagnostico do estoque"
    if not wh.existe("mart_estoque_posicao"):
        r.alerta(B, "mart_estoque_posicao ausente", "rode o dbt com o modelo novo")
        return
    pos = wh.query(f"select * from {ref('mart_estoque_posicao')}")
    plano = ctx["plano"]

    # 9.1 um registro por SKU do catalogo, e o saldo e o do plano
    r.registrar("OK" if pos.sku.is_unique and len(pos) == len(plano) else "FALHA", B,
                "um registro por SKU no mart",
                f"{len(pos)} linhas, {pos.sku.nunique()} SKUs, plano com {len(plano)}")
    j = pos.merge(plano[["sku", "estoque_fisico"]], on="sku")
    r.compara(B, "saldo_cd = estoque_fisico do plano", j.saldo_cd, j.estoque_fisico, tol=1e-9)

    # 9.2 faixas recompostas em numpy
    df = diagnostico.carregar(wh, 180)
    fis = df.estoque_fisico.fillna(0).to_numpy(float)
    posi = df.posicao_estoque.fillna(0).to_numpy(float)
    dem = df.demanda_media_dia.fillna(0).to_numpy(float)
    rop = df.ponto_de_pedido.fillna(0).to_numpy(float)
    mx = df.estoque_maximo.fillna(0).to_numpy(float)
    dias = (pd.to_datetime(df.data_posicao) - pd.to_datetime(df.ultima_venda)).dt.days.to_numpy(float)
    dias = np.where(np.isnan(dias), np.inf, dias)
    esperado = np.full(len(df), diagnostico.FAIXAS[4], dtype=object)
    esperado[(fis <= 0) & (dem > 0)] = diagnostico.FAIXAS[0]
    m_risco = ~((fis <= 0) & (dem > 0)) & (posi <= rop)
    esperado[m_risco] = diagnostico.FAIXAS[1]
    m_sg = (esperado == diagnostico.FAIXAS[4]) & (fis > 0) & (dias > 180)
    esperado[m_sg] = diagnostico.FAIXAS[2]
    m_ex = (esperado == diagnostico.FAIXAS[4]) & (fis > mx)
    esperado[m_ex] = diagnostico.FAIXAS[3]
    dif = int((esperado != df.faixa.to_numpy()).sum())
    r.registrar("OK" if dif == 0 else "FALHA", B, "faixas recompostas em numpy",
                f"{dif} divergencias em {len(df)} itens")
    r.compara(B, "capital_modelo = fisico x custo", fis * df.custo_unitario.fillna(0).to_numpy(float),
              df.capital_modelo, tol=TOL)
    r.compara(B, "excesso_valor = (fisico - maximo) x custo, so na faixa Excesso",
              np.where(m_ex, (fis - mx) * df.custo_unitario.fillna(0).to_numpy(float), 0.0),
              df.excesso_valor, tol=TOL)

    # 9.3 idade FIFO numa amostra de 300 itens com saldo
    ent = wh.query(f"select sku, data, pecas from {ref('stg_entradas')}")
    ent["data"] = pd.to_datetime(ent.data)
    com_saldo = pos[pos.saldo_cd > 0]
    amostra = com_saldo.sample(min(300, len(com_saldo)), random_state=7)
    data_pos = pd.Timestamp(pos.data_posicao.max())
    calc, grav, cob_ok = [], [], 0
    for row in amostra.itertuples(index=False):
        e = ent[(ent.sku == row.sku) & (ent.data <= data_pos)]
        idade, cobre = _fifo_python(e, float(row.saldo_cd), data_pos)
        if idade is None or row.idade_fifo_dias is None or pd.isna(row.idade_fifo_dias):
            if (idade is None) == (row.idade_fifo_dias is None or pd.isna(row.idade_fifo_dias)):
                cob_ok += 1
            continue
        calc.append(idade); grav.append(float(row.idade_fifo_dias))
        cob_ok += int(bool(cobre) == bool(row.entradas_cobrem_saldo))
    if calc:
        pior = float(np.max(np.abs(np.array(calc) - np.array(grav))))
        r.registrar("OK" if pior <= 0.5 else "FALHA", B, "idade FIFO recomposta em Python",
                    f"pior diferenca {pior:.3f} dias em {len(calc)} itens")
    r.registrar("OK" if cob_ok == len(amostra) else "FALHA", B, "flag entradas_cobrem_saldo",
                f"{cob_ok} de {len(amostra)} coincidem")
    r.registrar("OK" if (pos.idade_fifo_dias.dropna() >= 0).all() else "FALHA", B,
                "idade FIFO nunca negativa", f"min {pos.idade_fifo_dias.min()}")

    # 9.4 somas por dimensao fecham com o total
    g = diagnostico.resumo_geral(df)
    for por in diagnostico.DIMENSOES:
        a = diagnostico.agregar(df, por)
        soma = sum(x["capital_modelo"] for x in a)
        n = sum(x["itens"] for x in a)
        ok = abs(soma - g["capital_modelo"]) < 1e-6 and n == len(df)
        r.registrar("OK" if ok else "FALHA", B, f"agregado por {por} fecha com o total",
                    f"R$ {soma:,.2f} vs R$ {g['capital_modelo']:,.2f} · {n} itens")
    soma_f = sum(f["capital_modelo"] for f in g["faixas"])
    r.registrar("OK" if abs(soma_f - g["capital_modelo"]) < 1e-6 else "FALHA", B,
                "faixas fecham com o total", f"R$ {soma_f:,.2f}")

    # 9.5 diagnostico: quanto ha em cada faixa (informativo)
    for f in g["faixas"]:
        r.ok(B, f"[info] {f['faixa']}", f"{f['itens']} itens · R$ {f['capital_modelo']:,.0f}")
    r.ok(B, "[info] real - otimo", f"R$ {g['diferenca_real_otimo']:,.0f} · cobertura real "
         f"{g['cobertura_real_dias']:.0f}d vs otima {g['cobertura_otima_dias']:.0f}d")
```

Em `main()`: `--so` help para `(1..9)`; lista `blocos = [bloco1, bloco2, bloco3, bloco4, bloco5, None, bloco7, bloco8, bloco9]`.

- [ ] **Step 2: Rodar só o bloco 9, depois a bateria completa**

```bash
cd $WT && python scripts/revisao.py --so 9
python scripts/revisao.py
python scripts/conferir.py --linhas 800
```
Expected: bloco 9 sem FALHA; bateria completa com o mesmo resultado de antes nos blocos 1-8 (nenhuma FALHA nova); `conferir.py` inalterado.

- [ ] **Step 3: Commit**

```bash
cd $WT && git add scripts/revisao.py
git commit -m "revisao: bloco 9 recompoe faixas, idade FIFO e somas do diagnostico

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Foto congelada numa data (`ate`)

**Files:**
- Modify: `backend/diagnostico.py` (`carregar` ganha `ate`)
- Modify: `backend/main.py` (`_diag` e todas as rotas `/api/diagnostico/*`, `/diagnostico`, `/diagnostico.csv` aceitam `ate: str = ""`)
- Modify: `templates/diagnostico.html` + `static/diagnostico.js` (campo de data no cabeçalho; aviso quando `ate` está ativo)

**Interfaces:**
- `carregar(wh, dias_sem_giro=180, ate: str | None = None)`. Com `ate`: roda `modelo.executar(wh, Parametros.carregar(), ate=ate)["res_plano_compra"]` em memória (mesmo caminho do backtest), `ultima_venda` vem de `stg_vendas` até `ate`, `custo_medio_erp` de `stg_custo_medio_erp` até `ate`, `fornecedor/comprador` do mart; `idade_fifo_dias`, `saldo_lojas`, `lojas_com_saldo`, `valor_lojas_erp` ficam NaN/0 e `entradas_cobrem_saldo` False; `data_posicao = ate`.
- `resumo_geral` ganha `"congelado": bool` (True quando `ate`).

- [ ] **Step 1: Checagem (falha antes)**

```bash
cd $WT && python -c "
import sys; sys.path.insert(0,'.')
from backend import diagnostico as dg; from backend.warehouse import abrir
df = dg.carregar(abrir(), 180, ate='2026-06-30')
assert str(df.data_posicao.max())[:10] == '2026-06-30'
assert df.idade_fifo_dias.isna().all() and (df.saldo_lojas == 0).all()
assert set(df.faixa) <= set(dg.FAIXAS); print('OK ate', len(df))"
```
Expected: `TypeError: carregar() got an unexpected keyword argument 'ate'`.

- [ ] **Step 2: Implementar em `diagnostico.carregar`**

Substituir a função por:

```python
def carregar(wh: Warehouse, dias_sem_giro: int = DIAS_SEM_GIRO_PADRAO,
             ate: str | None = None) -> pd.DataFrame:
    """res_plano_compra (politica) x mart_estoque_posicao (foto), classificado.

    Com `ate`, a foto e congelada naquela data: o motor roda em memoria como
    no backtest (mesmo caminho, nunca um paralelo), a ultima venda e o custo
    contabil sao lidos ate a data, e o que so existe para hoje (idade FIFO,
    saldo das lojas) fica em branco - a pagina avisa.
    """
    posicao = wh.query(f"select {', '.join(COLUNAS_POSICAO)} from {ref('mart_estoque_posicao')}")
    if ate is None:
        plano = wh.query(f"select {', '.join(COLUNAS_PLANO)} from {ref('res_plano_compra')}")
    else:
        from .config import Parametros
        from .modelo import executar
        corte = str(pd.Timestamp(ate).date())
        plano = executar(wh, Parametros.carregar(), ate=corte)["res_plano_compra"][COLUNAS_PLANO].copy()
        venda = wh.query(
            f"select sku, max(data) as ultima_venda from {ref('stg_vendas')} "
            f"where pecas_vendidas > 0 and data <= DATE '{corte}' group by sku")
        custo = wh.query(
            f"select sku, custo as custo_medio_erp from ("
            f"  select sku, custo, row_number() over (partition by sku order by data desc) rn "
            f"  from {ref('stg_custo_medio_erp')} where data <= DATE '{corte}') where rn = 1")
        posicao = posicao[["sku", "fornecedor", "comprador"]].merge(venda, on="sku", how="left") \
                                                              .merge(custo, on="sku", how="left")
        posicao["data_posicao"] = pd.Timestamp(corte)
        for c, v in (("saldo_cd", np.nan), ("saldo_lojas", 0.0), ("lojas_com_saldo", 0),
                     ("valor_lojas_erp", 0.0), ("ultima_entrada", pd.NaT),
                     ("idade_fifo_dias", np.nan), ("entrada_mais_antiga_em_estoque", pd.NaT),
                     ("entradas_cobrem_saldo", False)):
            posicao[c] = v
    df = plano.merge(posicao, on="sku", how="left")
    df["data_posicao"] = pd.to_datetime(df.data_posicao).fillna(pd.Timestamp(posicao.data_posicao.max()))
    for c in ("saldo_lojas", "lojas_com_saldo", "valor_lojas_erp"):
        df[c] = df[c].fillna(0)
    df["fornecedor"] = df.fornecedor.fillna("Nao informado")
    df["comprador"] = df.comprador.fillna("Nao informado")
    df.attrs["congelado"] = ate is not None
    return classificar_faixas(df, dias_sem_giro)
```

Em `resumo_geral`, acrescentar ao dicionário devolvido: `"congelado": bool(df.attrs.get("congelado", False)),`.

- [ ] **Step 3: Rotas**

Em `main.py`, `_diag`:

```python
def _diag(dias_sem_giro: int, ate: str = "") -> pd.DataFrame:
    chave = (int(dias_sem_giro), ate or "")
    if chave not in _cache_diagnostico:
        _cache_diagnostico[chave] = diagnostico.carregar(wh(), int(dias_sem_giro), ate or None)
    return _cache_diagnostico[chave]
```

Cada rota `/api/diagnostico/*`, `/diagnostico` e `/diagnostico.csv` ganha o parâmetro `ate: str = ""` e passa `_diag(dias_sem_giro, ate)`. Na página, `contexto(..., ate=ate)`.

- [ ] **Step 4: Frontend**

No template, dentro de `{% block acoes %}`, antes do input de dias:

```html
  <label class="pequeno t4" style="display:inline-flex;align-items:center;gap:8px">
    foto em <input type="date" id="ate" value="{{ ate }}"> </label>
```

E logo no início de `{% block conteudo %}`:

```html
<div class="msg msg-am" id="aviso-ate" style="display:none">
  Foto congelada em <b id="aviso-ate-data"></b>: o motor rodou como naquele dia. Idade FIFO e saldo das lojas só existem para a posição de hoje e ficam em branco.
</div>
```

No JS: `estado.ate = document.getElementById("ate").value || ""`; `qs()` inclui `ate` quando não vazio; listener `change` em `#ate` seta `estado.ate` e chama `tudo()`; em `geral()`, após receber `g`: `document.getElementById("aviso-ate").style.display = g.congelado ? "" : "none"; document.getElementById("aviso-ate-data").textContent = N.dataLonga(g.data_posicao);`.

- [ ] **Step 5: Verificar**

```bash
cd $WT && python -c "
import sys; sys.path.insert(0,'.')
from backend import diagnostico as dg; from backend.warehouse import abrir
df = dg.carregar(abrir(), 180, ate='2026-06-30')
assert str(df.data_posicao.max())[:10] == '2026-06-30'
assert df.idade_fifo_dias.isna().all() and (df.saldo_lojas == 0).all()
assert set(df.faixa) <= set(dg.FAIXAS); print('OK ate', len(df))"
python -c "
from fastapi.testclient import TestClient; from backend.main import app
c = TestClient(app); r = c.get('/api/diagnostico/geral?ate=2026-06-30'); print(r.status_code, r.json()['congelado'], r.json()['data_posicao'])
print(c.get('/diagnostico?ate=2026-06-30').status_code)"
```
Expected: `OK ate 19141` (ou o número de SKUs com histórico até a data), `200 True 2026-06-30`, `200`.

- [ ] **Step 6: Commit**

```bash
cd $WT && git add backend/diagnostico.py backend/main.py templates/diagnostico.html static/diagnostico.js
git commit -m "diagnostico: foto congelada numa data (ate), pelo mesmo caminho do backtest

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Documentação e verificação final

**Files:**
- Modify: `CLAUDE.md` (módulo novo na lista "Other backend modules"; `mart_estoque_posicao` na descrição do fluxo; nota sobre a var `posicao_lojas`)
- Modify: `README.md` (seção curta "Diagnóstico do estoque" com as cinco faixas, a idade FIFO e as duas valorizações)
- Modify: `backend/main.py` docstring (lista de telas ganha `/diagnostico`)

- [ ] **Step 1: CLAUDE.md**

Na lista de módulos, depois do item `backend/acompanhamento.py`:

```markdown
- `backend/diagnostico.py` — leitura do estoque **de hoje** (ou de uma data `ate`): cruza
  `res_plano_compra` com `mart_estoque_posicao` e classifica cada SKU em uma faixa
  (zerado com demanda / risco / sem giro / excesso / saudável, nesta ordem de prioridade),
  valoriza ao custo do modelo e ao custo médio contábil do ERP, mede a idade FIFO do saldo
  e agrega por fornecedor, comprador e família. Não recalcula política: só lê. Funções puras
  sobre DataFrame para `revisao.py` (bloco 9) recompor. Feeds the `/diagnostico` page.
```

Na seção de comandos, depois do parágrafo sobre `dbt build`:

```markdown
A foto da posição das lojas (`raw_estoque_posicao_lojas`) só existe na extração direta do DW;
`rodar_pipeline.py` passa `posicao_lojas: true` ao dbt quando a base é `dw`, e a view
`stg_estoque_posicao_lojas` sai vazia nas outras bases.
```

- [ ] **Step 2: README.md**

Acrescentar, perto do fim (antes de qualquer seção de "como rodar" se houver), uma seção:

```markdown
## Diagnóstico do estoque atual

A página `/diagnostico` responde "o que está parado, o que está em risco e quanto isso custa",
sem recalcular a política. Cada SKU cai numa faixa, avaliada nesta ordem: **zerado com demanda**
(sem peça e com demanda corrigida positiva), **risco** (posição ≤ ponto de pedido), **sem giro**
(há peça e não vende há mais de N dias, padrão 180), **excesso** (acima do estoque máximo do
modelo), **saudável**. O estoque é valorizado duas vezes: ao custo que o modelo usa e ao custo
médio contábil do ERP. A **idade FIFO** aproxima há quanto tempo o saldo está no CD percorrendo
as entradas da mais recente para trás até cobrir o saldo. A foto das lojas (uma linha por SKU e
local, sem histórico) aponta transferência em vez de compra. `scripts/revisao.py` (bloco 9)
recompõe faixas, idade e somas por fora.
```

- [ ] **Step 3: Docstring de `main.py`**

Na lista `Telas:` do docstring, depois de `/plano`:

```
  /diagnostico  o estoque de hoje por faixa (zerado, risco, sem giro, excesso), idade e rede
```

- [ ] **Step 4: Verificação final completa**

```bash
cd $WT && python scripts/revisao.py && python scripts/conferir.py --linhas 800 && \
python -c "
from fastapi.testclient import TestClient; from backend.main import app
c = TestClient(app)
for u in ['/diagnostico','/api/diagnostico/geral','/api/diagnostico/idade','/api/diagnostico/agregado?por=familia','/api/diagnostico/rede','/api/diagnostico/itens','/api/diagnostico/confianca','/diagnostico.csv']:
    assert c.get(u).status_code == 200, u
print('rotas OK')"
git status --short
```
Expected: revisão sem FALHA, conferência inalterada, rotas OK, árvore limpa.

- [ ] **Step 5: Commit**

```bash
cd $WT && git add CLAUDE.md README.md backend/main.py
git commit -m "docs: diagnostico do estoque atual

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 6: Entrega**

Não fazer merge em `main` sem o usuário: a árvore principal tem trabalho não commitado de outra sessão. Reportar a branch `diagnostico-estoque` no worktree, os arquivos compartilhados que vão precisar de merge manual (`stg_catalogo.sql`, `sources.yml`, `schema.yml`, `extrair_dw.py`, `rodar_pipeline.py`, `main.py`, `revisao.py`, `glossario.js`, `base.html`) e os números do bloco 9 (reais por faixa, real − ótimo).
