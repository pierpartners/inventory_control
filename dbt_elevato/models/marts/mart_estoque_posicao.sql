{#
  Foto do estoque de HOJE por item, para o diagnostico: quanto ha no CD e na
  rede, a que custo (modelo x contabil), ha quanto tempo esta parado.

  IDADE FIFO. O ERP nao guarda lote. A aproximacao: as pecas que estao na
  prateleira sao as das ULTIMAS entradas - percorre-se as entradas da mais
  recente para tras ate cobrir o saldo. A idade e a media das idades das
  entradas usadas, ponderada pela quantidade que cada uma contribui (a ultima
  entra parcialmente). Quando as entradas registradas nao cobrem o saldo
  (item que ja estava em estoque antes da janela do extrato), a fracao
  descoberta recebe a idade da entrada mais antiga disponivel e
  `entradas_cobrem_saldo` denuncia. E aproximacao: separa "parado ha 3 meses"
  de "parado ha 2 anos", que e o que o diagnostico precisa.
#}

with pos as (
    select max(data) as data_posicao from {{ ref('stg_estoque_diario') }}
),

saldo as (
    select e.sku, e.saldo_final as saldo_cd
    from {{ ref('stg_estoque_diario') }} e
    cross join pos
    where e.data = pos.data_posicao
),

custo as (
    select sku, custo as custo_medio_erp
    from (
        select c.sku, c.custo,
               row_number() over (partition by c.sku order by c.data desc) as rn
        from {{ ref('stg_custo_medio_erp') }} c
        cross join pos
        where c.data <= pos.data_posicao
    ) where rn = 1
),

lojas as (
    select sku,
           sum(saldo)                                         as saldo_lojas,
           count(distinct empresa * 100000 + local)           as lojas_com_saldo,
           sum(saldo * coalesce(custo_medio, 0))              as valor_lojas_erp
    from {{ ref('stg_estoque_posicao_lojas') }}
    where saldo > 0
    group by sku
),

venda as (
    select v.sku, max(v.data) as ultima_venda
    from {{ ref('stg_vendas') }} v
    cross join pos
    where v.pecas_vendidas > 0 and v.data <= pos.data_posicao
    group by v.sku
),

entradas as (
    select e.sku, e.data, e.pecas
    from {{ ref('stg_entradas') }} e
    cross join pos
    where e.data <= pos.data_posicao
),

ult_entrada as (
    select sku, max(data) as ultima_entrada, min(data) as primeira_entrada,
           sum(pecas) as pecas_entradas
    from entradas
    group by sku
),

-- FIFO: acumula da entrada mais recente para tras
acum as (
    select e.sku, e.data, e.pecas,
           sum(e.pecas) over (partition by e.sku order by e.data desc, e.pecas
                              rows between unbounded preceding and current row) as acumulado
    from entradas e
),

consumo as (
    select a.sku, a.data, a.pecas,
           least(a.pecas, greatest(s.saldo_cd - (a.acumulado - a.pecas), 0)) as consumido
    from acum a
    join saldo s on s.sku = a.sku
    where s.saldo_cd > 0
      and a.acumulado - a.pecas < s.saldo_cd
),

fifo as (
    select c.sku,
           sum(c.consumido)                                                    as coberto,
           sum(c.consumido * datediff('day', c.data, pos.data_posicao))        as soma_idade,
           min(c.data)                                                         as entrada_mais_antiga_em_estoque
    from consumo c
    cross join pos
    group by c.sku
)

select
    c.sku,
    c.fornecedor,
    c.comprador,
    pos.data_posicao,
    coalesce(s.saldo_cd, 0)                     as saldo_cd,
    cu.custo_medio_erp,
    coalesce(l.saldo_lojas, 0)                  as saldo_lojas,
    coalesce(l.lojas_com_saldo, 0)              as lojas_com_saldo,
    coalesce(l.valor_lojas_erp, 0)              as valor_lojas_erp,
    v.ultima_venda,
    ue.ultima_entrada,
    case
        when coalesce(s.saldo_cd, 0) <= 0 or f.sku is null then null
        when f.coberto >= s.saldo_cd - 1e-9 then f.soma_idade / f.coberto
        -- fracao descoberta herda a idade da entrada mais antiga disponivel
        else (f.soma_idade
              + (s.saldo_cd - f.coberto) * datediff('day', ue.primeira_entrada, pos.data_posicao))
             / s.saldo_cd
    end                                         as idade_fifo_dias,
    f.entrada_mais_antiga_em_estoque,
    case when coalesce(s.saldo_cd, 0) <= 0 then true
         when f.sku is null then false
         else f.coberto >= s.saldo_cd - 1e-9 end as entradas_cobrem_saldo
from {{ ref('stg_catalogo') }} c
cross join pos
left join saldo s        on s.sku = c.sku
left join custo cu       on cu.sku = c.sku
left join lojas l        on l.sku = c.sku
left join venda v        on v.sku = c.sku
left join ult_entrada ue on ue.sku = c.sku
left join fifo f         on f.sku = c.sku
