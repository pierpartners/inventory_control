-- A grade completa item x dia. A base do estoque ja traz todos os dias,
-- inclusive os sem venda - e sao eles que carregam a informacao.
select
    e.sku,
    e.data,
    e.estado_estoque,
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
