# Demanda por canal (e-commerce × lojas) — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** separar a demanda de cada SKU em dois fluxos (e-commerce = empresa 33, lojas = as demais somadas), manter uma só política de estoque e uma só compra da empresa 26, e dar ao e-commerce uma fatia interna de caixa com teto próprio.

**Architecture:** o dbt passa a carregar `pecas_ecommerce`/`pecas_lojas` na grade SKU × dia (formato largo, censura compartilhada). `modelo.py` estima média/desvio por canal com as mesmas máscaras de dia, deriva `share_ecommerce`, pondera o custo de ruptura por canal e cobra cada peça da fila aos dois caixas na proporção da participação. O backtest e as telas ganham a quebra por canal; `revisao.py` recompõe cada conta nova.

**Tech Stack:** dbt + DuckDB (SQL), Python 3 (pandas/numpy/scipy), FastAPI + Jinja2, ECharts. Sem pytest: a verificação é `scripts/revisao.py` (bateria) e `scripts/conferir.py`, mais checagens `python -c` pontuais indicadas em cada tarefa.

**Spec:** `docs/superpowers/specs/2026-09-17-demanda-por-canal-design.md`

## Global Constraints

- Interpretador: sempre `.venv/bin/python` (o `python` do sistema não tem duckdb).
- Recalcular depois de mexer no dbt: `.venv/bin/python scripts/rodar_pipeline.py --pular-carga` (roda dbt build + modelo). Só no Python: acrescentar `--pular-dbt`.
- Bateria: `.venv/bin/python scripts/revisao.py` (ou `--so N` para um bloco). Estado de partida: 87 ok · 13 alertas · 2 falhas (as duas falhas são de sanidade econômica pré-existentes: custo de cadastro do item "P TOALHA DOCOL…" e "as duas estratégias gastam caixa comparável"). Nenhuma tarefa pode adicionar falha; as duas pré-existentes ficam.
- Nomes: o canal do modelo é a coluna **`canal_demanda`** com valores `'ecommerce'` e `'lojas'`. A coluna `canal` já existe em `stg_vendas` (é o nome da loja) e **não** muda. Colunas por canal levam sufixo `_ecommerce` / `_lojas`.
- Invariante de dados: `pecas_ecommerce + pecas_lojas = pecas_vendidas` em toda linha da grade diária, em qualquer base (`real`/`dw`, `exports`, `sintetica`).
- Toda query multi-etapa em uma só chamada `wh.query()` (CTEs), nunca em chamadas separadas.
- Comentários e textos de tela em português, sem acento nos comentários de código SQL/Python (segue o padrão do repositório); textos de interface com acento.
- Commits pequenos, um por tarefa, mensagem em português, terminando com `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

## Mapa de arquivos

| Arquivo | Responsabilidade nesta feature |
|---|---|
| `dbt_elevato/models/staging/stg_vendas.sql` | cria `canal_demanda` nas três bases |
| `dbt_elevato/models/intermediate/int_vendas_sku_dia.sql` | soma `pecas_ecommerce` e `lucro_ecommerce` por SKU × dia |
| `dbt_elevato/models/intermediate/int_demanda_diaria.sql` | põe `pecas_ecommerce`/`pecas_lojas` na grade, com o invariante por construção |
| `dbt_elevato/models/marts/mart_estoque_diario.sql` | expõe as duas colunas |
| `dbt_elevato/models/marts/mart_sku_financeiro.sql` | peças, receita, lucro e lucro/peça por canal |
| `backend/modelo.py` | `SQL_FINANCEIRO_ATE` espelho; `ler_base`; `estatistica_demanda` por canal; `_margem_coerente` por canal; `modelar` (Cu ponderado); `candidatas_marginais` (colunas por canal, `custo_ecommerce`); `regra_de_parada`/`caminhar`/`alocacao_marginal`/`plano_marginal` (dois caixas) |
| `backend/config.py` | 4 parâmetros novos + metadados de tela |
| `backend/analitico.py` | `ETAPAS_FILA`, `rateio_por_loja` por canal, `dossie` |
| `backend/validacao.py` | fatia rígida desligada no backtest; colunas por canal no relatório |
| `backend/main.py` | resumo do `/plano` e `/painel` por canal; `/api/plano/rateio` |
| `templates/plano.html`, `templates/painel.html`, `templates/validacao.html` | blocos por canal |
| `static/item.js`, `static/peca.js` | estatística por canal no dossiê; fórmula de M |
| `scripts/revisao.py`, `scripts/conferir.py` | checks novos; M ponderado; bloco 8 |
| `README.md`, `CLAUDE.md`, spec | documentação |

---

### Task 1: dbt — `canal_demanda` e a grade diária por canal

**Files:**
- Modify: `dbt_elevato/models/staging/stg_vendas.sql` (três ramos: real ≈ l.138, exports ≈ l.210, sintetica ≈ l.242)
- Modify: `dbt_elevato/models/intermediate/int_vendas_sku_dia.sql`
- Modify: `dbt_elevato/models/intermediate/int_demanda_diaria.sql`
- Modify: `dbt_elevato/models/marts/mart_estoque_diario.sql`
- Modify: `dbt_elevato/models/marts/mart_sku_financeiro.sql`
- Modify: `backend/modelo.py` (`SQL_FINANCEIRO_ATE`, ≈ l.1034)
- Modify: `scripts/revisao.py` (`main()` queries de `dia`; `bloco1`)

**Interfaces:**
- Produces: `stg_vendas.canal_demanda` (varchar: `'ecommerce'|'lojas'`); `mart_estoque_diario.pecas_ecommerce`, `.pecas_lojas` (integer); `mart_sku_financeiro.pecas_vendidas_ecommerce`, `.pecas_vendidas_lojas`, `.receita_liquida_ecommerce`, `.receita_liquida_lojas`, `.lucro_observado_ecommerce`, `.lucro_observado_lojas`, `.lucro_por_peca_ecommerce`, `.lucro_por_peca_lojas` (double). `SQL_FINANCEIRO_ATE` devolve as mesmas colunas.

- [ ] **Step 1: escrever o check que falha (bloco 1 da revisão)**

Em `scripts/revisao.py::main()`, trocar as duas queries de `ctx["dia"]` e `ctx["dia_completo"]` para `select *` (mantendo o `where`/`order by`), para que colunas novas apareçam sem editar a lista:

```python
        "dia": wh.query(
            f"select * from {ref('mart_estoque_diario')} "
            f"where data > (select max(data) from {ref('mart_estoque_diario')})"
            f" - INTERVAL {int(getattr(p, 'janela_estimacao_dias', 0) or 99999)} DAY "
            f"order by sku, data"),
        "dia_completo": wh.query(
            f"select * from {ref('mart_estoque_diario')} order by sku, data"),
        "financeiro": wh.query(f"select * from {ref('mart_sku_financeiro')}"),
```

No fim de `bloco1`, acrescentar:

```python
    # --- os dois canais fecham com o total, dia a dia e no agregado ---
    # A grade traz a venda por canal em formato LARGO porque a censura e do
    # estoque compartilhado: um dia de ruptura censura os dois canais. A
    # identidade abaixo vale por construcao (int_demanda_diaria) e e o que
    # permite estimar cada canal com as mesmas mascaras de dia do total.
    if {"pecas_ecommerce", "pecas_lojas"} <= set(dia.columns):
        r.compara(B, "pecas e-commerce + lojas = pecas vendidas (dia a dia)",
                  (dia.pecas_ecommerce + dia.pecas_lojas).to_numpy(),
                  dia.pecas_vendidas.to_numpy(), contexto="grade SKU x dia")
        r.afirma(B, "pecas por canal nao negativas",
                 bool((dia.pecas_ecommerce >= 0).all() and (dia.pecas_lojas >= 0).all()),
                 f"e-commerce {int(dia.pecas_ecommerce.sum()):,} pecas · "
                 f"lojas {int(dia.pecas_lojas.sum()):,} pecas na janela")
    else:
        r.falha(B, "grade diaria traz pecas por canal",
                "faltam pecas_ecommerce / pecas_lojas em mart_estoque_diario")
    fin = ctx["financeiro"]
    if {"pecas_vendidas_ecommerce", "lucro_observado_ecommerce"} <= set(fin.columns):
        r.compara(B, "financeiro: pecas por canal fecham com o total",
                  (fin.pecas_vendidas_ecommerce + fin.pecas_vendidas_lojas).to_numpy(),
                  fin.pecas_vendidas.to_numpy())
        r.compara(B, "financeiro: lucro por canal fecha com o total",
                  (fin.lucro_observado_ecommerce + fin.lucro_observado_lojas).to_numpy(),
                  fin.lucro_observado.to_numpy(), tol=TOL_FROUXA)
    else:
        r.falha(B, "financeiro traz colunas por canal",
                "faltam *_ecommerce / *_lojas em mart_sku_financeiro")
```

- [ ] **Step 2: rodar e ver falhar**

Run: `.venv/bin/python scripts/revisao.py --so 1 2>&1 | tail -8`
Expected: 2 FALHAS novas: "grade diaria traz pecas por canal" e "financeiro traz colunas por canal".

- [ ] **Step 3: `stg_vendas.sql` — `canal_demanda` nos três ramos**

Ramo `real` (logo após a linha `v.loja_id,` no `select` final, ≈ l.139):

```sql
    -- O canal que o MODELO separa: o e-commerce (empresa 33) contra as demais
    -- lojas somadas. Sao dinamicas de venda diferentes, e a mistura escondia
    -- isso. `canal`, logo acima, e o NOME da loja e continua existindo.
    case when v.loja_id = '33' then 'ecommerce' else 'lojas' end as canal_demanda,
```

Ramo `exports` (após `'33' as loja_id,` ≈ l.210) — o export diario e so a venda do e-commerce:

```sql
    'ecommerce'                                       as canal_demanda,
```

Ramo `sintetica` (após `cast(null as varchar) as loja_id,` ≈ l.242) — sem loja, tudo cai em lojas e o modelo degenera para um canal:

```sql
    'lojas'                               as canal_demanda,
```

- [ ] **Step 4: `int_vendas_sku_dia.sql` — soma do e-commerce**

Acrescentar duas linhas ao `select` (antes de `from`):

```sql
    sum(case when canal_demanda = 'ecommerce' then pecas_vendidas else 0 end) as pecas_ecommerce,
    sum(case when canal_demanda = 'ecommerce' then lucro else 0 end)          as lucro_ecommerce,
```

- [ ] **Step 5: `int_demanda_diaria.sql` — o invariante por construção**

Acrescentar ao `select` (depois de `e.pecas_vendidas,`):

```sql
    -- Venda por canal, presa a identidade pecas_ecommerce + pecas_lojas =
    -- pecas_vendidas POR CONSTRUCAO: o e-commerce nunca passa do total do dia
    -- e as lojas ficam com o resto. Na base real as duas fontes sao a mesma
    -- tabela e o `least` nao corta nada; na sintetica a grade vem do estoque e
    -- a venda de outra tabela, e sem isso a identidade quebraria.
    cast(least(coalesce(v.pecas_ecommerce, 0), e.pecas_vendidas) as integer)      as pecas_ecommerce,
    cast(e.pecas_vendidas
         - least(coalesce(v.pecas_ecommerce, 0), e.pecas_vendidas) as integer)    as pecas_lojas,
```

- [ ] **Step 6: `mart_estoque_diario.sql` — expor**

Acrescentar `pecas_ecommerce,` e `pecas_lojas,` após `pecas_vendidas,`.

- [ ] **Step 7: `mart_sku_financeiro.sql` — por canal**

No CTE `v`, após `sum(lucro) as lucro,`:

```sql
        sum(case when canal_demanda = 'ecommerce' then pecas_vendidas else 0 end) as pecas_ecommerce,
        sum(case when canal_demanda = 'ecommerce' then receita_liquida else 0 end) as receita_ecommerce,
        sum(case when canal_demanda = 'ecommerce' then lucro else 0 end)          as lucro_ecommerce
```
(atenção à vírgula da linha anterior: `sum(lucro) as lucro,`).

No `select` final, após `margem_pct`:

```sql
    ,
    -- a mesma economia, por canal. O e-commerce tem frete e preco proprios;
    -- a margem dele nao e a das lojas, e e isso que a ponderacao do custo de
    -- ruptura em modelo.py usa.
    coalesce(v.pecas_ecommerce, 0)                                  as pecas_vendidas_ecommerce,
    coalesce(v.pecas_vendidas, 0) - coalesce(v.pecas_ecommerce, 0)  as pecas_vendidas_lojas,
    coalesce(v.receita_ecommerce, 0)                                as receita_liquida_ecommerce,
    coalesce(v.receita_liquida, 0) - coalesce(v.receita_ecommerce, 0) as receita_liquida_lojas,
    coalesce(v.lucro_ecommerce, 0)                                  as lucro_observado_ecommerce,
    coalesce(v.lucro, 0) - coalesce(v.lucro_ecommerce, 0)           as lucro_observado_lojas,
    case when coalesce(v.pecas_ecommerce, 0) > 0
         then v.lucro_ecommerce / v.pecas_ecommerce else 0 end      as lucro_por_peca_ecommerce,
    case when coalesce(v.pecas_vendidas, 0) - coalesce(v.pecas_ecommerce, 0) > 0
         then (v.lucro - v.lucro_ecommerce) / (v.pecas_vendidas - v.pecas_ecommerce)
         else 0 end                                                 as lucro_por_peca_lojas
```

- [ ] **Step 8: `SQL_FINANCEIRO_ATE` em `backend/modelo.py` — espelhar**

No CTE `v` do SQL, após `sum(lucro) as lucro`:

```sql
           ,
           sum(case when canal_demanda = 'ecommerce' then pecas_vendidas else 0 end) as pecas_ecommerce,
           sum(case when canal_demanda = 'ecommerce' then receita_liquida else 0 end) as receita_ecommerce,
           sum(case when canal_demanda = 'ecommerce' then lucro else 0 end)          as lucro_ecommerce
```

E no `select` final, após a linha de `margem_pct`, as mesmas 8 expressões do Step 7 (copiar literalmente, com o mesmo alias). O `revisao.py` cobra a igualdade entre o mart e este espelho na última data.

- [ ] **Step 9: rodar o pipeline e a bateria**

Run: `.venv/bin/python scripts/rodar_pipeline.py --pular-carga 2>&1 | tail -5`
Expected: dbt build sem erro, modelo grava `res_*`.

Run: `.venv/bin/python scripts/revisao.py 2>&1 | tail -6`
Expected: os 4 checks novos em OK; total ≥ 91 ok; falhas continuam sendo apenas as 2 pré-existentes.

Run (conferência direta do invariante):
```bash
.venv/bin/python -c "
from backend.warehouse import abrir, ref
wh = abrir()
print(wh.query(f'select count(*) n, sum(case when pecas_ecommerce + pecas_lojas <> pecas_vendidas then 1 else 0 end) quebra, sum(pecas_ecommerce) e, sum(pecas_lojas) l from {ref(\"mart_estoque_diario\")}'))
print(wh.query(f'select canal_demanda, count(*) n, sum(pecas_vendidas) pecas from {ref(\"stg_vendas\")} group by 1'))
"
```
Expected: `quebra = 0`; dois canais em `stg_vendas` com `ecommerce` na casa de 10–20% das peças.

- [ ] **Step 10: commit**

```bash
git add dbt_elevato/models backend/modelo.py scripts/revisao.py
git commit -m "dbt: canal_demanda (e-commerce x lojas) e venda por canal na grade diaria e no financeiro

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: estatística de demanda por canal (`modelo.py`)

**Files:**
- Modify: `backend/modelo.py` (`ler_base` ≈ l.1090; `estatistica_demanda` ≈ l.74–125)
- Modify: `scripts/revisao.py` (`bloco2`)

**Interfaces:**
- Consumes: `mart_estoque_diario.pecas_ecommerce/pecas_lojas` (Task 1).
- Produces em `res_sku_modelo` (via `estatistica_demanda` → merge em `executar`): `demanda_media_dia_ecommerce`, `desvio_padrao_dia_ecommerce`, `demanda_media_dia_lojas`, `desvio_padrao_dia_lojas`, `demanda_media_dia_ingenua_ecommerce`, `demanda_media_dia_ingenua_lojas`, `share_ecommerce` (float em [0,1], = μ_e/(μ_e+μ_l), 0 se ambos zero), `covariancia_canais` (float, covariância nos dias disponíveis).

- [ ] **Step 1: check que falha (bloco 2)**

No fim de `bloco2` em `scripts/revisao.py`:

```python
    # --- os dois canais, estimados com as mesmas mascaras de dia ---
    cols = {"demanda_media_dia_ecommerce", "demanda_media_dia_lojas", "share_ecommerce",
            "demanda_media_dia_ingenua_ecommerce", "demanda_media_dia_ingenua_lojas"}
    if cols <= set(m.columns) and "pecas_ecommerce" in dia.columns:
        g_e = dia.groupby("sku").pecas_ecommerce.mean()
        g_l = dia.groupby("sku").pecas_lojas.mean()
        mi = m.set_index("sku")
        idx = g_e.index.intersection(mi.index)
        r.compara(B, "media ingenua do e-commerce = media da coluna na grade",
                  g_e.reindex(idx).to_numpy(), mi.demanda_media_dia_ingenua_ecommerce.reindex(idx).to_numpy(),
                  tol=TOL_FROUXA)
        r.compara(B, "media ingenua das lojas = media da coluna na grade",
                  g_l.reindex(idx).to_numpy(), mi.demanda_media_dia_ingenua_lojas.reindex(idx).to_numpy(),
                  tol=TOL_FROUXA)
        soma = m.demanda_media_dia_ecommerce + m.demanda_media_dia_lojas
        esperado = np.where(soma > 0, m.demanda_media_dia_ecommerce / soma.replace(0, np.nan), 0.0)
        r.compara(B, "participacao do e-commerce = mu_e / (mu_e + mu_l)",
                  np.nan_to_num(esperado), m.share_ecommerce.to_numpy(), tol=TOL_FROUXA)
        r.afirma(B, "participacao do e-commerce em [0, 1]",
                 bool(((m.share_ecommerce >= 0) & (m.share_ecommerce <= 1)).all()),
                 f"mediana {float(m.share_ecommerce.median()):.1%} · "
                 f"{int((m.share_ecommerce > 0.5).sum())} itens com o e-commerce majoritario")
        # A correcao de censura e nao linear: a soma das medias corrigidas por
        # canal nao precisa bater com a media corrigida do total. O tamanho da
        # diferenca e diagnostico, nao erro.
        com = m.demanda_media_dia > 0
        gap = ((soma - m.demanda_media_dia) / m.demanda_media_dia)[com]
        r.alerta(B, "soma dos canais corrigidos vs. total corrigido",
                 f"diferenca relativa mediana {float(gap.median()):+.2%} · "
                 f"p95 {float(gap.abs().quantile(.95)):.2%} · a EM por canal e a EM do total "
                 f"nao somam exatamente, e o modelo usa o total para a politica")
    else:
        r.falha(B, "modelo traz a demanda por canal",
                "faltam demanda_media_dia_ecommerce / _lojas / share_ecommerce em res_sku_modelo")
```

Run: `.venv/bin/python scripts/revisao.py --so 2 2>&1 | tail -5`
Expected: FALHA "modelo traz a demanda por canal".

- [ ] **Step 2: `ler_base` — trazer as colunas**

Em `ler_base`, a string `colunas` passa a ser:

```python
    colunas = ("sku, data, pecas_vendidas, pecas_ecommerce, pecas_lojas, estado_estoque, "
               "saldo_final, disponivel_final")
```

- [ ] **Step 3: `estatistica_demanda` — três estimativas por SKU**

Logo após `V = g.to_numpy(float)`:

```python
    # A venda por canal na MESMA grade (mesmos SKUs, mesmos dias). As mascaras
    # de dia sao as do estoque compartilhado: quando o CD zera, os dois canais
    # ficam censurados juntos - por isso o formato largo, e nao uma serie por
    # canal com censura propria.
    def grade(col: str) -> np.ndarray:
        if col not in diario.columns:
            return np.zeros_like(V)
        t = diario.pivot_table(index="sku", columns="data", values=col, aggfunc="sum")
        return t.reindex(index=g.index, columns=g.columns).fillna(0.0).to_numpy(float)
    VE = grade("pecas_ecommerce")
    VL = V - VE
```

Dentro do laço, depois do bloco `if insuficiente:` (que recalcula `m`, `s`), acrescentar:

```python
        # os dois canais pelo mesmo caminho do total, com as mesmas mascaras
        m_e, s_e, _, _ = em_censurado(VE[i], OK[i], CENS[i], p.imputar_dias_censurados)
        m_l, s_l, _, _ = em_censurado(VL[i], OK[i], CENS[i], p.imputar_dias_censurados)
        if insuficiente:
            m_e = float(VE[i].mean())
            m_l = float(VL[i].mean())
            s_e = float(VE[i].std(ddof=1)) if V.shape[1] > 1 else 0.0
            s_l = float(VL[i].std(ddof=1)) if V.shape[1] > 1 else 0.0
        soma = m_e + m_l
        share = m_e / soma if soma > 0 else 0.0
        # covariancia entre canais so nos dias em que os dois podiam vender;
        # e o que o bloco 8 da revisao usa para decompor a variancia do total
        dias_ok = OK[i]
        cov = float(np.cov(VE[i][dias_ok], VL[i][dias_ok])[0, 1]) if dias_ok.sum() >= 2 else 0.0
```

E no `dict(...)` da linha, acrescentar as chaves:

```python
            demanda_media_dia_ecommerce=m_e,
            desvio_padrao_dia_ecommerce=s_e,
            demanda_media_dia_lojas=m_l,
            desvio_padrao_dia_lojas=s_l,
            demanda_media_dia_ingenua_ecommerce=float(VE[i].mean()),
            demanda_media_dia_ingenua_lojas=float(VL[i].mean()),
            share_ecommerce=share,
            covariancia_canais=cov,
```

- [ ] **Step 4: recalcular e conferir**

Run: `.venv/bin/python scripts/rodar_pipeline.py --pular-carga --pular-dbt 2>&1 | tail -3`
Run: `.venv/bin/python scripts/revisao.py 2>&1 | tail -6`
Expected: 4 checks novos OK + 1 alerta novo; sem falha nova.

Run:
```bash
.venv/bin/python -c "
from backend.warehouse import abrir, ref
wh = abrir()
print(wh.query(f'select round(avg(share_ecommerce),3) share_medio, round(sum(demanda_media_dia_ecommerce)/sum(demanda_media_dia_ecommerce+demanda_media_dia_lojas),3) share_pecas, count(*) n from {ref(\"res_sku_modelo\")}'))
"
```
Expected: `share_pecas` na casa de 0,10–0,20 (o e-commerce é cerca de 17% da saída do CD).

- [ ] **Step 5: commit**

```bash
git add backend/modelo.py scripts/revisao.py
git commit -m "modelo: demanda media, desvio e participacao por canal com as mesmas mascaras de censura

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: economia da peça por canal — margem e custo de ruptura ponderados

**Files:**
- Modify: `backend/config.py` (`Parametros`, `CAMPOS`)
- Modify: `backend/modelo.py` (`_margem_coerente` ≈ l.1116; `modelar` l.151; `candidatas_marginais` ≈ l.516; `alocacao_marginal` l.855)
- Modify: `backend/analitico.py` (`ETAPAS_FILA` ≈ l.189–191; `dossie` ≈ l.945)
- Modify: `scripts/revisao.py` (`bloco4` l.532; `bloco6` l.806)
- Modify: `scripts/conferir.py` (l.59)
- Modify: `static/peca.js` (l.45–48)

**Interfaces:**
- Consumes: `share_ecommerce` (Task 2); `lucro_por_peca_ecommerce/_lojas` do mart (Task 1).
- Produces: `Parametros.fator_perda_ruptura_ecommerce: float = 0.85`, `Parametros.fator_perda_ruptura_lojas: float = 0.85`. Em `fin`/`res_sku_modelo`: `lucro_por_peca_ecommerce`, `lucro_por_peca_lojas` (refeitos pelo preço da janela), `lucro_por_peca_historico_ecommerce`, `lucro_por_peca_historico_lojas`. `custo_falta_unit = s·lucro_e·fator_e + (1−s)·lucro_l·fator_l`. Na fila (`res_fila_marginal`): colunas `share_ecommerce`, `lucro_por_peca_ecommerce`, `lucro_por_peca_lojas`, `fator_perda_ruptura_ecommerce`, `fator_perda_ruptura_lojas`; a coluna `fator_perda_ruptura` **sai** da fila.
- `p.fator_perda_ruptura` continua existindo: é usado só na "Política atual" de referência (`Cu_ref` em `executar`).

- [ ] **Step 1: checks que falham (bloco 4 e bloco 6)**

Em `bloco4`, substituir a linha `Cu = m.lucro_por_peca * p.fator_perda_ruptura` por:

```python
    # o custo de ruptura e a media por canal: a margem e a perda do e-commerce
    # sao outras, e a participacao dele no item pondera as duas
    share = (m.share_ecommerce.fillna(0.0) if "share_ecommerce" in m.columns
             else pd.Series(0.0, index=m.index))
    le = m.lucro_por_peca_ecommerce if "lucro_por_peca_ecommerce" in m.columns else m.lucro_por_peca
    ll = m.lucro_por_peca_lojas if "lucro_por_peca_lojas" in m.columns else m.lucro_por_peca
    Cu = (share * le * getattr(p, "fator_perda_ruptura_ecommerce", p.fator_perda_ruptura)
          + (1 - share) * ll * getattr(p, "fator_perda_ruptura_lojas", p.fator_perda_ruptura))
```
e o rótulo do compara para `"margem perdida na ruptura = media por canal de lucro x fator"`.

Em `bloco6`, substituir `M = x.lucro_por_peca * x.fator_perda_ruptura` por:

```python
        M = (x.share_ecommerce * x.lucro_por_peca_ecommerce * x.fator_perda_ruptura_ecommerce
             + (1 - x.share_ecommerce) * x.lucro_por_peca_lojas * x.fator_perda_ruptura_lojas)
```
e o rótulo `"M = lucro x fator de ruptura"` para `"M = s x lucro_e x fator_e + (1-s) x lucro_l x fator_l"`.

Run: `.venv/bin/python scripts/revisao.py --so 6 2>&1 | tail -4`
Expected: erro `AttributeError` (a fila ainda não tem `share_ecommerce`) — é a falha esperada antes da implementação.

- [ ] **Step 2: parâmetros**

Em `Parametros` (após `fator_perda_ruptura: float = 0.85`):

```python
    # Perda na ruptura POR CANAL. No site o cliente nao espera e nao ha
    # vendedor para substituir; na loja ha. O custo de ruptura de cada item e
    # a media dos dois, ponderada pela participacao do canal na demanda dele.
    # `fator_perda_ruptura` acima fica para a politica atual de referencia.
    fator_perda_ruptura_ecommerce: float = 0.85
    fator_perda_ruptura_lojas: float = 0.85
```

Em `CAMPOS`, trocar a entrada de `fator_perda_ruptura` por estas três (grupo "Economia"):

```python
    ("fator_perda_ruptura_ecommerce", "Perda quando falta no e-commerce", "%", "pct", 0, 1,
     "Fração da margem do e-commerce que se perde quando a peça falta. No site o cliente "
     "não espera nem aceita substituto: tende a ser maior que nas lojas.", "Economia"),
    ("fator_perda_ruptura_lojas", "Perda quando falta nas lojas", "%", "pct", 0, 1,
     "Fração da margem das lojas que se perde na ruptura. O vendedor pode reservar ou "
     "oferecer outro item; costuma ser menor que no e-commerce. O custo de ruptura de cada "
     "item é a média dos dois fatores ponderada pela participação de cada canal nele.", "Economia"),
    ("fator_perda_ruptura", "Perda na ruptura (referência agregada)", "%", "pct", 0, 1,
     "Usado só na política atual de comparação. O modelo usa os dois fatores por canal acima.",
     "Economia"),
```

- [ ] **Step 3: `_margem_coerente` — margem por canal**

Logo após a linha `fin["margem_pct"] = np.where(...)` e antes de `fin = fin.drop(columns=["preco_janela"])`:

```python
    # A margem por canal pelo MESMO caminho: preco praticado do canal na
    # janela menos o custo de hoje. O e-commerce tem frete e preco proprios,
    # e e a margem dele que se perde quando falta no site. Canal sem venda na
    # janela herda a margem agregada - frequente, porque o e-commerce e um
    # decimo da saida do CD.
    for canal in ("ecommerce", "lojas"):
        col = f"lucro_por_peca_{canal}"
        fin[f"lucro_por_peca_historico_{canal}"] = (
            fin[col].astype(float) if col in fin.columns else fin.lucro_por_peca_historico)
    jan_c = wh.query(f"""
        select sku, canal_demanda, sum(receita_liquida) as receita, sum(pecas_vendidas) as pecas
        from {ref('stg_vendas')} {onde} group by 1, 2""")
    for canal in ("ecommerce", "lojas"):
        j = jan_c[jan_c.canal_demanda == canal][["sku", "receita", "pecas"]].copy()
        j["preco"] = j.receita.astype(float) / j.pecas.astype(float).replace(0.0, np.nan)
        preco_c = fin[["sku"]].merge(j[["sku", "preco"]], on="sku", how="left").preco
        preco_c.index = fin.index
        fin[f"lucro_por_peca_{canal}"] = (
            preco_c - fin.custo_unitario.astype(float)).where(preco_c.notna(), fin.lucro_por_peca)
```

- [ ] **Step 4: `modelar` — Cu ponderado**

Substituir `Cu = b.lucro_por_peca * p.fator_perda_ruptura` por:

```python
    # custo de ruptura = media por canal da margem perdida, ponderada pela
    # participacao do canal na demanda do item. Sem coluna de canal (base sem
    # loja) a participacao e zero e tudo cai no fator das lojas.
    share = (b["share_ecommerce"].fillna(0.0) if "share_ecommerce" in b.columns
             else pd.Series(0.0, index=b.index))
    lucro_e = b["lucro_por_peca_ecommerce"] if "lucro_por_peca_ecommerce" in b.columns else b.lucro_por_peca
    lucro_l = b["lucro_por_peca_lojas"] if "lucro_por_peca_lojas" in b.columns else b.lucro_por_peca
    Cu = (share * lucro_e * p.fator_perda_ruptura_ecommerce
          + (1.0 - share) * lucro_l * p.fator_perda_ruptura_lojas)
```

- [ ] **Step 5: `candidatas_marginais` — colunas de auditoria**

Substituir a linha `"fator_perda_ruptura": float(p.fator_perda_ruptura),` por:

```python
            # a margem capturada e a media por canal: os cinco insumos ficam
            # na linha para a conta poder ser refeita a mao
            "share_ecommerce": float(np.nan_to_num(getattr(r, "share_ecommerce", 0.0))),
            "lucro_por_peca_ecommerce": float(getattr(r, "lucro_por_peca_ecommerce", r.lucro_por_peca)),
            "lucro_por_peca_lojas": float(getattr(r, "lucro_por_peca_lojas", r.lucro_por_peca)),
            "fator_perda_ruptura_ecommerce": float(p.fator_perda_ruptura_ecommerce),
            "fator_perda_ruptura_lojas": float(p.fator_perda_ruptura_lojas),
```

- [ ] **Step 6: `alocacao_marginal` — risco inicial com o mesmo Cu**

Substituir `margem_un = (df.lucro_por_peca * p.fator_perda_ruptura).to_numpy(float)` por:

```python
    # a mesma margem que a fila usa (custo_falta_unit, ja ponderada por canal)
    margem_un = df.custo_falta_unit.to_numpy(float)
```

- [ ] **Step 7: `ETAPAS_FILA`, `dossie`, `conferir.py`, `peca.js`**

`backend/analitico.py`, bloco "5. Economia por peça": trocar a linha `("fator_perda_ruptura", "Fator de perda na ruptura", "pct"),` por:

```python
        ("share_ecommerce", "Participação do e-commerce na demanda  ·  s", "pct"),
        ("lucro_por_peca_ecommerce", "Lucro por peça no e-commerce", "brl2"),
        ("lucro_por_peca_lojas", "Lucro por peça nas lojas", "brl2"),
        ("fator_perda_ruptura_ecommerce", "Perda na ruptura · e-commerce", "pct"),
        ("fator_perda_ruptura_lojas", "Perda na ruptura · lojas", "pct"),
```
e o rótulo de `margem_unit` para `"Margem capturada  ·  M = s×lucro_e×fator_e + (1−s)×lucro_l×fator_l"`.

`dossie()` → no dict `"parametros"`, acrescentar:
```python
            "fator_perda_ruptura_ecommerce": p.fator_perda_ruptura_ecommerce,
            "fator_perda_ruptura_lojas": p.fator_perda_ruptura_lojas,
```

`scripts/conferir.py::refazer`, trocar `M = r.lucro_por_peca * r.fator_perda_ruptura` por:
```python
    M = (r.share_ecommerce * r.lucro_por_peca_ecommerce * r.fator_perda_ruptura_ecommerce
         + (1 - r.share_ecommerce) * r.lucro_por_peca_lojas * r.fator_perda_ruptura_lojas)
```

`static/peca.js` (≈ l.45–48), trocar as duas linhas da fórmula de M por:
```js
       "M = s × lucro_e × fator_e + (1 − s) × lucro_l × fator_l\n" +
       "M = " + N.pct(r.share_ecommerce, 0) + " × " + N.moeda(r.lucro_por_peca_ecommerce, 2) + " × " +
       N.num(r.fator_perda_ruptura_ecommerce, 2) + " + " + N.pct(1 - r.share_ecommerce, 0) + " × " +
       N.moeda(r.lucro_por_peca_lojas, 2) + " × " + N.num(r.fator_perda_ruptura_lojas, 2) +
       " = <b>R$ " + N.moeda(r.margem_unit, 2) + "</b>\n\n" +
```

- [ ] **Step 8: recalcular e conferir**

Run: `.venv/bin/python scripts/rodar_pipeline.py --pular-carga --pular-dbt 2>&1 | tail -3`
Run: `.venv/bin/python scripts/revisao.py 2>&1 | tail -6`
Expected: bloco 4 "margem perdida na ruptura = media por canal…" OK; bloco 6 "M = s x lucro_e…" OK; sem falha nova.
Run: `.venv/bin/python scripts/conferir.py --linhas 300 2>&1 | tail -5`
Expected: coluna M sem divergência.

- [ ] **Step 9: commit**

```bash
git add backend/config.py backend/modelo.py backend/analitico.py scripts/revisao.py scripts/conferir.py static/peca.js
git commit -m "economia por canal: margem e fator de perda na ruptura ponderados pela participacao do e-commerce

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: alocação marginal com dois caixas

**Files:**
- Modify: `backend/config.py` (`Parametros`, `CAMPOS`, `CHAVES`)
- Modify: `backend/modelo.py` (`regra_de_parada` l.632; `caminhar` l.657; `candidatas_marginais` ≈ l.530; `alocacao_marginal` l.837; `plano_marginal` l.891)
- Modify: `backend/analitico.py` (`ETAPAS_FILA` bloco "7. Decisão do caixa")
- Modify: `scripts/revisao.py` (`bloco5`)

**Interfaces:**
- Produces: `Parametros.teto_compra_ecommerce: float = 0.0`, `Parametros.fatia_ecommerce_rigida: bool = False`. `regra_de_parada()` devolve também `"teto_ecommerce": float` (limitado a `[0, teto]`) e `"fatia_rigida": bool`. Fila: `custo_ecommerce = custo × share_ecommerce`, `custo_lojas = custo − custo_ecommerce`, `caixa_acumulado_ecommerce`, `caixa_acumulado_lojas`, `caixa_restante_ecommerce`, `caixa_restante_lojas`, `teto_ecommerce`, `fatia_rigida`. Motivos novos em `motivo`: `"nao coube na fatia do e-commerce"`, `"nao coube na fatia das lojas"`. Plano por item: `valor_da_compra_ecommerce`, `valor_da_compra_lojas`.

- [ ] **Step 1: teste unitário que falha (`caminhar` com fatia rígida)**

Salvar em `/private/tmp/claude-501/-Users-dougzec-Documents-GitHub-inventory-control/b98628d3-9710-4113-a8ce-f227646547af/scratchpad/teste_caminhar_canais.py`:

```python
import sys; sys.path.insert(0, "/Users/dougzec/Documents/GitHub/inventory_control")
import pandas as pd
from backend.modelo import caminhar

fila = pd.DataFrame(dict(
    sku=["A", "B", "C"], bloco=[0, 0, 0], quantidade=[1, 1, 1],
    custo=[100.0, 100.0, 100.0], custo_ecommerce=[80.0, 80.0, 10.0],
    valor_esperado=[10.0, 9.0, 8.0], reducao_risco=[1.0, 1.0, 1.0], reducao_falta=[1.0, 1.0, 1.0],
    nota=[3.0, 2.0, 1.0], p_vender=[.9, .8, .7]))
base = dict(criterios=["caixa"], criterio="caixa", piso_retorno=0.0, piso_chance=0.0,
            alvo_risco=None, teto=300.0, caixa_limita=True)

# fatia rigida de R$ 100 para o e-commerce: A gasta 80, B (80) nao cabe, C (10) cabe
r = caminhar(fila, dict(base, teto_ecommerce=100.0, fatia_rigida=True), 0.0, 0.0)
print(list(r["comprar"]), list(r["motivo"]))
assert list(r["comprar"]) == [True, False, True], "fatia rigida nao barrou B"
assert r["motivo"][1] == "nao coube na fatia do e-commerce"
assert abs(r["caixa_acumulado_ecommerce"][-1] - 90.0) < 1e-9
assert abs(r["caixa_acumulado_lojas"][-1] - 110.0) < 1e-9
assert abs(r["caixa_restante_ecommerce"][-1] - 10.0) < 1e-9
assert abs(r["caixa_restante_lojas"][-1] - 90.0) < 1e-9

# fatia flexivel: so o total limita, os tres cabem
r = caminhar(fila, dict(base, teto_ecommerce=100.0, fatia_rigida=False), 0.0, 0.0)
assert list(r["comprar"]) == [True, True, True], "fatia flexivel nao pode barrar"
assert abs(r["caixa_acumulado_ecommerce"][-1] - 170.0) < 1e-9

# regra antiga, sem as chaves novas: comportamento identico ao de hoje
r = caminhar(fila, base, 0.0, 0.0)
assert list(r["comprar"]) == [True, True, True]
print("OK")
```

Run: `.venv/bin/python /private/tmp/claude-501/-Users-dougzec-Documents-GitHub-inventory-control/b98628d3-9710-4113-a8ce-f227646547af/scratchpad/teste_caminhar_canais.py`
Expected: `AssertionError: fatia rigida nao barrou B`.

- [ ] **Step 2: parâmetros**

Em `Parametros`, após `teto_compra_ciclo`:

```python
    # Fatia INTERNA do e-commerce dentro do caixa do ciclo. A compra e uma so,
    # da empresa 26; a fatia diz quanto desse dinheiro a demanda do e-commerce
    # pode puxar. 0 = sem fatia declarada. Rigida: cada canal para no seu
    # teto; flexivel (padrao): so o total limita e a fatia e leitura.
    teto_compra_ecommerce: float = 0.0
    fatia_ecommerce_rigida: bool = False
```

Em `CAMPOS`, após a entrada de `teto_compra_ciclo`:

```python
    ("teto_compra_ecommerce", "Fatia do e-commerce no caixa do ciclo", "R$", "float", 0, 100_000_000,
     "Quanto do caixa do ciclo cabe à demanda do e-commerce. Cada peça comprada é cobrada aos "
     "dois caixas na proporção da participação do e-commerce naquele item. Com a fatia rígida "
     "ligada (abaixo, nos interruptores), o e-commerce para de puxar peças quando a fatia acaba; "
     "as lojas ficam com o resto do caixa.", "Restrições"),
```

Em `CHAVES`, no fim:

```python
    ("fatia_ecommerce_rigida", "Fatia do e-commerce rígida",
     "Ligado: o e-commerce não passa da fatia dele e as lojas não passam do resto do caixa. "
     "Desligado: só o caixa total limita a compra, e a fatia aparece só como leitura de quanto "
     "cada canal puxou."),
```

- [ ] **Step 3: `regra_de_parada`**

Acrescentar ao dict devolvido:

```python
        # a fatia do e-commerce nunca passa do caixa do ciclo
        "teto_ecommerce": float(min(max(getattr(p, "teto_compra_ecommerce", 0.0), 0.0),
                                    p.teto_compra_ciclo)),
        "fatia_rigida": bool(getattr(p, "fatia_ecommerce_rigida", False)),
```

- [ ] **Step 4: `caminhar` — dois saldos**

Após `custo = fila.custo.to_numpy(float)`:

```python
    # o custo de cada peca repartido pelos dois caixas, na participacao do
    # e-commerce no item. Sem a coluna (fila antiga) tudo e das lojas.
    custo_e = (fila.custo_ecommerce.to_numpy(float) if "custo_ecommerce" in fila.columns
               else np.zeros(len(fila)))
    custo_l = custo - custo_e
```

Após `restante = teto if regra["caixa_limita"] else np.inf`:

```python
    # a fatia so morde quando e rigida E o caixa limita; flexivel, os dois
    # saldos sao infinitos e viram so leitura acumulada
    teto_e = float(regra.get("teto_ecommerce", 0.0))
    rigida = bool(regra.get("fatia_rigida", False)) and bool(regra["caixa_limita"])
    restante_e = teto_e if rigida else np.inf
    restante_l = (teto - teto_e) if rigida else np.inf
    gasto_e = 0.0
    acumulado_e = np.zeros(n, dtype=float)
```

No laço, trocar `cabe = custo[i] <= restante` por:

```python
        cabe = custo[i] <= restante
        # tolerancia: custo_e e custo x participacao, e a soma de muitas
        # fatias pode passar do teto por erro de ponto flutuante
        cabe_e = custo_e[i] <= restante_e + 1e-9
        cabe_l = custo_l[i] <= restante_l + 1e-9
```

Trocar a cadeia de `elif` a partir de `elif depende and not cabe:` por:

```python
        elif depende and not (cabe and cabe_e and cabe_l):
            motivo[i] = "caixa ja esgotado quando chegou a vez dela"
        elif depende:
            motivo[i] = "bloqueada: a peca anterior deste item nao entrou"
        elif not cabe:
            motivo[i] = "nao coube no caixa restante"
        elif not cabe_e:
            motivo[i] = "nao coube na fatia do e-commerce"
        elif not cabe_l:
            motivo[i] = "nao coube na fatia das lojas"
        else:
            comprado[i] = True
            restante -= custo[i]
            restante_e -= custo_e[i]
            restante_l -= custo_l[i]
            gasto += custo[i]
            gasto_e += custo_e[i]
            pecas += int(qtds[i])
            ganho += float(valores[i])
            risco -= float(red_risco[i])
            falta -= float(red_falta[i])
            ultimo_bloco[s] = b
            motivo[i] = "comprada"

        acumulado[i] = gasto
        acumulado_e[i] = gasto_e
```

E no dict devolvido:

```python
        "caixa_acumulado_ecommerce": acumulado_e,
        "caixa_acumulado_lojas": acumulado - acumulado_e,
        # sempre contra a fatia declarada, para leitura, rigida ou nao
        "caixa_restante_ecommerce": teto_e - acumulado_e,
        "caixa_restante_lojas": (teto - teto_e) - (acumulado - acumulado_e),
```

- [ ] **Step 5: rodar o teste unitário**

Run: `.venv/bin/python /private/tmp/claude-501/-Users-dougzec-Documents-GitHub-inventory-control/b98628d3-9710-4113-a8ce-f227646547af/scratchpad/teste_caminhar_canais.py`
Expected: `[True, False, True] ['comprada', 'nao coube na fatia do e-commerce', 'comprada']` e `OK`.

- [ ] **Step 6: `candidatas_marginais`, `alocacao_marginal`, `plano_marginal`**

Em `candidatas_marginais`, antes de `partes.append(...)`:
```python
        share_e = float(np.nan_to_num(getattr(r, "share_ecommerce", 0.0)))
```
e no DataFrame, logo após `"custo": custo,`:
```python
            # o custo repartido pelos dois caixas, na participacao do canal
            "custo_ecommerce": custo * share_e,
            "custo_lojas": custo * (1.0 - share_e),
```
(usar `share_e` também na coluna `"share_ecommerce"` criada na Task 3, no lugar do `float(np.nan_to_num(...))` repetido).

Em `alocacao_marginal`, após `fila["teto_ciclo"] = float(p.teto_compra_ciclo)`:
```python
    fila["teto_ecommerce"] = regra["teto_ecommerce"]
    fila["fatia_rigida"] = regra["fatia_rigida"]
```
e no `por_item = compradas.groupby("sku").agg(...)`:
```python
        valor_da_compra_ecommerce=("custo_ecommerce", "sum"),
        valor_da_compra_lojas=("custo_lojas", "sum"),
```

Em `plano_marginal`, na lista de `(col, padrao)` a preencher, acrescentar `("valor_da_compra_ecommerce", 0.0), ("valor_da_compra_lojas", 0.0)`.

Em `analitico.ETAPAS_FILA`, bloco "7. Decisão do caixa", após `("caixa_acumulado", ...)`:
```python
        ("teto_ecommerce", "Fatia do e-commerce no caixa", "brl"),
        ("caixa_acumulado_ecommerce", "Gasto do e-commerce até aqui", "brl"),
        ("caixa_acumulado_lojas", "Gasto das lojas até aqui", "brl"),
        ("caixa_restante_ecommerce", "Fatia do e-commerce restante", "brl"),
        ("caixa_restante_lojas", "Fatia das lojas restante", "brl"),
```

- [ ] **Step 7: checks na bateria (bloco 5)**

No fim de `bloco5`:

```python
    # --- dois caixas: a fatia de cada canal fecha e respeita o teto ---
    if {"custo_ecommerce", "caixa_acumulado_ecommerce"} <= set(f.columns):
        r.compara(B, "custo do e-commerce = custo x participacao",
                  (f.custo * f.share_ecommerce).to_numpy(), f.custo_ecommerce.to_numpy(),
                  tol=TOL_FROUXA)
        r.compara(B, "custo e-commerce + lojas = custo da peca",
                  (f.custo_ecommerce + f.custo_lojas).to_numpy(), f.custo.to_numpy(), tol=TOL_FROUXA)
        esp_e = (fo.custo_ecommerce * fo.comprar).cumsum().to_numpy()
        r.compara(B, "caixa acumulado do e-commerce = soma corrida da fatia dele",
                  esp_e, fo.caixa_acumulado_ecommerce.to_numpy(), tol=TOL_FROUXA)
        r.compara(B, "caixa acumulado das lojas = total - e-commerce",
                  esperado - esp_e, fo.caixa_acumulado_lojas.to_numpy(), tol=TOL_FROUXA)
        agg_c = f[f.comprar].groupby("sku").agg(e=("custo_ecommerce", "sum"), l=("custo_lojas", "sum"))
        r.compara(B, "valor da compra do e-commerce por item = soma da fila",
                  agg_c.e.to_numpy(), pl.valor_da_compra_ecommerce.reindex(agg_c.index).to_numpy(),
                  tol=TOL_FROUXA)
        r.compara(B, "valor e-commerce + lojas = valor da compra do item",
                  (pl.valor_da_compra_ecommerce + pl.valor_da_compra_lojas).reindex(idx).to_numpy(),
                  pl.valor_da_compra.reindex(idx).to_numpy(), tol=TOL_FROUXA)
        teto_e = float(f.teto_ecommerce.iloc[0])
        gasto_e = float(f[f.comprar].custo_ecommerce.sum())
        if bool(f.fatia_rigida.iloc[0]) and "caixa" in set(p.criterio_parada.split(",")):
            r.afirma(B, "fatia rigida: e-commerce nao passa da fatia dele",
                     gasto_e <= teto_e + 1e-6, f"gasto {gasto_e:,.2f} de {teto_e:,.2f}")
            r.afirma(B, "fatia rigida: lojas nao passam do resto do caixa",
                     gasto - gasto_e <= p.teto_compra_ciclo - teto_e + 1e-6,
                     f"gasto {gasto - gasto_e:,.2f} de {p.teto_compra_ciclo - teto_e:,.2f}")
        else:
            r.alerta(B, "fatia do e-commerce e leitura, nao limite",
                     f"o e-commerce puxou R$ {gasto_e:,.0f} de R$ {gasto:,.0f} "
                     f"({gasto_e / gasto if gasto else 0:.1%}) · fatia declarada R$ {teto_e:,.0f}")
    else:
        r.falha(B, "fila traz o custo por canal", "faltam custo_ecommerce / caixa_acumulado_ecommerce")
```

- [ ] **Step 8: recalcular e conferir, nos dois modos**

Run: `.venv/bin/python scripts/rodar_pipeline.py --pular-carga --pular-dbt 2>&1 | tail -3`
Run: `.venv/bin/python scripts/revisao.py 2>&1 | tail -6`
Expected: 6 checks novos OK + 1 alerta (fatia é leitura); sem falha nova.

Depois, ligar a fatia rígida com um valor apertado e recalcular, para exercitar o caminho rígido:
```bash
.venv/bin/python -c "
from backend.config import Parametros
p = Parametros.carregar(); p.teto_compra_ecommerce = 0.05 * p.teto_compra_ciclo; p.fatia_ecommerce_rigida = True; p.salvar()"
.venv/bin/python scripts/rodar_pipeline.py --pular-carga --pular-dbt 2>&1 | tail -2
.venv/bin/python scripts/revisao.py --so 5 2>&1 | grep -i "fatia\|motivo"
```
Expected: "fatia rigida: e-commerce nao passa da fatia dele" OK; no dict de motivos aparece `'nao coube na fatia do e-commerce'`.

Voltar ao estado flexível:
```bash
.venv/bin/python -c "
from backend.config import Parametros
p = Parametros.carregar(); p.teto_compra_ecommerce = 0.0; p.fatia_ecommerce_rigida = False; p.salvar()"
.venv/bin/python scripts/rodar_pipeline.py --pular-carga --pular-dbt 2>&1 | tail -2
```

- [ ] **Step 9: commit**

```bash
git add backend/config.py backend/modelo.py backend/analitico.py scripts/revisao.py data/parametros.json
git commit -m "alocacao marginal com dois caixas: fatia interna do e-commerce, rigida ou de leitura

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: rateio da compra por canal, coerente com o plano

**Files:**
- Modify: `backend/analitico.py` (`rateio_por_loja` l.1437)
- Modify: `backend/main.py` (`api_rateio` l.514)

**Interfaces:**
- Consumes: `res_plano_compra.valor_da_compra_ecommerce/_lojas` (Task 4).
- Produces: `rateio_por_loja()` devolve também `canais: {"ecommerce": {valor, pecas, itens}, "lojas": {valor, pecas, itens}}` e cada loja recebe a fatia **do seu canal** (loja 33 = e-commerce; as demais repartem `valor_da_compra_lojas` pela participação delas dentro das lojas). `api_rateio` acrescenta `teto_ecommerce` e `fatia_rigida` dos parâmetros.

- [ ] **Step 1: check que falha**

Run:
```bash
.venv/bin/python -c "
from backend.warehouse import abrir; from backend.analitico import rateio_por_loja
d = rateio_por_loja(abrir())
assert 'canais' in d, 'sem bloco canais'
e = [l for l in d['lojas'] if l['loja_id'] == '33'][0]['valor']
assert abs(e - d['canais']['ecommerce']['valor']) < 1e-6, (e, d['canais']['ecommerce']['valor'])
print('OK', d['canais'])"
```
Expected: `AssertionError: sem bloco canais`.

- [ ] **Step 2: implementar**

Substituir o corpo de `rateio_por_loja` a partir de `plano = wh.query(...)` até o `return` por:

```python
    plano = wh.query(f"""
        select sku, quantidade_a_comprar as q, valor_da_compra as valor,
               coalesce(valor_da_compra_ecommerce, 0.0)              as valor_e,
               coalesce(valor_da_compra_lojas, valor_da_compra)      as valor_l
        from {ref('res_plano_compra')} where quantidade_a_comprar > 0""")

    # A fatia de cada CANAL vem do proprio plano (e a que o motor cobrou dos
    # dois caixas). A venda observada por loja so reparte a fatia das lojas
    # ENTRE as lojas - assim o rateio nunca discorda do plano por canal.
    v["ecom"] = v.loja_id.astype(str).eq("33")
    tot_c = v.groupby(["sku", "ecom"]).pecas.sum().rename("total_canal").reset_index()
    v = v.merge(tot_c, on=["sku", "ecom"])
    v["participacao_canal"] = v.pecas / v.total_canal
    r = v.merge(plano, on="sku", how="inner")
    frac_e = np.where(r.valor > 0, r.valor_e / r.valor.replace(0, np.nan), 0.0)
    r["valor_canal"] = np.where(r.ecom, r.valor_e, r.valor_l)
    r["pecas_canal"] = np.where(r.ecom, r.q * frac_e, r.q * (1.0 - frac_e))
    r["valor_rateado"] = r.valor_canal * r.participacao_canal
    r["pecas_rateadas"] = r.pecas_canal * r.participacao_canal
    r["participacao"] = np.where(r.valor > 0, r.valor_rateado / r.valor.replace(0, np.nan), 0.0)

    com_venda = set(r.sku)
    sem = plano[~plano.sku.isin(com_venda)]

    lojas = (r.groupby("loja_id")
             .agg(pecas=("pecas_rateadas", "sum"), valor=("valor_rateado", "sum"),
                  itens=("sku", "count"))
             .reset_index().sort_values("valor", ascending=False))
    lojas["loja"] = lojas.loja_id.map(lambda k: nome.get(k, f"Loja {k}"))
    detalhe = {}
    for loja_id, g in r.groupby("loja_id"):
        detalhe[str(loja_id)] = {
            row.sku: dict(participacao=float(row.participacao),
                          pecas=float(row.pecas_rateadas), valor=float(row.valor_rateado))
            for row in g.itertuples(index=False)}
    canais = {
        "ecommerce": dict(valor=float(plano.valor_e.sum()),
                          pecas=float((plano.q * np.where(plano.valor > 0, plano.valor_e / plano.valor.replace(0, np.nan), 0.0)).sum()),
                          itens=int((plano.valor_e > 0).sum())),
        "lojas": dict(valor=float(plano.valor_l.sum()),
                      pecas=float((plano.q * np.where(plano.valor > 0, plano.valor_l / plano.valor.replace(0, np.nan), 0.0)).sum()),
                      itens=int((plano.valor_l > 0).sum())),
    }
    return dict(
        janela_dias=int(janela),
        total_valor=float(plano.valor.sum()), total_pecas=float(plano.q.sum()),
        sem_venda=dict(itens=int(len(sem)), pecas=float(sem.q.sum()), valor=float(sem.valor.sum())),
        canais=canais,
        lojas=registros(lojas[["loja_id", "loja", "itens", "pecas", "valor"]]),
        detalhe=detalhe)
```

Ajustar a docstring: acrescentar a frase "A fatia de cada canal vem do plano (`valor_da_compra_ecommerce` / `_lojas`); a venda por loja só reparte a fatia das lojas entre elas."

Em `main.py::api_rateio`:
```python
@app.get("/api/plano/rateio")
def api_rateio():
    """Quanto da compra do ciclo cabe a cada empresa, pela demanda dela em cada item."""
    p = Parametros.carregar()
    d = analitico.rateio_por_loja(wh())
    d["teto_ecommerce"] = float(p.teto_compra_ecommerce)
    d["fatia_rigida"] = bool(p.fatia_ecommerce_rigida)
    return JSONResponse(d)
```

- [ ] **Step 3: rodar o check**

Run: o comando do Step 1.
Expected: `OK {'ecommerce': {...}, 'lojas': {...}}`. Se a loja 33 não tiver venda na janela para algum item comprado com `valor_e > 0`, a igualdade pode divergir; nesse caso imprimir a diferença e registrar como esperado (o item aparece em `sem_venda` daquele canal). Não relaxar a asserção sem medir.

- [ ] **Step 4: commit**

```bash
git add backend/analitico.py backend/main.py
git commit -m "rateio da compra por canal coerente com a fatia cobrada de cada caixa

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: backtest por canal

**Files:**
- Modify: `backend/validacao.py` (`rodar` l.470–700; `_futuro` l.310; `_resumo` l.722)

**Interfaces:**
- Consumes: `mart_estoque_diario.pecas_ecommerce/_lojas`; plano com `share_ecommerce`, `valor_da_compra_ecommerce/_lojas`.
- Produces: `rodar()` com `fatia_ecommerce_rigida=False` quando `usar_reais`; por item: `share_ecommerce`, `investimento_ecommerce`, `investimento_lojas`, `vendeu_ecommerce`, `vendeu_lojas`, `share_realizada`, `faltou_com_plano_ecommerce`, `faltou_com_plano_lojas`; em `resumo["canais"]`: `{"ecommerce": {investimento, vendeu, faltou_com_plano, share_prevista, share_realizada}, "lojas": {...}}`.

- [ ] **Step 1: check que falha**

Run:
```bash
.venv/bin/python -c "
from backend.warehouse import abrir; from backend import validacao
wh = abrir(); lim = validacao.datas_disponiveis(wh)
import pandas as pd
corte = str((pd.Timestamp(lim['ultimo']) - pd.Timedelta(days=60)).date())
d = validacao.rodar(wh, corte)
assert 'canais' in d['resumo'], 'sem canais no resumo'
c = d['resumo']['canais']
assert abs(c['ecommerce']['investimento'] + c['lojas']['investimento'] - d['resumo']['investimento']) < 1e-6
print('OK', c)"
```
Expected: `AssertionError: sem canais no resumo`.

- [ ] **Step 2: fatia desligada no backtest e vetores por canal**

Em `rodar`, dentro de `if usar_reais:`, antes de `teto = ...`:
```python
        # nao existe caixa historico do e-commerce: a compra real e toda da
        # empresa 26. A fatia fica desligada e o teto e o total real da data.
        base["fatia_ecommerce_rigida"] = False
```

A query de `diario` em `rodar` passa a incluir as duas colunas:
```python
    diario = wh.query(
        f"select sku, data, saldo_final, disponivel_final, pecas_vendidas, "
        f"pecas_ecommerce, pecas_lojas, estado_estoque from {ref('mart_estoque_diario')} "
        f"where 1 = 1 {_so(skus, 'sku')} order by sku, data")
```

Em `_futuro`, a tupla ganha dois vetores no fim:
```python
            g.data.to_numpy("datetime64[D]"),
            g.pecas_ecommerce.to_numpy(float) if "pecas_ecommerce" in g.columns else np.zeros(len(g)),
            g.pecas_lojas.to_numpy(float) if "pecas_lojas" in g.columns else g.pecas_vendidas.to_numpy(float),
```
e em `rodar` o desempacotamento vira `v, ok, cens, fis, disp, dias, ve, vl = fut[r.sku]`. Conferir com `grep -n "_futuro(" backend/*.py` que não há outro consumidor; se houver, ajustar o desempacotamento nele também.

- [ ] **Step 3: colunas por item e agregado**

No `linhas.append(dict(...))`, após `investimento=float(r.valor_da_compra),`:
```python
            # --- a quebra por canal: o que o plano atribuiu e o que vendeu
            share_ecommerce=float(np.nan_to_num(getattr(r, "share_ecommerce", 0.0))),
            investimento_ecommerce=float(getattr(r, "valor_da_compra_ecommerce", 0.0) or 0.0),
            investimento_lojas=float(getattr(r, "valor_da_compra_lojas", 0.0) or 0.0),
            vendeu_ecommerce=float(ve[:H].sum()),
            vendeu_lojas=float(vl[:H].sum()),
```

Logo após `rel = pd.DataFrame(linhas)` (l.679) e antes do `if rel.empty`... (colocar depois do teste de vazio):
```python
    # a falta e do estoque compartilhado; cada canal leva a parte dele pela
    # participacao REALIZADA no horizonte (a prevista, se nada vendeu)
    rel["share_realizada"] = np.where(rel.vendeu_observado > 0,
                                      rel.vendeu_ecommerce / rel.vendeu_observado.replace(0, np.nan),
                                      rel.share_ecommerce)
    rel["faltou_com_plano_ecommerce"] = rel.faltou_com_plano * rel.share_realizada
    rel["faltou_com_plano_lojas"] = rel.faltou_com_plano - rel.faltou_com_plano_ecommerce
```

Em `_resumo`, antes de `"comparativo": _comparativo(rel),`:
```python
        # a quebra por canal, nos itens que o modelo compraria
        "canais": {
            "ecommerce": {
                "investimento": float(c.investimento_ecommerce.sum()),
                "vendeu": float(c.vendeu_ecommerce.sum()),
                "faltou_com_plano": float(c.faltou_com_plano_ecommerce.sum()),
                "share_prevista": (float(c.investimento_ecommerce.sum() / c.investimento.sum())
                                   if c.investimento.sum() else 0.0),
                "share_realizada": (float(c.vendeu_ecommerce.sum() / c.vendeu_observado.sum())
                                    if c.vendeu_observado.sum() else 0.0),
            },
            "lojas": {
                "investimento": float(c.investimento_lojas.sum()),
                "vendeu": float(c.vendeu_lojas.sum()),
                "faltou_com_plano": float(c.faltou_com_plano_lojas.sum()),
                "share_prevista": (float(c.investimento_lojas.sum() / c.investimento.sum())
                                   if c.investimento.sum() else 0.0),
                "share_realizada": (float(c.vendeu_lojas.sum() / c.vendeu_observado.sum())
                                    if c.vendeu_observado.sum() else 0.0),
            },
        },
```

- [ ] **Step 4: rodar o check**

Run: o comando do Step 1.
Expected: `OK {...}`, com `share_prevista` e `share_realizada` do e-commerce na mesma ordem de grandeza (10–25%).

- [ ] **Step 5: commit**

```bash
git add backend/validacao.py
git commit -m "backtest por canal: investimento atribuido, venda realizada e falta por canal

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: telas — parâmetros, plano e painel

**Files:**
- Modify: `backend/main.py` (`plano` ≈ l.331–395; `painel` l.198–260)
- Modify: `templates/plano.html` (herói l.66–78; rateio l.238–248 e JS l.411–480)
- Modify: `templates/painel.html` (herói l.20–30)

(`/parametros` já renderiza `CAMPOS` e `CHAVES` genericamente — as Tasks 3 e 4 bastam. Conferir abrindo a página.)

**Interfaces:**
- Consumes: `res_plano_compra.valor_da_compra_ecommerce/_lojas`; `Parametros.teto_compra_ecommerce`, `.fatia_ecommerce_rigida`; `/api/plano/rateio` com `canais`.
- Produces: `resumo.investimento_ecommerce`, `resumo.investimento_lojas`, `resumo.teto_ecommerce`, `resumo.fatia_rigida` no `/plano`; `k.comprar_valor_ecommerce`, `k.comprar_valor_lojas`, `k.teto_ecommerce` no `/painel`.

- [ ] **Step 1: `main.py::plano` — resumo e coluna**

No dict `resumo`, após `"sobra"`:
```python
        "investimento_ecommerce": float(comprados.get("valor_da_compra_ecommerce", pd.Series(dtype=float)).sum()),
        "investimento_lojas": float(comprados.get("valor_da_compra_lojas", pd.Series(dtype=float)).sum()),
        "teto_ecommerce": float(p.teto_compra_ecommerce),
        "fatia_rigida": bool(p.fatia_ecommerce_rigida),
```
Na lista `cols`, após `"valor_da_compra"`: `"valor_da_compra_ecommerce", "valor_da_compra_lojas",`.

- [ ] **Step 2: `plano.html` — herói e faixa por canal**

No bloco herói "Compra deste ciclo", substituir o `<div class="heroi-txt">…</div>` (l.71–75) por:

```html
    <div class="heroi-txt">
      <b>{{ resumo.pecas|n }} peças</b> de <b>{{ resumo.itens }} produtos diferentes</b>,
      em {{ resumo.familias }} famílias. Sobram R$ {{ resumo.sobra|n }} do caixa
      de R$ {{ resumo.teto|curto }}.
      <span class="pequeno t3" style="display:block;margin-top:6px">
        Puxado pelo <b>e-commerce: R$ {{ resumo.investimento_ecommerce|curto }}</b>
        ({{ (resumo.investimento_ecommerce / resumo.investimento if resumo.investimento else 0)|pct(0) }})
        · pelas <b>lojas: R$ {{ resumo.investimento_lojas|curto }}</b>.
        {% if resumo.teto_ecommerce %}
          Fatia do e-commerce: R$ {{ resumo.teto_ecommerce|curto }}
          {{ '(rígida)' if resumo.fatia_rigida else '(leitura)' }}.
        {% endif %}
      </span>
    </div>
```

No painel "Quem demanda esta compra" (l.238–248), acrescentar antes de `<div id="faixa-lojas" ...>`:
```html
      <div id="faixa-canais" style="display:flex;height:22px;border-radius:6px;overflow:hidden;gap:1px;margin-bottom:8px"></div>
      <div class="legenda mb10" id="lg-canais"></div>
```

No JS do rateio, logo após `var total = d.total_valor || 1;` (≈ l.420):
```js
    /* a barra de cima e a quebra por CANAL, a que o motor cobrou dos dois
       caixas; a de baixo reparte a fatia das lojas entre as lojas */
    var cn = d.canais || {};
    var ce = (cn.ecommerce || {}).valor || 0, cl = (cn.lojas || {}).valor || 0;
    document.getElementById("faixa-canais").innerHTML =
      '<div style="width:' + (ce / total * 100).toFixed(3) + '%;min-width:2px;background:' + C.menta +
      '" title="e-commerce | R$ ' + N.moeda(ce) + '"></div>' +
      '<div style="width:' + (cl / total * 100).toFixed(3) + '%;min-width:2px;background:' + C.ambar +
      '" title="lojas | R$ ' + N.moeda(cl) + '"></div>';
    document.getElementById("lg-canais").innerHTML =
      '<span><i style="background:' + C.menta + '"></i>e-commerce <b class="mono">' + N.pct(ce / total, 0) + "</b></span>" +
      '<span><i style="background:' + C.ambar + '"></i>lojas <b class="mono">' + N.pct(cl / total, 0) + "</b></span>" +
      (d.teto_ecommerce ? '<span class="t4">fatia do e-commerce R$ ' + N.curto(d.teto_ecommerce) +
        (d.fatia_rigida ? " (rígida)" : " (leitura)") + "</span>" : "");
```
(`C.menta` e `C.ambar` já são usados neste template; se `C` não estiver no escopo desse bloco, usar `N.corProduto("canal-ecommerce")` e `N.corProduto("canal-lojas")`.)

- [ ] **Step 3: `main.py::painel` e `painel.html`**

No dict `k`, após `"comprar_valor"`:
```python
        "comprar_valor_ecommerce": float(comprar.get("valor_da_compra_ecommerce", pd.Series(dtype=float)).sum()),
        "comprar_valor_lojas": float(comprar.get("valor_da_compra_lojas", pd.Series(dtype=float)).sum()),
        "teto_ecommerce": p.teto_compra_ecommerce,
```
Em `painel.html`, após o `<div class="pequeno t4">… do caixa do ciclo … utilizado</div>` (≈ l.27–30):
```html
    <div class="pequeno t4">
      e-commerce <b>R$ {{ k.comprar_valor_ecommerce|curto }}</b>
      ({{ ((k.comprar_valor_ecommerce / k.comprar_valor) if k.comprar_valor else 0)|pct(0) }})
      · lojas <b>R$ {{ k.comprar_valor_lojas|curto }}</b>
    </div>
```

- [ ] **Step 4: subir o servidor e conferir as três páginas**

Run (em background): `.venv/bin/uvicorn backend.main:app --port 8000`
Run:
```bash
curl -s http://localhost:8000/plano | grep -c "Puxado pelo"
curl -s http://localhost:8000/painel | grep -c "e-commerce <b>"
curl -s http://localhost:8000/parametros | grep -c "fatia_ecommerce_rigida\|teto_compra_ecommerce\|fator_perda_ruptura_lojas"
curl -s http://localhost:8000/api/plano/rateio | .venv/bin/python -c "import json,sys; d=json.load(sys.stdin); print(d['canais'], d['teto_ecommerce'], d['fatia_rigida'])"
```
Expected: `1`, `1`, `3`, e o dict de canais. Sem erro 500 no log do uvicorn. Encerrar o servidor.

- [ ] **Step 5: commit**

```bash
git add backend/main.py templates/plano.html templates/painel.html
git commit -m "telas: compra e caixa por canal no plano e no painel; fatia do e-commerce nos parametros

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: telas — dossiê do item e validação

**Files:**
- Modify: `backend/analitico.py` (`dossie` l.863–950)
- Modify: `static/item.js` (bloco "5. economia" ≈ l.415)
- Modify: `templates/validacao.html` (tabela por data ≈ l.897–912)

**Interfaces:**
- Consumes: `res_sku_modelo` colunas por canal (Tasks 2–3); `resumo.canais` do backtest (Task 6).
- Produces: `dossie()["canais"] = {"ecommerce": {demanda_dia, desvio_dia, share, lucro_por_peca, fator}, "lojas": {...}, "covariancia": float}`.

- [ ] **Step 1: `dossie` — bloco `canais`**

No dict devolvido por `dossie`, após `"economia": {...},`:
```python
        "canais": {
            "ecommerce": {
                "demanda_dia": float(np.nan_to_num(m.get("demanda_media_dia_ecommerce", 0.0))),
                "desvio_dia": float(np.nan_to_num(m.get("desvio_padrao_dia_ecommerce", 0.0))),
                "share": float(np.nan_to_num(m.get("share_ecommerce", 0.0))),
                "lucro_por_peca": float(np.nan_to_num(m.get("lucro_por_peca_ecommerce", m.lucro_por_peca))),
                "fator": p.fator_perda_ruptura_ecommerce,
            },
            "lojas": {
                "demanda_dia": float(np.nan_to_num(m.get("demanda_media_dia_lojas", 0.0))),
                "desvio_dia": float(np.nan_to_num(m.get("desvio_padrao_dia_lojas", 0.0))),
                "share": 1.0 - float(np.nan_to_num(m.get("share_ecommerce", 0.0))),
                "lucro_por_peca": float(np.nan_to_num(m.get("lucro_por_peca_lojas", m.lucro_por_peca))),
                "fator": p.fator_perda_ruptura_lojas,
            },
            "covariancia": float(np.nan_to_num(m.get("covariancia_canais", 0.0))),
        },
```

- [ ] **Step 2: `item.js` — bloco por canal**

Antes do `/* ------- 5. economia */`, acrescentar:
```js
      /* ------- 4b. os dois canais */
      var cn = d.canais || {}, ce = cn.ecommerce || {}, cl = cn.lojas || {};
      h += bloco("Demanda por canal",
        '<div class="gr gr-2" style="gap:0 22px"><div>' +
        kv("E-commerce · demanda/dia", N.num(ce.demanda_dia, 3) + " ± " + N.num(ce.desvio_dia, 3)) +
        kv("Participação", N.pct(ce.share, 1)) +
        kv("Lucro por peça", "R$ " + N.moeda(ce.lucro_por_peca, 2) + " × " + N.num(ce.fator, 2)) +
        "</div><div>" +
        kv("Lojas · demanda/dia", N.num(cl.demanda_dia, 3) + " ± " + N.num(cl.desvio_dia, 3)) +
        kv("Participação", N.pct(cl.share, 1)) +
        kv("Lucro por peça", "R$ " + N.moeda(cl.lucro_por_peca, 2) + " × " + N.num(cl.fator, 2)) +
        "</div></div>" +
        '<div class="pequeno t4 mt10">Os dois canais são estimados com as mesmas máscaras de ' +
        'ruptura do CD. A política de estoque usa a soma; a participação pondera o custo de ' +
        'ruptura e reparte o custo de cada peça entre os dois caixas. Covariância diária entre ' +
        'canais: ' + N.num(cn.covariancia, 3) + '.</div>',
        "a mesma demanda, separada em quem a puxa");
```
(`bloco` e `kv` são as funções auxiliares já usadas neste arquivo.)

- [ ] **Step 3: `validacao.html` — coluna por canal na tabela de datas**

Na tabela `#tb-datas` (JS ≈ l.897–912), acrescentar uma célula ao fim de cada linha, antes de `"</tr>"`:
```js
        '<td class="n fraco">' + (s.canais ? N.pct(s.canais.ecommerce.share_prevista, 0) + " → " +
          N.pct(s.canais.ecommerce.share_realizada, 0) : "–") + "</td>" +
```
e no `<thead>` correspondente da tabela (procurar `id="tb-datas"` no HTML) acrescentar `<th class="n" title="participação do e-commerce: prevista pelo plano → realizada na venda">e-com prev → real</th>`.

- [ ] **Step 4: conferir**

Run: subir o uvicorn e
```bash
SKU=$(.venv/bin/python -c "from backend.warehouse import abrir, ref; print(abrir().query(f'select sku from {ref(\"res_plano_compra\")} where quantidade_a_comprar > 0 order by valor_da_compra desc limit 1').iloc[0,0])")
curl -s http://localhost:8000/api/item/$SKU | .venv/bin/python -c "import json,sys; d=json.load(sys.stdin); print(d['canais'])"
curl -s http://localhost:8000/validacao | grep -c "e-com prev"
```
Expected: dict com `ecommerce`/`lojas`/`covariancia`; `1`.

- [ ] **Step 5: commit**

```bash
git add backend/analitico.py static/item.js templates/validacao.html
git commit -m "dossie do item e validacao com a demanda e a participacao por canal

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: bloco 8 — a separação por canal confrontada com o dado

**Files:**
- Modify: `scripts/revisao.py` (`bloco8`)

**Interfaces:**
- Consumes: `ctx["dia"]` com `pecas_ecommerce/_lojas`; `ctx["modelo"]` com colunas por canal.
- Produces: 4 alertas diagnósticos no bloco 8 (nunca falha): decomposição da variância, coeficiente de variação por canal, deriva da participação, sazonalidade semanal por canal.

- [ ] **Step 1: implementar os diagnósticos**

No fim de `bloco8`:

```python
    # (e) os dois canais se comportam igual? Se sim, separar nao muda nada;
    # se nao, e aqui que aparece o quanto muda.
    if {"pecas_ecommerce", "pecas_lojas"} <= set(dia.columns) and "share_ecommerce" in m.columns:
        ok = dia.estado_estoque.eq("Disponivel")
        d_ok = dia[ok]
        # e1. decomposicao da variancia: Var(total) = Var(e) + Var(l) + 2 Cov
        g = d_ok.groupby("sku")
        var_t = g.pecas_vendidas.var(ddof=1)
        var_e = g.pecas_ecommerce.var(ddof=1)
        var_l = g.pecas_lojas.var(ddof=1)
        cov = g.apply(lambda x: np.cov(x.pecas_ecommerce, x.pecas_lojas)[0, 1] if len(x) > 2 else np.nan)
        rho = cov / np.sqrt(var_e * var_l)
        rho = rho[np.isfinite(rho)]
        recomposta = var_e + var_l + 2 * cov
        gap = ((recomposta - var_t) / var_t)[(var_t > 0) & np.isfinite(recomposta)]
        r.compara(B, "Var(total) = Var(e) + Var(l) + 2 Cov nos dias disponiveis",
                  recomposta[gap.index].to_numpy(), var_t[gap.index].to_numpy(), tol=TOL_FROUXA)
        r.alerta(B, "correlacao diaria entre e-commerce e lojas",
                 f"rho mediano {float(rho.median()):+.2f} · p10 {float(rho.quantile(.1)):+.2f} · "
                 f"p90 {float(rho.quantile(.9)):+.2f} em {len(rho)} itens — perto de zero, os canais "
                 f"sao independentes e o sigma da soma e a raiz da soma das variancias; positivo, "
                 f"promocoes puxam os dois juntos e a soma e mais volatil que a independencia diz")
        # e2. dispersao relativa por canal
        cv_e = (m.desvio_padrao_dia_ecommerce / m.demanda_media_dia_ecommerce.replace(0, np.nan)).dropna()
        cv_l = (m.desvio_padrao_dia_lojas / m.demanda_media_dia_lojas.replace(0, np.nan)).dropna()
        r.alerta(B, "coeficiente de variacao por canal",
                 f"e-commerce mediano {float(cv_e.median()):.2f} · lojas mediano {float(cv_l.median()):.2f} · "
                 f"total {float((m.desvio_padrao_dia / m.demanda_media_dia.replace(0, np.nan)).median()):.2f} — "
                 f"quanto mais o CV de um canal difere do outro, mais a mistura escondia")
        # e3. deriva da participacao do e-commerce ao longo do tempo
        mes = pd.to_datetime(dia.data).dt.to_period("M")
        por_mes = dia.groupby(mes).agg(e=("pecas_ecommerce", "sum"), t=("pecas_vendidas", "sum"))
        sh = (por_mes.e / por_mes.t.replace(0, np.nan)).dropna()
        r.alerta(B, "participacao do e-commerce mes a mes",
                 f"primeiro mes {float(sh.iloc[0]):.1%} · ultimo {float(sh.iloc[-1]):.1%} · "
                 f"minimo {float(sh.min()):.1%} · maximo {float(sh.max()):.1%} — a participacao usada "
                 f"e a media da janela; deriva forte pede janela mais curta para a participacao")
        # e4. sazonalidade semanal por canal
        dow = pd.to_datetime(d_ok.data).dt.dayofweek
        sem = d_ok.groupby(dow).agg(e=("pecas_ecommerce", "mean"), l=("pecas_lojas", "mean"))
        perfil_e = (sem.e / sem.e.mean()).round(2).tolist()
        perfil_l = (sem.l / sem.l.mean()).round(2).tolist()
        r.alerta(B, "perfil semanal por canal (seg..dom, 1,00 = media)",
                 f"e-commerce {perfil_e} · lojas {perfil_l} — perfis diferentes sao o sinal mais "
                 f"direto de que sao dinamicas distintas")
```

- [ ] **Step 2: rodar**

Run: `.venv/bin/python scripts/revisao.py --so 8 2>&1 | tail -12`
Expected: 1 OK novo ("Var(total) = …") e 4 alertas novos com números plausíveis; nenhuma exceção.

- [ ] **Step 3: commit**

```bash
git add scripts/revisao.py
git commit -m "revisao bloco 8: correlacao, dispersao, deriva e perfil semanal por canal

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: bateria completa, documentação e fechamento

**Files:**
- Modify: `README.md` (seção de metodologia: um parágrafo sobre canais)
- Modify: `CLAUDE.md` (arquitetura: uma frase)
- Modify: `docs/superpowers/specs/2026-09-17-demanda-por-canal-design.md` (desvios registrados)

- [ ] **Step 1: bateria inteira e conferência**

Run: `.venv/bin/python scripts/revisao.py 2>&1 | tail -8`
Expected: ≥ 100 ok; falhas = exatamente as 2 pré-existentes (custo de cadastro do item Docol; estratégias com caixa comparável). Qualquer outra falha é regressão desta feature: corrigir antes de seguir.

Run: `.venv/bin/python scripts/conferir.py --linhas 800 2>&1 | tail -4`
Expected: sem divergência.

- [ ] **Step 2: documentação**

`README.md`: na seção que explica o custo de ruptura / a fila marginal, acrescentar:

> **Dois canais, um estoque.** A venda de cada SKU é separada em e-commerce (empresa 33) e lojas (as demais). Os dois fluxos são estimados com as mesmas máscaras de ruptura do CD, porque o estoque é um só. A política usa a soma; a participação do e-commerce (μ_e/(μ_e+μ_l)) pondera o custo de ruptura (`fator_perda_ruptura_ecommerce`/`_lojas` × margem do canal) e reparte o custo de cada peça da fila entre dois caixas: a fatia do e-commerce (`teto_compra_ecommerce`) e o resto. Com `fatia_ecommerce_rigida` ligada cada canal para no seu teto; desligada, só o total limita e a fatia é leitura. Nível de serviço por canal não existe: num estoque compartilhado sem reserva a probabilidade de faltar é a mesma.

`CLAUDE.md`, no parágrafo sobre `backend/modelo.py`: acrescentar "A demanda é estimada por canal (`canal_demanda` = `ecommerce`|`lojas`, colunas `*_ecommerce`/`*_lojas`) com as mesmas máscaras de censura; a política usa a soma e `share_ecommerce` pondera o custo de ruptura e reparte o custo de cada peça entre `teto_compra_ecommerce` e o resto do caixa."

Spec (`docs/superpowers/specs/2026-09-17-demanda-por-canal-design.md`), acrescentar seção final:

```markdown
## Desvios registrados na implementação

- A coluna do canal chama-se `canal_demanda` (a coluna `canal` de `stg_vendas` já existia e é o nome da loja).
- `int_vendas_sku_dia` continua no grão SKU × dia e ganhou `pecas_ecommerce`/`lucro_ecommerce`; o invariante `pecas_ecommerce + pecas_lojas = pecas_vendidas` é garantido em `int_demanda_diaria` com `least()`.
- Na base `exports` tudo é `ecommerce` (o export é só a venda do e-commerce); na `sintetica`, tudo é `lojas`.
- `mart_demanda_estatistica` não mudou: as estatísticas por canal vivem em `res_sku_modelo`.
- `fator_perda_ruptura` (agregado) só é usado na "Política atual" de referência. O custo de ruptura do modelo usa sempre os dois fatores por canal.
```

- [ ] **Step 3: commit final**

```bash
git add README.md CLAUDE.md docs/superpowers/specs/2026-09-17-demanda-por-canal-design.md
git commit -m "docs: demanda por canal, dois caixas e os desvios da implementacao

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Auto-revisão do plano (feita ao escrever)

- **Cobertura da spec:** §1 dados → Task 1; §2 estatística → Tasks 2–3; §3 dois caixas → Tasks 4–5; §4 backtest → Task 6; §5 telas → Tasks 7–8 (`/parametros` é genérico e coberto pelas Tasks 3–4); §6 verificação → checks nas Tasks 1–4 + Task 9 + `conferir.py` na Task 3.
- **Consistência de nomes:** `canal_demanda`; `pecas_ecommerce`/`pecas_lojas`; `share_ecommerce`; `lucro_por_peca_ecommerce`/`_lojas`; `fator_perda_ruptura_ecommerce`/`_lojas`; `teto_compra_ecommerce`; `fatia_ecommerce_rigida`; `custo_ecommerce`/`custo_lojas`; `valor_da_compra_ecommerce`/`_lojas`; `caixa_acumulado_ecommerce`/`_lojas`; `caixa_restante_ecommerce`/`_lojas`; motivos `"nao coube na fatia do e-commerce"`/`"nao coube na fatia das lojas"` — usados com a mesma grafia em todas as tarefas.
- **Sem placeholders:** cada step traz o código ou o comando e o resultado esperado.
