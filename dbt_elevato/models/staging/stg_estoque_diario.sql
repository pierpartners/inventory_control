-- Posicao diaria + a classificacao do dia. Esta e a regra mais importante
-- do projeto inteiro:
--   Sem estoque      -> o dia nao diz NADA sobre a demanda. Fica fora da conta.
--   Ruptura parcial  -> a venda registrada e um PISO ("vendeu pelo menos X").
--                       Entra na conta, mas com valor imputado pelo modelo.
--   Disponivel       -> a venda e a demanda. Inclusive quando foi zero.
select
    cast(sku as varchar)            as sku,
    cast(data as date)              as data,
    cast(saldo_inicial as double)   as saldo_inicial,
    cast(saldo_final as double)     as saldo_final,
    cast(pecas_vendidas as integer) as pecas_vendidas,
    case
        when saldo_inicial <= 0 then 'Sem estoque'
        when saldo_final  <= 0 then 'Ruptura parcial'
        else 'Disponivel'
    end as estado_estoque,
    case when saldo_inicial <= 0 then 0 else 1 end as dia_utilizavel,
    case when saldo_inicial > 0 and saldo_final <= 0 then 1 else 0 end as dia_censurado
from {{ source('raw', 'raw_estoque_diario') }}
