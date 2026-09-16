{#
  Posicao diaria + a classificacao do dia. Esta e a regra mais importante do
  projeto inteiro:
    Sem estoque      -> o dia nao diz NADA sobre a demanda. Fica fora da conta.
    Ruptura parcial  -> a venda registrada e um PISO ("vendeu pelo menos X").
                        Entra na conta, mas com valor imputado pelo modelo.
    Disponivel       -> a venda e a demanda. Inclusive quando foi zero.

  No extrato real o ERP nao entrega saldo de abertura nem de fechamento: da um
  retrato por dia (qtdatualestoque, qtdreserva, qtddisponivel). A grade e
  reconstruida daqui, e duas escolhas ficam registradas porque mudam decisao:

  1. A posicao e o DISPONIVEL, nao o fisico. A peca reservada ja tem dono e nao
     protege a proxima venda; conta-la faria o motor deixar de comprar o que
     precisa. 3.681 linhas vem com disponivel negativo (reserva acima do
     fisico) e nessas o piso e zero.
  2. O saldo de abertura de um dia e o fechamento do dia anterior. Com isso a
     cadeia fecha por construcao e o recebimento de mercadoria aparece como o
     salto entre o fechamento de um dia e a abertura do seguinte - a mesma
     convencao que backend/validacao.py usa para reconstruir as entradas.
#}

{% if var('base', 'sintetica') == 'real' %}

with pos as (
    select
        cast(idsubproduto as varchar)                     as sku,
        cast(dtmovimento as date)                         as data,
        greatest(cast(qtddisponivel as double), 0)        as disponivel,
        greatest(cast(qtdatualestoque as double), 0)      as fisico
    from {{ source('raw', 'raw_estoque_diario_erp') }}
),

vendido as (
    -- venda do dia de TODAS as lojas, liquida de devolucao e com piso zero:
    -- devolucao nao e demanda negativa.
    --
    -- Esta e A coluna do projeto: dela sai toda estimativa de demanda. Ate
    -- aqui ela lia `raw_vendas_ecommerce` - so a empresa 33 -, enquanto o
    -- estoque desta grade e o do CD (empresa 26, local 124), que abastece as
    -- 33 lojas. Medido em 3 anos nos 1.190 SKUs com estoque: a baixa de
    -- disponivel foi de 584.158 pecas, o e-commerce vendeu 91.821 (17%) e
    -- todas as lojas 672.225. No par SKU-dia, o e-commerce explicava 8,3% da
    -- baixa e o conjunto explica 85,8%.
    select cast(IDSUBPRODUTO as varchar) as sku,
           cast(DATA as date)            as data,
           greatest(sum(cast(QTDPRODUTO as double)), 0) as pecas
    from {{ source('raw', 'raw_vendas_todas') }}
    group by 1, 2
),

grade as (
    select
        p.sku,
        p.data,
        -- FISICO governa o sinal de demanda: se havia mercadoria na prateleira,
        -- o dia diz algo sobre o que o mercado quis, mesmo que a peca estivesse
        -- reservada para outro pedido. Em 145.190 dias-item o disponivel era
        -- zero com fisico positivo, e nesses dias houve venda 25 vezes mais
        -- frequentemente do que nos dias de prateleira vazia - descarta-los
        -- jogaria 11% da base fora e inflaria a demanda estimada.
        p.fisico                                         as saldo_final,
        lag(p.fisico) over (partition by p.sku order by p.data) as saldo_ant,
        -- DISPONIVEL governa a decisao de compra: a peca reservada nao protege
        -- a proxima venda. As duas colunas seguem juntas de proposito, porque
        -- responder as duas perguntas com o mesmo numero e o erro facil aqui.
        p.disponivel                                     as disponivel_final,
        coalesce(v.pecas, 0)                             as pecas_vendidas
    from pos p
    left join vendido v on v.sku = p.sku and v.data = p.data
)

select
    sku,
    data,
    -- no primeiro dia da serie nao existe anterior: reconstroi somando de volta
    -- o que saiu, que e a unica leitura coerente com a identidade do estoque
    coalesce(saldo_ant, saldo_final + pecas_vendidas)     as saldo_inicial,
    saldo_final,
    disponivel_final,
    cast(pecas_vendidas as integer)                       as pecas_vendidas,
    case
        when coalesce(saldo_ant, saldo_final + pecas_vendidas) <= 0 then 'Sem estoque'
        when saldo_final <= 0 then 'Ruptura parcial'
        else 'Disponivel'
    end as estado_estoque,
    case when coalesce(saldo_ant, saldo_final + pecas_vendidas) <= 0
         then 0 else 1 end as dia_utilizavel,
    case when coalesce(saldo_ant, saldo_final + pecas_vendidas) > 0
          and saldo_final <= 0 then 1 else 0 end as dia_censurado
from grade

{% else %}

select
    cast(sku as varchar)            as sku,
    cast(data as date)              as data,
    cast(saldo_inicial as double)   as saldo_inicial,
    cast(saldo_final as double)     as saldo_final,
    -- a base sintetica nao tem reserva: disponivel e o proprio saldo
    cast(saldo_final as double)     as disponivel_final,
    cast(pecas_vendidas as integer) as pecas_vendidas,
    case
        when saldo_inicial <= 0 then 'Sem estoque'
        when saldo_final  <= 0 then 'Ruptura parcial'
        else 'Disponivel'
    end as estado_estoque,
    case when saldo_inicial <= 0 then 0 else 1 end as dia_utilizavel,
    case when saldo_inicial > 0 and saldo_final <= 0 then 1 else 0 end as dia_censurado
from {{ source('raw', 'raw_estoque_diario') }}

{% endif %}
