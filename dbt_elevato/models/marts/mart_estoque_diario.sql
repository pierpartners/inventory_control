-- Grade item x dia pronta para os graficos de variacao de estoque.
select
    sku,
    data,
    saldo_inicial,
    saldo_final,
    pecas_vendidas,
    estado_estoque,
    dia_utilizavel,
    dia_censurado
from {{ ref('int_demanda_diaria') }}
