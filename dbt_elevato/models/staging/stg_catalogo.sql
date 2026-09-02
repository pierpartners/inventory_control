-- Cadastro de itens, tipado e normalizado.
select
    cast(sku as varchar)                as sku,
    cast(item as varchar)               as item,
    cast(familia as varchar)            as familia,
    cast(unidade as varchar)            as unidade,
    cast(origem as varchar)             as origem,
    cast(custo_unitario as double)      as custo_unitario,
    cast(preco_tabela as double)        as preco_tabela,
    cast(peso_unit_kg as double)        as peso_unit_kg,
    cast(lead_time_dias as integer)     as lead_time_dias,
    cast(lote_minimo_compra as integer) as lote_minimo_compra
from {{ source('raw', 'raw_catalogo') }}
