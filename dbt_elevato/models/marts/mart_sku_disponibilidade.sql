-- Quantos dias cada item pode ser observado, e quantos foram perdidos.
select
    sku,
    count(*)                                     as dias_historico,
    sum(dia_utilizavel)                          as dias_utilizaveis,
    sum(case when estado_estoque = 'Sem estoque' then 1 else 0 end)     as dias_sem_estoque,
    sum(dia_censurado)                           as dias_ruptura_parcial,
    sum(case when estado_estoque = 'Sem estoque' then 1 else 0 end) * 1.0 / count(*) as pct_indisponivel,
    sum(case when pecas_vendidas > 0 then 1 else 0 end)               as dias_com_venda,
    max(pecas_vendidas)                          as demanda_max_dia,
    min(saldo_final)                             as saldo_minimo,
    max(saldo_final)                             as saldo_maximo,
    avg(saldo_final)                             as saldo_medio
from {{ ref('int_demanda_diaria') }}
group by sku
