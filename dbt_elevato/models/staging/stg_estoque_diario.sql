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

  1. A grade carrega FISICO e DISPONIVEL lado a lado. O motor decide pelo
     fisico (ver executar() em backend/modelo.py): a reserva e fila de pedidos
     que fatura pela mesma venda que o modelo mede, e descontar a reserva da
     posicao contaria o pedido duas vezes. O disponivel fica para diagnostico
     e para a tela de acompanhamento. 3.681 linhas vem com disponivel negativo
     (reserva acima do fisico) e nessas o piso e zero.
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
    -- venda do dia de TODAS as lojas, pela MESMA regra da tabela de vendas:
    -- sai de stg_vendas, que ja descarta devolucao/cancelamento (linha
    -- negativa) e a venda sob ENCOMENDA. Ate aqui esta CTE lia a fonte crua
    -- direto, somava por dia e truncava em zero - e com isso a encomenda
    -- entrava no sinal de demanda e o cancelamento lancado em outro dia se
    -- perdia. Medido em 365 dias, so nos dias utilizaveis: 158 mil pecas de
    -- ENCOMENDA (1.457 SKUs) dentro da grade, e 1.966 dos 3.231 itens com
    -- total diferente entre a grade e a tabela de vendas. O caso que denunciou:
    -- a pastilha 1088006, vendida duas vezes em tres anos sob encomenda, lida
    -- como 25 m2/dia porque a passagem da mercadoria pelo CD (5 dias, toda
    -- reservada) era o unico historico "utilizavel".
    --
    -- Esta e A coluna do projeto: dela sai toda estimativa de demanda. A
    -- fonte e a de todas as lojas porque o estoque desta grade e o do CD
    -- (empresa 26, local 124), que abastece as 33 lojas. Medido em 3 anos nos
    -- 1.190 SKUs com estoque: a baixa de disponivel foi de 584.158 pecas, o
    -- e-commerce vendeu 91.821 (17%) e todas as lojas 672.225.
    select sku, data, sum(pecas_vendidas) as pecas
    from {{ ref('stg_vendas') }}
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
        -- DISPONIVEL (fisico menos reserva) fica na grade para diagnostico e
        -- conferencia. Nao decide a compra: a reserva fatura pela venda que o
        -- modelo ja mede em `fisico` (2,0% dos dias com disponivel zero e
        -- fisico positivo tem venda, contra 0,1% dos dias com fisico zero).
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

{% elif var('base', 'sintetica') == 'exports' %}

-- A exportacao ja traz saldo de abertura e fechamento encadeados (conferido na
-- origem: final = inicial + entradas - saidas em 100% das linhas). A venda do
-- dia e a do e-commerce (qtd_vendida, pedidos brutos: devolucao nao e demanda
-- negativa). ATENCAO: `saidas` do CD inclui transferencia para as lojas e e
-- ~11x maior que a venda do e-commerce; a demanda aqui e so a do canal online.
select
    cast(sku as varchar)                          as sku,
    cast(data as date)                            as data,
    greatest(cast(estoque_inicial as double), 0)  as saldo_inicial,
    greatest(cast(estoque_final as double), 0)    as saldo_final,
    greatest(cast(estoque_final as double), 0)    as disponivel_final,
    cast(round(cast(qtd_vendida as double)) as integer) as pecas_vendidas,
    case
        when cast(estoque_inicial as double) <= 0 then 'Sem estoque'
        when cast(estoque_final as double)   <= 0 then 'Ruptura parcial'
        else 'Disponivel'
    end as estado_estoque,
    case when cast(estoque_inicial as double) <= 0 then 0 else 1 end as dia_utilizavel,
    case when cast(estoque_inicial as double) > 0
          and cast(estoque_final as double) <= 0 then 1 else 0 end as dia_censurado
from {{ source('raw', 'raw_diario_sku') }}

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
