{#
  Linhas de venda de TODAS as lojas do grupo. Atencao: pecas_vendidas e o que
  SAIU, nao o que o mercado queria - a diferenca e tratada em
  int_demanda_diaria.

  ---------------------------------------------------------------------------
  POR QUE A FONTE MUDOU (e o maior erro corrigido no projeto)

  Antes este modelo lia `raw_vendas_ecommerce`: so a empresa 33. Mas o estoque
  diario e de UMA posicao - idempresa 26, idlocalestoque 124, o centro de
  distribuicao -, e essa posicao abastece as 33 lojas do grupo. Dimensionar a
  prateleira do CD contra a demanda de um canal era comparar duas entidades
  diferentes.

  Medido nos 1.190 SKUs que tem estoque, em 3 anos:

    baixa de estoque DISPONIVEL                    584.158 pecas
    venda do e-commerce (a fonte antiga)            91.821 pecas    17%
    venda de todas as lojas (esta fonte)           672.225 pecas   115%

  E no par SKU-dia, quanto da baixa de disponivel cada fonte explica no mesmo
  dia: e-commerce 8,3%, todas as lojas 85,8%. O modelo estimava demanda com um
  oitavo do sinal.

  O arquivo de e-commerce e SUBCONJUNTO deste (64.331 dos 64.433 pedidos dele
  estao aqui), entao ele saiu do caminho de demanda - somar os dois contaria a
  mesma venda duas vezes.

  ---------------------------------------------------------------------------
  A CHAVE

  (IDORCAMENTO, NUMSEQUENCIA) nao serve: 790.364 linhas para 720.882 pares. A
  mesma linha de credito aparece repetida em datas diferentes. A chave usa
  empresa, pedido, sequencia, produto e data; o que ainda repetir depois disso
  e duplicidade de verdade, e a tela de qualidade dos dados a mostra.

  ---------------------------------------------------------------------------
  O LUCRO

  O campo mais importante - o lucro da linha - NAO vem pronto. O ERP traz
  PERCMARGEMCONTRIBUICAO, negativo em 68% das linhas (mediana -7,5%) mesmo em
  produto vendido com folga sobre o custo: o aquecedor Rinnai 21L sai a
  R$ 2.623 custando R$ 1.700 (+35% de margem) e o campo diz -9,7%. Aquele
  percentual carrega custo alocado, e nao serve para decidir compra.

  O motor precisa de outra pergunta: se esta peca vender, quanto dinheiro entra
  a mais? Isso e venda liquida menos o custo medio do dia da venda.
#}

{% if var('base', 'sintetica') == 'real' %}

with custo_dia as (
    -- o custo que estava nos livros no dia da venda, nao o de hoje
    select cast(idsubproduto as varchar) as sku,
           cast(dtmovimento as date)     as data,
           cast(valcustomedio as double) as custo
    from {{ source('raw', 'raw_estoque_diario_erp') }}
    where valcustomedio > 0
),

custo_medio as (
    -- reserva para as linhas cujo dia nao tem custo lancado
    select sku, median(custo) as custo
    from custo_dia group by 1
),

compra as (
    -- a mesma contraprova de stg_catalogo: custo do ERP abaixo de um terco do
    -- preco pago ao fornecedor e cadastro simbolico, e o lucro da venda sairia
    -- igual ao preco inteiro. Sem isto, corrigir o custo so no cadastro
    -- deixaria o lucro por peca (que vem daqui) inflado do mesmo jeito.
    select cast(idsubproduto as varchar) as sku,
           median(cast(valunitario as double)) as preco_compra
    from {{ source('raw', 'raw_compras') }}
    where cast(valunitario as double) > 0
    group by 1
),

v as (
    select
        -- empresa + pedido + sequencia + produto + data. Menos que isso
        -- repete: a mesma linha de credito aparece em varias datas.
        -- IDORCAMENTO fica como texto: no DW a venda direta ('VD...') e a
        -- Revest ('R...') nao sao numericas.
        cast(IDEMPRESA as varchar) || '|' ||
            coalesce(cast(IDORCAMENTO as varchar), 's') || '|' ||
            cast(NUMSEQUENCIA as varchar) || '|' ||
            cast(IDSUBPRODUTO as varchar) || '|' ||
            cast(cast(DATA as date) as varchar)      as chave,
        coalesce(cast(IDORCAMENTO as varchar),
                 '(sem pedido)')                     as pedido,
        cast(DATAHORA as timestamp)           as data_hora_venda,
        cast(DATA as date)                    as data,
        cast(IDSUBPRODUTO as varchar)         as sku,
        cast(QTDPRODUTO as double)            as pecas_vendidas,
        cast(VALOR_VENDA as double)           as receita_bruta,
        cast(VALORLIQUIDOVENDA as double)     as receita_liquida,
        cast(VALFRETE_VENDA as double)        as valor_do_frete,
        cast(VALOR_DEV as double)             as valor_dev,
        cast(VALOR_CAN as double)             as valor_can,
        cast(MARCA as varchar)                as canal_marca,
        -- a loja que vendeu. Com uma fonte de um canal so isto era a
        -- constante 'E-commerce'; agora e a dimensao mais util da base.
        cast(IDEMPRESA as varchar)            as loja_id,
        cast(NOMEFANTASIA as varchar)         as loja,
        -- de que posicao de estoque a peca saiu. O campo morre em 2025-07:
        -- preenchido antes, 100% vazio depois. Nao serve para filtrar o
        -- periodo inteiro, e por isso a demanda nao o usa - fica exposto para
        -- a tela de qualidade poder mostrar isso.
        cast(LOCALRETESTOQUE as varchar)      as local_estoque,
        -- NORMAL, ENCOMENDA, IMEDIATO, AGUARDANDO, Venda Direta. ENCOMENDA e
        -- venda que a loja fecha sem a peca na prateleira e so entao compra
        -- para entregar: nao passa pelo estoque do CD e por isso nao pode
        -- dimensionar a prateleira - sai do sinal de demanda no `where`
        -- abaixo. Os demais tipos ficam (medido: NORMAL sozinho explica 85,8%
        -- da baixa de disponivel, igual ao total).
        cast(TIPOENTREGA as varchar)          as tipo_entrega,
        cast(MOTIVODEVCAN as varchar)         as motivo_dev_can,
        cast(DESCRDEPARTAMENTO as varchar)    as departamento,
        cast(DESCRCIDADE as varchar)          as cidade,
        cast(UF as varchar)                   as uf,
        cast(IDCLIFOR as varchar)             as cliente_id,
        cast(NOMEVENDEDOR as varchar)         as vendedor
    from {{ source('raw', 'raw_vendas_todas') }}
),

vc as (
    -- o custo da linha: o do dia, senao a mediana do item, trocado pelo preco
    -- pago quando o do ERP e simbolico (ver `compra`)
    select v.*,
           case when coalesce(cd.custo, cm.custo) > 0 and pc.preco_compra > 0
                     and coalesce(cd.custo, cm.custo) < pc.preco_compra / 3
                then pc.preco_compra
                else coalesce(cd.custo, cm.custo, 0.0) end as custo_ok
    from v
    left join custo_dia   cd on cd.sku = v.sku and cd.data = v.data
    left join custo_medio cm on cm.sku = v.sku
    left join compra      pc on pc.sku = v.sku
)

select
    row_number() over (order by v.data, v.chave)      as id_linha,
    v.pedido,
    v.data_hora_venda,
    v.data,
    v.sku,
    v.pecas_vendidas,
    case when v.pecas_vendidas <> 0
         then v.receita_liquida / v.pecas_vendidas else 0.0 end as valor_da_peca,
    case when v.pecas_vendidas <> 0
         then v.receita_bruta / v.pecas_vendidas else 0.0 end   as preco_tabela_unit,
    0.0                                               as desconto_pct,
    v.receita_bruta,
    v.receita_liquida,
    v.custo_ok                                        as custo_unitario,
    v.pecas_vendidas * v.custo_ok                     as cmv,
    v.valor_do_frete,
    0.0                                               as frete_cobrado_cliente,
    0.0                                               as impostos_sobre_venda,
    -- o lucro que interessa a decisao de compra: o que entra a mais se a peca
    -- vender. Devolucao e cancelamento ja estao descontados na venda liquida.
    v.receita_liquida - v.pecas_vendidas * v.custo_ok as lucro,
    v.receita_liquida                                 as total,
    coalesce(v.loja, 'Loja ' || v.loja_id)            as canal,
    v.loja_id,
    -- O canal que o MODELO separa: o e-commerce (empresa 33) contra as demais
    -- lojas somadas. Sao dinamicas de venda diferentes, e a mistura escondia
    -- isso. `canal`, logo acima, e o NOME da loja e continua existindo.
    case when v.loja_id = '33' then 'ecommerce' else 'lojas' end as canal_demanda,
    coalesce(v.loja, 'Loja ' || v.loja_id)            as loja,
    v.local_estoque,
    coalesce(v.tipo_entrega, 'NAO INFORMADO')         as tipo_entrega,
    v.motivo_dev_can,
    v.departamento,
    v.cidade,
    coalesce(v.tipo_entrega, 'NAO INFORMADO')         as tipo_cliente,
    v.cliente_id,
    v.uf,
    v.vendedor                                        as regiao,
    coalesce(v.tipo_entrega, 'NORMAL')                as modalidade_frete,
    0.0                                               as peso_total_kg
from vc v
-- a linha de devolucao vem com quantidade negativa (22.960 linhas, -72.020
-- pecas no extrato completo, com MOTIVODEVCAN preenchido). Ela pertence ao
-- financeiro, nao ao sinal de demanda: manter aqui inverteria o sinal do dia e
-- o modelo leria "o mercado devolveu" como "o mercado nao quis". A tela de
-- qualidade dos dados mostra quanto ficou de fora por esta regra.
-- A venda sob encomenda (TIPOENTREGA = 'ENCOMENDA') tambem sai: e comprada
-- para o pedido, nao atendida da prateleira, e o modelo dimensiona a
-- prateleira. A tela de qualidade mostra o volume excluido.
where v.pecas_vendidas > 0
  and coalesce(upper(trim(v.tipo_entrega)), '') <> 'ENCOMENDA'

{% elif var('base', 'sintetica') == 'exports' %}

-- A exportacao nao tem linha de pedido: a venda vem agregada por SKU e dia.
-- Cada dia com venda vira UMA linha; `pedido` e sintetico e por isso a
-- contagem de pedidos nos marts e na verdade a contagem de dias com venda.
-- O lucro e venda liquida menos custo medio x pecas, como no extrato real.
with d as (
    select cast(sku as varchar)                          as sku,
           cast(data as date)                            as data,
           cast(qtd_vendida as double)                   as pecas,
           cast(qtd_venda_liquida as double)             as pecas_liq,
           cast(valor_venda_liquido as double)           as receita,
           cast(n_pedidos as integer)                    as n_pedidos
    from {{ source('raw', 'raw_diario_sku') }}
    where cast(qtd_vendida as double) > 0
),
c as (
    select cast(sku as varchar) as sku,
           coalesce(case when cast(custo_ultimo as double) < cast(custo_mediano as double) * 0.25
                           or cast(custo_ultimo as double) > cast(custo_mediano as double) * 4
                         then cast(custo_mediano as double) else cast(custo_ultimo as double) end, 0.0) as custo
    from {{ source('raw', 'raw_atributos_sku') }}
)
select
    row_number() over (order by d.data, d.sku)        as id_linha,
    d.sku || '@' || cast(d.data as varchar)           as pedido,
    cast(d.data as timestamp)                         as data_hora_venda,
    d.data,
    d.sku,
    cast(round(d.pecas) as integer)                   as pecas_vendidas,
    case when d.pecas_liq > 0 then d.receita / d.pecas_liq else 0.0 end as valor_da_peca,
    case when d.pecas_liq > 0 then d.receita / d.pecas_liq else 0.0 end as preco_tabela_unit,
    0.0                                               as desconto_pct,
    d.receita                                         as receita_bruta,
    d.receita                                         as receita_liquida,
    c.custo                                           as custo_unitario,
    d.pecas * c.custo                                 as cmv,
    0.0                                               as valor_do_frete,
    0.0                                               as frete_cobrado_cliente,
    0.0                                               as impostos_sobre_venda,
    d.receita - d.pecas * c.custo                     as lucro,
    d.receita                                         as total,
    'E-commerce'                                      as canal,
    -- a tela de acompanhamento separa e-commerce (33) das lojas por loja_id
    '33'                                              as loja_id,
    'ecommerce'                                       as canal_demanda,
    'E-commerce'                                      as tipo_cliente,
    cast(null as varchar)                             as cliente_id,
    cast(null as varchar)                             as uf,
    cast(null as varchar)                             as regiao,
    'NORMAL'                                          as modalidade_frete,
    0.0                                               as peso_total_kg
from d
left join c on c.sku = d.sku

{% else %}

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
    cast(null as varchar)                 as loja_id,
    'lojas'                               as canal_demanda,
    cast(tipo_cliente as varchar)         as tipo_cliente,
    cast(cliente_id as varchar)           as cliente_id,
    cast(uf as varchar)                   as uf,
    cast(regiao as varchar)               as regiao,
    cast(modalidade_frete as varchar)     as modalidade_frete,
    cast(peso_total_kg as double)         as peso_total_kg
from {{ source('raw', 'raw_vendas') }}

{% endif %}
