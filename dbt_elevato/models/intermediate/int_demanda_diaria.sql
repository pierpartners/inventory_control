-- A grade completa item x dia. A base do estoque ja traz todos os dias,
-- inclusive os sem venda - e sao eles que carregam a informacao.
select
    e.sku,
    e.data,
    e.estado_estoque,
    e.dia_utilizavel,
    e.dia_censurado,
    e.saldo_inicial,
    e.saldo_final,
    e.pecas_vendidas,
    coalesce(v.receita_liquida, 0) as receita_liquida,
    coalesce(v.lucro, 0)           as lucro,
    coalesce(v.pedidos, 0)         as pedidos
from {{ ref('stg_estoque_diario') }} e
left join {{ ref('int_vendas_sku_dia') }} v
       on v.sku = e.sku and v.data = e.data
