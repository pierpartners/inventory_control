# -*- coding: utf-8 -*-
"""
Extrai do DW da Elevato (Postgres `dwanalitico`) as seis tabelas que a base
`real` do staging espera, e grava em Parquet em data/fonte_dw/.

    python scripts/extrair_dw.py                 # 3 anos ate hoje
    python scripts/extrair_dw.py --anos 1        # janela menor
    python scripts/extrair_dw.py --ate 2026-06-30
    python scripts/extrair_dw.py --so raw_compras raw_ciclo_pagamento

Depois: python scripts/rodar_pipeline.py  (detecta a pasta e usa base=dw)

O universo e TUDO que o CD Gravatai (empresa 26, local 124) estocou na janela:
todo SKU com linha em db2.estoque_sintetico nesse local. A demanda e a de
TODAS as lojas (gold.vendas, todas as empresas), porque e esse CD que abastece
o grupo inteiro. Ate 2026-09-17 o universo era so o que o e-commerce (empresa
33) vendeu nos ultimos 12 meses - 3.230 SKUs contra 19.139 agora; a venda do
e-commerce ainda e extraida a parte (raw_vendas_ecommerce) para a tela de
qualidade e para a separacao da demanda por canal.

Cada consulta esta documentada em docs/estudo_dados_faltantes_dw.md, com os
numeros medidos e as armadilhas de cada fonte.

Credenciais: .env na raiz (DWANALITICO_HOST/PORT/DBNAME, DW_USER, DW_PASSWORD),
as mesmas de scripts/consultar_dw.py. Sem .env o script para na primeira linha.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from backend.warehouse import carregar_env  # noqa: E402

DESTINO = RAIZ / "data" / "fonte_dw"

# CD Gravatai: o unico estoque fisico. O local 185 da empresa 33 e transito
# fiscal do e-commerce (entra e sai no mesmo dia) e fica fora.
EMPRESA_CD, LOCAL_CD = 26, 124
EMPRESA_ECOMMERCE = 33
LOTE_LINHAS = 200_000


# ------------------------------------------------------------------ SQL
# Todas as consultas recebem %(dt_ini)s, %(dt_fim)s (a janela desta consulta, que
# na grade de estoque e UM ANO por vez) e %(dt_universo)s/%(dt_universo_fim)s (a
# janela do universo, sempre a global - senao o chunk de 2023 so acharia os SKUs
# com movimento em 2023).

UNIVERSO = f"""
universo as (
    select distinct e.idsubproduto
    from db2.estoque_sintetico e
    where e.idempresa = {EMPRESA_CD} and e.idlocalestoque = {LOCAL_CD}
      and e.dtmovimento between %(dt_universo)s and %(dt_universo_fim)s
)
"""

# Cadastro. gold.produtos_compras ja traz a hierarquia (secao/grupo) que o
# db2.produto so tem por id; a marca vem de db2.marca.
SQL_PRODUTOS = f"""
with {UNIVERSO}
select
    pg.idsubproduto                                        as "IDSUBPRODUTO",
    pg.idproduto                                           as "IDPRODUTO",
    coalesce(pg.descrresproduto, p.descrcomproduto)        as "DESCRCOMPRODUTO",
    pc."DESCRSECAO"                                        as "DESCRSECAO",
    pc."DESCRGRUPO"                                        as "DESCRGRUPO",
    pc."DESCRSUBGRUPO"                                     as "DESCRSUBGRUPO",
    pc."DESCRDIVISAO"                                      as "DESCRDIVISAO",
    pc."UNMEDIDA"                                          as "UNMEDIDA",
    p.fabricante                                           as "FABRICANTE",
    m.descricao                                            as "MARCA",
    pc."IDCLIFOR"                                          as "IDCLIFOR_FORNECEDOR",
    pc."COMPRADOROFICIAL"                                  as "COMPRADOROFICIAL"
from universo u
join db2.produto_grade pg on pg.idsubproduto = u.idsubproduto
join db2.produto p        on p.idproduto = pg.idproduto
left join db2.marca m     on m.idmarcafabricante = p.idmarcafabricante
left join gold.produtos_compras pc on pc."IDSUBPRODUTO" = cast(pg.idsubproduto as text)
"""

# Venda linha a linha, todas as lojas. gold.vendas ja traz devolucao e
# cancelamento como linhas negativas (cte = devolucao_*/cancelamentos); o
# staging descarta quantidade <= 0 do sinal de demanda, e VALOR_DEV/VALOR_CAN
# ficam para a tela de qualidade. IDORCAMENTO e texto: venda direta ('VD...')
# e Revest ('R...') nao sao numericos.
SQL_VENDAS = f"""
with {UNIVERSO}
select
    v.cte                                  as "CTE",
    v.idempresa                            as "IDEMPRESA",
    v.nomefantasia                         as "NOMEFANTASIA",
    v.idorcamento                          as "IDORCAMENTO",
    v.numsequencia                         as "NUMSEQUENCIA",
    cast(v.idsubproduto as integer)        as "IDSUBPRODUTO",
    pg_desc.descricao                      as "DESCRICAOPRODUTO",
    v.data                                 as "DATA",
    v.datahora                             as "DATAHORA",
    v.qtdproduto                           as "QTDPRODUTO",
    v.valor_venda                          as "VALOR_VENDA",
    v.valorliquidovenda                    as "VALORLIQUIDOVENDA",
    v.valfrete_venda                       as "VALFRETE_VENDA",
    case when v.cte like 'devolucao%%' then -v.valorliquidovenda end as "VALOR_DEV",
    case when v.cte = 'cancelamentos'  then -v.valorliquidovenda end as "VALOR_CAN",
    v.tipo                                 as "MARCA",
    cast(null as text)                     as "LOCALRETESTOQUE",
    v.tipoentrega                          as "TIPOENTREGA",
    v.motivo_devcan                        as "MOTIVODEVCAN",
    v.descrdepartamento                    as "DESCRDEPARTAMENTO",
    cast(null as text)                     as "DESCRCIDADE",
    v.uf                                   as "UF",
    v.idclifor                             as "IDCLIFOR",
    v.nomevendedor                         as "NOMEVENDEDOR",
    v.percmargemcontribuicao               as "PERCMARGEMCONTRIBUICAO",
    v.valormargem                          as "VALORMARGEM"
from gold.vendas v
join universo u on v.idsubproduto ~ '^[0-9]+$'
                and u.idsubproduto = cast(v.idsubproduto as integer)
left join lateral (
    select coalesce(pg.descrresproduto, p.descrcomproduto) as descricao
    from db2.produto_grade pg join db2.produto p on p.idproduto = pg.idproduto
    where pg.idsubproduto = u.idsubproduto limit 1
) pg_desc on true
where v.data between %(dt_ini)s and %(dt_fim)s
  {{filtro_empresa}}
order by v.data, v.idempresa, v.idorcamento, v.numsequencia
"""

# Grade diaria do CD, densificada. db2.estoque_sintetico so tem linha em dia
# COM movimento; o modelo precisa de todos os dias (os de venda zero carregam
# informacao). O saldo e reconstruido por soma acumulada de entradas - saidas a
# partir do ultimo saldo anterior a janela (identidade conferida na origem em
# 100%% das linhas). Custo medio e reserva sao "o ultimo valor conhecido".
SQL_ESTOQUE = f"""
with {UNIVERSO},
dias as (
    select generate_series(%(dt_ini)s::date, %(dt_fim)s::date, interval '1 day')::date as dt
),
est as (
    select e.idproduto, e.idsubproduto, e.dtmovimento,
           e.qtdentraestoque, e.qtdsaidaestoque, e.qtdajustebalanco, e.qtdatualestoque,
           e.valcustomedio, e.qtdcompra, e.qtdvenda, e.qtdentradatransfer, e.qtdsaidatransfer,
           e.qtdsaldoinicial - coalesce(lag(e.qtdatualestoque) over (
               partition by e.idproduto, e.idsubproduto order by e.dtmovimento), 0) as delta_encadeamento
    from db2.estoque_sintetico e
    join universo u on u.idsubproduto = e.idsubproduto
    where e.idempresa = {EMPRESA_CD} and e.idlocalestoque = {LOCAL_CD}
    -- sem filtro de data de proposito: `prod` precisa ver o SKU mesmo que a
    -- primeira posicao dele no CD seja posterior a este chunk. Assim a grade
    -- de cada SKU cobre a janela inteira (saldo zero antes da primeira
    -- entrada), e todo item tem o mesmo numero de dias de historico.
),
abertura as (
    select distinct on (idsubproduto) idsubproduto, idproduto,
           qtdatualestoque as saldo_abertura, valcustomedio as custo_abertura
    from est where dtmovimento < %(dt_ini)s
    order by idsubproduto, dtmovimento desc
),
mov as (
    select idproduto, idsubproduto, dtmovimento as dt,
           sum(qtdentraestoque + greatest(qtdajustebalanco, 0) + greatest(delta_encadeamento, 0))  as entradas,
           sum(qtdsaidaestoque + greatest(-qtdajustebalanco, 0) + greatest(-delta_encadeamento, 0)) as saidas,
           sum(qtdcompra) as qtdcompra, sum(qtdvenda) as qtdvenda,
           sum(qtdentradatransfer) as qtdentradatransfer, sum(qtdsaidatransfer) as qtdsaidatransfer,
           sum(qtdajustebalanco) as qtdajustebalanco,
           max(qtdatualestoque) as saldo_erp,
           max(case when valcustomedio > 0 then valcustomedio end) as custo_dia
    from est where dtmovimento between %(dt_ini)s and %(dt_fim)s
    group by 1, 2, 3
),
prod as (
    -- o universo planejado: SKUs que em algum momento tiveram posicao no CD.
    -- Quem vende sem nunca ter estoque proprio e venda sob encomenda.
    select idsubproduto, min(idproduto) as idproduto from est group by 1
),
reserva as (
    -- retrato de reserva por dia; tambem so existe em dia com mudanca
    select r.idsubproduto, r.dtmovimento as dt, sum(r.qtdreserva) as qtdreserva
    from db2.reserva_sintetico r
    join universo u on u.idsubproduto = r.idsubproduto
    where r.idempresa = {EMPRESA_CD} and r.idlocalestoque = {LOCAL_CD}
      and r.dtmovimento between %(dt_ini)s and %(dt_fim)s
    group by 1, 2
),
grade as (
    select u.idsubproduto, d.dt,
           p.idproduto,
           coalesce(a.saldo_abertura, 0) as saldo_abertura,
           a.custo_abertura,
           m.entradas, m.saidas, m.qtdcompra, m.qtdvenda, m.qtdentradatransfer, m.qtdsaidatransfer,
           m.qtdajustebalanco, m.saldo_erp, m.custo_dia,
           r.qtdreserva,
           -- grupos de preenchimento: cada valor conhecido abre um grupo e os
           -- dias seguintes sem valor herdam o dele
           count(m.custo_dia)  over (partition by u.idsubproduto order by d.dt) as g_custo,
           count(r.qtdreserva) over (partition by u.idsubproduto order by d.dt) as g_res
    from universo u
    join prod p on p.idsubproduto = u.idsubproduto
    cross join dias d
    left join abertura a on a.idsubproduto = u.idsubproduto
    left join mov m on m.idsubproduto = u.idsubproduto and m.dt = d.dt
    left join reserva r on r.idsubproduto = u.idsubproduto and r.dt = d.dt
),
serie as (
    select g.*,
           saldo_abertura + coalesce(sum(coalesce(entradas, 0) - coalesce(saidas, 0)) over (
               partition by idsubproduto order by dt rows between unbounded preceding and 1 preceding), 0) as saldo_inicial,
           saldo_abertura + sum(coalesce(entradas, 0) - coalesce(saidas, 0)) over (
               partition by idsubproduto order by dt rows between unbounded preceding and current row) as saldo_final,
           coalesce(max(custo_dia) over (partition by idsubproduto, g_custo), custo_abertura) as custo_ff,
           coalesce(max(qtdreserva) over (partition by idsubproduto, g_res), 0) as reserva_ff
    from grade g
)
select
    {EMPRESA_CD}                         as idempresa,
    {LOCAL_CD}                           as idlocalestoque,
    idproduto,
    idsubproduto,
    dt                                   as dtmovimento,
    saldo_inicial                        as qtdsaldoinicial,
    coalesce(entradas, 0)                as qtdentraestoque,
    coalesce(saidas, 0)                  as qtdsaidaestoque,
    coalesce(qtdajustebalanco, 0)        as qtdajustebalanco,
    saldo_final                          as qtdatualestoque,
    reserva_ff                           as qtdreserva,
    saldo_final - reserva_ff             as qtddisponivel,
    coalesce(custo_ff, 0)                as valcustomedio,
    coalesce(qtdcompra, 0)               as qtdcompra,
    coalesce(qtdvenda, 0)                as qtdvenda,
    coalesce(qtdentradatransfer, 0)      as qtdentradatransfer,
    coalesce(qtdsaidatransfer, 0)        as qtdsaidatransfer,
    saldo_erp,
    case when saldo_erp is not null then 1 else 0 end as flag_movimento
from serie
order by idsubproduto, dt
"""

# Pedidos de compra. silver.compras repete a linha do item uma vez por parcela
# de pagamento (numsequencia varia): sem o distinct, somar qtdatendida
# multiplica pelo numero de parcelas.
SQL_COMPRAS = f"""
with {UNIVERSO}
select distinct on (c.idempresa, c.idpedido, c.idsubproduto, c.qtdsolicitada, c.valunitario)
    c.idempresa, c.idclifor, c.nome as fornecedor, c.idpedido,
    c.dtmovimento, c.datacadastro, c.diasprevisaoentrega, c.previsaoentrega,
    c.idproduto, c.idsubproduto,
    c.qtdsolicitada, c.qtdatendida, c.valunitario, c.valtotbruto, c.valtotliquido,
    c.perfrete, c.descrformapagamento, c.compradorpedido, c.compradoroficial
from silver.compras c
join universo u on u.idsubproduto = c.idsubproduto
where c.dtmovimento between %(dt_ini)s and %(dt_fim)s
order by c.idempresa, c.idpedido, c.idsubproduto, c.qtdsolicitada, c.valunitario, c.numsequencia
"""

# Pedido -> entrada fisica no CD. silver.compras nao tem data de chegada; ela
# so existe no livro de estoque: db2.estoque_analitico com idoperacao = 1
# ("Compra de Mercadoria") traz numpedido em 41.312 de 41.315 linhas do ultimo
# ano. dias_entrega_realizado = entrada - pedido; mediana 12 dias contra 30
# combinados (40 mil pares, 2025-09 a 2026-09).
# Prazo ao fornecedor: db2.contas_pagar liga-se a entrada pela mesma
# idplanilha (16.131 de 16.134 entradas do CD nos ultimos 12 meses tem
# titulo). prazo_titulo_dias = quantos dias depois da ENTRADA o dinheiro sai,
# media dos vencimentos das parcelas ponderada pelo valor - mediana 25 dias na
# primeira parcela, 34 na ultima, 1,4 parcelas por nota.
SQL_CICLO = f"""
with {UNIVERSO},
titulos as (
    select idplanilha,
           sum((dtvencimento - date '1970-01-01') * valtitulo) filter (where valtitulo > 0) as soma_venc,
           sum(valtitulo) filter (where valtitulo > 0)                                    as soma_val,
           min(dtvencimento - date '1970-01-01')                                          as venc_min
    from db2.contas_pagar
    where idempresa = {EMPRESA_CD} and dtvencimento is not null
    group by 1
),
entradas as (
    select ea.idsubproduto, cast(ea.numpedido as integer) as idpedido, ea.idplanilha,
           ea.dtmovimento as dt_entrada_estoque,
           sum(ea.qtdproduto) as qtdatendida, sum(ea.valtotliquido) as valtitulo
    from db2.estoque_analitico ea
    join universo u on u.idsubproduto = ea.idsubproduto
    where ea.idempresa = {EMPRESA_CD} and ea.idlocalestoque = {LOCAL_CD}
      and ea.idoperacao = 1 and ea.numpedido > 0 and ea.qtdproduto > 0
      and ea.dtmovimento between %(dt_ini)s and %(dt_fim)s
    group by 1, 2, 3, 4
)
select
    e.idpedido, e.idsubproduto,
    n.numnota,
    pc.idclifor,
    pc.dtmovimento                                    as dt_pedido,
    e.dt_entrada_estoque,
    e.qtdatendida,
    e.valtitulo,
    pc.diasentrega                                    as dias_entrega_combinado,
    (e.dt_entrada_estoque - pc.dtmovimento)           as dias_entrega_realizado,
    cast(round(coalesce(t.soma_venc / nullif(t.soma_val, 0), t.venc_min)
               - (e.dt_entrada_estoque::date - date '1970-01-01')) as integer)
                                                      as prazo_titulo_dias
from entradas e
join db2.pedido_compra pc on pc.idpedido = e.idpedido and pc.idempresa = {EMPRESA_CD}
left join db2.notas n on n.idplanilha = e.idplanilha and n.idempresa = {EMPRESA_CD}
left join titulos t on t.idplanilha = e.idplanilha
order by e.dt_entrada_estoque, e.idpedido
"""

# Foto da posicao ATUAL nas lojas, um registro por (empresa, local, SKU).
# Nao e grade diaria de proposito: as lojas somam ~R$ 7,5 mi contra R$ 33 mi
# do CD (medido 2026-09-18), pulverizados em 169 locais - o historico
# multiplicaria os 21 milhoes de linhas do estoque do CD por pouco ganho. O
# diagnostico so precisa saber QUANTO ha na rede hoje, para apontar
# transferencia em vez de compra e excesso parado em loja. A ultima linha de
# cada local nos ultimos 120 dias e a posicao; local sem movimento ha mais de
# 120 dias fica fora (saldo velho demais para valer como posicao).
SQL_POSICAO_LOJAS = f"""
with {UNIVERSO},
ult as (
    select distinct on (e.idempresa, e.idlocalestoque, e.idsubproduto)
           e.idempresa, e.idlocalestoque, e.idsubproduto,
           e.dtmovimento, e.qtdatualestoque, e.valcustomedio
    from db2.estoque_sintetico e
    join universo u on u.idsubproduto = e.idsubproduto
    where e.dtmovimento >= %(dt_fim)s::date - interval '120 days'
      and not (e.idempresa = {EMPRESA_CD} and e.idlocalestoque = {LOCAL_CD})
      and not (e.idempresa = {EMPRESA_ECOMMERCE} and e.idlocalestoque = 185)
    order by e.idempresa, e.idlocalestoque, e.idsubproduto, e.dtmovimento desc
)
select idempresa, idlocalestoque, idsubproduto,
       dtmovimento::date as dtmovimento,
       qtdatualestoque, valcustomedio
from ult
where qtdatualestoque <> 0
order by idempresa, idlocalestoque, idsubproduto
"""

TABELAS = {
    "raw_produtos":           ("simples", SQL_PRODUTOS),
    "raw_vendas_todas":       ("simples", SQL_VENDAS.replace("{filtro_empresa}", "")),
    "raw_vendas_ecommerce":   ("simples", SQL_VENDAS.replace(
                                   "{filtro_empresa}", f"and v.idempresa = {EMPRESA_ECOMMERCE}")),
    "raw_estoque_diario_erp": ("por_ano", SQL_ESTOQUE),
    "raw_compras":            ("simples", SQL_COMPRAS),
    "raw_ciclo_pagamento":    ("simples", SQL_CICLO),
    "raw_estoque_posicao_lojas": ("simples", SQL_POSICAO_LOJAS),
}


# ------------------------------------------------------------ execucao
def conectar():
    import psycopg
    carregar_env()
    host = os.environ.get("DWANALITICO_HOST")
    user = os.environ.get("DW_USER")
    if not host or not user:
        raise SystemExit("Faltam DWANALITICO_HOST / DW_USER / DW_PASSWORD no .env "
                         "(modelo em .env.example).")
    con = psycopg.connect(
        host=host, port=int(os.environ.get("DWANALITICO_PORT", "5432")),
        dbname=os.environ.get("DWANALITICO_DBNAME", "dwanalitico"),
        user=user, password=os.environ.get("DW_PASSWORD", ""),
        connect_timeout=15, options="-c statement_timeout=1800000",
    )
    con.read_only = True
    return con


def _tipar(df: pd.DataFrame) -> pd.DataFrame:
    """numeric do Postgres chega como Decimal; o DuckDB inferiria uma precisao
    pela primeira linha e estouraria nas seguintes. Vira float."""
    from decimal import Decimal
    for c in df.columns:
        if df[c].dtype == object:
            amostra = df[c].dropna()
            if len(amostra) and isinstance(amostra.iloc[0], Decimal):
                df[c] = df[c].astype(float)
    return df


def _gravar_parquet(df: pd.DataFrame, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    df = _tipar(df)
    con = duckdb.connect()
    con.register("_df", df)
    con.execute(f"copy _df to '{destino.as_posix()}' (format parquet, compression zstd)")
    con.close()


def extrair(con, sql: str, params: dict, pasta: Path, parte: str) -> int:
    """Roda a consulta em cursor de servidor e grava a resposta em lotes."""
    total, lote = 0, 0
    with con.cursor(name=f"cur_{parte}") as cur:
        cur.itersize = LOTE_LINHAS
        cur.execute(sql, params)
        cols = [d.name for d in cur.description]
        while True:
            linhas = cur.fetchmany(LOTE_LINHAS)
            if not linhas:
                break
            df = pd.DataFrame(linhas, columns=cols)
            _gravar_parquet(df, pasta / f"{parte}-{lote:03d}.parquet")
            total += len(df)
            lote += 1
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--anos", type=int, default=3, help="tamanho da janela (padrao 3)")
    ap.add_argument("--ate", type=str, default=None, help="ultimo dia (padrao: hoje)")
    ap.add_argument("--so", nargs="*", default=None, help="extrai so estas tabelas")
    ap.add_argument("--destino", type=str, default=str(DESTINO))
    args = ap.parse_args()

    dt_fim = date.fromisoformat(args.ate) if args.ate else date.today()
    dt_ini = dt_fim - timedelta(days=365 * args.anos - 1)
    dt_universo = dt_ini  # o universo e a janela inteira
    destino = Path(args.destino)
    alvo = args.so or list(TABELAS)
    desconhecidas = [t for t in alvo if t not in TABELAS]
    if desconhecidas:
        raise SystemExit(f"tabelas desconhecidas: {desconhecidas}")

    print(f"janela {dt_ini} -> {dt_fim} · universo: SKUs com movimento no CD {EMPRESA_CD}/{LOCAL_CD} desde {dt_universo}")
    print(f"destino {destino}")
    con = conectar()
    t_total = time.time()
    resumo = []
    for tabela in alvo:
        modo, sql = TABELAS[tabela]
        pasta = destino / tabela
        if pasta.exists():
            shutil.rmtree(pasta)
        t0 = time.time()
        n = 0
        if modo == "por_ano":
            # a grade densificada e grande (SKUs x dias); um ano por vez
            # limita a memoria do servidor e do cliente
            ano_ini = dt_ini
            while ano_ini <= dt_fim:
                ano_fim = min(date(ano_ini.year, 12, 31), dt_fim)
                params = {"dt_ini": ano_ini, "dt_fim": ano_fim,
                          "dt_universo": dt_universo, "dt_universo_fim": dt_fim}
                n += extrair(con, sql, params, pasta, f"{tabela}-{ano_ini.year}")
                print(f"    {tabela} {ano_ini.year}: acumulado {n:,} linhas ({time.time() - t0:.0f}s)")
                ano_ini = ano_fim + timedelta(days=1)
        else:
            params = {"dt_ini": dt_ini, "dt_fim": dt_fim,
                      "dt_universo": dt_universo, "dt_universo_fim": dt_fim}
            n = extrair(con, sql, params, pasta, tabela)
        dt = time.time() - t0
        print(f"  {tabela:26} {n:>10,} linhas  {dt:6.0f}s")
        resumo.append((tabela, n))
        if n == 0:
            raise SystemExit(f"{tabela} veio vazia - a extracao para aqui para nao apagar a base anterior")
    con.close()

    # um registro por tabela, preservado entre rodadas parciais (--so)
    reg = destino / "_extracao.txt"
    linhas = {}
    if reg.exists():
        for l in reg.read_text(encoding="utf-8").splitlines():
            if "\t" in l:
                linhas[l.split("\t")[0]] = l
    for t, n in resumo:
        linhas[t] = f"{t}\t{n}\t{date.today()}\t{dt_ini}->{dt_fim}\tuniverso: movimento no CD desde {dt_universo}"
    reg.write_text("tabela\tlinhas\textraido_em\tjanela\tuniverso\n"
                   + "\n".join(linhas[k] for k in sorted(linhas)) + "\n", encoding="utf-8")
    print(f"\nextracao concluida em {time.time() - t_total:.0f}s")


if __name__ == "__main__":
    main()
