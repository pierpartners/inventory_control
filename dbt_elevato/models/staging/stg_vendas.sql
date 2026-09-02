-- Linhas de venda. Atencao: pecas_vendidas e o que SAIU, nao o que o mercado
-- queria. A diferenca entre as duas coisas e tratada em int_demanda_diaria.
select
    cast(id_linha as bigint)              as id_linha,
    cast(pedido as varchar)               as pedido,
    cast(data_hora_venda as timestamp)    as data_hora_venda,
    cast(data as date)                    as data,
    cast(sku as varchar)                  as sku,
    cast(pecas_vendidas as integer)       as pecas_vendidas,
    cast(valor_da_peca as double)         as valor_da_peca,
    cast(preco_tabela_unit as double)     as preco_tabela_unit,
    cast(desconto_pct as double)          as desconto_pct,
    cast(receita_bruta as double)         as receita_bruta,
    cast(receita_liquida as double)       as receita_liquida,
    cast(custo_unitario as double)        as custo_unitario,
    cast(cmv as double)                   as cmv,
    cast(valor_do_frete as double)        as valor_do_frete,
    cast(frete_cobrado_cliente as double) as frete_cobrado_cliente,
    cast(impostos_sobre_venda as double)  as impostos_sobre_venda,
    cast(lucro as double)                 as lucro,
    cast(total as double)                 as total,
    cast(canal as varchar)                as canal,
    cast(tipo_cliente as varchar)         as tipo_cliente,
    cast(cliente_id as varchar)           as cliente_id,
    cast(uf as varchar)                   as uf,
    cast(regiao as varchar)               as regiao,
    cast(modalidade_frete as varchar)     as modalidade_frete,
    cast(peso_total_kg as double)         as peso_total_kg
from {{ source('raw', 'raw_vendas') }}
