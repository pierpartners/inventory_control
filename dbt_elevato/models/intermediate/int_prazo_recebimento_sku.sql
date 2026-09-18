{#
  Quantos dias depois da VENDA o dinheiro de cada item entra no caixa, por
  canal. E a ponta de entrada do ciclo financeiro da nota (a de saida e o
  prazo ao fornecedor, em stg_catalogo).

  O titulo a receber e do PEDIDO, nao do item: um pedido com tres itens gera
  um titulo (ou um por parcela) e o prazo dele vale para os tres. Por isso a
  conta e: para cada item, a mediana de `dias_recebimento` dos titulos de
  todos os pedidos em que ele apareceu, separada em e-commerce (empresa 33)
  e lojas - sao meios de pagamento diferentes: o site recebe pelo gateway em
  D+30, a loja recebe a vista, no debito ou em cartao parcelado.

  A contagem de titulos fica ao lado para o motor decidir quando confiar:
  item com menos de 3 titulos num canal herda a mediana do canal no catalogo
  (a mesma regra do prazo do fornecedor), e sem tabela nenhuma vale o
  parametro. So a base `real`/`dw` tem a tabela; nas outras o modelo sai
  vazio e o motor cai no parametro.
#}

-- depends_on: {{ ref('stg_vendas') }}
-- depends_on: {{ source('raw', 'raw_ciclo_recebimento') }}

{% set rel = adapter.get_relation(database=target.database, schema=target.schema,
                                  identifier='raw_ciclo_recebimento') %}

{% if var('base', 'sintetica') == 'real' and rel is not none %}

with titulos as (
    select cast(idempresa as varchar)          as loja_id,
           cast(pedido as varchar)             as pedido,
           cast(dias_recebimento as double)    as dias,
           cast(valtitulo as double)           as valor
    from {{ source('raw', 'raw_ciclo_recebimento') }}
    where dias_recebimento is not null
      and cast(dias_recebimento as double) between 0 and 365
),

itens as (
    -- um item por pedido, uma vez: a linha de venda repete o par quando o
    -- pedido tem mais de uma sequencia do mesmo produto
    select distinct sku, loja_id, pedido, canal_demanda
    from {{ ref('stg_vendas') }}
    where loja_id is not null
),

por_canal as (
    select i.sku, i.canal_demanda,
           median(t.dias)  as prazo_dias,
           count(*)        as titulos,
           sum(t.valor)    as valor
    from itens i
    join titulos t on t.loja_id = i.loja_id and t.pedido = i.pedido
    group by 1, 2
)

select
    sku,
    max(case when canal_demanda = 'ecommerce' then prazo_dias end) as prazo_recebimento_ecommerce_dias,
    coalesce(max(case when canal_demanda = 'ecommerce' then titulos end), 0) as titulos_ecommerce,
    max(case when canal_demanda = 'lojas' then prazo_dias end)     as prazo_recebimento_lojas_dias,
    coalesce(max(case when canal_demanda = 'lojas' then titulos end), 0)     as titulos_lojas
from por_canal
group by 1

{% else %}

select
    cast(null as varchar) as sku,
    cast(null as double)  as prazo_recebimento_ecommerce_dias,
    cast(null as integer) as titulos_ecommerce,
    cast(null as double)  as prazo_recebimento_lojas_dias,
    cast(null as integer) as titulos_lojas
where false

{% endif %}
