-- Financeiro consolidado por item e por dia.
select
    sku,
    data,
    count(*)                       as linhas_de_venda,
    count(distinct pedido)         as pedidos,
    sum(pecas_vendidas)            as pecas_vendidas,
    sum(receita_liquida)           as receita_liquida,
    sum(total)                     as total_faturado,
    sum(cmv)                       as cmv,
    sum(valor_do_frete)            as frete_custo,
    sum(impostos_sobre_venda)      as impostos,
    sum(lucro)                     as lucro
from {{ ref('stg_vendas') }}
group by sku, data
