{#
  Custo medio CONTABIL do ERP por SKU e dia, so nos dias em que havia estoque
  (quando o saldo zera o ERP zera o custo junto e lanca R$ 1,00 - ver
  stg_catalogo). E a segunda valorizacao do diagnostico: o custo que a
  contabilidade ve, ao lado do custo que o modelo usa.
#}

{% if var('base', 'sintetica') == 'real' %}

select
    cast(idsubproduto as varchar) as sku,
    cast(dtmovimento as date)     as data,
    cast(valcustomedio as double) as custo
from {{ source('raw', 'raw_estoque_diario_erp') }}
where valcustomedio > 0
  and cast(qtdatualestoque as double) > 0

{% else %}

select cast(null as varchar) as sku, cast(null as date) as data, cast(null as double) as custo
where false

{% endif %}
