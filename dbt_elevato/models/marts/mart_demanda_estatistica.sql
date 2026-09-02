-- As duas estimativas que SQL faz bem, lado a lado:
--   ingenua    -> divide por todos os dias, tratando falta como demanda zero
--   disponivel -> divide so pelos dias em que dava para vender
-- A terceira (imputacao dos dias censurados) e feita em Python, porque precisa
-- de iteracao, e entra como um fator multiplicativo sobre a segunda.
with base as (
    select
        sku,
        pecas_vendidas,
        dia_utilizavel
    from {{ ref('int_demanda_diaria') }}
),
ingenua as (
    select
        sku,
        avg(cast(pecas_vendidas as double))      as demanda_media_dia_ingenua,
        stddev_samp(cast(pecas_vendidas as double)) as desvio_padrao_dia_ingenuo
    from base
    group by sku
),
disponivel as (
    select
        sku,
        avg(cast(pecas_vendidas as double))      as demanda_media_dia_disponivel,
        stddev_samp(cast(pecas_vendidas as double)) as desvio_padrao_dia_disponivel,
        count(*)                                  as n_dias_utilizaveis
    from base
    where dia_utilizavel = 1
    group by sku
)
select
    i.sku,
    i.demanda_media_dia_ingenua,
    coalesce(i.desvio_padrao_dia_ingenuo, 0)        as desvio_padrao_dia_ingenuo,
    coalesce(d.demanda_media_dia_disponivel, 0)     as demanda_media_dia_disponivel,
    coalesce(d.desvio_padrao_dia_disponivel, 0)     as desvio_padrao_dia_disponivel,
    coalesce(d.n_dias_utilizaveis, 0)               as n_dias_utilizaveis,
    case when i.demanda_media_dia_ingenua > 0
         then coalesce(d.demanda_media_dia_disponivel, 0) / i.demanda_media_dia_ingenua - 1
         else 0 end                                  as subestimacao_ingenua_pct
from ingenua i
left join disponivel d on d.sku = i.sku
