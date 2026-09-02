-- Serie diaria da operacao inteira, para o acompanhamento no dashboard.
with dia as (
    select
        data,
        count(distinct pedido)    as pedidos,
        count(*)                  as linhas,
        sum(pecas_vendidas)       as pecas,
        sum(receita_liquida)      as receita_liquida,
        sum(total)                as faturamento,
        sum(cmv)                  as cmv,
        sum(lucro)                as lucro
    from {{ ref('stg_vendas') }}
    group by data
),
falta as (
    select
        data,
        sum(case when estado_estoque = 'Sem estoque' then 1 else 0 end) as skus_sem_estoque,
        count(*)                                                        as skus_total,
        sum(saldo_final)                                                as pecas_em_estoque
    from {{ ref('int_demanda_diaria') }}
    group by data
)
select
    f.data,
    coalesce(d.pedidos, 0)          as pedidos,
    coalesce(d.linhas, 0)           as linhas,
    coalesce(d.pecas, 0)            as pecas,
    coalesce(d.receita_liquida, 0)  as receita_liquida,
    coalesce(d.faturamento, 0)      as faturamento,
    coalesce(d.lucro, 0)            as lucro,
    f.skus_sem_estoque,
    f.skus_total,
    f.skus_sem_estoque * 1.0 / f.skus_total as pct_skus_sem_estoque,
    f.pecas_em_estoque
from falta f
left join dia d on d.data = f.data
