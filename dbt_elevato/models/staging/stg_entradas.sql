{#
  Entradas de mercadoria no CD: quando e quantas pecas. Alimenta a idade FIFO
  do saldo atual (mart_estoque_posicao). Vem do ciclo de pagamento porque e a
  unica fonte com a DATA DE ENTRADA NO ESTOQUE por nota.
#}

{% if var('base', 'sintetica') == 'real' %}

select
    cast(idsubproduto as varchar)        as sku,
    cast(dt_entrada_estoque as date)     as data,
    cast(qtdatendida as double)          as pecas
from {{ source('raw', 'raw_ciclo_pagamento') }}
where dt_entrada_estoque is not null
  and cast(qtdatendida as double) > 0

{% else %}

select cast(null as varchar) as sku, cast(null as date) as data, cast(null as double) as pecas
where false

{% endif %}
