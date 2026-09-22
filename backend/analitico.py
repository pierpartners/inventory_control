# -*- coding: utf-8 -*-
"""
Camada analitica da aplicacao web.

Nao contem metodologia nova: tudo aqui reaproveita as funcoes de
`backend.modelo` (a mesma correcao de censura, a mesma escolha de
distribuicao, o mesmo teste da unidade marginal, o mesmo preco-sombra)
para produzir os recortes que as telas precisam mostrar.

Existe para que a explicacao na tela seja literalmente o mesmo calculo
que gerou o numero, e nao uma reimplementacao paralela.
"""
from __future__ import annotations

import json
from dataclasses import asdict

import numpy as np
import pandas as pd
from scipy import stats

from dataclasses import replace

from .config import CRITERIOS, Parametros
from .modelo import ajustar_distribuicao, modelar, MAX_UNIDADES_MARGINAIS
from .warehouse import Warehouse, ref


# ----------------------------------------------------------------------
# utilidades
# ----------------------------------------------------------------------
def limpo(v):
    """Converte escalar numpy/pandas em algo serializavel em JSON."""
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if (np.isnan(f) or np.isinf(f)) else f
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (pd.Timestamp,)):
        return str(v)
    if pd.isna(v) if np.isscalar(v) else False:
        return None
    return v


def registros(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> lista de dicts sem NaN/NaT, pronta para JSONResponse."""
    d = df.copy()
    for col in d.columns:
        if pd.api.types.is_datetime64_any_dtype(d[col]):
            d[col] = d[col].astype(str)
        elif d[col].dtype == object:
            d[col] = d[col].map(lambda x: str(x) if isinstance(x, (pd.Timestamp,)) else x)
    d = d.replace({np.nan: None, np.inf: None, -np.inf: None})
    return d.to_dict(orient="records")


def linha(serie: pd.Series) -> dict:
    return {k: limpo(v) for k, v in serie.items()}


# ----------------------------------------------------------------------
# leitura do warehouse
# ----------------------------------------------------------------------
def modelo_df(wh: Warehouse) -> pd.DataFrame:
    return wh.query(f"select * from {ref('res_sku_modelo')}")


def plano_df(wh: Warehouse) -> pd.DataFrame:
    return wh.query(f"select * from {ref('res_plano_compra')}")


def execucao(wh: Warehouse) -> pd.Series:
    return wh.query(f"select * from {ref('res_execucao')}").iloc[0]


def fila_df(wh: Warehouse) -> pd.DataFrame:
    return wh.query(f"select * from {ref('res_fila_marginal')} order by posicao_fila")


def estrategias_df(wh: Warehouse) -> pd.DataFrame:
    return wh.query(f"select * from {ref('res_estrategias')}")


def corrida(wh: Warehouse, n: int = 100, so_compradas: bool = True) -> list[dict]:
    """As primeiras `n` PECAS da fila, uma a uma.

    Os blocos de lote minimo sao expandidos de volta em pecas individuais para
    mostrar o que realmente acontece na fila: as primeiras pecas saem todas do
    mesmo item, e a partir de certo ponto a proxima peca ja e de outro produto,
    porque a chance de vender mais uma daquele primeiro caiu.

    Cada peca guarda a `posicao_fila` do bloco de onde veio, para que o clique
    consiga puxar a trilha de calculo completa daquela linha.
    """
    # so as colunas do bloquinho, e so as linhas necessarias: cada linha rende
    # ao menos uma peca, entao `limit n` nunca corta cedo demais
    onde = "where comprar " if so_compradas else ""
    limite = f"limit {int(n)}" if n < 1_000_000 else ""
    f = wh.query(
        f"select posicao_fila, sku, item, unidade_de, quantidade, p_vender, "
        f"custo_unitario, valor_esperado, comprar "
        f"from {ref('res_fila_marginal')} {onde}order by posicao_fila {limite}")
    if f.empty:
        return []

    # no caso comum (sem lote minimo) cada linha e uma peca: da para montar
    # tudo de uma vez, sem laco em Python sobre dezenas de milhares de itens
    if bool((f.quantidade == 1).all()):
        f = f.head(n).copy()
        f["ordem"] = np.arange(1, len(f) + 1)
        saida = pd.DataFrame({
            "ordem": f.ordem, "sku": f.sku, "item": f.item,
            "unidade": f.unidade_de.astype(int),
            "p": f.p_vender.round(4), "c": f.custo_unitario.round(2),
            "v": f.valor_esperado.round(2),
            "pos": f.posicao_fila.astype(int), "ok": f.comprar.astype(bool),
        })
        return saida.to_dict(orient="records")

    fora, i = [], 0
    for r in f.itertuples(index=False):
        if i >= n:
            break
        for u in range(int(r.quantidade)):
            if i >= n:
                break
            i += 1
            fora.append({
                "ordem": i, "sku": r.sku, "item": r.item,
                "unidade": int(r.unidade_de) + u,
                "p": round(float(r.p_vender), 4),
                "c": round(float(r.custo_unitario), 2),
                "v": round(float(r.valor_esperado) / max(int(r.quantidade), 1), 2),
                "pos": int(r.posicao_fila),
                "ok": bool(r.comprar),
            })
    return fora


# nome legivel de cada coluna da fila, agrupado pela etapa do calculo.
# E o que a tela de conferencia usa para montar a trilha de auditoria.
ETAPAS_FILA = [
    ("Identificação", [
        ("posicao_fila", "Posição na fila", "int"),
        ("sku", "SKU", "txt"), ("item", "Produto", "txt"),
        ("familia", "Família", "txt"), ("classificacao", "Classe ABC×XYZ", "txt"),
        ("regime", "Regime de política", "txt"),
        ("posicao_estoque", "Posição de estoque hoje", "int"),
        ("unidade_de", "Esta linha é a unidade nº", "int"),
        ("unidade_ate", "…até a unidade nº", "int"),
        ("quantidade", "Peças nesta linha", "int"),
    ]),
    ("1. Demanda medida no histórico", [
        ("dias_historico", "Dias de histórico", "int"),
        ("dias_sem_estoque", "Dias sem nada para vender (descartados)", "int"),
        ("dias_ruptura_parcial", "Dias que acabaram no meio (imputados)", "int"),
        ("pecas_imputadas", "Peças recuperadas pela imputação", "num2"),
        ("demanda_dia_ingenua", "Demanda/dia sem corrigir a ruptura", "num3"),
        ("demanda_dia_corrigida", "Demanda/dia corrigida  ·  d", "num3"),
        ("subestimacao_pct", "Subestimação evitada", "pct"),
        ("desvio_dia", "Desvio-padrão diário", "num3"),
    ]),
    ("2. Horizonte da compra", [
        ("lead_time_dias", "Prazo do fornecedor", "int"),
        ("periodo_revisao_dias", "Intervalo entre revisões", "int"),
        ("horizonte", "Horizonte  ·  H = prazo + revisão", "int"),
        ("prazo_recebimento_dias", "Prazo de recebimento  ·  s×receb_e + (1−s)×receb_l", "num1"),
        ("prazo_pagamento_dias", "Prazo de pagamento ao fornecedor", "num1"),
        ("dias_capital", "Dias com o dinheiro preso  ·  D = max(1, H + recebimento − pagamento)", "num1"),
    ]),
    ("3. Distribuição da demanda no horizonte", [
        ("mu_periodo", "Demanda esperada  ·  μ = d × H", "num3"),
        ("sd_periodo", "Desvio no horizonte  ·  σ = desvio diário × √H", "num3"),
        ("variancia_periodo", "Variância  ·  σ²", "num2"),
        ("razao_var_media", "Razão σ²/μ  (>1 ⇒ Binomial Negativa)", "num3"),
        ("distribuicao", "Distribuição escolhida", "txt"),
        ("nb_r", "Parâmetro r  ·  μ²/(σ²−μ)", "num4"),
        ("nb_p", "Parâmetro p  ·  r/(r+μ)", "num4"),
    ]),
    ("4. Probabilidade desta peça", [
        ("cdf_ate_k_menos_1", "F(k−1) = chance de a demanda NÃO chegar a k", "num6"),
        ("p_vender", "P = 1 − F(k−1)  ·  chance de esta peça vender", "num6"),
        ("p_encalhar", "1 − P  ·  chance de encalhar", "num6"),
        ("p_vender_ultima", "P da última peça desta linha", "num6"),
    ]),
    ("5. Economia por peça", [
        ("lucro_por_peca", "Lucro bruto por peça", "brl2"),
        ("share_ecommerce", "Participação do e-commerce na demanda  ·  s", "pct"),
        ("lucro_por_peca_ecommerce", "Lucro por peça no e-commerce", "brl2"),
        ("lucro_por_peca_lojas", "Lucro por peça nas lojas", "brl2"),
        ("fator_perda_ruptura_ecommerce", "Perda na ruptura · e-commerce", "pct"),
        ("fator_perda_ruptura_lojas", "Perda na ruptura · lojas", "pct"),
        ("margem_unit", "Margem capturada  ·  M = s×lucro_e×fator_e + (1−s)×lucro_l×fator_l", "brl2"),
        ("custo_unitario", "Custo de compra  ·  c", "brl2"),
        ("taxa_manutencao_ano", "Taxa de manutenção ao ano", "pct"),
        ("premio_escassez", "Prêmio de escassez do capital  ·  λ", "num4"),
        ("custo_manter_no_periodo", "Custo de carregar no horizonte  ·  c×(taxa+λ)×H/365", "brl2"),
        ("perda_encalhe_pct", "Perda se encalhar (% do custo)", "pct"),
        ("custo_obsolescencia", "Perda por obsolescência  ·  c × %", "brl2"),
        ("perda_unit", "Perda total se encalhar  ·  L = carregar + obsolescência", "brl2"),
        ("limite_marginal_compra", "Limite: só vale se P > L/(M+L)", "num4"),
    ]),
    ("6. Valor desta peça", [
        ("ganho_esperado", "Ganho esperado  ·  P × M × peças", "brl2"),
        ("custo_esperado", "Custo esperado  ·  (1−P) × L × peças", "brl2"),
        ("valor_esperado", "Valor  ·  V = ganho − custo", "brl2"),
        ("custo", "Investimento desta linha  ·  c × peças", "brl2"),
        ("valor_por_real", "Retorno por real  ·  V / (c × peças)", "num4"),
        ("nota", "NOTA  ·  V / (c × peças) / D", "num6"),
    ]),
    ("7. Decisão do caixa", [
        ("teto_ciclo", "Caixa do ciclo", "brl"),
        ("caixa_antes", "Já gasto quando esta linha foi avaliada", "brl"),
        ("caixa_restante", "Caixa restante depois desta linha", "brl"),
        ("caixa_acumulado", "Total gasto até aqui", "brl"),
        ("teto_ecommerce", "Fatia do e-commerce no caixa", "brl"),
        ("caixa_acumulado_ecommerce", "Gasto do e-commerce até aqui", "brl"),
        ("caixa_acumulado_lojas", "Gasto das lojas até aqui", "brl"),
        ("caixa_restante_ecommerce", "Fatia do e-commerce restante", "brl"),
        ("caixa_restante_lojas", "Fatia das lojas restante", "brl"),
        ("comprar", "Entrou na compra?", "bool"),
        ("motivo", "Motivo", "txt"),
        ("pecas_acumuladas", "Peças compradas até aqui", "int"),
        ("valor_acumulado", "Margem esperada acumulada", "brl"),
    ]),
]

COLUNAS_FILA = [c for _, campos in ETAPAS_FILA for c, _, _ in campos]


def fila_pagina(wh: Warehouse, sku: str = "", motivo: str = "", busca: str = "",
                ordem: str = "posicao_fila", desc: bool = False,
                pg: int = 1, tam: int = 120) -> dict:
    """Uma pagina da fila, com os filtros aplicados no banco.

    A fila tem dezenas de milhares de linhas - filtrar e ordenar em SQL evita
    trazer tudo para a memoria a cada clique.
    """
    tabela = ref("res_fila_marginal")
    onde, params = [], []
    if sku:
        onde.append("sku = ?")
        params.append(sku)
    if motivo == "comprada":
        onde.append("comprar")
    elif motivo == "fora":
        onde.append("not comprar")
    if busca:
        onde.append("(lower(item) like ? or lower(sku) like ? or lower(familia) like ?)")
        alvo = f"%{busca.lower()}%"
        params += [alvo, alvo, alvo]
    filtro = (" where " + " and ".join(onde)) if onde else ""

    if ordem not in COLUNAS_FILA:
        ordem = "posicao_fila"
    direcao = "desc" if desc else "asc"

    total = int(wh.query_params(f"select count(*) as n from {tabela}{filtro}", params)
                .iloc[0]["n"])
    pg = max(1, pg)
    df = wh.query_params(
        f'select * from {tabela}{filtro} order by "{ordem}" {direcao}, posicao_fila '
        f"limit {int(tam)} offset {int((pg - 1) * tam)}", params)
    return {
        "linhas": registros(df), "total": total, "pg": pg,
        "paginas": max(1, (total + tam - 1) // tam),
        "ordem": ordem, "desc": desc,
    }


_fila_previa: dict = {}


def _fila_para_previa(wh: Warehouse) -> tuple:
    """Carrega a fila e o ponto de partida uma vez e guarda em memoria.

    A previa da tela e chamada a cada tecla digitada. Ler 37 mil linhas do
    warehouse toda vez seria o gargalo; o motor em si roda em ~40 ms.
    """
    if "fila" not in _fila_previa:
        _fila_previa["fila"] = wh.query(
            f"select posicao_fila, sku, bloco, quantidade, custo, valor_esperado, "
            f"reducao_risco, reducao_falta, nota, p_vender "
            f"from {ref('res_fila_marginal')} order by posicao_fila")
        base = wh.query(f"select risco_inicial, falta_inicial from {ref('res_criterios')} limit 1")
        _fila_previa["risco"] = float(base.risco_inicial.iloc[0])
        _fila_previa["falta"] = float(base.falta_inicial.iloc[0])
    return _fila_previa["fila"], _fila_previa["risco"], _fila_previa["falta"]


def simular_criterios(wh: Warehouse, p: Parametros, ativos: str,
                      valores: dict | None = None) -> dict:
    """Corta a fila com um conjunto de criterios e valores hipoteticos.

    Roda o motor de verdade em vez de interpolar a fronteira. Nao e capricho:
    `caixa` e `retorno` sao cortes de prefixo (as duas grandezas sao monotonas
    ao longo da fila) e poderiam ser lidos na curva - mas `chance` e um FILTRO,
    porque a chance de vender nao e monotona entre itens diferentes. Ler a
    fronteira daria um numero errado justamente no criterio mais delicado.

    Com mais de um criterio ligado a peca precisa passar por todos: o corte
    cai onde o primeiro deles fecha a porta.
    """
    from .modelo import caminhar, criterios_ativos, regra_de_parada, ultimos_do_corte

    fila, risco0, falta0 = _fila_para_previa(wh)
    campos = {c[0]: c[2] for c in CRITERIOS}
    limpos = {}
    for chave, campo in campos.items():
        v = (valores or {}).get(campo)
        if v is not None:
            try:
                limpos[campo] = float(v)
            except (TypeError, ValueError):
                pass
    hipotese = replace(p, **limpos, criterio_parada=ativos or p.criterio_parada)

    regra = regra_de_parada(hipotese)
    r = caminhar(fila, regra, risco0, falta0)
    ok = r["comprar"]
    n = int(ok.sum())
    caixa = float(fila.custo.to_numpy()[ok].sum()) if n else 0.0
    return {
        "ativos": regra["criterios"],
        "valores": {k: float(getattr(hipotese, v)) for k, v in campos.items()},
        "posicao_corte": int(np.max(np.where(ok)[0]) + 1) if n else 0,
        "blocos": n,
        "pecas": int(fila.quantidade.to_numpy()[ok].sum()) if n else 0,
        "itens": int(pd.unique(fila.sku.to_numpy()[ok]).size) if n else 0,
        "caixa": caixa,
        "margem": float(fila.valor_esperado.to_numpy()[ok].sum()) if n else 0.0,
        "margem_em_risco": float(r["margem_em_risco_restante"][-1]),
        "falta": float(r["falta_restante"][-1]),
        "risco_inicial": risco0,
        "teto_ciclo": float(p.teto_compra_ciclo),
        "estoura_caixa": bool(caixa > p.teto_compra_ciclo + 1e-6),
        **ultimos_do_corte(fila, ok),
    }


def conferencia_item(wh: Warehouse, sku: str, limite: int = 400) -> dict:
    """O dado cru de um item: venda a venda, compra a compra, custo a custo.

    Nao passa por nenhuma coluna do modelo. E a trilha que permite conferir a
    mao de onde saiu cada numero da decisao - e foi assim que se descobriu que
    o custo de seis itens vinha do reset do ERP em dia de estoque zero.
    """
    seguro = str(sku).replace("'", "''")
    cab = wh.query(f"""
        select sku, item, familia, unidade, origem, custo_unitario,
               custo_ultimo_lancado, custo_mediano, preco_tabela,
               lead_time_dias, lead_time_desvio_dias, lead_time_pedidos,
               lote_minimo_compra
        from {ref('stg_catalogo')} where sku = '{seguro}'""")
    if cab.empty:
        return {}

    vendas = wh.query(f"""
        select data, pedido, pecas_vendidas, valor_da_peca, receita_bruta,
               receita_liquida, custo_unitario, cmv, lucro, valor_do_frete,
               canal, tipo_cliente, uf, regiao as vendedor, cliente_id
        from {ref('stg_vendas')} where sku = '{seguro}'
        order by data desc limit {int(limite)}""")

    # o pedido de compra como o ERP registra, com prazo combinado e realizado
    compras = wh.query(f"""
        with c as (
            select cast(dtmovimento as date) as "data", idpedido, fornecedor,
                   cast(qtdsolicitada as double) solicitado,
                   cast(qtdatendida as double)   atendido,
                   cast(valunitario as double)   valor_unitario,
                   cast(valtotliquido as double) valor_total,
                   cast(diasprevisaoentrega as double) prazo_previsto,
                   descrformapagamento pagamento, compradoroficial comprador
            from {ref('raw_compras')}
            where cast(idsubproduto as varchar) = '{seguro}'),
        k as (
            select idpedido,
                   min(cast(dt_entrada_estoque as date)) entrou_em,
                   min(cast(dias_entrega_realizado as double)) prazo_realizado,
                   min(cast(prazo_titulo_dias as double)) prazo_pagamento
            from {ref('raw_ciclo_pagamento')}
            where cast(idsubproduto as varchar) = '{seguro}'
            group by 1)
        select c.*, k.entrou_em, k.prazo_realizado, k.prazo_pagamento
        from c left join k on k.idpedido = c.idpedido
        order by c.data desc limit {int(limite)}""")

    # o que de fato ENTROU no estoque: a subida do saldo de um dia para o outro.
    # Fecha com o estoque por construcao, ao contrario da tabela de compras -
    # que salta 18x em marco de 2026 sem o estoque acusar.
    entradas = wh.query(f"""
        with e as (
            select data, saldo_final, custo_unitario,
                   lag(saldo_final) over (order by data) ant
            from (select d.data, d.saldo_final, c.custo_unitario
                  from {ref('mart_estoque_diario')} d
                  join {ref('stg_catalogo')} c on c.sku = d.sku
                  where d.sku = '{seguro}'))
        select "data", (saldo_final - ant) as pecas,
               (saldo_final - ant) * custo_unitario as valor
        from e where ant is not null and saldo_final - ant > 0
        order by data desc limit {int(limite)}""")

    # so os dias em que o custo MUDOU, e se havia estoque naquele dia: e a
    # coluna que denuncia o reset do ERP
    custos = wh.query(f"""
        with c as (
            select cast(dtmovimento as date) as "data",
                   cast(valcustomedio as double) as custo,
                   cast(qtdatualestoque as double) as estoque,
                   lag(cast(valcustomedio as double))
                       over (order by dtmovimento) ant
            from {ref('raw_estoque_diario_erp')}
            where cast(idsubproduto as varchar) = '{seguro}')
        select "data", custo, estoque
        from c where ant is null or abs(custo - ant) > 0.005
        order by data desc limit 120""")

    # os totais vem do historico INTEIRO, nao das linhas exibidas. Trinta
    # itens passam do limite de 400 linhas de venda (o maior tem 2.241), e
    # somar so o que a tela mostra seria escrever "vendeu no total" embaixo de
    # uma soma parcial - o erro que esta tela existe para pegar.
    tv = wh.query(f"""
        select count(*) linhas, coalesce(sum(pecas_vendidas),0) pecas,
               coalesce(sum(receita_liquida),0) receita,
               coalesce(sum(cmv),0) cmv, coalesce(sum(lucro),0) lucro,
               min(data) primeira, max(data) ultima
        from {ref('stg_vendas')} where sku = '{seguro}'""").iloc[0]
    tc = wh.query(f"""
        select count(*) pedidos,
               coalesce(sum(cast(qtdsolicitada as double)),0) solicitado,
               coalesce(sum(cast(qtdatendida as double)),0) atendido,
               coalesce(sum(cast(valtotliquido as double)),0) valor
        from {ref('raw_compras')}
        where cast(idsubproduto as varchar) = '{seguro}'""").iloc[0]
    te = wh.query(f"""
        with e as (
            select d.saldo_final, c.custo_unitario,
                   lag(d.saldo_final) over (order by d.data) ant
            from {ref('mart_estoque_diario')} d
            join {ref('stg_catalogo')} c on c.sku = d.sku
            where d.sku = '{seguro}')
        select count(*) eventos,
               coalesce(sum(saldo_final - ant),0) pecas,
               coalesce(sum((saldo_final - ant) * custo_unitario),0) valor
        from e where ant is not null and saldo_final - ant > 0""").iloc[0]

    pecas = float(tv.pecas) or 0.0
    tot = {
        "vendas_linhas": int(tv.linhas),
        "vendas_pecas": pecas,
        "vendas_receita": float(tv.receita),
        "vendas_cmv": float(tv.cmv),
        "vendas_lucro": float(tv.lucro),
        "preco_medio": float(tv.receita) / pecas if pecas else 0.0,
        "custo_medio_vendido": float(tv.cmv) / pecas if pecas else 0.0,
        "lucro_medio": float(tv.lucro) / pecas if pecas else 0.0,
        "primeira_venda": str(tv.primeira)[:10] if tv.primeira is not None else None,
        "ultima_venda": str(tv.ultima)[:10] if tv.ultima is not None else None,
        "compras_pedidos": int(tc.pedidos),
        "compras_solicitado": float(tc.solicitado),
        "compras_atendido": float(tc.atendido),
        "compras_valor": float(tc.valor),
        "entradas_eventos": int(te.eventos),
        "entradas_pecas": float(te.pecas),
        "entradas_valor": float(te.valor),
    }
    return {
        "cabecalho": linha(cab.iloc[0]),
        "totais": tot,
        "vendas": registros(vendas),
        "compras": registros(compras),
        "entradas": registros(entradas),
        "custos": registros(custos),
        # quantas linhas a tela mostra de quantas existem, por tabela
        "mostrados": {
            "vendas": [int(len(vendas)), int(tv.linhas)],
            "compras": [int(len(compras)), int(tc.pedidos)],
            "entradas": [int(len(entradas)), int(te.eventos)],
        },
        "limite": int(limite),
    }


def entradas_na_compra(wh: Warehouse) -> list[dict]:
    """Em que peca do ciclo cada produto entra na compra.

    E a leitura mais direta do rodizio: o primeiro produto leva as primeiras N
    pecas sozinho, ate que mais uma unidade dele passe a render menos que a
    primeira unidade do segundo produto - e assim por diante.
    """
    f = fila_df(wh)
    if f.empty:
        return []
    f = f[f.comprar].sort_values("posicao_fila").copy()
    if f.empty:
        return []
    f["pecas_acum"] = f.quantidade.cumsum()
    f["peca_inicial"] = f.pecas_acum - f.quantidade + 1
    g = f.groupby("sku").agg(
        item=("item", "first"), familia=("familia", "first"),
        classificacao=("classificacao", "first"), curva_abc=("curva_abc", "first"),
        entra_na_peca=("peca_inicial", "min"),
        pecas=("quantidade", "sum"),
        investimento=("custo", "sum"),
        valor_esperado=("valor_esperado", "sum"),
        custo_unitario=("custo_unitario", "first"),
        chance_primeira=("p_vender", "max"),
        chance_ultima=("p_vender_ultima", "min"),
    ).reset_index().sort_values("entra_na_peca")
    g["ordem_entrada"] = range(1, len(g) + 1)
    return registros(g)


def diario_sku(wh: Warehouse, sku: str) -> pd.DataFrame:
    seguro = sku.replace("'", "''")
    return wh.query(
        f"select data, saldo_inicial, saldo_final, disponivel_final, pecas_vendidas, estado_estoque "
        f"from {ref('mart_estoque_diario')} where sku = '{seguro}' order by data")


# ----------------------------------------------------------------------
# 1. reconstrucao da imputacao (estacao "censura")
# ----------------------------------------------------------------------
def imputacao_detalhada(vendas: np.ndarray, censurado: np.ndarray,
                        mu: float, sd: float) -> np.ndarray:
    """Valor imputado por dia censurado: E[D | D >= observado].

    Usa a distribuicao ja convergida pelo EM (media/desvio corrigidos que o
    modelo gravou), que e exatamente a distribuicao da ultima iteracao.
    """
    imputado = vendas.astype(float).copy()
    if mu <= 0 or not censurado.any():
        return imputado
    _, dist, _, _ = ajustar_distribuicao(mu, sd)
    teto = int(max(30, dist.ppf(0.99999) + 10))
    k = np.arange(teto + 1)
    pk = dist.pmf(k)
    num = np.cumsum((k * pk)[::-1])[::-1]
    den = np.cumsum(pk[::-1])[::-1]
    idx = np.clip(vendas[censurado].astype(int), 0, teto)
    est = np.where(den[idx] > 1e-12, num[idx] / den[idx], vendas[censurado].astype(float))
    est = np.maximum(np.minimum(est, dist.ppf(0.95)), vendas[censurado])
    imputado[censurado] = est
    return imputado


# ----------------------------------------------------------------------
# 2. distribuicao no periodo de protecao
# ----------------------------------------------------------------------
def pontos_distribuicao(mu: float, sd: float, max_pontos: int = 150) -> dict:
    """Distribuicao da demanda no periodo de protecao, pronta para o grafico.

    A demanda do catalogo vai de 3 a mais de 2.000 pecas por janela, entao um
    numero fixo de pontos nao serve: em item de alto giro cortaria so a cauda
    esquerda. Aqui a faixa vem da propria distribuicao (0,05% a 99,95%) e, se
    ela for larga demais, os valores sao agrupados em faixas de mesma largura.
    Cada barra e a probabilidade *acumulada dentro da faixa*, nao a pmf de um
    ponto - assim a soma continua valendo 1 com ou sem agrupamento.
    """
    nome, dist, r, prob = ajustar_distribuicao(mu, sd)
    vazio = {"nome": nome, "x": [], "pmf": [], "cdf": [], "cauda": [],
             "passo": 1, "r": None, "p": None, "percentis": {}}
    if mu <= 0:
        return vazio

    base = int(max(0, np.floor(dist.ppf(0.0005))))
    topo = int(np.ceil(dist.ppf(0.9995)))
    if topo - base < 6:
        topo = base + 6
    passo = max(1, int(np.ceil((topo - base + 1) / max_pontos)))
    x = np.arange(base, topo + passo, passo)

    inferior = dist.cdf(x - 1)                 # P(D <= inicio da faixa - 1)
    superior = dist.cdf(x + passo - 1)         # P(D <= fim da faixa)
    return {
        "nome": nome,
        "x": [int(v) for v in x],
        "pmf": [float(v) for v in (superior - inferior)],
        "cdf": [float(v) for v in superior],
        "cauda": [float(1 - v) for v in superior],
        "passo": int(passo),
        "r": limpo(r), "p": limpo(prob),
        "percentis": {q: float(dist.ppf(v))
                      for q, v in [("p50", .5), ("p75", .75), ("p90", .9),
                                   ("p95", .95), ("p99", .99)]},
    }


# ----------------------------------------------------------------------
# 3. teste da unidade marginal
# ----------------------------------------------------------------------
def _dias_capital(m: pd.Series) -> float:
    """O denominador da nota: dias em que o dinheiro da peca fica preso.

    Resultado gravado antes do ciclo financeiro existir nao tem a coluna; ai
    o capital conta so ate a venda, como antes.
    """
    v = m.get("dias_capital") if hasattr(m, "get") else None
    if v is None or v != v:
        return float(m.periodo_protecao_dias)
    return float(v)


def proxima_peca(m: pd.Series, p: Parametros, pos: float) -> dict | None:
    """A conta de uma unica peca: a proxima que se pensa em comprar."""
    if float(m.mu_periodo) <= 0 or float(m.custo_unitario) <= 0:
        return None
    _, dist, _, _ = ajustar_distribuicao(float(m.mu_periodo), float(m.sd_periodo))
    cu = float(m.custo_falta_unit)
    perda = float(m.custo_manter_no_periodo) + float(m.custo_unitario) * p.perda_encalhe
    horizonte = _dias_capital(m)
    k = int(max(0, round(pos))) + 1
    pv = float(1 - dist.cdf(k - 1))
    valor = pv * cu - (1 - pv) * perda
    return {
        "unidade": k, "p_vender": pv,
        "ganho": pv * cu, "custo_encalhe": (1 - pv) * perda, "valor": valor,
        "nota": valor / (float(m.custo_unitario) * horizonte) if horizonte else 0.0,
        "vale": bool(valor > 0),
    }


def escada_de_pecas(m: pd.Series, p: Parametros, pos: float,
                    pontos: int = 46) -> list[dict]:
    """A proxima peca, a seguinte, a seguinte... cada uma com o proprio valor.

    E a mesma conta que o motor de compra faz. A faixa vai da posicao atual ate
    um pouco depois do ponto em que a peca deixa de se pagar - assim o corte
    sempre aparece no grafico, tanto no item que aceita 6 pecas quanto no que
    aceita 2.000. Quando a faixa e larga demais, as pecas sao amostradas de
    tantas em tantas (o campo `passo` diz de quantas).
    """
    if float(m.mu_periodo) <= 0:
        return []
    _, dist, _, _ = ajustar_distribuicao(float(m.mu_periodo), float(m.sd_periodo))
    cu = float(m.custo_falta_unit)
    perda = float(m.custo_manter_no_periodo) + float(m.custo_unitario) * p.perda_encalhe
    horizonte = _dias_capital(m)
    base = int(max(0, round(pos)))

    # onde o valor da peca cruza zero: valor >= 0  <=>  P >= perda/(Cu+perda)
    limite = perda / (cu + perda) if (cu + perda) > 0 else 1.0
    k_zero = int(np.floor(dist.ppf(min(max(1 - limite, 0.0), 0.999999)))) + 1
    topo = max(int(k_zero + max(3, (k_zero - base) * 0.18)), base + 6)
    passo = max(1, int(np.ceil((topo - base) / pontos)))

    fora = []
    for i, k in enumerate(range(base + 1, topo + 1, passo)):
        pv = float(1 - dist.cdf(k - 1))
        ganho = pv * cu
        custo_encalhe = (1 - pv) * perda
        valor = ganho - custo_encalhe
        fora.append({
            "peca": i * passo + 1, "unidade": k, "passo": passo,
            "p_vender": pv,
            "ganho": ganho, "custo_encalhe": custo_encalhe,
            "valor": valor,
            "nota": valor / (float(m.custo_unitario) * horizonte) if horizonte else 0.0,
            "vale": bool(valor > 0),
        })
    return fora


def escada_por_loja(wh: Warehouse, m: pd.Series, p: Parametros, escada: list[dict],
                    pos: float = 0.0, comprar: int = 0, janela: int = 365) -> dict:
    """A mesma escada, vista pela demanda de UMA empresa do grupo.

    O estoque e um so (o CD) e a compra e uma so, mas cada loja puxa uma fatia
    da demanda. Para a curva de uma loja, a demanda do horizonte e a do CD
    multiplicada pela participacao da loja na venda DESTE SKU na janela - o
    mesmo rateio de `rateio_por_loja`. A distribuicao e afinada de forma
    consistente: se cada peca demandada vai para a loja com probabilidade s,
    uma Poisson(mu) vira Poisson(s*mu) e uma Binomial Negativa(r, mu) vira
    Binomial Negativa(r, s*mu) - o mesmo r, media e variancia menores.

    A posicao de estoque e a compra do plano tambem sao rateadas por s: a
    k-esima peca "da loja" e a k-esima alem da fatia dela no estoque do CD.
    Sem isso, o estoque inteiro do CD medido contra a demanda de uma loja so
    daria chance zero em qualquer loja pequena, o que nao diz nada. O eixo
    (`peca`, a n-esima a mais) e o mesmo da escada total, com o mesmo passo,
    para as curvas ficarem sobrepostas e comparaveis.
    """
    if not escada or float(m.mu_periodo) <= 0:
        return {"janela_dias": int(janela), "lojas": []}
    seguro = str(m.sku).replace("'", "''")
    fim = f"(select max(data) from {ref('mart_estoque_diario')})"
    v = wh.query(f"""
        with v as (
            select loja_id, loja, pecas_vendidas
            from {ref('stg_vendas')}
            where sku = '{seguro}' and loja_id is not null
              and data <= {fim} and data > {fim} - INTERVAL {int(janela)} DAY)
        select loja_id, arg_max(loja, n) as loja, sum(pecas) as pecas
        from (select loja_id, loja, sum(pecas_vendidas) pecas, count(*) n
              from v group by 1, 2)
        group by 1""")
    total = float(v.pecas.sum()) if not v.empty else 0.0
    if total <= 0:
        return {"janela_dias": int(janela), "lojas": []}

    mu, sd = float(m.mu_periodo), float(m.sd_periodo)
    var = sd * sd
    r = mu * mu / (var - mu) if var > mu * 1.05 else None
    cu = float(m.custo_falta_unit)
    perda = float(m.custo_manter_no_periodo) + float(m.custo_unitario) * p.perda_encalhe
    horizonte = _dias_capital(m)

    lojas = []
    for row in v.sort_values("pecas", ascending=False).itertuples(index=False):
        s = float(row.pecas) / total
        if s <= 0:
            continue
        mu_l = s * mu
        var_l = mu_l + (mu_l * mu_l / r if r else 0.0)
        _, dist, _, _ = ajustar_distribuicao(mu_l, float(np.sqrt(var_l)))
        base_l = int(max(0, round(pos * s)))
        pontos = []
        for e in escada:
            k = base_l + int(e["peca"])
            pv = float(1 - dist.cdf(k - 1))
            ganho, enc = pv * cu, (1 - pv) * perda
            valor = ganho - enc
            pontos.append({
                "peca": e["peca"], "unidade": k, "passo": e["passo"],
                "p_vender": pv, "ganho": ganho, "custo_encalhe": enc, "valor": valor,
                "nota": valor / (float(m.custo_unitario) * horizonte) if horizonte else 0.0,
                "vale": bool(valor > 0),
            })
        lojas.append({
            "loja_id": str(row.loja_id),
            "loja": "".join(ch for ch in str(row.loja) if ch.isprintable()).strip()
                    or f"Loja {row.loja_id}",
            "pecas_janela": float(row.pecas), "participacao": s,
            "mu_periodo": mu_l, "sd_periodo": float(np.sqrt(var_l)),
            "posicao": base_l, "comprar": int(round(comprar * s)),
            "escada": pontos,
        })
    return {"janela_dias": int(janela), "pecas_total_janela": total, "lojas": lojas}


def comparar_produtos(wh: Warehouse, p: Parametros, n: int = 5) -> list[dict]:
    """Alguns produtos bem diferentes entre si, na mesma regua.

    Serve para mostrar por que a nota divide por custo e por prazo: sem isso,
    o item caro de margem gorda pareceria sempre o melhor negocio.
    """
    df = plano_df(wh)
    if df.empty:
        return []
    comprados = df[df.quantidade_a_comprar > 0]
    escolha = pd.concat([
        df.nlargest(1, "custo_unitario"),            # o mais caro do catalogo
        df.nlargest(1, "lucro_por_peca"),            # o de maior margem
        df.nlargest(1, "periodo_protecao_dias"),     # o de prazo mais longo
        comprados.nlargest(1, "melhor_nota") if not comprados.empty
        else df.nlargest(1, "melhor_nota"),          # o campeao da fila
        df.nlargest(1, "demanda_media_dia"),         # o de maior giro
        df.nsmallest(1, "custo_unitario"),           # o mais barato
    ]).drop_duplicates("sku").head(n)

    fora = []
    for _, m in escolha.iterrows():
        primeiro = proxima_peca(m, p, float(m.posicao_estoque))
        if primeiro is None:
            continue
        fora.append({
            "sku": m.sku, "item": m["item"], "familia": m.familia,
            "custo_unitario": float(m.custo_unitario),
            "margem": float(m.custo_falta_unit),
            "horizonte": float(m.periodo_protecao_dias),
            "dias_capital": _dias_capital(m),
            "p_vender": primeiro["p_vender"],
            "valor": primeiro["valor"],
            "retorno_por_real": primeiro["valor"] / float(m.custo_unitario),
            "nota": primeiro["nota"],
            "comprado": int(m.quantidade_a_comprar),
        })
    return sorted(fora, key=lambda x: -x["nota"])


def teste_marginal(mu: float, limite: float, cu: float, co: float,
                   maximo: int = MAX_UNIDADES_MARGINAIS) -> list[dict]:
    """Para cada unidade k: vale a pena carregar a k-esima peca?

    Guarda a k-esima peca se P(demanda >= k) > Co/(Cu+Co), ou seja, se a
    chance de precisar dela paga o custo de mante-la parada.
    """
    if mu <= 0:
        return []
    d = stats.poisson(mu)
    topo = int(min(maximo, max(4, np.ceil(d.ppf(0.999)) + 3)))
    fora = []
    for k in range(1, topo + 1):
        p = float(1 - d.cdf(k - 1))
        fora.append({
            "k": k,
            "p_precisar": p,
            "ganho": p * cu,
            "custo": co,
            "vale": bool(p > limite),
        })
    return fora


# ----------------------------------------------------------------------
# 4. curva de custo do estoque de seguranca (regime continuo)
# ----------------------------------------------------------------------
def curva_seguranca(sd: float, h: float, cu: float, ciclos: float,
                    es_otimo: float, pontos: int = 46) -> list[dict]:
    """Custo anual de manter + custo anual de faltar, em funcao do estoque
    de seguranca. O minimo desta curva e o ponto que o modelo escolhe."""
    if sd <= 0 or ciclos <= 0:
        return []
    topo = max(es_otimo * 2.2, sd * 3.0, 1.0)
    saida = []
    for es in np.linspace(0, topo, pontos):
        z = es / sd
        g = float(stats.norm.pdf(z) - z * (1 - stats.norm.cdf(z)))
        faltas = sd * g * ciclos
        manter = es * h
        ruptura = faltas * cu
        saida.append({
            "es": float(es),
            "nivel_servico": float(stats.norm.cdf(z)),
            "custo_manter": float(manter),
            "custo_ruptura": float(ruptura),
            "custo_total": float(manter + ruptura),
        })
    return saida


# ----------------------------------------------------------------------
# 5. preco-sombra do capital: a curva inteira
# ----------------------------------------------------------------------
_memo_lambda: dict[str, list[dict]] = {}


def curva_capital(wh: Warehouse, p: Parametros, pontos: int = 15) -> list[dict]:
    """Capital imobilizado e lucro liquido como funcao do premio de escassez.

    E a curva que o solver percorre: sobe o preco interno do dinheiro ate o
    estoque caber no teto. Mostrar a curva inteira e o que torna a restricao
    compreensivel - da para ver quanto o teto custa por ano.
    """
    chave = json.dumps(asdict(p), sort_keys=True) + f"|{pontos}"
    if chave in _memo_lambda:
        return _memo_lambda[chave]

    base = modelo_df(wh)
    lam_max = max(1.2, float(base.premio_escassez.iloc[0]) * 2.6)
    lams = np.unique(np.concatenate([
        np.linspace(0, lam_max, pontos),
        [float(base.premio_escassez.iloc[0])],
    ]))
    saida = []
    for lam in lams:
        m = modelar(base, p, float(lam))
        saida.append({
            "lam": float(lam),
            "capital": float(m.capital_imobilizado.sum()),
            "lucro_liquido": float(m.lucro_liquido_ano.sum()),
            "custo_total": float(m.custo_total_ano.sum()),
            "custo_ruptura": float(m.custo_ruptura_ano.sum()),
            "custo_manter": float(m.custo_manter_ano.sum()),
            "faltas": float(m.faltas_esperadas_ano.sum()),
            "nivel_servico": float(m.nivel_servico.mean()),
        })
    _memo_lambda[chave] = saida
    if len(_memo_lambda) > 8:
        _memo_lambda.pop(next(iter(_memo_lambda)))
    return saida


def invalidar_cache() -> None:
    _memo_lambda.clear()
    _fila_previa.clear()


# ----------------------------------------------------------------------
# 6. dossie completo de um item
# ----------------------------------------------------------------------
def dossie(wh: Warehouse, p: Parametros, sku: str) -> dict:
    """Tudo que as telas precisam saber sobre um item: o dado bruto, cada
    etapa do calculo com o numero que saiu dela, e a decisao final."""
    seguro = sku.replace("'", "''")
    m = wh.query(f"select * from {ref('res_sku_modelo')} where sku = '{seguro}'")
    if m.empty:
        return {}
    m = m.iloc[0]

    pl = wh.query(f"select * from {ref('res_plano_compra')} where sku = '{seguro}'")
    pl = pl.iloc[0] if not pl.empty else None

    dia = diario_sku(wh, sku)
    vendas = dia.pecas_vendidas.to_numpy(float)
    censurado = (dia.estado_estoque == "Ruptura parcial").to_numpy()
    disponivel = (dia.estado_estoque == "Disponivel").to_numpy()
    sem = (dia.estado_estoque == "Sem estoque").to_numpy()

    imput = imputacao_detalhada(vendas, censurado, float(m.demanda_media_dia),
                                float(m.desvio_padrao_dia))

    dias = []
    for i, r in enumerate(dia.itertuples(index=False)):
        dias.append({
            "data": str(r.data)[:10],
            "saldo_inicial": limpo(r.saldo_inicial),
            "saldo_final": limpo(r.saldo_final),
            "disponivel_final": limpo(r.disponivel_final),
            "vendido": limpo(r.pecas_vendidas),
            "estado": r.estado_estoque,
            "imputado": float(imput[i]) if censurado[i] else None,
        })

    dist = pontos_distribuicao(float(m.mu_periodo), float(m.sd_periodo))
    regime_discreto = str(m.regime) == "Unidade marginal"

    marginal = teste_marginal(
        float(m.mu_periodo), float(m.limite_marginal),
        float(m.custo_falta_unit), float(m.custo_manter_no_periodo)
    ) if regime_discreto or float(m.mu_periodo) < p.limiar_giro_baixo * 2 else []

    seguranca = curva_seguranca(
        float(m.sd_periodo), float(m.custo_manter_unit_real),
        float(m.custo_falta_unit), float(m.pedidos_por_ano),
        float(m.estoque_seguranca)) if not regime_discreto else []

    pos_atual = float(pl.posicao_estoque) if pl is not None else 0.0
    escada = escada_de_pecas(m, p, pos_atual)
    escada_lojas = escada_por_loja(
        wh, m, p, escada, pos_atual,
        int(pl.quantidade_a_comprar) if pl is not None else 0)

    return {
        "item": linha(m),
        "plano": linha(pl) if pl is not None else None,
        "escada": escada,
        "escada_lojas": escada_lojas,
        "projecao": projecao_item(wh, m, pl, p, dia),
        "economia": {
            "margem_se_vender": float(m.custo_falta_unit),
            "perda_se_encalhar": float(m.custo_manter_no_periodo)
                                 + float(m.custo_unitario) * p.perda_encalhe,
            "custo_carregar": float(m.custo_manter_no_periodo),
            "custo_obsolescencia": float(m.custo_unitario) * p.perda_encalhe,
            "custo_unitario": float(m.custo_unitario),
            "lucro_por_peca": float(m.lucro_por_peca),
            "posicao": pos_atual,
            "comprar": int(pl.quantidade_a_comprar) if pl is not None else 0,
        },
        "canais": {
            "ecommerce": {
                "demanda_dia": float(np.nan_to_num(m.get("demanda_media_dia_ecommerce", 0.0))),
                "desvio_dia": float(np.nan_to_num(m.get("desvio_padrao_dia_ecommerce", 0.0))),
                "share": float(np.nan_to_num(m.get("share_ecommerce", 0.0))),
                "lucro_por_peca": float(np.nan_to_num(m.get("lucro_por_peca_ecommerce", m.lucro_por_peca))),
                "fator": p.fator_perda_ruptura_ecommerce,
            },
            "lojas": {
                "demanda_dia": float(np.nan_to_num(m.get("demanda_media_dia_lojas", 0.0))),
                "desvio_dia": float(np.nan_to_num(m.get("desvio_padrao_dia_lojas", 0.0))),
                "share": 1.0 - float(np.nan_to_num(m.get("share_ecommerce", 0.0))),
                "lucro_por_peca": float(np.nan_to_num(m.get("lucro_por_peca_lojas", m.lucro_por_peca))),
                "fator": p.fator_perda_ruptura_lojas,
            },
            "covariancia": float(np.nan_to_num(m.get("covariancia_canais", 0.0))),
        },
        "dias": dias,
        "resumo_dias": {
            "disponivel": int(disponivel.sum()),
            "ruptura_parcial": int(censurado.sum()),
            "sem_estoque": int(sem.sum()),
            "pre_lancamento": int((dia.estado_estoque == "Pre-lancamento").sum()),
            "total": int(len(dia)),
        },
        "distribuicao": dist,
        "marginal": marginal,
        "seguranca": seguranca,
        "prazos": {"pedidos": prazos_item(wh, sku), "catalogo": distribuicao_prazo(wh),
                   "recebimento": distribuicao_recebimento(wh)},
        "parametros": {
            "periodo_revisao_dias": p.periodo_revisao_dias,
            "taxa_manutencao_ano": p.taxa_manutencao_ano,
            "custo_por_pedido": p.custo_por_pedido,
            "fator_perda_ruptura": p.fator_perda_ruptura,
            "fator_perda_ruptura_ecommerce": p.fator_perda_ruptura_ecommerce,
            "fator_perda_ruptura_lojas": p.fator_perda_ruptura_lojas,
            "limiar_giro_baixo": p.limiar_giro_baixo,
            "dias_por_ano": p.dias_por_ano,
            "dias_utilizaveis_minimo": getattr(p, "dias_utilizaveis_minimo", 0),
        },
    }


# ----------------------------------------------------------------------
# 6a. o prazo do fornecedor como distribuicao, nao como um numero
# ----------------------------------------------------------------------
PRAZO_MAX_DIAS = 365      # mesmo corte de stg_catalogo.sql: fora disso e erro de registro
PRAZO_PASSO_DIAS = 7      # uma faixa por semana; a ultima acumula o que passa do teto
PRAZO_TETO_DIAS = 126     # 18 semanas cobrem o p99 (157 no extrato real fica na faixa "+")


def distribuicao_prazo(wh: Warehouse) -> dict:
    """O prazo de recebimento das compras, pedido a pedido, no catalogo inteiro.

    Histograma semanal do prazo REALIZADO (do pedido a entrada no estoque) e do
    COMBINADO (o que o cadastro do pedido dizia), com mediana e p90 de cada um;
    e, na outra ponta do ciclo, o PAGAMENTO ao fornecedor (dias da entrada ao
    vencimento do titulo), que o motor le item a item (mediana das notas do
    item; `prazo_pagamento_fornecedor_dias` e so o reserva sem dado).
    E a evidencia por tras de `lead_time_desvio_dias`: se a cauda do realizado
    for longa, o desvio do prazo tem de entrar no estoque de seguranca. Base
    sem `raw_ciclo_pagamento` (sintetica/exports) devolve {} e as telas omitem
    o grafico.
    """
    if not wh.existe("raw_ciclo_pagamento"):
        return {}
    r = wh.query(f"""
        with k as (
            select cast(dias_entrega_realizado as double) realizado,
                   cast(dias_entrega_combinado as double) combinado
            from {ref('raw_ciclo_pagamento')}
            where dias_entrega_realizado is not null
              and cast(dias_entrega_realizado as double) between 0 and {PRAZO_MAX_DIAS}),
        faixa as (
            select least(floor(realizado / {PRAZO_PASSO_DIAS}), {PRAZO_TETO_DIAS // PRAZO_PASSO_DIAS}) f,
                   count(*) n_realizado
            from k group by 1),
        faixa_c as (
            select least(floor(combinado / {PRAZO_PASSO_DIAS}), {PRAZO_TETO_DIAS // PRAZO_PASSO_DIAS}) f,
                   count(*) n_combinado
            from k where combinado is not null and combinado between 0 and {PRAZO_MAX_DIAS}
            group by 1),
        pg as (
            select cast(prazo_titulo_dias as double) pagamento
            from {ref('raw_ciclo_pagamento')}
            where prazo_titulo_dias is not null
              and cast(prazo_titulo_dias as double) between -{PRAZO_MAX_DIAS} and {PRAZO_MAX_DIAS}),
        faixa_p as (
            -- pago antes da entrada (antecipado) cai na primeira faixa
            select least(greatest(floor(pagamento / {PRAZO_PASSO_DIAS}), 0), {PRAZO_TETO_DIAS // PRAZO_PASSO_DIAS}) f,
                   count(*) n_pagamento
            from pg group by 1),
        resumo_p as (
            select count(*) pagamentos, median(pagamento) mediana_pagamento,
                   quantile_cont(pagamento, 0.9) p90_pagamento, stddev(pagamento) desvio_pagamento,
                   avg(case when pagamento < 0 then 1.0 else 0.0 end) pago_antes_entrada
            from pg),
        resumo as (
            select count(*) pedidos,
                   median(realizado) mediana, quantile_cont(realizado, 0.9) p90,
                   stddev(realizado) desvio,
                   median(combinado) mediana_combinado, quantile_cont(combinado, 0.9) p90_combinado,
                   avg(case when realizado > combinado then 1.0 else 0.0 end) atrasou,
                   avg(case when realizado < combinado then 1.0 else 0.0 end) adiantou
            from k),
        fora as (
            select count(*) n from {ref('raw_ciclo_pagamento')}
            where dias_entrega_realizado is null
               or cast(dias_entrega_realizado as double) not between 0 and {PRAZO_MAX_DIAS})
        select coalesce(a.f, c.f, g.f) f, coalesce(a.n_realizado, 0) n_realizado,
               coalesce(c.n_combinado, 0) n_combinado, coalesce(g.n_pagamento, 0) n_pagamento,
               (select pagamentos from resumo_p) pagamentos,
               (select mediana_pagamento from resumo_p) mediana_pagamento,
               (select p90_pagamento from resumo_p) p90_pagamento,
               (select desvio_pagamento from resumo_p) desvio_pagamento,
               (select pago_antes_entrada from resumo_p) pago_antes_entrada,
               (select pedidos from resumo) pedidos, (select mediana from resumo) mediana,
               (select p90 from resumo) p90, (select desvio from resumo) desvio,
               (select mediana_combinado from resumo) mediana_combinado,
               (select p90_combinado from resumo) p90_combinado,
               (select atrasou from resumo) atrasou, (select adiantou from resumo) adiantou,
               (select n from fora) fora
        from faixa a full outer join faixa_c c on c.f = a.f
                     full outer join faixa_p g on g.f = coalesce(a.f, c.f)
        order by 1""")
    if r.empty or int(r.pedidos.iloc[0] or 0) == 0:
        return {}
    n_faixas = PRAZO_TETO_DIAS // PRAZO_PASSO_DIAS + 1
    real = np.zeros(n_faixas); comb = np.zeros(n_faixas); pag = np.zeros(n_faixas)
    for t in r.itertuples(index=False):
        i = int(t.f)
        real[i] = t.n_realizado; comb[i] = t.n_combinado; pag[i] = t.n_pagamento
    tot = float(real.sum()); tot_c = float(comb.sum()) or 1.0; tot_p = float(pag.sum()) or 1.0
    cab = r.iloc[0]
    return {
        "passo": PRAZO_PASSO_DIAS,
        "inicio": [i * PRAZO_PASSO_DIAS for i in range(n_faixas)],
        "realizado": [float(v) / tot for v in real],
        "combinado": [float(v) / tot_c for v in comb],
        # quando o dinheiro SAI: dias entre a entrada no estoque e o vencimento
        # do titulo ao fornecedor (media das parcelas ponderada pelo valor)
        "pagamento": [float(v) / tot_p for v in pag],
        "pagamentos": int(cab.pagamentos or 0),
        "mediana_pagamento": limpo(cab.mediana_pagamento), "p90_pagamento": limpo(cab.p90_pagamento),
        "desvio_pagamento": limpo(cab.desvio_pagamento), "pago_antes_entrada": limpo(cab.pago_antes_entrada),
        "pedidos": int(cab.pedidos),
        "fora": int(cab.fora or 0),
        "mediana": limpo(cab.mediana), "p90": limpo(cab.p90), "desvio": limpo(cab.desvio),
        "mediana_combinado": limpo(cab.mediana_combinado), "p90_combinado": limpo(cab.p90_combinado),
        "atrasou": limpo(cab.atrasou), "adiantou": limpo(cab.adiantou),
    }


def distribuicao_recebimento(wh: Warehouse) -> dict:
    """A ponta de ENTRADA do ciclo financeiro: quantos dias depois da venda o
    dinheiro entra, titulo a titulo, separado por canal.

    E-commerce e lojas sao meios de pagamento diferentes (gateway em D+30
    contra dinheiro, debito e cartao parcelado), por isso o histograma e um
    por canal - e e por canal que o motor le o prazo de cada item. Base sem
    `raw_ciclo_recebimento` devolve {} e as telas omitem o painel.
    """
    if not wh.existe("raw_ciclo_recebimento"):
        return {}
    n_faixas = PRAZO_TETO_DIAS // PRAZO_PASSO_DIAS + 1
    r = wh.query(f"""
        with t as (
            select case when cast(idempresa as integer) = 33 then 'ecommerce' else 'lojas' end canal,
                   cast(dias_recebimento as double) dias, cast(valtitulo as double) valor, tipocartao
            from {ref('raw_ciclo_recebimento')}
            where dias_recebimento is not null
              and cast(dias_recebimento as double) between 0 and {PRAZO_MAX_DIAS}),
        faixa as (
            select canal, least(floor(dias / {PRAZO_PASSO_DIAS}), {n_faixas - 1}) f, count(*) n
            from t group by 1, 2),
        resumo as (
            select canal, count(*) titulos, median(dias) mediana, quantile_cont(dias, 0.9) p90,
                   sum(dias * valor) / nullif(sum(valor), 0) media_ponderada,
                   avg(case when tipocartao = 'C' then 1.0 else 0.0 end) credito,
                   avg(case when tipocartao = 'D' then 1.0 else 0.0 end) debito,
                   avg(case when dias <= 1 then 1.0 else 0.0 end) no_dia
            from t group by 1)
        select f.canal, f.f, f.n, r.titulos, r.mediana, r.p90, r.media_ponderada,
               r.credito, r.debito, r.no_dia
        from faixa f join resumo r on r.canal = f.canal
        order by 1, 2""")
    if r.empty:
        return {}
    fora = {"passo": PRAZO_PASSO_DIAS,
            "inicio": [i * PRAZO_PASSO_DIAS for i in range(n_faixas)]}
    for canal in ("ecommerce", "lojas"):
        parte = r[r.canal == canal]
        if parte.empty:
            continue
        hist = np.zeros(n_faixas)
        for t in parte.itertuples(index=False):
            hist[int(t.f)] = t.n
        tot = float(hist.sum()) or 1.0
        cab = parte.iloc[0]
        fora[canal] = {
            "hist": [float(v) / tot for v in hist],
            "titulos": int(cab.titulos), "mediana": limpo(cab.mediana), "p90": limpo(cab.p90),
            "media_ponderada": limpo(cab.media_ponderada),
            "credito": limpo(cab.credito), "debito": limpo(cab.debito), "no_dia": limpo(cab.no_dia),
        }
    return fora


def prazos_usados(wh: Warehouse) -> dict:
    """O que o motor de fato usou, item a item, no ciclo financeiro: a mediana
    dos prazos usados e a fracao de itens em que o prazo veio do proprio item
    (e nao do catalogo, do canal ou do parametro). Resultado gravado antes do
    ciclo por item nao tem as colunas e devolve {}."""
    if not wh.existe("res_sku_modelo"):
        return {}
    try:
        r = wh.query(f"""
            select median(prazo_pagamento_dias) pagamento_mediana,
                   avg(case when prazo_pagamento_origem = 'item' then 1.0 else 0.0 end) pagamento_item,
                   median(prazo_recebimento_ecommerce_usado) recebimento_ecommerce_mediana,
                   median(prazo_recebimento_lojas_usado) recebimento_lojas_mediana,
                   median(prazo_recebimento_dias) recebimento_mediana,
                   avg(case when prazo_recebimento_origem like 'item/%' then 1.0 else 0.0 end) recebimento_ecommerce_item,
                   avg(case when prazo_recebimento_origem like '%/item' then 1.0 else 0.0 end) recebimento_lojas_item,
                   median(dias_capital) dias_capital_mediana, median(periodo_protecao_dias) horizonte_mediana
            from {ref('res_sku_modelo')} where custo_unitario > 0""")
    except Exception:
        return {}
    return {k: limpo(v) for k, v in r.iloc[0].to_dict().items()}


def prazos_item(wh: Warehouse, sku: str) -> list[dict]:
    """Cada recebimento do item: quando pediu, quando entrou, quantos dias levou
    e quantos o pedido prometia. Sao os pontos que sustentam `lead_time_dias`."""
    if not wh.existe("raw_ciclo_pagamento"):
        return []
    seguro = str(sku).replace("'", "''")
    tem_compras = wh.existe("raw_compras")
    forn = (f"""left join (select distinct idpedido, cast(idsubproduto as varchar) sku, fornecedor
                          from {ref('raw_compras')}
                          where cast(idsubproduto as varchar) = '{seguro}') c
                 on c.idpedido = k.idpedido and c.sku = cast(k.idsubproduto as varchar)"""
            if tem_compras else "")
    r = wh.query(f"""
        select k.idpedido pedido, cast(k.dt_pedido as date) pedido_em,
               cast(k.dt_entrada_estoque as date) entrou_em,
               cast(k.dias_entrega_realizado as double) realizado,
               cast(k.dias_entrega_combinado as double) combinado,
               {'c.fornecedor' if tem_compras else 'null'} fornecedor,
               cast(k.prazo_titulo_dias as double) pagamento,
               cast(k.dias_entrega_realizado as double) between 0 and {PRAZO_MAX_DIAS} usado
        from {ref('raw_ciclo_pagamento')} k {forn}
        where cast(k.idsubproduto as varchar) = '{seguro}'
          and k.dias_entrega_realizado is not null
        order by 2""")
    return registros(r)


# ----------------------------------------------------------------------
# 6b. o futuro do item: consumo esperado, chegadas e prazos
# ----------------------------------------------------------------------
def pedidos_em_aberto(wh: Warehouse, sku: str, hoje, janela_dias: int = 180) -> list[dict]:
    """Pedidos de compra do item ainda nao atendidos, com a data prevista.

    Aberto = pediu mais do que recebeu E o pedido nao aparece no livro de
    entradas do CD (`raw_ciclo_pagamento`). Pedidos com mais de `janela_dias`
    ficam de fora: na pratica sao cancelados que o ERP nunca fechou. A
    extracao traz a mesma linha repetida quando o pedido aparece em mais de
    uma empresa - o distinct por pedido + quantidade + preco remove isso.
    O que ja passou da previsao e marcado `atrasado` e desenhado em `hoje`.
    """
    seguro = str(sku).replace("'", "''")
    c = wh.query(f"""
        with c as (
            select distinct idpedido, fornecedor,
                   cast(dtmovimento as date)      as pedido_em,
                   cast(previsaoentrega as date)  as previsto_para,
                   cast(diasprevisaoentrega as integer) as prazo_previsto,
                   cast(qtdsolicitada as double)  as solicitado,
                   cast(qtdatendida as double)    as atendido,
                   cast(valunitario as double)    as valor_unitario
            from {ref('raw_compras')}
            where cast(idsubproduto as varchar) = '{seguro}'
              and cast(qtdatendida as double) < cast(qtdsolicitada as double)
              and cast(dtmovimento as date) >= date '{str(hoje)[:10]}' - INTERVAL {int(janela_dias)} DAY)
        select c.* from c
        where not exists (select 1 from {ref('raw_ciclo_pagamento')} k
                          where k.idpedido = c.idpedido
                            and cast(k.idsubproduto as varchar) = '{seguro}')
        order by previsto_para, idpedido""")
    fora = []
    for r in c.itertuples(index=False):
        pend = float(r.solicitado) - float(r.atendido or 0.0)
        if pend <= 0:
            continue
        prev = pd.Timestamp(r.previsto_para) if r.previsto_para is not None else None
        if prev is None:
            prev = pd.Timestamp(r.pedido_em) + pd.Timedelta(days=int(r.prazo_previsto or 0))
        atrasado = bool(prev < pd.Timestamp(hoje))
        fora.append({
            "pedido": int(r.idpedido), "fornecedor": str(r.fornecedor or ""),
            "pedido_em": str(r.pedido_em)[:10], "previsto_para": str(prev)[:10],
            "chega_em": str(pd.Timestamp(hoje) if atrasado else prev)[:10],
            "pecas": pend, "valor": pend * float(r.valor_unitario or 0.0),
            "atrasado": atrasado,
        })
    return fora


def projecao_item(wh: Warehouse, m: pd.Series, pl, p: Parametros, dia: pd.DataFrame) -> dict:
    """O grafico de venda e estoque continuado para a frente.

    Do ultimo dia com dado, dia a dia: a demanda esperada (a media corrigida
    do modelo, `demanda_media_dia`), o saldo disponivel descontado dela e
    somado ao que chega - os pedidos em aberto na data prevista e, se o plano
    manda comprar, a compra deste ciclo chegando em `lead_time_dias`. A faixa
    em volta do saldo e +-1 desvio da demanda acumulada (sd_dia * raiz(t), a
    mesma hipotese de dias independentes do bloco 8 da revisao). Os marcos:
    a recompra (proxima revisao, `periodo_revisao_dias`), o recebimento de
    uma compra feita hoje (lead time) e o fim do periodo de protecao.

    A projecao parte do estoque FISICO do ultimo dia (`estoque_fisico` do
    plano), a mesma posicao que o modelo usa, e por isso emenda na linha do
    historico sem salto. O em transito que a posicao inclui entra aqui como
    chegada na data prevista, nao no ponto de partida.
    """
    if dia.empty:
        return {}
    hoje = pd.Timestamp(dia.data.max())
    mu_dia = float(m.demanda_media_dia)
    sd_dia = float(m.desvio_padrao_dia)
    lead = int(round(float(m.lead_time_dias)))
    revisao = int(p.periodo_revisao_dias)
    protecao = int(round(float(m.periodo_protecao_dias)))
    comprar = int(pl.quantidade_a_comprar) if pl is not None else 0
    # parte do DISPONIVEL, nao da posicao: o em transito que a posicao inclui
    # entra aqui como chegada na data prevista, senao contaria duas vezes
    pos = (float(pl.estoque_fisico) if pl is not None and "estoque_fisico" in pl.index
           else float(pl.posicao_estoque) if pl is not None else float(dia.saldo_final.iloc[-1]))

    abertos = pedidos_em_aberto(wh, str(m.sku), hoje)
    ultima_chegada = max([pd.Timestamp(a["chega_em"]) for a in abertos], default=hoje)
    # ate a proxima compra chegar, e um pouco alem, para ver o consumo depois
    horizonte = max(protecao + lead, int((ultima_chegada - hoje).days) + 7, 30)
    horizonte = min(horizonte, 180)

    chega = {}
    for a in abertos:
        chega[a["chega_em"]] = chega.get(a["chega_em"], 0.0) + a["pecas"]
    data_compra_chega = str(hoje + pd.Timedelta(days=lead))[:10]

    futuro, saldo, saldo_c = [], pos, pos
    for t in range(1, horizonte + 1):
        d = hoje + pd.Timedelta(days=t)
        chave = str(d)[:10]
        entrada = chega.get(chave, 0.0)
        saldo = saldo - mu_dia + entrada
        saldo_c = saldo_c - mu_dia + entrada + (comprar if chave == data_compra_chega else 0)
        desvio = float(sd_dia * np.sqrt(t))
        futuro.append({
            "data": chave,
            "demanda_esperada": mu_dia,
            "entrada": entrada,
            "compra_plano": comprar if chave == data_compra_chega else 0,
            "saldo": max(saldo, 0.0),
            "saldo_bruto": saldo,
            "saldo_baixo": max(saldo - desvio, 0.0),
            "saldo_alto": max(saldo + desvio, 0.0),
            "saldo_com_compra": max(saldo_c, 0.0),
        })
    zera = next((f["data"] for f in futuro if f["saldo_bruto"] <= 0), None)
    return {
        "hoje": str(hoje)[:10],
        "posicao_inicial": pos,
        "demanda_dia": mu_dia, "desvio_dia": sd_dia,
        "lead_time_dias": lead, "revisao_dias": revisao, "protecao_dias": protecao,
        "marcos": {
            "recompra": str(hoje + pd.Timedelta(days=revisao))[:10],
            "recebimento": data_compra_chega,
            "fim_protecao": str(hoje + pd.Timedelta(days=protecao))[:10],
        },
        "compra_plano": comprar,
        "em_transito_na_posicao": float(pl.em_transito) if pl is not None and "em_transito" in pl.index else 0.0,
        "pedidos_abertos": abertos,
        "pecas_em_aberto": float(sum(a["pecas"] for a in abertos)),
        "zera_em": zera,
        "dias": futuro,
    }


# ----------------------------------------------------------------------
# 7. recortes agregados para o painel
# ----------------------------------------------------------------------
def falta_esperada_no_ciclo(df: pd.DataFrame) -> np.ndarray:
    """Pecas que devem faltar ate a reposicao chegar, dada a posicao de hoje.

    E[max(0, demanda no periodo de protecao - posicao atual)]. Usa as mesmas
    duas formas de contar falta que `modelo.modelar` usa - normal no regime
    continuo, Poisson no discreto - para nao criar uma terceira convencao.

    Importante: e uma falta *por ciclo*, nao por ano. Multiplicar a chance de
    ruptura de uma janela pelo lucro anual do item misturaria escalas de tempo
    e inflaria a exposicao em uma ordem de grandeza.
    """
    mu = df.mu_periodo.to_numpy(float)
    sd = df.sd_periodo.to_numpy(float)
    pos = df.posicao_estoque.fillna(0).to_numpy(float)
    discreto = df.regime.eq("Unidade marginal").to_numpy()

    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(sd > 0, (pos - mu) / sd, 0.0)
    g = stats.norm.pdf(z) - z * (1 - stats.norm.cdf(z))
    continuo = np.maximum(sd * g, 0.0)

    disc = np.array([
        max(0.0, m * (1 - stats.poisson.cdf(max(0, int(s) - 1), m))
            - int(s) * (1 - stats.poisson.cdf(int(s), m))) if m > 0 else 0.0
        for m, s in zip(mu, pos)])

    return np.where(mu > 0, np.where(discreto, disc, continuo), 0.0)


def alerta_ruptura(wh: Warehouse, limite: int = 25) -> list[dict]:
    """Itens cuja posicao ja cruzou o ponto de pedido, ordenados pela margem
    que se perde neste ciclo se nada for reposto."""
    df = plano_df(wh)
    df = df[df.posicao_estoque <= df.ponto_de_pedido].copy()
    df["falta_ciclo"] = falta_esperada_no_ciclo(df)
    df["margem_em_risco"] = df.falta_ciclo * df.lucro_por_peca
    cols = ["sku", "item", "familia", "classificacao", "curva_abc", "regime",
            "posicao_estoque", "ponto_de_pedido", "estoque_maximo", "risco_de_faltar",
            "quantidade_a_comprar", "valor_da_compra", "decisao", "margem_em_risco",
            "falta_ciclo", "cobertura_dias", "demanda_media_dia", "lucro_bruto_ano",
            "retorno_por_real"]
    cols = [c for c in cols if c in df.columns]
    return registros(df.sort_values("margem_em_risco", ascending=False).head(limite)[cols])


def cobertura_familias(wh: Warehouse) -> list[dict]:
    """Onde o capital esta e o que ele esta segurando, por familia."""
    df = modelo_df(wh)
    g = df.groupby("familia").agg(
        skus=("sku", "count"),
        capital=("capital_imobilizado", "sum"),
        lucro_ano=("lucro_bruto_ano", "sum"),
        lucro_perdido=("lucro_perdido_ruptura", "sum"),
        faltas=("faltas_esperadas_ano", "sum"),
        cobertura=("cobertura_dias", "mean"),
        nivel_servico=("nivel_servico", "mean"),
    ).reset_index()
    g["retorno"] = np.where(g.capital > 0, g.lucro_ano / g.capital, 0)
    return registros(g.sort_values("capital", ascending=False))


# ======================================================================
# O RETORNO DO DINHEIRO
# ======================================================================
# Todas as contas desta secao respondem a mesma pergunta em escalas
# diferentes: quanto volta por real aplicado. O denominador e sempre
# capital *imobilizado* (estoque medio ao custo), nunca faturamento -
# margem sobre venda mede preco, retorno sobre capital mede o negocio.

def _quadrante(capital: np.ndarray, retorno: np.ndarray,
               corte_capital: float, corte_retorno: float) -> np.ndarray:
    """Classifica cada item pelo par (capital preso, retorno sobre ele).

    Os dois cortes sao as medianas da propria carteira, nao numeros de fora.
    Tentei usar a taxa de carregamento como piso do retorno e os quatro
    quadrantes viraram dois: nesta carteira o retorno sobre capital passa de
    400% ao ano, e um piso de 25% nao separa nada. Mediana separa.
    """
    return np.select(
        [retorno <= 0,
         (capital >= corte_capital) & (retorno >= corte_retorno),
         (capital < corte_capital) & (retorno >= corte_retorno),
         (capital >= corte_capital)],
        ["Destrói valor", "Motor de lucro", "Joia pequena", "Dinheiro preso"],
        default="Cauda longa")


def _retorno_marginal(fr: pd.DataFrame, teto: float, faixas: int = 40) -> list[dict]:
    """Quanto de margem o proximo real compra, em faixas de caixa.

    A fronteira e cumulativa: dela sai a inclinacao, que e o numero que
    interessa a quem decide. Uma faixa que devolve R$ 0,15 por real nao
    esta perdendo dinheiro - esta rendendo menos que a anterior, e e esse
    decaimento que diz onde parar.
    """
    if fr.empty:
        return []
    fim = float(fr.caixa.iloc[-1])
    if fim <= 0:
        return []
    bordas = np.linspace(0.0, fim, faixas + 1)
    caixa = fr.caixa.to_numpy(float)
    margem = fr.margem.to_numpy(float)
    risco = fr.margem_em_risco.to_numpy(float)
    linhas = []
    for i in range(faixas):
        a, b = float(bordas[i]), float(bordas[i + 1])
        ma, mb = np.interp([a, b], caixa, margem)
        ra, rb = np.interp([a, b], caixa, risco)
        gasto = b - a
        if gasto <= 0:
            continue
        linhas.append({
            "de": a, "ate": b, "meio": (a + b) / 2,
            "margem_por_real": float((mb - ma) / gasto),
            "risco_cortado_por_real": float((ra - rb) / gasto),
            "margem_acumulada": float(mb),
            "retorno_acumulado": float(mb / b) if b > 0 else 0.0,
            "dentro_do_teto": bool(b <= teto + 1e-6),
        })
    return linhas


def retorno_do_capital(wh: Warehouse, p: Parametros) -> dict:
    """A conta de retorno sobre o capital, do total ao item.

    Tres olhares que precisam fechar entre si:

    1. o estoque que existe hoje - quanto de lucro por ano ele devolve
       sobre o capital que carrega;
    2. a compra deste ciclo - quanto de margem volta por real aplicado
       agora, e onde o proximo real deixa de se pagar;
    3. o item - quem sustenta o retorno, quem trava capital sem devolver
       e quem destroi valor.
    """
    # o plano ja traz o modelo inteiro mais a decisao de compra do ciclo
    df = plano_df(wh).copy()

    cap = df.capital_imobilizado.to_numpy(float)
    bruto = df.lucro_bruto_ano.to_numpy(float)
    liq = df.lucro_liquido_ano.to_numpy(float)
    manter = df.custo_manter_ano.to_numpy(float)
    pedir = df.custo_pedir_ano.to_numpy(float)
    ruptura = df.custo_ruptura_ano.to_numpy(float)

    seguro = np.where(cap > 0, cap, np.nan)
    df["retorno_capital"] = np.nan_to_num(liq / seguro)
    df["gmroi"] = np.nan_to_num(bruto / seguro)
    with np.errstate(divide="ignore", invalid="ignore"):
        df["payback_dias"] = np.where(liq > 0, cap / (liq / 365.0), np.inf)
    df["custo_total_carregar"] = manter + pedir + ruptura

    # hurdle = o piso absoluto (carregar uma peca um ano custa isso);
    # os cortes do quadrante = as medianas da carteira
    hurdle = float(p.taxa_manutencao_ano)
    ret = df.retorno_capital.to_numpy(float)
    corte_capital = float(np.median(cap[cap > 0])) if (cap > 0).any() else 0.0
    corte_retorno = float(np.median(ret[ret > 0])) if (ret > 0).any() else 0.0
    df["quadrante"] = _quadrante(cap, ret, corte_capital, corte_retorno)

    # exposicao de hoje: pecas que devem faltar antes da reposicao chegar,
    # valorizadas pela margem que cada uma levaria embora
    df["falta_ciclo"] = falta_esperada_no_ciclo(df)
    df["margem_em_risco"] = df.falta_ciclo * df.lucro_por_peca

    ciclo = df.valor_da_compra.fillna(0).to_numpy(float)
    ganho = df.margem_esperada.fillna(0).to_numpy(float)
    df["retorno_ciclo"] = np.divide(ganho, ciclo, out=np.zeros_like(ganho),
                                    where=ciclo > 0)

    # ---- a cascata: de onde o lucro vem e por onde escapa
    cascata = [
        {"etapa": "Lucro bruto do estoque", "valor": float(bruto.sum()), "tipo": "entra"},
        {"etapa": "Custo de manter parado", "valor": -float(manter.sum()), "tipo": "sai"},
        {"etapa": "Custo de pedir", "valor": -float(pedir.sum()), "tipo": "sai"},
        {"etapa": "Lucro perdido por faltar", "valor": -float(ruptura.sum()), "tipo": "sai"},
        {"etapa": "Lucro líquido", "valor": float(liq.sum()), "tipo": "fecha"},
    ]

    # ---- o retorno marginal do proximo real: onde o dinheiro para de pagar
    fr = wh.query(f"select posicao_fila, caixa, margem, margem_em_risco, pecas, "
                  f"nota, p_vender from {ref('res_fronteira')} order by caixa")
    marginal = _retorno_marginal(fr, float(p.teto_compra_ciclo))

    ct = float(cap.sum())
    total = {
        "capital": ct,
        "lucro_bruto": float(bruto.sum()),
        "custo_manter": float(manter.sum()),
        "custo_pedir": float(pedir.sum()),
        "custo_ruptura": float(ruptura.sum()),
        "lucro_liquido": float(liq.sum()),
        "retorno_capital": float(liq.sum() / ct) if ct else 0.0,
        "gmroi": float(bruto.sum() / ct) if ct else 0.0,
        "payback_dias": (float(ct / (liq.sum() / 365.0)) if liq.sum() > 0 else None),
        "hurdle": hurdle,
        "custo_por_pedido": float(p.custo_por_pedido),
        "pedidos_ano": float(df.pedidos_por_ano.sum()),
        "teto_ciclo": float(p.teto_compra_ciclo),
        "corte_capital": corte_capital,
        "corte_retorno": corte_retorno,
        "giro_medio": float(np.average(df.giro_ano.to_numpy(float),
                                       weights=np.where(cap > 0, cap, 1e-9))),
        "margem_em_risco": float(df.margem_em_risco.sum()),
        "compra_ciclo": float(ciclo.sum()),
        "margem_ciclo": float(ganho.sum()),
        "retorno_ciclo": float(ganho.sum() / ciclo.sum()) if ciclo.sum() else 0.0,
        "itens_na_compra": int((ciclo > 0).sum()),
        "skus": int(len(df)),
        "destroem_valor": int((ret <= 0).sum()),
        "capital_destruidor": float(cap[ret <= 0].sum()),
        "custo_destruidor": float(df.custo_total_carregar.to_numpy(float)[ret <= 0].sum()),
        "abaixo_do_hurdle": int(((ret > 0) & (ret < hurdle)).sum()),
        "capital_abaixo_hurdle": float(cap[(ret > 0) & (ret < hurdle)].sum()),
    }
    # quanto do lucro liquido vem dos 20% de capital mais produtivo
    ordem = df.sort_values("retorno_capital", ascending=False)
    corte20 = ordem.capital_imobilizado.cumsum() <= ct * 0.2
    total["capital_dos_20pct"] = float(ordem.capital_imobilizado[corte20].sum())
    total["lucro_dos_20pct"] = float(ordem.lucro_liquido_ano[corte20].sum())
    total["parte_dos_20pct"] = (total["lucro_dos_20pct"] / total["lucro_liquido"]
                               if total["lucro_liquido"] else 0.0)

    # ---- por quadrante, para a leitura de carteira
    q = df.groupby("quadrante").agg(
        skus=("sku", "count"), capital=("capital_imobilizado", "sum"),
        lucro_liquido=("lucro_liquido_ano", "sum"),
        lucro_bruto=("lucro_bruto_ano", "sum"),
        custo=("custo_total_carregar", "sum"),
        risco=("margem_em_risco", "sum"),
    ).reset_index()
    q["retorno"] = np.where(q.capital > 0, q.lucro_liquido / q.capital, 0.0)
    q["parte_capital"] = q.capital / ct if ct else 0.0

    cols = ["sku", "item", "familia", "classificacao", "curva_abc", "regime",
            "capital_imobilizado", "estoque_medio", "custo_unitario",
            "lucro_por_peca", "margem_pct", "pecas_vendidas", "demanda_anual",
            "lucro_bruto_ano", "custo_manter_ano", "custo_pedir_ano",
            "custo_ruptura_ano", "custo_total_carregar", "lucro_liquido_ano",
            "retorno_capital", "gmroi", "payback_dias", "giro_ano",
            "cobertura_dias", "quadrante", "margem_em_risco", "falta_ciclo",
            "quantidade_a_comprar", "valor_da_compra", "margem_esperada",
            "retorno_ciclo", "lucro_perdido_ruptura"]
    cols = [c for c in cols if c in df.columns]
    itens = df[cols].replace([np.inf, -np.inf], np.nan)

    return {
        "total": total,
        "cascata": cascata,
        "marginal": marginal,
        "quadrantes": registros(q.sort_values("capital", ascending=False)),
        "itens": registros(itens.sort_values("lucro_liquido_ano", ascending=False)),
    }


# ----------------------------------------------------------------------
# ABC x XYZ por empresa (loja)
# ----------------------------------------------------------------------
def abc_xyz_por_loja(wh: Warehouse, p: Parametros, janela: int = 365) -> dict:
    """Classifica cada item DENTRO de cada loja, so com a venda daquela loja.

    ABC pela participacao no lucro da loja na janela, com os mesmos cortes do
    modelo (corte_curva_a / corte_curva_b); XYZ pelo coeficiente de variacao
    da venda DIARIA da loja, contando os dias sem venda como zero, com os
    mesmos cortes (corte_xyz_x / corte_xyz_y).

    Premissa a deixar clara: nao ha correcao de ruptura aqui. O estoque
    diario que o modelo tem e o do CD, nao o de cada loja, entao um item que
    faltou muito na loja parece mais erratico do que e. A classe "no CD" que
    acompanha cada item e a do modelo (res_sku_modelo), essa sim corrigida.
    """
    fim = f"(select max(data) from {ref('mart_estoque_diario')})"
    base = wh.query(f"""
        with d as (
            select loja_id, loja, sku, data,
                   sum(pecas_vendidas) as pecas, sum(lucro) as lucro,
                   sum(receita_liquida) as receita
            from {ref('stg_vendas')}
            where loja_id is not null
              and data <= {fim} and data > {fim} - INTERVAL {int(janela)} DAY
            group by 1, 2, 3, 4)
        select loja_id, sku,
               sum(pecas) as pecas, sum(lucro) as lucro, sum(receita) as receita,
               sum(pecas * pecas) as soma_quadrados,
               count(*) as dias_com_venda
        from d group by 1, 2""")
    nomes = wh.query(f"""
        select loja_id, loja, count(*) n from {ref('stg_vendas')}
        where loja_id is not null group by 1, 2""")
    # o ERP grava a mesma loja com grafias diferentes e caracteres de
    # controle no nome; fica a grafia mais frequente, limpa
    nome_por_loja = {k: "".join(ch for ch in str(v) if ch.isprintable()).strip()
                     for k, v in (nomes.sort_values("n", ascending=False)
                                  .drop_duplicates("loja_id").set_index("loja_id")
                                  .loja.to_dict()).items()}
    cd = wh.query(f"select sku, item, familia, curva_abc, classe_xyz, classificacao "
                  f"from {ref('res_sku_modelo')}")

    n = float(janela)
    # CV da venda diaria com os dias de zero: var = (soma(x^2) - n*media^2)/(n-1)
    media = base.pecas / n
    var = (base.soma_quadrados - n * media ** 2) / (n - 1)
    base["cv"] = np.where(media > 0, np.sqrt(var.clip(lower=0)) / media, np.nan)
    base["classe_xyz"] = np.where(base.cv < p.corte_xyz_x, "X",
                          np.where(base.cv < p.corte_xyz_y, "Y", "Z"))

    lojas, detalhe = [], {}
    for loja_id, g in base.groupby("loja_id"):
        g = g.sort_values("lucro", ascending=False).reset_index(drop=True)
        total = float(g.lucro.clip(lower=0).sum())
        acum = g.lucro.clip(lower=0).cumsum() / total if total > 0 else pd.Series(1.0, index=g.index)
        g["curva_abc"] = np.where(acum <= p.corte_curva_a, "A",
                          np.where(acum <= p.corte_curva_b, "B", "C"))
        # lucro zero ou negativo nunca e A: fica no fim da fila
        g.loc[g.lucro <= 0, "curva_abc"] = "C"
        g["classificacao"] = g.curva_abc + g.classe_xyz
        g = g.merge(cd.rename(columns={"curva_abc": "abc_cd", "classe_xyz": "xyz_cd",
                                       "classificacao": "classe_cd"}), on="sku", how="left")
        nA = int((g.curva_abc == "A").sum())
        lojas.append(dict(
            loja_id=str(loja_id), loja=nome_por_loja.get(loja_id, f"Loja {loja_id}"),
            skus=int(len(g)), pecas=float(g.pecas.sum()), lucro=float(g.lucro.sum()),
            itens_a=nA, pct_itens_a=nA / max(len(g), 1),
            a_na_loja_c_no_cd=int(((g.curva_abc == "A") & (g.abc_cd == "C")).sum()),
            a_na_loja_a_no_cd=int(((g.curva_abc == "A") & (g.abc_cd == "A")).sum()),
        ))
        celulas = (g.groupby(["curva_abc", "classe_xyz"])
                   .agg(skus=("sku", "count"), lucro=("lucro", "sum"), pecas=("pecas", "sum"))
                   .reset_index())
        cruz = (g.dropna(subset=["abc_cd"]).groupby(["curva_abc", "abc_cd"])
                .agg(skus=("sku", "count"), lucro=("lucro", "sum")).reset_index())
        # quem nao tem classe no CD nunca teve estoque la; e contado a parte
        divergentes = g[(g.curva_abc == "A") & g.abc_cd.notna() & (g.abc_cd != "A")].head(12)
        detalhe[str(loja_id)] = dict(
            celulas=registros(celulas), cruzamento=registros(cruz),
            divergentes=registros(divergentes[["sku", "item", "familia", "classificacao",
                                               "classe_cd", "lucro", "pecas", "cv"]]),
            sem_classe_cd=int(g.abc_cd.isna().sum()),
        )
    lojas.sort(key=lambda x: -x["lucro"])
    return dict(janela_dias=int(janela), cortes=dict(
        abc_a=p.corte_curva_a, abc_b=p.corte_curva_b, xyz_x=p.corte_xyz_x, xyz_y=p.corte_xyz_y),
        lojas=lojas, detalhe=detalhe)


# ----------------------------------------------------------------------
# Rateio da compra do ciclo por empresa
# ----------------------------------------------------------------------
def rateio_por_loja(wh: Warehouse, janela: int = 365) -> dict:
    """Quanto da compra decidida cabe a cada empresa do grupo.

    A compra e UMA, decidida pela demanda somada sobre o estoque do CD. O
    rateio divide as pecas e o dinheiro de cada SKU pela participacao de cada
    loja na demanda DAQUELE SKU na janela - a venda observada, sem correcao de
    ruptura por loja. E aditivo: a soma das fatias fecha com o plano. Um SKU
    comprado sem venda em nenhuma loja na janela fica em `sem_venda`.

    A fatia de cada canal vem do plano (`valor_da_compra_ecommerce` / `_lojas`);
    a venda por loja so reparte a fatia das lojas entre elas.
    """
    fim = f"(select max(data) from {ref('mart_estoque_diario')})"
    v = wh.query(f"""
        select loja_id, sku, sum(pecas_vendidas) as pecas
        from {ref('stg_vendas')}
        where loja_id is not null
          and data <= {fim} and data > {fim} - INTERVAL {int(janela)} DAY
        group by 1, 2""")
    nomes = wh.query(f"""
        select loja_id, loja, count(*) n from {ref('stg_vendas')}
        where loja_id is not null group by 1, 2""")
    nome = {k: "".join(ch for ch in str(x) if ch.isprintable()).strip()
            for k, x in (nomes.sort_values("n", ascending=False).drop_duplicates("loja_id")
                         .set_index("loja_id").loja.to_dict()).items()}
    plano = wh.query(f"""
        select sku, quantidade_a_comprar as q, valor_da_compra as valor,
               coalesce(valor_da_compra_ecommerce, 0.0)              as valor_e,
               coalesce(valor_da_compra_lojas, valor_da_compra)      as valor_l
        from {ref('res_plano_compra')} where quantidade_a_comprar > 0""")

    # A fatia de cada CANAL vem do proprio plano (e a que o motor cobrou dos
    # dois caixas). A venda observada por loja so reparte a fatia das lojas
    # ENTRE as lojas - assim o rateio nunca discorda do plano por canal.
    v["ecom"] = v.loja_id.astype(str).eq("33")
    tot_c = v.groupby(["sku", "ecom"]).pecas.sum().rename("total_canal").reset_index()
    v = v.merge(tot_c, on=["sku", "ecom"])
    v["participacao_canal"] = v.pecas / v.total_canal
    r = v.merge(plano, on="sku", how="inner")
    frac_e = np.where(r.valor > 0, r.valor_e / r.valor.replace(0, np.nan), 0.0)
    r["valor_canal"] = np.where(r.ecom, r.valor_e, r.valor_l)
    r["pecas_canal"] = np.where(r.ecom, r.q * frac_e, r.q * (1.0 - frac_e))
    r["valor_rateado"] = r.valor_canal * r.participacao_canal
    r["pecas_rateadas"] = r.pecas_canal * r.participacao_canal
    r["participacao"] = np.where(r.valor > 0, r.valor_rateado / r.valor.replace(0, np.nan), 0.0)

    com_venda = set(r.sku)
    sem = plano[~plano.sku.isin(com_venda)]

    lojas = (r.groupby("loja_id")
             .agg(pecas=("pecas_rateadas", "sum"), valor=("valor_rateado", "sum"),
                  itens=("sku", "count"))
             .reset_index().sort_values("valor", ascending=False))
    lojas["loja"] = lojas.loja_id.map(lambda k: nome.get(k, f"Loja {k}"))
    detalhe = {}
    for loja_id, g in r.groupby("loja_id"):
        detalhe[str(loja_id)] = {
            row.sku: dict(participacao=float(row.participacao),
                          pecas=float(row.pecas_rateadas), valor=float(row.valor_rateado))
            for row in g.itertuples(index=False)}
    canais = {
        "ecommerce": dict(valor=float(plano.valor_e.sum()),
                          pecas=float((plano.q * np.where(plano.valor > 0, plano.valor_e / plano.valor.replace(0, np.nan), 0.0)).sum()),
                          itens=int((plano.valor_e > 0).sum())),
        "lojas": dict(valor=float(plano.valor_l.sum()),
                      pecas=float((plano.q * np.where(plano.valor > 0, plano.valor_l / plano.valor.replace(0, np.nan), 0.0)).sum()),
                      itens=int((plano.valor_l > 0).sum())),
    }
    return dict(
        janela_dias=int(janela),
        total_valor=float(plano.valor.sum()), total_pecas=float(plano.q.sum()),
        sem_venda=dict(itens=int(len(sem)), pecas=float(sem.q.sum()), valor=float(sem.valor.sum())),
        canais=canais,
        lojas=registros(lojas[["loja_id", "loja", "itens", "pecas", "valor"]]),
        detalhe=detalhe)
