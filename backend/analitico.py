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

from .config import Parametros
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
        ("fator_perda_ruptura", "Fator de perda na ruptura", "pct"),
        ("margem_unit", "Margem capturada  ·  M = lucro × fator", "brl2"),
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
        ("nota", "NOTA  ·  V / (c × peças) / H", "num6"),
    ]),
    ("7. Decisão do caixa", [
        ("teto_ciclo", "Caixa do ciclo", "brl"),
        ("caixa_antes", "Já gasto quando esta linha foi avaliada", "brl"),
        ("caixa_restante", "Caixa restante depois desta linha", "brl"),
        ("caixa_acumulado", "Total gasto até aqui", "brl"),
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
        f"select data, saldo_inicial, saldo_final, pecas_vendidas, estado_estoque "
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
def proxima_peca(m: pd.Series, p: Parametros, pos: float) -> dict | None:
    """A conta de uma unica peca: a proxima que se pensa em comprar."""
    if float(m.mu_periodo) <= 0 or float(m.custo_unitario) <= 0:
        return None
    _, dist, _, _ = ajustar_distribuicao(float(m.mu_periodo), float(m.sd_periodo))
    cu = float(m.custo_falta_unit)
    perda = float(m.custo_manter_no_periodo) + float(m.custo_unitario) * p.perda_encalhe
    horizonte = float(m.periodo_protecao_dias)
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
    horizonte = float(m.periodo_protecao_dias)
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

    return {
        "item": linha(m),
        "plano": linha(pl) if pl is not None else None,
        "escada": escada,
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
        "dias": dias,
        "resumo_dias": {
            "disponivel": int(disponivel.sum()),
            "ruptura_parcial": int(censurado.sum()),
            "sem_estoque": int(sem.sum()),
            "total": int(len(dia)),
        },
        "distribuicao": dist,
        "marginal": marginal,
        "seguranca": seguranca,
        "parametros": {
            "periodo_revisao_dias": p.periodo_revisao_dias,
            "taxa_manutencao_ano": p.taxa_manutencao_ano,
            "custo_por_pedido": p.custo_por_pedido,
            "fator_perda_ruptura": p.fator_perda_ruptura,
            "limiar_giro_baixo": p.limiar_giro_baixo,
            "dias_por_ano": p.dias_por_ano,
        },
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
