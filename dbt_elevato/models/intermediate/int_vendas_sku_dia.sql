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
    sum(lucro)                     as lucro,
    sum(case when canal_demanda = 'ecommerce' then pecas_vendidas else 0 end) as pecas_ecommerce,
    sum(case when canal_demanda = 'ecommerce' then lucro else 0 end)          as lucro_ecommerce
from {{ ref('stg_vendas') }}
group by sku, data
