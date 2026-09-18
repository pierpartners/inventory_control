{#
  Foto da posicao de estoque FORA do CD, um registro por (empresa, local, SKU).
  So existe na extracao direta do DW (var posicao_lojas=true); nas outras
  bases a view sai vazia com as mesmas colunas, e o mart soma zero.
#}

{% if var('base', 'sintetica') == 'real' and var('posicao_lojas', false) %}

select
    cast(idsubproduto as varchar)       as sku,
    cast(idempresa as integer)          as empresa,
    cast(idlocalestoque as integer)     as local,
    cast(dtmovimento as date)           as data,
    cast(qtdatualestoque as double)     as saldo,
    cast(valcustomedio as double)       as custo_medio
from {{ source('raw', 'raw_estoque_posicao_lojas') }}

{% else %}

select
    cast(null as varchar) as sku,
    cast(null as integer) as empresa,
    cast(null as integer) as local,
    cast(null as date)    as data,
    cast(null as double)  as saldo,
    cast(null as double)  as custo_medio
where false

{% endif %}
