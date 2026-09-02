-- Onde a falta doeu. O lucro perdido usa a demanda corrigida, que so existe
-- depois do modelo em Python - aqui fica a parte que nao depende dele.
select
    d.sku,
    f.item,
    f.familia,
    f.custo_unitario,
    f.lucro_por_peca,
    d.dias_sem_estoque,
    d.dias_ruptura_parcial,
    d.dias_utilizaveis,
    d.pct_indisponivel,
    e.demanda_media_dia_disponivel,
    e.demanda_media_dia_ingenua,
    d.dias_sem_estoque * e.demanda_media_dia_disponivel as venda_perdida_pecas_sql
from {{ ref('mart_sku_disponibilidade') }} d
join {{ ref('mart_sku_financeiro') }} f on f.sku = d.sku
join {{ ref('mart_demanda_estatistica') }} e on e.sku = d.sku
