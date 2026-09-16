-- Um registro por item: o que ele vendeu e o que sobrou, no periodo inteiro.
with v as (
    select
        sku,
        sum(pecas_vendidas)       as pecas_vendidas,
        count(*)                  as linhas_de_venda,
        count(distinct pedido)    as pedidos,
        sum(receita_liquida)      as receita_liquida,
        sum(total)                as total_faturado,
        sum(cmv)                  as cmv,
        sum(valor_do_frete)       as frete_custo,
        sum(impostos_sobre_venda) as impostos,
        sum(lucro)                as lucro
    from {{ ref('stg_vendas') }}
    -- O extrato de venda vai mais longe que o retrato de estoque (ate
    -- 2026-09-08 contra 2026-09-04). A grade diaria do modelo termina no
    -- ultimo dia de estoque, entao somar a venda dos dias sobrantes poria peca
    -- no numerador sem por dia no denominador. O corte tambem e o que faz o
    -- espelho com data de backtest em modelo.py reproduzir este mart - a
    -- igualdade que scripts/revisao.py cobra.
    where data <= (select max(data) from {{ ref('stg_estoque_diario') }})
    group by sku
)
select
    c.sku,
    c.item,
    c.familia,
    c.unidade,
    c.origem,
    c.custo_unitario,
    c.custo_ultimo_lancado,
    c.custo_mediano,
    c.preco_tabela,
    c.lead_time_dias,
    -- desvio do prazo de entrega. Zero na base sintetica (prazo fixo);
    -- no extrato real a mediana e 13,2 dias sobre um prazo mediano de 18,
    -- e e o motor que decide o que fazer com isso.
    c.lead_time_desvio_dias,
    c.lead_time_pedidos,
    c.lote_minimo_compra,
    coalesce(v.pecas_vendidas, 0)  as pecas_vendidas,
    coalesce(v.linhas_de_venda, 0) as linhas_de_venda,
    coalesce(v.pedidos, 0)         as pedidos,
    coalesce(v.receita_liquida, 0) as receita_liquida,
    coalesce(v.total_faturado, 0)  as total_faturado,
    coalesce(v.cmv, 0)             as cmv,
    coalesce(v.frete_custo, 0)     as frete_custo,
    coalesce(v.impostos, 0)        as impostos,
    coalesce(v.lucro, 0)           as lucro_observado,
    case when coalesce(v.pecas_vendidas, 0) > 0
         then v.lucro / v.pecas_vendidas else 0 end as lucro_por_peca,
    case when coalesce(v.total_faturado, 0) > 0
         then v.lucro / v.total_faturado else 0 end as margem_pct
from {{ ref('stg_catalogo') }} c
left join v on v.sku = c.sku
