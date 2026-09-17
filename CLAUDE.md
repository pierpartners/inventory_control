# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Núcleo — a FastAPI + dbt + DuckDB application that computes, per SKU, the reorder point, purchase
lot, and a capital-return-prioritized purchase plan for Elevato (a retailer). It corrects for the
zero-stock bias in demand estimation and treats low-turnover/high-value items separately (the
"marginal unit" regime). Full methodology is in [README.md](README.md) — read it for the modeling
rationale (censored-demand EM correction, marginal peça-a-peça allocation, the four stopping
criteria, capital-return math) before touching `backend/modelo.py`.

## Commands

```bash
pip install -r requirements.txt

# full pipeline: load CSVs -> dbt (staging/intermediate/marts) -> Python model -> write res_* tables
python scripts/rodar_pipeline.py
python scripts/rodar_pipeline.py --pular-dbt              # only recompute the Python model
python scripts/rodar_pipeline.py --pular-carga --pular-dbt  # only recompute, skip dbt and CSV load

uvicorn backend.main:app --reload --port 8000   # then open http://localhost:8000

# pull the real data straight from the Elevato DW (needs .env with DW credentials), then run the pipeline
python scripts/extrair_dw.py                    # 3 years, writes Parquet to data/fonte_dw/
python scripts/extrair_dw.py --anos 1 --so raw_compras raw_ciclo_pagamento
```

After changing a parameter, use **Salvar e recalcular** on the `/parametros` page instead of
rerunning the pipeline from the terminal — it only reruns the Python model. `dbt build` (inside
`rodar_pipeline.py`) is only needed when the source CSVs themselves change.

**Tests / verification** (there is no pytest suite — verification is two standalone scripts that
recompute results independently and diff against what's stored):

```bash
python scripts/revisao.py              # ~90 independent checks across 8 blocks; see below
python scripts/conferir.py --linhas 800  # recomputes mu/sigma/F(k-1)/P/M/L/ganho/custo/V/nota
python scripts/conferir.py --sku <SKU>    # same, for one item
python scripts/conferir.py --tudo         # same, for the whole fila
```

`scripts/revisao.py` is the closest thing to a test suite: it recalculates every stage
(censura/EM, distribution choice, per-item policy, marginal allocation, per-peça math, economic
sanity) with plain numpy/scipy and fails loudly if a number doesn't match. Block 8 is model
assumptions confronted with the actual data (overdispersion, day independence, weekly
seasonality, lead-time variability) — read its output before changing any statistical assumption
in `modelo.py`, since the printed diagnostics tell you whether the current assumption still holds
against the loaded data. There is no CI wired up; run both scripts locally after touching
`backend/modelo.py`, `backend/validacao.py`, or any dbt model.

## Architecture

**Data flow:** CSV → DuckDB `raw_*` tables → dbt (`staging` → `intermediate` → `marts`) → Python
model (`backend/modelo.py`) → `res_*` result tables written back to the warehouse → FastAPI reads
`res_*`/`mart_*` for every page. Nothing in `backend/` talks to source CSVs directly; everything
goes through dbt-produced marts or `res_*` tables.

**Three source formats coexist**, selected by the dbt var `base` (`sintetica`, `exports` or `real`), and
`scripts/rodar_pipeline.py::base_disponivel()` auto-detects which one to use by checking whether
`data/fonte_verdadeira/` (the real ERP extract, preferred), `../dbt-elevato/exports/` (the DW export: one daily SKU file + `atributos_sku.csv`; override the folder with `EXPORTS_DIR`, base name `exports`) or `data/fonte/` (synthetic simulation
CSVs) exists on disk — override with the `BASE` env var. The two extracts have genuinely
different shapes (the real catalog has no cost/lead-time columns, the real sales table has no
margin, etc.); translation between them happens entirely in the `staging` dbt layer
(`stg_catalogo.sql`, `stg_vendas.sql`, `stg_estoque_diario.sql`), so anything downstream of
staging is format-agnostic. `dbt_elevato/models/sources.yml` documents both raw schemas.

**Warehouse abstraction (`backend/warehouse.py`):** all SQL access goes through `Warehouse`
(`DuckDBWarehouse` today, `BigQueryWarehouse` ready but unwired — switch via `WAREHOUSE=bigquery`
in `.env`, no code changes needed since the same dbt SQL and Python run against either). Two
things to know when writing queries:
- **Every `wh.query()` call opens a fresh connection.** Temp tables do NOT persist across
  separate `.query()` calls — any multi-step SQL must be inlined as CTEs in one query, not split
  across calls that assume shared session state.
- DuckDB reads are opened read-only so the app, the pipeline, and a second server instance can
  all touch the file concurrently; writes (`gravar`, `carregar_csv`) need exclusive access.
- Use `ref('nome_da_tabela')` to qualify table names — it resolves to the right quoting for
  whichever engine is active.

**`backend/modelo.py` is the calculation engine** — the only place that should contain modeling
logic. Order of the pipeline inside `executar()`: `ler_base` → censored-demand EM correction
(`em_censurado`) → per-item demand statistics → distribution fit (Poisson/Negative Binomial,
`ajustar_distribuicao`) → classification (`classificar`) → scarcity shadow price
(`resolver_premio_escassez`) → per-item policy (`modelar`) → marginal peça-a-peça allocation
(`plano_marginal`/`alocacao_marginal`) → `gravar_resultados`. `executar()` takes an optional `ate`
(cuts history at a date, used by backtesting) and an optional `skus` set (restricts the universe
*before* capital allocation — filtering after the plan is built would let the model have spent
money on excluded items). `backend/analitico.py` re-derives page-level slices by calling back
into `modelo.py`'s functions rather than reimplementing math, so a page's explanation is
guaranteed to be the same calculation that produced the number, never a parallel reimplementation.
A demanda é estimada por canal (`canal_demanda` = `ecommerce`|`lojas`, colunas
`*_ecommerce`/`*_lojas`) com as mesmas máscaras de censura; a política usa a soma e
`share_ecommerce` pondera o custo de ruptura e reparte o custo de cada peça entre
`teto_compra_ecommerce` e o resto do caixa.

**Other backend modules, each a distinct concern:**
- `backend/config.py` — `Parametros` dataclass (all economic assumptions), `CAMPOS`/`GRUPOS`
  metadata for the `/parametros` page, `CHAVES`/`CRITERIOS` for the stopping-criteria page.
- `backend/validacao.py` — historical backtesting: reruns the whole model as of a past cutoff
  date with that date's real cash/capital ceilings (never a hand-picked number), then compares
  the model's plan against what the company actually bought and what actually sold afterward.
  `rodar_intervalo()` runs several cutoffs back to back for a multi-week series.
- `backend/qualidade.py` — data-quality checks (`verificar()`), grouped by `GRUPOS`, each check a
  `_v(...)` dict with grupo/título/nível/valor/achado/ação; feeds the `/qualidade` page and the
  qualidade-alert badge in the nav.
- `backend/acompanhamento.py` — day-by-day per-SKU reconciliation (stock vs. sales vs. reserva)
  for manual conference, independent of the optimization model.
- `backend/main.py` — FastAPI app; one route pair per page (`GET /pagina` renders the template,
  `GET /api/pagina/recurso` returns JSON for that page's charts/tables). Route handlers follow a
  fixed shape: get a `Warehouse` via `wh()`, bail out to `sem_dados()` if `pronto(w)` is false,
  otherwise build a context dict and return a `TemplateResponse`.

**Caching:** three module-level dicts in `main.py` (`_cache_validacao`, `_cache_qualidade`,
`_cache_ranking`) hold results that are too expensive to recompute per click (backtesting ~2s per
date, the quality battery ~13s over 1.3M rows). All three are cleared together by
`invalidar_caches()`, which must be called any time parameters change or the pipeline reruns —
never clear just one cache in isolation, since they can go stale together.

**Frontend:** server-rendered Jinja2 templates (no JS framework/build step). `static/nucleo.js`
holds the shared chart/table primitives reused across every page (`N.grafico`, `N.tabela`,
`N.buscar`, `N.espera`, per-SKU color hashing) — check there before writing bespoke chart/table
code in a page-specific script. Charts are ECharts 5.5.1; `dataZoom` (inside + slider) is the
pattern for PowerBI-style time-axis zoom.

**Presentation generation:** one-off PowerPoint decks (e.g. board updates) are generated with
`python-pptx`, not `pptxgenjs`/Node — this environment has no Node.js. If asked to build or update
a `.pptx`, use `python-pptx` directly rather than assuming a JS toolchain is available.
