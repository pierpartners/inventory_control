-- A grade completa item x dia. A base do estoque ja traz todos os dias,
-- inclusive os sem venda - e sao eles que carregam a informacao.
--
-- Um quarto estado sai daqui: `Pre-lancamento`, os dias ANTES do primeiro
-- sinal de vida do item (primeiro dia com saldo ou com venda). Nesses dias o
-- produto nao existia para o cliente - nao ha demanda sendo perdida - e o
-- staging os marcava como `Sem estoque` porque o saldo era zero. Contados
-- como ruptura, inventavam venda perdida e inflavam a "correcao de censura".
-- O caso que denunciou: o piso vinilico 1172691, pedido em nov/2025 e
-- recebido em jun/2026, lido com 259 dias "sem estoque" em 365 e R$ 235 mil
-- de lucro perdido que nunca existiu. Medido na base real: 3.585 SKUs e
-- R$ 7,5 mi dos R$ 16,5 mi de lucro perdido vinham desses dias.
--
-- Item sem nenhum sinal de vida na grade inteira fica como esta: nao ha data
-- de lancamento para marcar, e ele nao tem demanda de qualquer jeito. O marco
-- olha so para tras (o primeiro sinal ate o dia), entao o backtest com `ate`
-- ve a mesma classificacao que teria visto naquela data.
with marco as (
    select sku,
           min(case when saldo_inicial > 0 or saldo_final > 0 or pecas_vendidas > 0
                    then data end) as primeiro_dia_vivo
    from {{ ref('stg_estoque_diario') }}
    group by sku
)
select
    e.sku,
    e.data,
    case when e.data < m.primeiro_dia_vivo then 'Pre-lancamento'
         else e.estado_estoque end as estado_estoque,
    e.dia_utilizavel,
    e.dia_censurado,
    e.saldo_inicial,
    e.saldo_final,
    e.disponivel_final,
    e.pecas_vendidas,
    -- Venda por canal, presa a identidade pecas_ecommerce + pecas_lojas =
    -- pecas_vendidas POR CONSTRUCAO: o e-commerce nunca passa do total do dia
    -- e as lojas ficam com o resto. Na base real as duas fontes sao a mesma
    -- tabela e o `least` nao corta nada; na sintetica a grade vem do estoque e
    -- a venda de outra tabela, e sem isso a identidade quebraria.
    -- `pecas_lojas` subtrai o INTEIRO ja arredondado de pecas_ecommerce, nunca
    -- um segundo cast sobre outra expressao em ponto flutuante: a venda real
    -- vem fracionada (rateio entre depositos) e o cast double->integer do
    -- DuckDB arredonda para o par mais proximo (banker's rounding) - dois
    -- casts independentes sobre 16.5 e 23-16.5 podem arredondar os DOIS para
    -- baixo e quebrar a soma por 1 unidade.
    cast(least(coalesce(v.pecas_ecommerce, 0), e.pecas_vendidas) as integer)      as pecas_ecommerce,
    e.pecas_vendidas
        - cast(least(coalesce(v.pecas_ecommerce, 0), e.pecas_vendidas) as integer) as pecas_lojas,
    coalesce(v.receita_liquida, 0) as receita_liquida,
    coalesce(v.lucro, 0)           as lucro,
    coalesce(v.pedidos, 0)         as pedidos
from {{ ref('stg_estoque_diario') }} e
left join {{ ref('int_vendas_sku_dia') }} v
       on v.sku = e.sku and v.data = e.data
left join marco m on m.sku = e.sku
