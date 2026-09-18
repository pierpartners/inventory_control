{#
  Cadastro de itens, tipado e normalizado.

  Duas fontes possiveis, escolhidas pela var `base`:

    sintetica  o CSV de simulacao, que ja vinha no contrato do modelo
    real       o extrato do ERP da Elevato, que NAO tem catalogo pronto -
               cada campo e montado de uma tabela diferente

  No extrato real o cadastro (produtos.csv) traz descricao e hierarquia, mas
  nao traz custo, prazo nem lote: custo vem do estoque diario, prazo vem do
  ciclo de pagamento do fornecedor e lote vem do historico de compras. E o
  primeiro lugar do projeto onde o "cadastro" e uma juncao, e nao uma leitura.
#}

{% if var('base', 'sintetica') == 'real' %}

with universo as (
    -- o modelo so planeja o que a empresa estoca. Dos 162 mil itens do
    -- cadastro, 1.190 tem posicao diaria no local 124; os outros 1.572 que
    -- vendem sem estoque proprio sao venda sob encomenda, e nao ha estoque
    -- para dimensionar.
    select distinct cast(idsubproduto as varchar) as sku
    from {{ source('raw', 'raw_estoque_diario_erp') }}
),

custo as (
    -- O custo medio mais recente de um dia em que HAVIA ESTOQUE.
    --
    -- A condicao de estoque positivo nao e detalhe. Quando o saldo de um item
    -- vai a zero, o ERP zera o custo medio junto e passa a lancar R$ 1,00. A
    -- PRATELEIRA DECA QUADRATTA custou entre R$ 669 e R$ 1.043 por tres anos,
    -- zerou em 15/07/2026 e o custo virou R$ 1,00 - e como a versao anterior
    -- pegava simplesmente o ultimo valor, o modelo passou a acreditar que uma
    -- prateleira de R$ 1.016 custava um real e dava R$ 314 de lucro. A nota
    -- dela saiu 3,33 contra 0,065 do segundo colocado: 51 vezes maior, e o
    -- item foi para o topo da fila de compra por causa de um artefato.
    --
    -- Sao 6 itens de 1.189 com esse padrao, mas o efeito em cada um e total.
    -- A trava contra a mediana existe para o caso de o reset acontecer no
    -- unico dia com estoque que restou.
    select sku,
           case when custo_dia < mediana * 0.25 or custo_dia > mediana * 4
                then mediana else custo_dia end as custo_unitario,
           custo_dia   as custo_ultimo_dia_com_estoque,
           mediana     as custo_mediano
    from (
        select sku, mediana,
               first_value(c) over (partition by sku order by dia desc) as custo_dia,
               row_number()   over (partition by sku order by dia desc) as rn
        from (
            select cast(idsubproduto as varchar) as sku,
                   cast(dtmovimento as date)     as dia,
                   cast(valcustomedio as double) as c,
                   median(cast(valcustomedio as double))
                       over (partition by idsubproduto) as mediana
            from {{ source('raw', 'raw_estoque_diario_erp') }}
            where valcustomedio > 0
              and cast(qtdatualestoque as double) > 0
        )
    ) where rn = 1
),

prazo as (
    -- PRAZO REALIZADO, nao o combinado. No extrato os dois divergem muito:
    -- combinado 40,6 dias de media contra 17,9 realizados. Dimensionar
    -- estoque pelo combinado imobilizaria capital para uma espera que
    -- raramente acontece.
    --
    -- O desvio sai junto porque ele e grande: mediana 13,2 dias sobre uma
    -- mediana de prazo de 18, um coeficiente de variacao de 0,75. Prazo que
    -- varia tanto tem de entrar no estoque de seguranca, e e isso que
    -- `lead_time_desvio_dias` carrega para o motor.
    select sku,
           cast(round(mediana) as integer) as lead_time_dias,
           coalesce(desvio, 0.0)           as lead_time_desvio_dias,
           pedidos                         as lead_time_pedidos
    from (
        select cast(idsubproduto as varchar) as sku,
               median(cast(dias_entrega_realizado as double)) as mediana,
               stddev(cast(dias_entrega_realizado as double)) as desvio,
               count(*) as pedidos
        from {{ source('raw', 'raw_ciclo_pagamento') }}
        where dias_entrega_realizado is not null
          and cast(dias_entrega_realizado as double) between 0 and 365
        group by 1
    )
),

pagamento as (
    -- Quando o dinheiro SAI: dias da entrada no estoque ao vencimento do
    -- titulo ao fornecedor (media das parcelas ponderada pelo valor, ja
    -- calculada no extrator). Mediana por item; a contagem de notas diz se
    -- da para confiar nela (menos de 3 herda a mediana do catalogo, como o
    -- prazo de entrega). Entra no ciclo financeiro da nota: e o que abate
    -- dos dias em que o dinheiro fica preso.
    select cast(idsubproduto as varchar) as sku,
           median(cast(prazo_titulo_dias as double)) as prazo_pagamento_dias,
           count(*)                                  as prazo_pagamento_notas
    from {{ source('raw', 'raw_ciclo_pagamento') }}
    where prazo_titulo_dias is not null
      and cast(prazo_titulo_dias as double) between -365 and 365
    group by 1
),

compra as (
    -- O PRECO PAGO ao fornecedor, mediano por item. E a segunda leitura do
    -- custo, independente do estoque diario, e serve de contraprova: em 90%
    -- do catalogo o custo medio do ERP fica entre 0,77x e 1,36x deste preco.
    -- Quando o custo do ERP e menor que um terco do preco pago, nao e custo,
    -- e cadastro simbolico (R$ 0,005, R$ 0,02, R$ 1,00) - portas Rohden
    -- compradas a R$ 974 constavam a meio centavo, davam lucro igual ao preco
    -- inteiro e nota de 9.856, e iam para o topo da fila por artefato. Nesses
    -- o preco pago e o custo. A trava contra a mediana (acima) nao pega esse
    -- caso porque a mediana e igualmente simbolica. O pedido ainda nao
    -- atendido conta: o preco combinado com o fornecedor ja e evidencia.
    select cast(idsubproduto as varchar) as sku,
           median(cast(valunitario as double)) as preco_compra
    from {{ source('raw', 'raw_compras') }}
    where cast(valunitario as double) > 0
    group by 1
),

lote as (
    -- o menor pedido que o fornecedor de fato aceitou. Usar a mediana do
    -- pedido inflaria o lote minimo e obrigaria o modelo a comprar mais do
    -- que precisa.
    select cast(idsubproduto as varchar) as sku,
           greatest(cast(round(min(cast(qtdsolicitada as double))) as integer), 1)
               as lote_minimo_compra
    from {{ source('raw', 'raw_compras') }}
    where cast(qtdsolicitada as double) > 0
    group by 1
),

preco as (
    -- o extrato nao tem preco de tabela; o preco praticado mediano e a melhor
    -- referencia disponivel. Serve de rotulo, nao entra em decisao: o motor
    -- usa lucro por peca, que vem da venda liquida menos o custo.
    --
    -- De todas as lojas, e nao so do e-commerce: com uma fonte de um canal so,
    -- 1.572 dos 1.190 itens planejados ficavam sem preco e caiam no reserva
    -- de custo x 1,3.
    select cast(IDSUBPRODUTO as varchar) as sku,
           median(cast(VALORLIQUIDOVENDA as double)
                  / nullif(cast(QTDPRODUTO as double), 0)) as preco_tabela
    from {{ source('raw', 'raw_vendas_todas') }}
    where cast(QTDPRODUTO as double) > 0
    group by 1
),

cad as (
    select cast(IDSUBPRODUTO as varchar) as sku,
           cast(DESCRCOMPRODUTO as varchar) as item,
           cast(DESCRSECAO as varchar)      as familia,
           cast(UNMEDIDA as varchar)        as unidade,
           cast(FABRICANTE as varchar)      as origem
    from {{ source('raw', 'raw_produtos') }}
),

mediana_prazo as (
    select cast(round(median(lead_time_dias)) as integer) as m,
           median(lead_time_desvio_dias) as sd
    from prazo
),

mediana_pagamento as (
    -- so itens com 3 notas ou mais: a mediana de uma nota so nao e mediana
    select median(prazo_pagamento_dias) as m from pagamento where prazo_pagamento_notas >= 3
)

select
    u.sku,
    coalesce(cad.item, 'SKU ' || u.sku)          as item,
    coalesce(cad.familia, 'Sem classificacao')   as familia,
    coalesce(cad.unidade, 'UN')                  as unidade,
    coalesce(cad.origem, 'Nao informado')        as origem,
    -- item sem custo no estoque fica em zero de proposito: nunca teve peca no
    -- CD, o modelo nao dimensiona a prateleira dele (a coluna custo_origem
    -- diz quantos sao, e `compra` guarda o preco pago para quem quiser usar)
    -- Sem compra para corrigir, custo abaixo de 5% do preco de venda e a
    -- mesma pista de cadastro simbolico - e sem contraprova nao ha o que por
    -- no lugar. Vai a zero e o item sai da compra, como os sem custo: o
    -- modelo nao compra o que nao sabe precificar. custo_origem = 'simbolico'
    -- lista quais sao para o acerto no ERP ou o ajuste manual por item.
    case when custo.custo_unitario > 0 and compra.preco_compra > 0
              and custo.custo_unitario < compra.preco_compra / 3
         then compra.preco_compra
         when custo.custo_unitario > 0 and compra.preco_compra is null
              and preco.preco_tabela > 0
              and custo.custo_unitario < preco.preco_tabela * 0.05
         then 0.0
         else coalesce(custo.custo_unitario, 0.0) end as custo_unitario,
    -- as leituras cruas ficam a vista, para a conferencia por item poder
    -- mostrar de onde o custo veio e quando ele divergiu
    coalesce(custo.custo_ultimo_dia_com_estoque, 0.0) as custo_ultimo_lancado,
    coalesce(custo.custo_mediano, 0.0)           as custo_mediano,
    coalesce(compra.preco_compra, 0.0)           as custo_compra_mediano,
    case when custo.custo_unitario > 0 and compra.preco_compra > 0
              and custo.custo_unitario < compra.preco_compra / 3 then 'compra'
         when custo.custo_unitario > 0 and compra.preco_compra is null
              and preco.preco_tabela > 0
              and custo.custo_unitario < preco.preco_tabela * 0.05 then 'simbolico'
         when custo.custo_unitario > 0 then 'estoque'
         else 'sem custo' end                    as custo_origem,
    coalesce(preco.preco_tabela,
             custo.custo_unitario * 1.3, 0.0)    as preco_tabela,
    0.0                                          as peso_unit_kg,
    -- 65 dos 1.190 itens nao tem pedido de compra no periodo; recebem a
    -- mediana do catalogo, e `lead_time_pedidos` = 0 denuncia quais sao
    coalesce(prazo.lead_time_dias, mp.m)         as lead_time_dias,
    coalesce(prazo.lead_time_desvio_dias, mp.sd) as lead_time_desvio_dias,
    coalesce(prazo.lead_time_pedidos, 0)         as lead_time_pedidos,
    -- prazo ao fornecedor: o do item quando ha 3 notas ou mais, senao a
    -- mediana do catalogo; `prazo_pagamento_notas` denuncia qual foi
    case when pg.prazo_pagamento_notas >= 3 then pg.prazo_pagamento_dias
         else mpg.m end                           as prazo_pagamento_dias,
    coalesce(pg.prazo_pagamento_notas, 0)        as prazo_pagamento_notas,
    coalesce(lote.lote_minimo_compra, 1)         as lote_minimo_compra
from universo u
cross join mediana_prazo mp
cross join mediana_pagamento mpg
left join cad   on cad.sku   = u.sku
left join custo on custo.sku = u.sku
left join compra on compra.sku = u.sku
left join prazo on prazo.sku = u.sku
left join pagamento pg on pg.sku = u.sku
left join lote  on lote.sku  = u.sku
left join preco on preco.sku = u.sku

{% elif var('base', 'sintetica') == 'exports' %}

-- Exportacao do DW: o cadastro ja vem juntado em raw_atributos_sku. A mesma
-- trava do extrato real contra o custo "R$ 1,00" de item zerado se aplica.
with a as (
    select
        cast(sku as varchar)                        as sku,
        cast(item as varchar)                       as item,
        cast(secao as varchar)                      as familia,
        cast(unidade as varchar)                    as unidade,
        coalesce(cast(marca as varchar),
                 cast(fabricante as varchar))       as origem,
        cast(custo_ultimo as double)                as custo_ultimo,
        cast(custo_mediano as double)               as custo_mediano,
        cast(prazo_previsto_mediano as double)      as prazo,
        cast(lote_min as double)                    as lote,
        cast(n_pedidos as integer)                  as n_pedidos
    from {{ source('raw', 'raw_atributos_sku') }}
),

preco as (
    -- preco praticado mediano no e-commerce (o diario nao tem preco de tabela)
    select cast(sku as varchar) as sku,
           median(cast(valor_venda_liquido as double)
                  / nullif(cast(qtd_venda_liquida as double), 0)) as preco_tabela
    from {{ source('raw', 'raw_diario_sku') }}
    where cast(qtd_venda_liquida as double) > 0
    group by 1
),

mediana_prazo as (
    select cast(round(median(prazo)) as integer) as m from a where prazo is not null
)

select
    a.sku,
    coalesce(a.item, 'SKU ' || a.sku)                as item,
    coalesce(a.familia, 'Sem classificacao')         as familia,
    coalesce(a.unidade, 'UN')                        as unidade,
    coalesce(a.origem, 'Nao informado')              as origem,
    coalesce(case when a.custo_ultimo < a.custo_mediano * 0.25
                    or a.custo_ultimo > a.custo_mediano * 4
                  then a.custo_mediano else a.custo_ultimo end, 0.0) as custo_unitario,
    coalesce(a.custo_ultimo, 0.0)                    as custo_ultimo_lancado,
    coalesce(a.custo_mediano, 0.0)                   as custo_mediano,
    0.0                                              as custo_compra_mediano,
    case when a.custo_ultimo > 0 then 'estoque' else 'sem custo' end as custo_origem,
    coalesce(preco.preco_tabela, a.custo_ultimo * 1.3, 0.0) as preco_tabela,
    0.0                                              as peso_unit_kg,
    coalesce(cast(round(a.prazo) as integer), mp.m)  as lead_time_dias,
    0.0                                              as lead_time_desvio_dias,
    coalesce(a.n_pedidos, 0)                         as lead_time_pedidos,
    cast(null as double)                             as prazo_pagamento_dias,
    0                                                as prazo_pagamento_notas,
    greatest(coalesce(cast(round(a.lote) as integer), 1), 1) as lote_minimo_compra
from a
cross join mediana_prazo mp
left join preco on preco.sku = a.sku

{% else %}

select
    cast(sku as varchar)                as sku,
    cast(item as varchar)               as item,
    cast(familia as varchar)            as familia,
    cast(unidade as varchar)            as unidade,
    cast(origem as varchar)             as origem,
    cast(custo_unitario as double)      as custo_unitario,
    cast(custo_unitario as double)      as custo_ultimo_lancado,
    cast(custo_unitario as double)      as custo_mediano,
    0.0                                 as custo_compra_mediano,
    'cadastro'                          as custo_origem,
    cast(preco_tabela as double)        as preco_tabela,
    cast(peso_unit_kg as double)        as peso_unit_kg,
    cast(lead_time_dias as integer)     as lead_time_dias,
    -- a base sintetica trata prazo como fixo. Zero aqui faz a formula de
    -- variancia do horizonte no motor recair exatamente na antiga, o que
    -- mantem os resultados dessa base identicos ao que eram.
    0.0                                 as lead_time_desvio_dias,
    0                                   as lead_time_pedidos,
    -- sem ciclo de pagamento na base sintetica: o motor usa o parametro
    cast(null as double)                as prazo_pagamento_dias,
    0                                   as prazo_pagamento_notas,
    cast(lote_minimo_compra as integer) as lote_minimo_compra
from {{ source('raw', 'raw_catalogo') }}

{% endif %}
