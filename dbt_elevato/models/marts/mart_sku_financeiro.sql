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
    group by sku
)
select
    c.sku,
    c.item,
    c.familia,
    c.unidade,
    c.origem,
    c.custo_unitario,
    c.preco_tabela,
    c.lead_time_dias,
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
