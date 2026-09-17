# -*- coding: utf-8 -*-
"""
Backtest: o que a plataforma teria mandado comprar, e o que aconteceu depois.

Escolhe-se uma DATA EXATA e os PARAMETROS EXATOS daquele dia. O motor roda com
o passado cortado ali - sem nenhuma informacao posterior - e produz o plano de
compra. Depois o banco responde, produto por produto, no horizonte de cada um:

    mandaria comprar     quantas pecas o plano pediria naquele instante
    vendeu de verdade    quantas sairam no horizonte daquele item
    sobrou               pecas compradas que a demanda do horizonte nao pediu
    faltou               pecas que a demanda pediu e nao havia
    dinheiro parado      as pecas que sobraram, ao custo delas

Nao ha teto arbitrario. O caixa do ciclo e o valor que a empresa DE FATO gastou
em compra naquela janela, e o teto de capital e o valor do estoque naquele dia -
os dois lidos do proprio extrato. Comparar o plano contra um teto escolhido a
mao mediria a escolha do teto, nao o modelo.

Duas armadilhas ficam registradas porque foram medidas nesta base:

1. A venda observada depois do corte NAO e a demanda: em 28% dos dias-item a
   prateleira estava vazia. Onde faltou, a demanda e reconstruida pela taxa
   estimada com os dias bons DO PROPRIO horizonte, e nunca fica abaixo do que
   fisicamente saiu.
2. O horizonte de cada item e o dele: 14 a 239 dias nesta base. Item cujo
   horizonte nao cabe na janela restante fica FORA do relatorio, e a tela diz
   quantos ficaram.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Parametros
from .modelo import em_censurado, executar
from .warehouse import Warehouse, ref


def _registros(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> lista de dicts sem NaN nem infinito.

    JSON nao tem NaN, e o encoder do FastAPI aborta a resposta inteira quando
    encontra um - o que aqui apareceu como erro 500 opaco por causa de uma
    coluna (taxa_recente) nula em item sem venda recente.
    """
    limpo = df.replace([np.inf, -np.inf], np.nan)
    return limpo.astype(object).where(pd.notna(limpo), None).to_dict("records")

# sem janela minima nao ha o que comparar
MIN_DIAS_JANELA = 7


def _so(skus, coluna: str) -> str:
    """Fragmento `and coluna in (...)` para restringir o backtest a um recorte.

    Quando o teste roda sobre um subconjunto do catalogo, os tetos tambem tem
    de ser os do subconjunto: o caixa e o que a empresa gastou NAQUELES itens e
    o teto de capital e o estoque NAQUELES itens. Dar ao modelo o caixa do
    catalogo inteiro para gastar em meio catalogo inverteria a comparacao a
    favor dele.
    """
    if not skus:
        return ""
    dentro = ", ".join("'" + str(s).replace("'", "''") + "'" for s in skus)
    return f" and {coluna} in ({dentro})"

# os parametros que a tela do backtest deixa mexer, com rotulo e tipo
PARAMETROS_BACKTEST = [
    ("teto_compra_ciclo", "Caixa do ciclo", "R$", "float",
     "Quanto se pode gastar nesta compra. O padrao e o que a empresa de fato "
     "gastou na janela, lido do extrato de compras."),
    ("teto_capital", "Teto de capital em estoque", "R$", "float",
     "O padrao e o valor do estoque naquele dia, lido do extrato."),
    ("janela_estimacao_dias", "Janela de estimação", "dias", "int",
     "Quantos dias de histórico o modelo usa para estimar a demanda diária. É a "
     "memória do modelo: janela longa dilui crescimento recente, janela curta vira "
     "ruído. A economia da peça (custo e margem) segue vindo do histórico inteiro."),
    ("periodo_revisao_dias", "Período de revisão", "dias", "int",
     "De quantos em quantos dias alguém olha a lista. Soma ao prazo do "
     "fornecedor e forma o horizonte que esta compra tem de cobrir."),
    ("taxa_manutencao_ano", "Custo de manter", "% ao ano", "pct",
     "Quanto custa carregar uma peça por um ano, sobre o custo dela."),
    ("fator_perda_ruptura", "Perda na ruptura", "%", "pct",
     "Da margem, quanto se perde de fato quando a peça falta."),
    ("perda_encalhe", "Perda no encalhe", "%", "pct",
     "Do custo, quanto se perde quando a peça não vende."),
    ("limiar_giro_baixo", "Limiar de giro baixo", "peças no horizonte", "float",
     "Abaixo disso o item entra no regime de unidade marginal."),
    ("fator_desvio_horizonte", "Fator do desvio", "×", "float",
     "Corrige a variação da demanda no horizonte. 1,00 é a hipótese conservadora."),
]


# ======================================================================
# 1. O que o extrato diz sobre a data escolhida
# ======================================================================
def contexto_da_data(wh: Warehouse, corte: str, dias_janela: int,
                     skus: set[str] | None = None) -> dict:
    """O caixa e o estoque REAIS da data, que substituem os tetos escolhidos.

    `gasto_na_janela` sai do extrato de compras restrito aos itens que a
    plataforma planeja, na mesma janela de revisao que o modelo usaria. E esse
    o dinheiro que o plano tem direito de gastar - nem mais, nem menos.
    """
    ini = (pd.Timestamp(corte) - pd.Timedelta(days=dias_janela - 1)).date()
    # O que de fato ENTROU de mercadoria, reconstruido do proprio estoque
    # diario: a subida do saldo de um dia para o outro. Fecha com o estoque por
    # construcao.
    #
    # Nao uso `raw_compras` aqui, e a razao esta medida: aquela tabela salta de
    # 18 mil para 322 mil pecas por mes em marco de 2026 enquanto venda e
    # estoque seguem estaveis (13 a 21 mil pecas recebidas por mes). Se as
    # compras tivessem crescido 18x com venda parada, o estoque teria
    # explodido; ele nao explodiu. A tabela mudou de escopo, e usa-la como
    # "caixa da empresa" daria ao plano um teto 20x maior que o real.
    gasto = wh.query(f"""
        with e as (
            select d.sku, d.data, d.saldo_final,
                   lag(d.saldo_final) over (partition by d.sku order by d.data) ant,
                   c.custo_unitario
            from {ref('mart_estoque_diario')} d
            join {ref('stg_catalogo')} c on c.sku = d.sku),
        r as (
            select sku, data, greatest(saldo_final - ant, 0) entrada, custo_unitario
            from e where ant is not null)
        select coalesce(sum(entrada), 0) pecas,
               coalesce(sum(entrada * custo_unitario), 0) valor,
               count(distinct case when entrada > 0 then sku end) skus
        from r where data between DATE '{ini}' and DATE '{corte}'
              {_so(skus, 'sku')}""")
    estoque = wh.query(f"""
        select coalesce(sum(saldo_final * 1.0), 0) pecas
        from {ref('mart_estoque_diario')} where data = DATE '{corte}'
        {_so(skus, 'sku')}""")
    valor_est = wh.query(f"""
        select coalesce(sum(d.saldo_final * c.custo_unitario), 0) valor
        from {ref('mart_estoque_diario')} d
        join {ref('stg_catalogo')} c on c.sku = d.sku
        where d.data = DATE '{corte}' {_so(skus, 'd.sku')}""")
    # O PEDIDO colocado na janela - a decisao de compra que o modelo disputa.
    # E este valor que serve de teto de caixa, e nao o recebimento: o modelo
    # esta decidindo uma ordem de compra, entao o dinheiro dele tem de ser o
    # que a empresa poe em ordem de compra num ciclo. O recebimento reflete
    # ordens colocadas semanas antes.
    ped, _ = compras_do_ciclo(wh, corte, dias_janela, skus)
    return {
        "compra_real_pecas": float(gasto.pecas.iloc[0]),
        "compra_real_valor": float(gasto.valor.iloc[0]),
        "compra_real_skus": int(gasto.skus.iloc[0]),
        "pedido_ciclo_pecas": float(ped.pedido_pecas.sum()) if len(ped) else 0.0,
        "pedido_ciclo_valor": float(ped.pedido_valor_nota.sum()) if len(ped) else 0.0,
        "pedido_ciclo_skus": int(len(ped)),
        "janela_compra_de": str(ini),
        "estoque_pecas": float(estoque.pecas.iloc[0]),
        "estoque_valor": float(valor_est.valor.iloc[0]),
    }


def compra_real_por_item(wh: Warehouse, corte: str, dias_janela: int,
                         skus: set[str] | None = None) -> pd.DataFrame:
    """O que a empresa COMPROU DE VERDADE de cada item, na mesma janela.

    Reconstruido do estoque diario: a subida do saldo de um dia para o outro e
    o recebimento de mercadoria. Fecha com o estoque por construcao, o que a
    tabela de compras do ERP nao faz (ela salta 18x em marco de 2026 sem o
    estoque acusar).

    Sem esta coluna a tela nao respondia a pergunta obvia - "e o que foi
    comprado de fato?" - e o leitor nao tinha como saber se a lista era do
    modelo ou da empresa.
    """
    ini = (pd.Timestamp(corte) - pd.Timedelta(days=dias_janela - 1)).date()
    return wh.query(f"""
        with e as (
            select d.sku, d.data, d.saldo_final,
                   lag(d.saldo_final) over (partition by d.sku order by d.data) ant,
                   c.custo_unitario
            from {ref('mart_estoque_diario')} d
            join {ref('stg_catalogo')} c on c.sku = d.sku)
        select sku,
               sum(greatest(saldo_final - ant, 0)) comprou_de_verdade,
               sum(greatest(saldo_final - ant, 0) * custo_unitario) investiu_de_verdade
        from e
        where ant is not null and data between DATE '{ini}' and DATE '{corte}'
              {_so(skus, 'sku')}
        group by 1""")


def compras_do_ciclo(wh: Warehouse, corte: str, dias: int,
                     skus: set[str] | None = None) -> tuple[pd.DataFrame, dict]:
    """O PEDIDO DE COMPRA colocado na janela de revisao que termina no corte.

    E a decisao de compra da empresa que compete com a decisao do modelo: uma
    ordem contra uma ordem, na mesma janela e com o mesmo dinheiro. Comparar
    contra tudo o que a empresa recebeu ao longo do horizonte daria ao lado
    dela varias decisoes de compra que o modelo nunca teve chance de tomar -
    na pratica 9x mais dinheiro, e uma conclusao errada garantida.

    Fonte: raw_ciclo_pagamento, que tem a data do pedido E a data de entrada em
    estoque de cada linha atendida. O `distinct` por (pedido, produto, nota) e
    obrigatorio: a tabela e do ciclo de pagamento e repete a linha uma vez por
    parcela.

    Devolve (agregado por sku, mapa de chegadas). No mapa entram so as pecas
    que chegam DEPOIS do corte - o que chegou antes ja esta dentro da posicao
    de estoque inicial, e somar de novo contaria duas vezes.
    """
    ini = (pd.Timestamp(corte) - pd.Timedelta(days=dias - 1)).date()
    d = wh.query(f"""
        with k as (
            select distinct idpedido, cast(idsubproduto as varchar) as sku,
                   numnota,
                   cast(dt_pedido as date)          as pedido_em,
                   cast(dt_entrada_estoque as date) as entrou_em,
                   cast(qtdatendida as double)      as pecas
            from {ref('raw_ciclo_pagamento')}
            where cast(qtdatendida as double) > 0
              and cast(dt_pedido as date) between DATE '{ini}'
                                              and DATE '{str(corte)[:10]}'),
        preco as (
            select cast(idsubproduto as varchar) as sku, idpedido,
                   median(cast(valunitario as double)) as vu
            from {ref('raw_compras')}
            where cast(valunitario as double) > 0
            group by 1, 2)
        select k.sku, k.entrou_em, sum(k.pecas) as pecas,
               sum(k.pecas * coalesce(preco.vu, 0)) as valor_nota
        from k
        join {ref('stg_catalogo')} c on c.sku = k.sku
        left join preco on preco.sku = k.sku and preco.idpedido = k.idpedido
        where 1 = 1 {_so(skus, 'k.sku')}
        group by 1, 2""")
    if d.empty:
        return pd.DataFrame(columns=["sku", "pedido_pecas", "pedido_valor_nota",
                                     "pedido_ja_recebido"]), {}

    T = pd.Timestamp(str(corte)[:10])
    d["entrou_em"] = pd.to_datetime(d.entrou_em)
    depois = d.entrou_em > T

    agg = (d.assign(ja=np.where(depois, 0.0, d.pecas))
             .groupby("sku", as_index=False)
             .agg(pedido_pecas=("pecas", "sum"),
                  pedido_valor_nota=("valor_nota", "sum"),
                  pedido_ja_recebido=("ja", "sum")))

    # mapa sku -> {dia de chegada: pecas}, so o que chega depois do corte
    mapa: dict[str, dict] = {}
    f = d[depois & d.entrou_em.notna()]
    if len(f):
        dias64 = f.entrou_em.to_numpy("datetime64[D]")
        pecas = f.pecas.to_numpy(float)
        for i, sku in enumerate(f.sku.to_numpy()):
            mapa.setdefault(sku, {})
            mapa[sku][dias64[i]] = mapa[sku].get(dias64[i], 0.0) + pecas[i]
    return agg, mapa


def chegadas_em_transito(wh: Warehouse, corte: str, ini_janela,
                         skus: set[str] | None = None) -> dict[str, dict]:
    """O que estava A CAMINHO no corte e chegou depois: pedidos colocados
    ANTES da janela de revisao, com entrada real depois do corte.

    E a contraparte, no que aconteceu, do `em_transito` que o motor soma a
    posicao ao decidir: o motor conta com a previsao do pedido; a simulacao
    entrega na data real do livro de entradas. Entra nas tres politicas
    comparadas (plano, empresa, nada), porque essa mercadoria chegaria
    independentemente da decisao do ciclo. Os pedidos DA janela ficam fora
    daqui: sao a compra da empresa e ja entram por `compras_do_ciclo`.
    """
    d = wh.query(f"""
        with k as (
            select distinct idpedido, cast(idsubproduto as varchar) as sku, numnota,
                   cast(dt_pedido as date)          as pedido_em,
                   cast(dt_entrada_estoque as date) as entrou_em,
                   cast(qtdatendida as double)      as pecas
            from {ref('raw_ciclo_pagamento')}
            where cast(qtdatendida as double) > 0
              and cast(dt_pedido as date) < DATE '{str(ini_janela)[:10]}'
              and cast(dt_entrada_estoque as date) > DATE '{str(corte)[:10]}')
        select sku, entrou_em, sum(pecas) pecas from k
        where 1 = 1 {_so(skus, 'sku')} group by 1, 2""")
    mapa: dict[str, dict] = {}
    if d.empty:
        return mapa
    dias64 = pd.to_datetime(d.entrou_em).to_numpy("datetime64[D]")
    pecas = d.pecas.to_numpy(float)
    for i, sku in enumerate(d.sku.astype(str).to_numpy()):
        mapa.setdefault(sku, {})
        mapa[sku][dias64[i]] = mapa[sku].get(dias64[i], 0.0) + pecas[i]
    return mapa


def datas_disponiveis(wh: Warehouse) -> dict:
    """Os limites do banco e o horizonte maximo do catalogo."""
    d = wh.query(f"select min(data) a, max(data) b, count(distinct data) n "
                 f"from {ref('mart_estoque_diario')}")
    h = wh.query(f"select max(lead_time_dias) mx, min(lead_time_dias) mn, "
                 f"median(lead_time_dias) med from {ref('stg_catalogo')}")
    return {"primeiro": str(d.a.iloc[0])[:10], "ultimo": str(d.b.iloc[0])[:10],
            "dias": int(d.n.iloc[0]),
            "lead_min": int(h.mn.iloc[0]), "lead_max": int(h.mx.iloc[0]),
            "lead_mediano": float(h.med.iloc[0])}


# ======================================================================
# 2. A demanda que o horizonte de cada item pediu
# ======================================================================
def _futuro(diario: pd.DataFrame, corte: pd.Timestamp) -> dict:
    """Vetores diarios crus depois do corte, por item. Sem correcao aqui:
    corrigir exige saber qual horizonte esta sendo cobrado."""
    fut = diario[diario.data > corte]
    saida: dict[str, tuple] = {}
    for sku, g in fut.groupby("sku", sort=False):
        saida[sku] = (
            g.pecas_vendidas.to_numpy(float),
            (g.estado_estoque == "Disponivel").to_numpy(),
            (g.estado_estoque == "Ruptura parcial").to_numpy(),
            g.saldo_final.to_numpy(float),
            g.disponivel_final.to_numpy(float),
            # os dias, para o recebimento real poder ser encaixado no indice
            # certo. Sem isso a chegada da mercadoria dependeria de a grade
            # diaria comecar exatamente no dia seguinte ao corte para todo
            # item - e item criado no meio do periodo nao comeca ali.
            g.data.to_numpy("datetime64[D]"),
            g.pecas_ecommerce.to_numpy(float) if "pecas_ecommerce" in g.columns else np.zeros(len(g)),
            g.pecas_lojas.to_numpy(float) if "pecas_lojas" in g.columns else g.pecas_vendidas.to_numpy(float),
        )
    return saida


def entradas_reais_no_futuro(wh: Warehouse, corte: str) -> dict[str, dict]:
    """O que a empresa RECEBEU depois do corte, no dia em que recebeu.

    Fonte: raw_ciclo_pagamento, que registra a data de entrada em estoque de
    cada linha de pedido atendida. E registro, nao reconstrucao - e por isso
    tem a data exata, que o simulador precisa para saber quando a peca ficou
    disponivel. Medido contra a reconstrucao pelo estoque diario, os dois
    batem dentro de 5 a 10% mes a mes (17.286 contra 15.245 pecas em jul/25,
    18.534 contra 17.891 em ago/25), e a diferenca tem explicacao conhecida: a
    reconstrucao por salto de saldo perde o que entrou e saiu no mesmo dia.
    raw_compras nao serve para isso - salta de 18 mil para 322 mil pecas por
    mes em marco de 2026 sem o estoque acusar.

    Duas armadilhas tratadas aqui:

      - a tabela e do ciclo de PAGAMENTO, entao uma linha de pedido aparece uma
        vez por parcela (56 mil das 121 mil linhas tem mais de uma). Somar
        qtdatendida direto multiplicaria o recebimento pelo numero de parcelas.
        O distinct por (pedido, produto, nota) resolve.
      - valtitulo e por NOTA, nao por linha de produto (uma nota de 25
        produtos repete o mesmo valor 25 vezes), entao nao serve para valorar
        o item. O valor unitario vem de raw_compras, cujo preco unitario
        confere com o custo do cadastro (razao mediana 0,91).

    Devolve, por sku: {dia: (pecas, valor_da_nota)}.
    """
    d = wh.query(f"""
        with k as (
            select distinct idpedido, cast(idsubproduto as varchar) as sku,
                   numnota,
                   cast(dt_entrada_estoque as date)  as dia,
                   cast(qtdatendida as double)       as pecas
            from {ref('raw_ciclo_pagamento')}
            where dt_entrada_estoque is not null
              and cast(qtdatendida as double) > 0),
        preco as (
            select cast(idsubproduto as varchar) as sku, idpedido,
                   median(cast(valunitario as double)) as vu
            from {ref('raw_compras')}
            where cast(valunitario as double) > 0
            group by 1, 2)
        select k.sku, k.dia, sum(k.pecas) as pecas,
               sum(k.pecas * coalesce(preco.vu, 0)) as valor
        from k left join preco
             on preco.sku = k.sku and preco.idpedido = k.idpedido
        where k.dia > DATE '{str(corte)[:10]}'
        group by 1, 2""")
    if d.empty:
        return {}
    dias = pd.to_datetime(d.dia).to_numpy("datetime64[D]")
    saida: dict[str, dict] = {}
    for sku, idx in d.groupby("sku", sort=False).groups.items():
        pos = d.index.get_indexer(idx)
        saida[sku] = {dias[i]: (float(d.pecas.iloc[i]), float(d.valor.iloc[i]))
                      for i in pos}
    return saida


def demanda_no_horizonte(v, ok, cens, H: int) -> tuple[float, float, int]:
    """Quanto o mercado pediu nos primeiros H dias. (observado, corrigido, dias_falta)

    O corrigido nunca fica abaixo do observado: num dia de ruptura parcial a
    venda registrada e um PISO, e substitui-la pela media - o que uma versao
    anterior fazia - deixava o alvo abaixo do que se sabe ter saido.
    """
    v, ok, cens = v[:H], ok[:H], cens[:H]
    if len(v) == 0:
        return 0.0, 0.0, 0
    obs = float(v.sum())
    taxa, _, _, _ = em_censurado(v, ok, cens, True)
    corr = float(v[ok].sum()
                 + np.maximum(v[cens], taxa).sum()
                 + taxa * int((~ok & ~cens).sum()))
    return obs, max(corr, obs), int((~ok).sum())


# ======================================================================
# 3. O relatorio, produto por produto
# ======================================================================
def _simular_fluxo(pos0: float, chegadas: np.ndarray,
                   demanda: np.ndarray) -> dict:
    """Desce o horizonte dia a dia, com um vetor de chegadas por dia.

    Uma funcao so para as tres politicas comparadas - a compra do modelo (um
    pico no dia do prazo), a compra da empresa (as entradas reais nas datas
    reais) e nao comprar (vetor zero). Se cada uma tivesse o seu simulador, a
    diferenca entre elas poderia vir do codigo em vez da decisao.

    A mercadoria do dia entra ANTES da venda do dia, que e como o estoque
    diario registra (o recebimento e lancado na virada).
    """
    saldo = float(pos0)
    vendeu = falta = 0.0
    zerados = 0
    n = len(chegadas)
    for i, d in enumerate(demanda):
        if i < n:
            saldo += float(chegadas[i])
        if saldo <= 0:
            zerados += 1
        x = min(saldo, d)
        vendeu += x
        falta += max(0.0, d - saldo)
        saldo -= x
    return {"vendeu": vendeu, "faltou": falta, "sobrou": max(saldo, 0.0),
            "dias_zerado": zerados}


def _simular(pos0: float, Q: float, dia_chegada: int, demanda: np.ndarray) -> dict:
    """Uma compra unica que chega num dia - o caso do plano do modelo.

    Prazo maior que o horizonte deixa o vetor zerado de proposito: a peca nao
    chega a tempo de servir a demanda que justificou a compra.
    """
    ch = np.zeros(len(demanda))
    if 0 <= dia_chegada < len(ch):
        ch[dia_chegada] = Q
    return _simular_fluxo(pos0, ch, demanda)


def taxa_recente(wh: Warehouse, corte: str, dias: int = 90) -> pd.Series:
    """Taxa diaria observada nos ultimos `dias` antes do corte, por item.

    Serve de contraprova da media longa que o modelo usa. Nesta base a media de
    tres anos sai cerca de metade da taxa recente - em TODO o catalogo, nao so
    nos itens comprados, o que descarta efeito de selecao e aponta crescimento
    de demanda que a janela de estimacao nao acompanha.
    """
    ini = (pd.Timestamp(corte) - pd.Timedelta(days=dias)).date()
    d = wh.query(f"""
        select sku,
               sum(pecas_vendidas) pecas,
               sum(case when estado_estoque = 'Disponivel' then 1 else 0 end) dias_ok
        from {ref('mart_estoque_diario')}
        where data > DATE '{ini}' and data <= DATE '{corte}'
        group by 1""")
    return (d.pecas / d.dias_ok.replace(0, np.nan)).set_axis(d.sku).rename("taxa_recente")


def rodar(wh: Warehouse, corte: str, ajustes: dict | None = None,
          usar_reais: bool = True, skus: set[str] | None = None) -> dict:
    """O backtest de uma data exata, com parametros exatos.

    `skus` roda o mesmo teste sobre um recorte do catalogo - e quando ele vem
    preenchido, TODOS os numeros do teste passam a ser do recorte: o caixa do
    ciclo, o teto de capital, a compra da empresa e a demanda cobrada. E o que
    permite perguntar quanto da diferenca entre o plano e a compra real vem da
    decisao, e quanto vem da base estar furada.
    """
    lim = datas_disponiveis(wh)
    corte = str(corte)[:10]
    T = pd.Timestamp(corte)
    d0, d1 = pd.Timestamp(lim["primeiro"]), pd.Timestamp(lim["ultimo"])
    janela = (d1 - T).days
    if not (d0 < T < d1):
        return {"erro": f"a data {corte} esta fora do banco "
                        f"({lim['primeiro']} a {lim['ultimo']})"}
    if janela < MIN_DIAS_JANELA:
        return {"erro": f"a data {corte} deixa so {janela} dias para cobrar; "
                        f"o minimo e {MIN_DIAS_JANELA}"}

    p = Parametros.carregar()
    base = {k: v for k, v in p.__dict__.items()}
    ctx = contexto_da_data(wh, corte, int(base["periodo_revisao_dias"]), skus)

    # Sem teto escolhido a mao: o caixa e o que a empresa gastou na janela e o
    # teto de capital e o estoque daquele dia. Se o extrato nao registra compra
    # na janela, o caixa fica livre - senao o plano sairia vazio por um zero
    # que e ausencia de dado, nao decisao.
    if usar_reais:
        # o teto de caixa e o PEDIDO do ciclo, nao o recebimento: o modelo esta
        # decidindo uma ordem de compra, e a comparacao so vale se os dois
        # lados tiverem o mesmo dinheiro para colocar em ordem de compra. Sem
        # pedido registrado na janela cai para o recebimento, que e a leitura
        # que existia antes - senao o plano sairia vazio por ausencia de dado.
        # nao existe caixa historico do e-commerce: a compra real e toda da
        # empresa 26. A fatia fica desligada e o teto e o total real da data.
        base["fatia_ecommerce_rigida"] = False
        teto = ctx["pedido_ciclo_valor"] or ctx["compra_real_valor"]
        if teto > 0:
            base["teto_compra_ciclo"] = teto
        if ctx["estoque_valor"] > 0:
            base["teto_capital"] = ctx["estoque_valor"]
    for k, v in (ajustes or {}).items():
        if k in base and v is not None:
            base[k] = type(base[k])(v) if not isinstance(base[k], bool) else bool(v)
    pb = Parametros(**base)

    res = executar(wh, pb, ate=corte, skus=skus)
    m = res["res_sku_modelo"]
    plano = res["res_plano_compra"]

    diario = wh.query(
        f"select sku, data, saldo_final, disponivel_final, pecas_vendidas, "
        f"pecas_ecommerce, pecas_lojas, estado_estoque from {ref('mart_estoque_diario')} "
        f"where 1 = 1 {_so(skus, 'sku')} order by sku, data")
    diario["data"] = pd.to_datetime(diario.data)
    fut = _futuro(diario, T)

    recente = taxa_recente(wh, corte)
    real_item = compra_real_por_item(
        wh, corte, int(base["periodo_revisao_dias"]), skus).set_index("sku")
    # A compra da empresa que compete com a do modelo: o pedido colocado na
    # mesma janela de revisao, chegando nas datas reais. `ped_item` tem o
    # tamanho da ordem; `ent_fut` diz em que dia cada peca ficou disponivel.
    ped_item, ent_fut = compras_do_ciclo(
        wh, corte, int(base["periodo_revisao_dias"]), skus)
    ped_item = ped_item.set_index("sku") if len(ped_item) else ped_item
    # o que ja estava a caminho no corte, chegando nas datas reais
    ini_janela = (T - pd.Timedelta(days=int(base["periodo_revisao_dias"]) - 1)).date()
    transito_fut = chegadas_em_transito(wh, corte, ini_janela, skus)
    linhas, fora = [], []
    for r in plano.itertuples(index=False):
        H = int(r.periodo_protecao_dias)
        if r.sku not in fut:
            continue
        v, ok, cens, fis, disp, dias, ve, vl = fut[r.sku]
        if H > len(v):
            fora.append({"sku": r.sku, "item": r.item, "horizonte": H,
                         "comprar": int(r.quantidade_a_comprar)})
            continue

        obs, corr, dias_falta = demanda_no_horizonte(v, ok, cens, H)
        # a demanda diaria que alimenta a simulacao: o observado, e nos dias em
        # que faltou mercadoria a taxa media do proprio horizonte
        dem = v[:H].copy()
        indisp = ~ok[:H]
        taxa = corr / H if H else 0.0
        dem[indisp] = np.maximum(dem[indisp], taxa)

        Q = float(r.quantidade_a_comprar)
        # a simulacao parte do DISPONIVEL: o em transito que o motor somou a
        # posicao para decidir entra aqui na data real em que chegou, nao no
        # dia zero - senao a peca a caminho venderia antes de existir
        pos0 = float(getattr(r, "estoque_fisico", r.posicao_estoque))
        dia_chegada = int(min(max(r.lead_time_dias, 1), H)) - 1
        transito = np.zeros(H)
        mapa_t = transito_fut.get(r.sku)
        if mapa_t:
            for i in range(H):
                q = mapa_t.get(dias[i])
                if q:
                    transito[i] = q

        ch_plano = np.zeros(H)
        if 0 <= dia_chegada < H:
            ch_plano[dia_chegada] = Q
        com = _simular_fluxo(pos0, ch_plano + transito, dem)
        sem = _simular_fluxo(pos0, transito, dem)

        # A compra da EMPRESA no mesmo horizonte: as entradas reais, nos dias
        # reais, pelo mesmo simulador. Sem isso nao ha como dizer qual das duas
        # decisoes deixou mais peca faltando.
        chegou = np.zeros(H)
        mapa = ent_fut.get(r.sku)
        if mapa:
            for i in range(H):
                q = mapa.get(dias[i])
                if q:
                    chegou[i] = q
        real = _simular_fluxo(pos0, chegou + transito, dem)

        # o tamanho da ordem que a empresa colocou naquele ciclo, e quanto
        # dela ja tinha entrado antes do corte (essa parte ja esta dentro de
        # pos0 e por isso nao entra na simulacao)
        if len(ped_item) and r.sku in ped_item.index:
            pr = ped_item.loc[r.sku]
            pedido_pecas = float(pr.pedido_pecas)
            pedido_valor = float(pr.pedido_valor_nota)
            pedido_antes = float(pr.pedido_ja_recebido)
        else:
            pedido_pecas = pedido_valor = pedido_antes = 0.0
        chegou_no_horizonte = float(chegou.sum())

        # A ruptura OBSERVADA, que nao depende de simulacao: o que o mercado
        # pediu menos o que saiu. Fica ao lado da simulada para poder ser
        # confrontada - divergencia grande acusa a simulacao, nao o banco.
        ruptura_obs = max(corr - obs, 0.0)

        # As pecas da COMPRA que a demanda do horizonte pediu. O resto sobrou -
        # e o dinheiro delas ficou parado sem necessidade dentro do horizonte
        # que justificou a compra.
        usadas = float(np.clip(corr - pos0, 0.0, Q))
        sobra_compra = max(Q - usadas, 0.0)

        linhas.append(dict(
            sku=r.sku, item=r.item, familia=r.familia,
            classificacao=r.classificacao, curva_abc=r.curva_abc,
            classe_xyz=getattr(r, "classe_xyz", ""), regime=r.regime,
            horizonte=H, lead_time_dias=int(r.lead_time_dias),
            lead_time_pedidos=int(getattr(r, "lead_time_pedidos", 0) or 0),
            custo_unitario=float(r.custo_unitario),
            lucro_por_peca=float(r.lucro_por_peca),
            posicao_inicial=pos0,
            em_transito=float(getattr(r, "em_transito", 0.0) or 0.0),
            transito_chegou_no_horizonte=float(transito.sum()),
            # --- o que a plataforma mandaria
            mandaria_comprar=int(Q),
            investimento=float(r.valor_da_compra),
            # --- a quebra por canal: o que o plano atribuiu e o que vendeu
            share_ecommerce=float(np.nan_to_num(getattr(r, "share_ecommerce", 0.0))),
            investimento_ecommerce=float(getattr(r, "valor_da_compra_ecommerce", 0.0) or 0.0),
            investimento_lojas=float(getattr(r, "valor_da_compra_lojas", 0.0) or 0.0),
            vendeu_ecommerce=float(ve[:H].sum()),
            vendeu_lojas=float(vl[:H].sum()),
            previsto=float(r.mu_periodo),
            # --- o que a empresa comprou de fato na mesma janela
            comprou_de_verdade=float(
                real_item.comprou_de_verdade.get(r.sku, 0.0) or 0.0),
            investiu_de_verdade=float(
                real_item.investiu_de_verdade.get(r.sku, 0.0) or 0.0),
            # --- o que aconteceu
            vendeu_observado=obs,
            demanda_real=corr,
            # a demanda que a SIMULACAO cobra. Difere de `demanda_real` por
            # pouco e por um motivo concreto: em dia marcado sem estoque o ERP
            # ainda registra venda (o estoque e de um local, a venda e de
            # outro canal), e o vetor guarda a venda observada em vez da taxa
            # media. Sem esta coluna, atendido + faltou nao fecharia com a
            # demanda na tela e pareceria erro de conta.
            demanda_simulada=float(dem.sum()),
            dias_sem_estoque_real=dias_falta,
            # --- o veredito da compra
            usadas_da_compra=usadas,
            sobrou_da_compra=sobra_compra,
            dinheiro_parado=sobra_compra * float(r.custo_unitario),
            sobra_no_fim=com["sobrou"],
            faltou_com_plano=com["faltou"],
            faltou_sem_plano=sem["faltou"],
            dias_zerado_com_plano=com["dias_zerado"],
            houve_ruptura=bool(com["dias_zerado"] > 0),
            houve_excesso=bool(sobra_compra > 0),
            # --- a compra da EMPRESA no mesmo ciclo, levada ao mesmo horizonte
            pediu_de_verdade=pedido_pecas,
            investiu_no_pedido=pedido_pecas * float(r.custo_unitario),
            investiu_nota_fiscal=pedido_valor,
            pedido_ja_recebido=pedido_antes,
            recebeu_no_horizonte=chegou_no_horizonte,
            faltou_real=real["faltou"],
            dias_zerado_real=real["dias_zerado"],
            sobra_real_no_fim=real["sobrou"],
            teve_ruptura_real=bool(real["dias_zerado"] > 0),
            vendeu_com_a_real=real["vendeu"],
            vendeu_com_o_plano=com["vendeu"],
            # a ruptura lida do banco, sem simulacao
            ruptura_observada=ruptura_obs,
            # o veredito da comparacao, item por item
            falta_evitada=real["faltou"] - com["faltou"],
            # --- o ganho da compra
            margem_do_plano=(com["vendeu"] - sem["vendeu"]) * float(r.lucro_por_peca),
            erro_previsao=float(r.mu_periodo) - corr,
            # a contraprova: o que os ultimos 90 dias diziam, contra a media
            # longa que o modelo usou
            taxa_modelo=float(r.demanda_media_dia),
            taxa_recente=float(recente.get(r.sku, np.nan)),
        ))

    rel = pd.DataFrame(linhas)
    if rel.empty:
        return {"erro": "nenhum item com horizonte que caiba na janela restante"}

    # a falta e do estoque compartilhado; cada canal leva a parte dele pela
    # participacao REALIZADA no horizonte (a prevista, se nada vendeu)
    rel["share_realizada"] = np.where(rel.vendeu_observado > 0,
                                      rel.vendeu_ecommerce / rel.vendeu_observado.replace(0, np.nan),
                                      rel.share_ecommerce)
    rel["faltou_com_plano_ecommerce"] = rel.faltou_com_plano * rel.share_realizada
    rel["faltou_com_plano_lojas"] = rel.faltou_com_plano - rel.faltou_com_plano_ecommerce

    comprados = rel[rel.mandaria_comprar > 0]
    return {
        "janela": {
            "corte": corte,
            "primeiro_dia": lim["primeiro"], "ultimo_dia": lim["ultimo"],
            "dias_para_cobrar": janela,
            "cobre_de": str((T + pd.Timedelta(days=1)).date()),
            "cobre_ate": lim["ultimo"],
            "treino_dias": (T - d0).days + 1,
            "janela_estimacao_dias": int(base["janela_estimacao_dias"]),
            "dias_historico_usados": int(m.dias_historico.max()) if len(m) else 0,
            "skus_no_catalogo": int(len(plano)),
            "skus_no_relatorio": int(len(rel)),
            "skus_fora": len(fora),
            "horizonte_min": int(rel.horizonte.min()),
            "horizonte_max": int(rel.horizonte.max()),
        },
        "parametros": {k: getattr(pb, k) for k, *_ in PARAMETROS_BACKTEST},
        "contexto": ctx,
        "usou_valores_reais": bool(usar_reais),
        "resumo": _sanear(_resumo(rel, comprados)),
        "itens": _registros(rel),
        "fora": fora[:60],
    }


def _sanear(d):
    """Troca NaN e infinito por None, descendo nos dicionarios aninhados.

    O codificador JSON do FastAPI nao aceita NaN, e um NaN escondido dentro de
    um sub-dicionario derruba a rota com um 500 sem mensagem.
    """
    if isinstance(d, dict):
        return {k: _sanear(v) for k, v in d.items()}
    if isinstance(d, float) and not np.isfinite(d):
        return None
    return d


def _resumo(rel: pd.DataFrame, c: pd.DataFrame) -> dict:
    """As respostas diretas, no agregado."""
    return {
        "itens_avaliados": int(len(rel)),
        "itens_que_compraria": int(len(c)),
        # o outro lado da comparacao: o que a empresa fez, nos mesmos itens
        "itens_que_a_empresa_comprou": int((rel.comprou_de_verdade > 0).sum()),
        "pecas_que_a_empresa_comprou": float(rel.comprou_de_verdade.sum()),
        "investimento_da_empresa": float(rel.investiu_de_verdade.sum()),
        "itens_em_comum": int(((rel.mandaria_comprar > 0)
                               & (rel.comprou_de_verdade > 0)).sum()),
        "itens_so_do_modelo": int(((rel.mandaria_comprar > 0)
                                   & (rel.comprou_de_verdade <= 0)).sum()),
        "itens_so_da_empresa": int(((rel.mandaria_comprar <= 0)
                                    & (rel.comprou_de_verdade > 0)).sum()),
        # 1. quantas pecas mandaria comprar
        "pecas_que_compraria": int(c.mandaria_comprar.sum()),
        "investimento": float(c.investimento.sum()),
        # 2. quantas venderam de verdade
        "demanda_real_dos_comprados": float(c.demanda_real.sum()),
        "vendeu_observado_dos_comprados": float(c.vendeu_observado.sum()),
        "previsto_dos_comprados": float(c.previsto.sum()),
        # 3. houve excesso?
        "itens_com_excesso": int(c.houve_excesso.sum()),
        "pecas_sobrando": float(c.sobrou_da_compra.sum()),
        "dinheiro_parado": float(c.dinheiro_parado.sum()),
        "pct_do_investimento_parado": (float(c.dinheiro_parado.sum() / c.investimento.sum())
                                       if c.investimento.sum() else 0.0),
        # 4. houve ruptura?
        "itens_com_ruptura": int(c.houve_ruptura.sum()),
        "pecas_faltando_com_plano": float(c.faltou_com_plano.sum()),
        "pecas_faltando_sem_plano": float(rel.faltou_sem_plano.sum()),
        "dias_zerado": int(c.dias_zerado_com_plano.sum()),
        # 5. e o ganho
        "margem_do_plano": float(c.margem_do_plano.sum()),
        "retorno_por_real": (float(c.margem_do_plano.sum() / c.investimento.sum())
                             if c.investimento.sum() else 0.0),
        # o erro de previsao, no agregado e ponderado
        "erro_pecas": float(c.erro_previsao.sum()),
        "erro_wape": (float(c.erro_previsao.abs().sum() / c.demanda_real.sum())
                      if c.demanda_real.sum() else float("nan")),
        "acertou_em_cheio": int(((c.sobrou_da_compra == 0) & (~c.houve_ruptura)).sum()),
        # A janela de estimacao esta acompanhando o negocio? Se a taxa recente
        # e sistematicamente acima da media longa, o modelo subestima por
        # construcao - e o mesmo gap aparecer nos NAO comprados prova que nao
        # e efeito de selecao.
        "taxa_modelo_comprados": float(c.taxa_modelo.mean()) if len(c) else float("nan"),
        "taxa_recente_comprados": float(c.taxa_recente.mean(skipna=True)) if len(c) else float("nan"),
        "taxa_modelo_nao_comprados": float(rel[rel.mandaria_comprar == 0].taxa_modelo.mean()),
        "taxa_recente_nao_comprados": float(
            rel[rel.mandaria_comprar == 0].taxa_recente.mean(skipna=True)),
        # a quebra por canal, nos itens que o modelo compraria
        "canais": {
            "ecommerce": {
                "investimento": float(c.investimento_ecommerce.sum()),
                "vendeu": float(c.vendeu_ecommerce.sum()),
                "faltou_com_plano": float(c.faltou_com_plano_ecommerce.sum()),
                "share_prevista": (float(c.investimento_ecommerce.sum() / c.investimento.sum())
                                   if c.investimento.sum() else 0.0),
                "share_realizada": (float(c.vendeu_ecommerce.sum() / c.vendeu_observado.sum())
                                    if c.vendeu_observado.sum() else 0.0),
            },
            "lojas": {
                "investimento": float(c.investimento_lojas.sum()),
                "vendeu": float(c.vendeu_lojas.sum()),
                "faltou_com_plano": float(c.faltou_com_plano_lojas.sum()),
                "share_prevista": (float(c.investimento_lojas.sum() / c.investimento.sum())
                                   if c.investimento.sum() else 0.0),
                "share_realizada": (float(c.vendeu_lojas.sum() / c.vendeu_observado.sum())
                                    if c.vendeu_observado.sum() else 0.0),
            },
        },
        "comparativo": _comparativo(rel),
    }


def _comparativo(rel: pd.DataFrame) -> dict:
    """As duas decisoes lado a lado, no mesmo horizonte e na mesma regua.

    O escopo e TODOS os itens avaliados. Restringir aos que o modelo compraria
    tiraria da coluna da empresa exatamente os itens que ela comprou e ele nao,
    e a comparacao ficaria a favor do modelo por construcao.

    A falta de cada lado sai do mesmo simulador, com a mesma posicao inicial e
    a mesma demanda; o que muda e so quando e quanta mercadoria chega. Ao lado
    fica a ruptura OBSERVADA no banco, que nao passa por simulador nenhum: se
    ela e a falta simulada da empresa divergirem muito, o numero a olhar e o
    observado.
    """
    emp_comprou = rel.pediu_de_verdade > 0
    mod_comprou = rel.mandaria_comprar > 0
    return {
        "itens_avaliados": int(len(rel)),
        # ---- o que cada lado comprou
        "empresa_itens": int(emp_comprou.sum()),
        "empresa_pecas": float(rel.pediu_de_verdade.sum()),
        "empresa_valor": float(rel.investiu_no_pedido.sum()),
        "empresa_valor_nota": float(rel.investiu_nota_fiscal.sum()),
        "empresa_chegou_no_horizonte": float(rel.recebeu_no_horizonte.sum()),
        "empresa_ja_recebido_antes": float(rel.pedido_ja_recebido.sum()),
        "modelo_itens": int(mod_comprou.sum()),
        "modelo_pecas": float(rel.mandaria_comprar.sum()),
        "modelo_valor": float(rel.investimento.sum()),
        # ---- quanto faltou de cada lado
        "empresa_faltou": float(rel.faltou_real.sum()),
        "modelo_faltou": float(rel.faltou_com_plano.sum()),
        "sem_compra_faltou": float(rel.faltou_sem_plano.sum()),
        # ---- o mundo real, sem simulacao nenhuma: o que o mercado pediu e nao
        # levou, com TODAS as compras da empresa dentro. Nao se compara com as
        # duas linhas acima - elas sao de uma ordem de compra so.
        "ruptura_observada": float(rel.ruptura_observada.sum()),
        "observado_itens_com_ruptura": int((rel.dias_sem_estoque_real > 0).sum()),
        "observado_dias_zerado": int(rel.dias_sem_estoque_real.sum()),
        "observado_atendeu": float(rel.vendeu_observado.sum()),
        # ---- em quantos produtos
        "empresa_itens_com_ruptura": int(rel.teve_ruptura_real.sum()),
        "modelo_itens_com_ruptura": int(rel.houve_ruptura.sum()),
        "itens_com_ruptura_nos_dois": int(
            (rel.teve_ruptura_real & rel.houve_ruptura).sum()),
        "itens_que_so_o_modelo_salva": int(
            (rel.teve_ruptura_real & ~rel.houve_ruptura).sum()),
        "itens_que_so_o_modelo_quebra": int(
            (~rel.teve_ruptura_real & rel.houve_ruptura).sum()),
        # ---- dias de prateleira vazia
        "empresa_dias_zerado": int(rel.dias_zerado_real.sum()),
        "modelo_dias_zerado": int(rel.dias_zerado_com_plano.sum()),
        # ---- a demanda que os dois disputam, e o que cada um atendeu
        "demanda_total": float(rel.demanda_real.sum()),
        "demanda_simulada": float(rel.demanda_simulada.sum()),
        "empresa_atendeu": float(rel.vendeu_com_a_real.sum()),
        "modelo_atendeu": float(rel.vendeu_com_o_plano.sum()),
        # ---- o que sobra na prateleira no fim do horizonte, dos dois lados
        #
        # Mesma posicao inicial e mesma demanda nos dois: o que muda e so a
        # mercadoria que entrou. A DIFERENCA entre as duas sobras e portanto
        # peca comprada que a demanda do horizonte nao pediu - dinheiro parado
        # por decisao de compra, nao por estoque herdado. Valorar a sobra de
        # cada lado isolada nao serviria: as duas carregam a mesma posicao
        # inicial dentro, e essa parte nao e decisao de ninguem aqui.
        "empresa_sobra": float(rel.sobra_real_no_fim.sum()),
        "modelo_sobra": float(rel.sobra_no_fim.sum()),
        "empresa_capital_parado": float(
            (rel.sobra_real_no_fim * rel.custo_unitario).sum()),
        "modelo_capital_parado": float(
            (rel.sobra_no_fim * rel.custo_unitario).sum()),
        "capital_parado_evitado": float(
            ((rel.sobra_real_no_fim - rel.sobra_no_fim) * rel.custo_unitario).sum()),
        # ---- o saldo: peca que o modelo deixaria de perder, e o que custa
        "falta_evitada": float(rel.falta_evitada.sum()),
        "margem_da_falta_evitada": float(
            (rel.falta_evitada * rel.lucro_por_peca).sum()),
        "diferenca_de_gasto": float(rel.investimento.sum()
                                    - rel.investiu_no_pedido.sum()),
        # O resultado da semana, em dinheiro: a margem que a prateleira cheia
        # realiza mais o capital que nao fica parado. Sao as duas pontas da
        # mesma decisao - comprar de menos custa margem, comprar de mais custa
        # caixa - e so a soma diz se a troca valeu.
        "resultado_liquido": float(
            (rel.falta_evitada * rel.lucro_por_peca).sum()
            + ((rel.sobra_real_no_fim - rel.sobra_no_fim)
               * rel.custo_unitario).sum()),
    }


def rodar_intervalo(wh: Warehouse, de: str, ate: str, ajustes: dict | None = None,
                    usar_reais: bool = True, passo: int | None = None) -> dict:
    """O mesmo backtest em varias datas, uma por ciclo de revisao.

    O passo padrao e o periodo de revisao: e de quantos em quantos dias alguem
    de fato olharia a lista, e portanto quantas decisoes de compra existem no
    intervalo. Rodar todo dia contaria a mesma decisao varias vezes.
    """
    p = Parametros.carregar()
    passo = int(passo or (ajustes or {}).get("periodo_revisao_dias")
                or p.periodo_revisao_dias)
    d, fim = pd.Timestamp(de), pd.Timestamp(ate)
    datas, cur = [], d
    while cur <= fim and len(datas) < 26:
        datas.append(str(cur.date()))
        cur += pd.Timedelta(days=max(passo, 1))

    rodadas, itens = [], []
    for dt in datas:
        r = rodar(wh, dt, ajustes, usar_reais)
        if "erro" in r:
            continue
        s = dict(r["resumo"])
        s["corte"] = dt
        s["compra_real_valor"] = r["contexto"]["compra_real_valor"]
        s["compra_real_pecas"] = r["contexto"]["compra_real_pecas"]
        rodadas.append(s)
        for x in r["itens"]:
            if x["mandaria_comprar"] > 0:
                itens.append({**x, "corte": dt})
    if not rodadas:
        return {"erro": "nenhuma data do intervalo produziu relatorio"}

    t = pd.DataFrame(rodadas)
    todos = pd.DataFrame(itens)
    return {
        "datas": rodadas,
        "intervalo": {"de": de, "ate": ate, "passo_dias": passo,
                      "rodadas": len(rodadas)},
        "total": {
            "pecas_que_compraria": int(t.pecas_que_compraria.sum()),
            "investimento": float(t.investimento.sum()),
            "compra_real_valor": float(t.compra_real_valor.sum()),
            "demanda_real": float(t.demanda_real_dos_comprados.sum()),
            "pecas_sobrando": float(t.pecas_sobrando.sum()),
            "dinheiro_parado": float(t.dinheiro_parado.sum()),
            "itens_com_ruptura": int(t.itens_com_ruptura.sum()),
            "pecas_faltando": float(t.pecas_faltando_com_plano.sum()),
            "margem_do_plano": float(t.margem_do_plano.sum()),
            "pct_do_investimento_parado": (
                float(t.dinheiro_parado.sum() / t.investimento.sum())
                if t.investimento.sum() else 0.0),
        },
        "itens": _registros(todos) if len(todos) else [],
    }
