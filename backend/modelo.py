# -*- coding: utf-8 -*-
"""
Motor de calculo do planejamento de estoque.

O dbt entrega as agregacoes; aqui entra o que SQL nao faz bem:

  1. Correcao de censura (EM) - o dia em que o estoque acabou no meio nao e
     uma observacao de demanda, e um piso. Imputar e reestimar ate convergir.
  2. Ajuste da distribuicao da demanda no periodo de protecao.
  3. Escolha do regime: EOQ + normal para giro relevante, teste da unidade
     marginal para item caro que vende pouco.
  4. Preco-sombra do capital: encarece o dinheiro ate o estoque caber no teto.
  5. Plano de compra com corte pelo caixa do ciclo.
"""
from __future__ import annotations

from dataclasses import asdict

import numpy as np
import pandas as pd
from scipy import stats
from scipy.optimize import brentq

from .config import Parametros
from .warehouse import Warehouse, ref

MAX_UNIDADES_MARGINAIS = 60


# ----------------------------------------------------------------------
# 1. Correcao de censura
# ----------------------------------------------------------------------
def em_censurado(v: np.ndarray, ok: np.ndarray, cens: np.ndarray,
                 imputar: bool = True, iters: int = 25, cap_q: float = 0.95):
    """Estima a taxa de demanda tratando os dias de ruptura parcial como
    observacoes censuradas a direita.

    Retorna (media, desvio, n_dias_censurados, pecas_imputadas).
    """
    vo = v[ok]
    n_c = int(cens.sum())
    if len(vo) < 2:
        return (float(vo.mean()) if len(vo) else 0.0), 0.0, n_c, 0.0
    if n_c == 0 or not imputar:
        return float(vo.mean()), float(vo.std(ddof=1)), n_c, 0.0

    obs = v[cens].astype(int)
    m, s2 = float(vo.mean()), float(vo.var(ddof=1))
    imp = obs.astype(float)
    for _ in range(iters):
        if m <= 0:
            break
        if s2 > m * 1.05:
            r = m * m / (s2 - m)
            d = stats.nbinom(r, r / (r + m))
        else:
            d = stats.poisson(m)
        cap = int(max(30, d.ppf(0.99999) + 10))
        k = np.arange(cap + 1)
        pk = d.pmf(k)
        num = np.cumsum((k * pk)[::-1])[::-1]
        den = np.cumsum(pk[::-1])[::-1]
        idx = np.clip(obs, 0, cap)
        novo = np.where(den[idx] > 1e-12, num[idx] / den[idx], obs.astype(float))
        novo = np.maximum(np.minimum(novo, d.ppf(cap_q)), obs)
        todos = np.concatenate([vo, novo])
        m_novo, s2 = float(todos.mean()), float(todos.var(ddof=1))
        convergiu = abs(m_novo - m) < 1e-8 * max(m, 1.0)
        m, imp = m_novo, novo
        if convergiu:
            break
    return m, float(np.sqrt(max(s2, 0.0))), n_c, float((imp - obs).sum())


def estatistica_demanda(diario: pd.DataFrame, p: Parametros) -> pd.DataFrame:
    """Media e desvio da demanda diaria por SKU, nas tres versoes."""
    g = diario.pivot_table(index="sku", columns="data", values="pecas_vendidas",
                           aggfunc="sum").fillna(0.0)
    est = diario.pivot_table(index="sku", columns="data", values="estado_estoque",
                             aggfunc="first")
    est = est.reindex(index=g.index, columns=g.columns)
    V = g.to_numpy(float)
    OK = (est == "Disponivel").to_numpy()
    CENS = (est == "Ruptura parcial").to_numpy()
    SEM = (est == "Sem estoque").to_numpy()

    if not p.corrigir_censura:
        OK = OK | CENS | SEM        # volta ao metodo ingenuo, de proposito
        CENS = np.zeros_like(CENS)

    linhas = []
    for i, sku in enumerate(g.index):
        m, s, n_c, imp = em_censurado(V[i], OK[i], CENS[i], p.imputar_dias_censurados)
        usaveis = int(OK[i].sum() + CENS[i].sum())
        linhas.append(dict(
            sku=sku,
            demanda_media_dia=m,
            desvio_padrao_dia=s,
            dias_utilizaveis=usaveis,
            dias_sem_estoque=int(SEM[i].sum()) if p.corrigir_censura else 0,
            dias_ruptura_parcial=n_c,
            pecas_imputadas=round(imp, 2),
            demanda_media_dia_ingenua=float(V[i].mean()),
            desvio_padrao_dia_ingenuo=float(V[i].std(ddof=1)),
            demanda_media_dia_disponivel=float(V[i][OK[i]].mean()) if OK[i].sum() else 0.0,
            demanda_max_dia=float(V[i].max()),
            dias_com_venda=int((V[i] > 0).sum()),
            dias_historico=V.shape[1],
        ))
    return pd.DataFrame(linhas)


# ----------------------------------------------------------------------
# 2. Distribuicao no periodo de protecao
# ----------------------------------------------------------------------
def ajustar_distribuicao(mu: float, sd: float):
    """Binomial negativa quando a variancia supera a media; Poisson caso
    contrario. Devolve (nome, objeto scipy, r, p)."""
    if mu <= 0:
        return "Sem historico", stats.poisson(1e-9), np.nan, np.nan
    var = sd ** 2
    if var <= mu * 1.05:
        return "Poisson", stats.poisson(mu), np.nan, np.nan
    r = mu * mu / (var - mu)
    prob = r / (r + mu)
    return "Binomial Negativa", stats.nbinom(r, prob), r, prob


# ----------------------------------------------------------------------
# 3. Modelo de estoque
# ----------------------------------------------------------------------
def modelar(base: pd.DataFrame, p: Parametros, lam: float) -> pd.DataFrame:
    """Aplica os dois regimes com um dado premio de escassez `lam`."""
    b = base
    D = b.demanda_media_dia * p.dias_por_ano
    c = b.custo_unitario
    h_decisao = c * (p.taxa_manutencao_ano + lam)
    h_real = c * p.taxa_manutencao_ano
    Cu = b.lucro_por_peca * p.fator_perda_ruptura
    P = b.periodo_protecao_dias
    Co = c * (p.taxa_manutencao_ano + lam) * P / p.dias_por_ano
    limite = np.where((Cu + Co) > 0, Co / (Cu + Co), 1.0)

    mu = b.mu_periodo.to_numpy()
    sd = b.sd_periodo.to_numpy()

    # --- regime EOQ + normal ---
    with np.errstate(divide="ignore", invalid="ignore"):
        eoq = np.sqrt(2 * D * p.custo_por_pedido / h_decisao.replace(0, np.nan))
    eoq = np.nan_to_num(eoq, nan=0.0, posinf=0.0)
    Q_n = np.ceil(np.maximum(eoq, b.lote_minimo_compra))
    with np.errstate(divide="ignore", invalid="ignore"):
        ns_n = 1 - (Q_n * h_decisao) / (D * Cu)
    ns_n = np.clip(np.nan_to_num(ns_n, nan=p.nivel_servico_min),
                   p.nivel_servico_min, p.nivel_servico_max)
    z = stats.norm.ppf(ns_n)
    es_n = np.ceil(z * sd)
    rop_n = np.ceil(mu + es_n)

    # --- regime discreto: teste da unidade marginal ---
    K = np.arange(1, MAX_UNIDADES_MARGINAIS + 1)
    s_disc = np.zeros(len(b), dtype=int)
    for i in range(len(b)):
        if mu[i] <= 0:
            continue
        s_disc[i] = int((1 - stats.poisson.cdf(K - 1, mu[i]) > limite[i]).sum())

    lento = (mu < p.limiar_giro_baixo) & (mu > 0)
    sem_hist = b.pecas_vendidas.to_numpy() <= 0

    rop = np.where(lento, s_disc, rop_n)
    Q = np.where(lento, np.maximum(1, b.lote_minimo_compra), Q_n)
    es = np.where(lento, np.maximum(0, s_disc - mu), es_n)
    emax = np.where(lento, s_disc, rop + Q)
    emed = np.where(lento, s_disc / 2.0, es_n + Q_n / 2.0)
    emed = np.where(sem_hist, 0.0, emed)
    rop = np.where(sem_hist, 0, rop)

    # vetorizado de proposito: congelar uma Poisson por SKU aqui custava
    # segundos, porque o solver do premio de escassez chama `modelar` varias vezes
    mu_pos = np.where(mu > 0, mu, 1.0)
    ns_disc = np.where(mu > 0, stats.poisson.cdf(np.maximum(0, rop - 1), mu_pos), 1.0)
    ns = np.where(lento, ns_disc, ns_n)

    # ciclos e faltas
    with np.errstate(divide="ignore", invalid="ignore"):
        ciclos_n = np.where(Q_n > 0, D / Q_n, 0)
    ciclos_l = p.dias_por_ano / max(p.periodo_revisao_dias, 1) * (
        1 - stats.poisson.cdf(0, b.demanda_media_dia * p.periodo_revisao_dias))
    ciclos = np.where(lento, ciclos_l, ciclos_n)

    Gz = stats.norm.pdf(z) - z * (1 - stats.norm.cdf(z))
    falta_n = sd * Gz
    falta_l = np.array([
        max(0.0, m * (1 - stats.poisson.cdf(max(0, s - 1), m)) - s * (1 - stats.poisson.cdf(s, m)))
        if m > 0 else 0.0 for m, s in zip(mu, rop)])
    faltas_ano = np.where(lento, falta_l, falta_n) * ciclos
    faltas_ano = np.where(sem_hist, 0.0, faltas_ano)

    custo_manter = emed * h_real
    custo_pedir = ciclos * p.custo_por_pedido
    custo_falta = faltas_ano * Cu
    lucro_bruto = D * b.lucro_por_peca

    out = b.copy()
    out["regime"] = np.where(lento, "Unidade marginal", "EOQ + normal")
    out["premio_escassez"] = lam
    out["demanda_anual"] = D
    out["custo_manter_unit_decisao"] = h_decisao
    out["custo_manter_unit_real"] = h_real
    out["custo_falta_unit"] = Cu
    out["custo_manter_no_periodo"] = Co
    out["limite_marginal"] = limite
    out["unidades_marginais"] = s_disc
    out["eoq"] = eoq
    out["rop_se_fosse_normal"] = rop_n
    out["lote_compra"] = Q
    out["nivel_servico"] = ns
    out["z"] = z
    out["estoque_seguranca"] = es
    out["ponto_de_pedido"] = rop
    out["estoque_maximo"] = emax
    out["estoque_medio"] = emed
    out["capital_imobilizado"] = emed * c
    out["cobertura_dias"] = np.where(b.demanda_media_dia > 0, emed / b.demanda_media_dia, 0)
    out["giro_ano"] = np.where(emed > 0, D / emed, 0)
    out["pedidos_por_ano"] = ciclos
    out["faltas_esperadas_ano"] = faltas_ano
    out["custo_manter_ano"] = custo_manter
    out["custo_pedir_ano"] = custo_pedir
    out["custo_ruptura_ano"] = custo_falta
    out["custo_total_ano"] = custo_manter + custo_pedir + custo_falta
    out["lucro_bruto_ano"] = lucro_bruto
    out["lucro_liquido_ano"] = lucro_bruto - out["custo_total_ano"]
    return out


def resolver_premio_escassez(base: pd.DataFrame, p: Parametros) -> float:
    """Sobe o preco-sombra do capital ate o estoque caber no teto."""
    if not p.aplicar_teto_capital:
        return 0.0

    def folga(lam: float) -> float:
        return float(modelar(base, p, lam).capital_imobilizado.sum()) - p.teto_capital

    if folga(0.0) <= 0:
        return 0.0
    try:
        return float(brentq(folga, 0.0, 50.0, xtol=1e-5))
    except ValueError:
        return 50.0


# ----------------------------------------------------------------------
# 4. Classificacao
# ----------------------------------------------------------------------
def classificar(b: pd.DataFrame, p: Parametros) -> pd.DataFrame:
    b = b.sort_values("lucro_potencial_periodo", ascending=False).reset_index(drop=True)
    total = b.lucro_potencial_periodo.sum()
    b["participacao_lucro"] = b.lucro_potencial_periodo / total if total else 0
    b["lucro_acumulado_pct"] = b.lucro_potencial_periodo.cumsum() / total if total else 0
    b["curva_abc"] = np.where(b.lucro_acumulado_pct <= p.corte_curva_a, "A",
                       np.where(b.lucro_acumulado_pct <= p.corte_curva_b, "B", "C"))
    b["classe_xyz"] = np.where(b.cv_diario < p.corte_xyz_x, "X",
                        np.where(b.cv_diario < p.corte_xyz_y, "Y", "Z"))
    b["classificacao"] = b.curva_abc + b.classe_xyz
    return b


# ----------------------------------------------------------------------
# 5. Plano de compra
# ----------------------------------------------------------------------
def plano_compra(modelo: pd.DataFrame, posicoes: pd.DataFrame, p: Parametros) -> pd.DataFrame:
    df = modelo.merge(posicoes, on="sku", how="left")
    df["estoque_fisico"] = df.estoque_fisico.fillna(0)
    df["em_transito"] = df.get("em_transito", pd.Series(0, index=df.index)).fillna(0)
    df["posicao_estoque"] = df.estoque_fisico + df.em_transito
    df["precisa_comprar"] = np.where(df.posicao_estoque <= df.ponto_de_pedido, "SIM", "NAO")
    bruto = df.estoque_maximo - df.posicao_estoque
    df["quantidade_a_comprar"] = np.where(
        df.precisa_comprar.eq("SIM") & (bruto > 0),
        np.maximum(df.lote_minimo_compra, np.ceil(bruto)), 0).astype(int)
    df["valor_da_compra"] = df.quantidade_a_comprar * df.custo_unitario
    df["retorno_por_real"] = np.where(df.valor_da_compra > 0,
                                      df.lucro_bruto_ano / df.valor_da_compra, -1.0)

    # Corte pelo caixa, descendo a lista por retorno por real. O item que nao
    # cabe no que sobrou e *pulado*, e a fila continua - a mesma regra que a
    # alocacao marginal usa. Sem isso a comparacao entre as duas estrategias
    # seria injusta: parte da vantagem da marginal viria so de uma regra de
    # corte melhor, e nao da logica de avaliar peca por peca.
    df = df.sort_values("retorno_por_real", ascending=False).reset_index(drop=True)
    val = df.valor_da_compra.to_numpy(float)
    dentro = np.zeros(len(df), dtype=bool)
    acumulado = np.zeros(len(df), dtype=float)
    restante, gasto = float(p.teto_compra_ciclo), 0.0
    for i in range(len(df)):
        if val[i] > 0 and val[i] <= restante:
            dentro[i] = True
            restante -= val[i]
            gasto += val[i]
        acumulado[i] = gasto
    df["capital_acumulado"] = acumulado
    df["decisao"] = np.where(
        df.valor_da_compra <= 0, "nao precisa",
        np.where(dentro, "COMPRAR AGORA", "SEGURAR - fora do teto"))

    aprovado = df.decisao.eq("COMPRAR AGORA")
    pos_final = df.posicao_estoque + np.where(aprovado, df.quantidade_a_comprar, 0)
    df["cobertura_apos_dias"] = np.where(df.demanda_media_dia > 0,
                                         pos_final / df.demanda_media_dia, 0)
    risco = []
    for _, r in df.iterrows():
        if r.mu_periodo <= 0:
            risco.append(0.0); continue
        if r.regime == "Unidade marginal":
            risco.append(float(1 - stats.poisson(r.mu_periodo).cdf(r.posicao_estoque)))
        else:
            risco.append(float(1 - stats.norm(r.mu_periodo, max(r.sd_periodo, 1e-9))
                               .cdf(r.posicao_estoque)))
    df["risco_de_faltar"] = risco
    df["prioridade"] = df.retorno_por_real.rank(ascending=False, method="min").astype(int)
    return df.sort_values("retorno_por_real", ascending=False).reset_index(drop=True)


# ----------------------------------------------------------------------
# 5b. Alocacao marginal do caixa - unidade a unidade, entre todos os itens
#
# A diferenca em relacao ao plano de reposicao acima e o que se pergunta.
#
#   Reposicao:  "de quais itens eu preciso, e quanto falta para cada um
#                chegar ao estoque ideal?"  -> enche poucos itens ate o topo
#                e o caixa acaba antes de olhar o resto do catalogo.
#
#   Marginal:   "de todas as proximas pecas possiveis do catalogo inteiro,
#                qual e a que mais rende por real e por dia?"  -> compra essa,
#                e refaz a pergunta. A 1a e a 2a peca podem ser do mesmo item;
#                a 3a ja tende a ser de outro, porque a chance de vender a
#                3a peca daquele item ja caiu.
#
# Como a chance de vender a k-esima peca cai a cada peca, o retorno marginal
# de um item decresce sozinho - e o caixa se espalha naturalmente por muitos
# produtos, sem precisar de nenhuma regra artificial de diversificacao.
# ----------------------------------------------------------------------
MAX_UNIDADES_POR_ITEM = 6000


def candidatas_marginais(df: pd.DataFrame, p: Parametros) -> pd.DataFrame:
    """Uma linha por bloco de unidades candidatas, item a item.

    Para a k-esima peca de um item, dentro do horizonte de protecao:

        P            = P(demanda no horizonte >= k)      chance de ela vender
        valor        = P x Cu - (1 - P) x perda
        nota         = valor / custo unitario / horizonte

    `Cu` e a margem que a peca captura se vender (ja descontada pelo fator de
    perda na ruptura) e `perda` e o que ela custa se ficar parada: o custo de
    carregar durante o horizonte mais a fracao do custo que se perde no
    encalhe. A nota divide por custo e por horizonte para que itens de precos
    e prazos diferentes possam ser comparados na mesma regua - um item caro de
    lead time longo prende muito mais capital por real de margem.

    A tabela devolvida e a trilha de auditoria do plano: cada linha carrega
    todos os valores intermediarios que entraram na conta, do dado de demanda
    ate a nota final, de forma que qualquer coluna possa ser refeita a mao a
    partir das anteriores.
    """
    partes = []
    for r in df.itertuples(index=False):
        if r.mu_periodo <= 0 or r.custo_unitario <= 0:
            continue
        nome_dist, dist, nb_r, nb_p = ajustar_distribuicao(r.mu_periodo, r.sd_periodo)

        Cu = float(r.custo_falta_unit)
        obsolescencia = float(r.custo_unitario) * p.perda_encalhe
        perda = float(r.custo_manter_no_periodo) + obsolescencia
        if Cu + perda <= 0:
            continue

        # valor >= 0  <=>  P >= perda / (Cu + perda). Alem desse ponto a peca
        # so encalha, entao nao vale nem expandir a lista de candidatas.
        limite = perda / (Cu + perda)
        k_max = int(np.floor(dist.ppf(min(max(1 - limite, 0.0), 0.999999)))) + 1

        pos = int(max(0, round(float(r.posicao_estoque))))
        lote = int(max(1, r.lote_minimo_compra)) if p.respeitar_lote_minimo else 1
        n = int(min(max(k_max - pos, lote), MAX_UNIDADES_POR_ITEM))
        if n <= 0:
            continue

        k = np.arange(pos + 1, pos + n + 1)
        cdf_ant = dist.cdf(k - 1)              # P(demanda <= k-1)
        pv = 1.0 - cdf_ant                     # P(demanda >= k)
        ve = pv * Cu - (1.0 - pv) * perda

        # o primeiro bloco tem o tamanho do lote minimo (e o que da para
        # comprar de verdade); do lote minimo em diante, peca a peca
        inicios = np.array([0] + list(range(lote, n)), dtype=int)
        fins = np.append(inicios[1:], n)
        valor = np.add.reduceat(ve, inicios)
        # ve e decrescente: assim que um bloco fica negativo, os seguintes tambem
        bons = valor > 0
        corte = int(np.argmin(bons)) if not bons.all() else len(inicios)
        if corte == 0:
            continue
        inicios, fins, valor = inicios[:corte], fins[:corte], valor[:corte]

        qtd = (fins - inicios).astype(int)
        custo = qtd * float(r.custo_unitario)
        horizonte = float(r.periodo_protecao_dias)
        var_periodo = float(r.sd_periodo) ** 2

        partes.append(pd.DataFrame({
            # ---- identificacao
            "sku": r.sku, "item": r.item, "familia": r.familia,
            "curva_abc": r.curva_abc, "classe_xyz": getattr(r, "classe_xyz", ""),
            "classificacao": r.classificacao, "regime": getattr(r, "regime", ""),
            "bloco": np.arange(corte),
            "unidade_de": k[inicios], "unidade_ate": k[fins - 1], "quantidade": qtd,
            # ---- 1. demanda observada e corrigida
            "posicao_estoque": pos,
            "dias_historico": float(getattr(r, "dias_historico", np.nan)),
            "dias_sem_estoque": float(getattr(r, "dias_sem_estoque", np.nan)),
            "dias_ruptura_parcial": float(getattr(r, "dias_ruptura_parcial", np.nan)),
            "pecas_imputadas": float(getattr(r, "pecas_imputadas", np.nan)),
            "demanda_dia_ingenua": float(getattr(r, "demanda_media_dia_ingenua", np.nan)),
            "demanda_dia_corrigida": float(r.demanda_media_dia),
            "desvio_dia": float(r.desvio_padrao_dia),
            "subestimacao_pct": float(getattr(r, "subestimacao_ingenua_pct", np.nan)),
            # ---- 2. horizonte
            "lead_time_dias": float(r.lead_time_dias),
            "periodo_revisao_dias": float(p.periodo_revisao_dias),
            "horizonte": horizonte,
            # ---- 3. distribuicao no horizonte
            "distribuicao": nome_dist,
            "mu_periodo": float(r.mu_periodo),
            "sd_periodo": float(r.sd_periodo),
            "variancia_periodo": var_periodo,
            "razao_var_media": var_periodo / float(r.mu_periodo) if r.mu_periodo else np.nan,
            "nb_r": float(nb_r) if nb_r == nb_r else np.nan,
            "nb_p": float(nb_p) if nb_p == nb_p else np.nan,
            # ---- 4. probabilidade da peca
            "cdf_ate_k_menos_1": cdf_ant[inicios],
            "p_vender": pv[inicios],
            "p_encalhar": cdf_ant[inicios],
            "p_vender_ultima": pv[fins - 1],
            # ---- 5. economia unitaria
            "lucro_por_peca": float(r.lucro_por_peca),
            "fator_perda_ruptura": float(p.fator_perda_ruptura),
            "margem_unit": Cu,
            "custo_unitario": float(r.custo_unitario),
            "taxa_manutencao_ano": float(p.taxa_manutencao_ano),
            "premio_escassez": float(getattr(r, "premio_escassez", 0.0)),
            "custo_manter_no_periodo": float(r.custo_manter_no_periodo),
            "perda_encalhe_pct": float(p.perda_encalhe),
            "custo_obsolescencia": obsolescencia,
            "perda_unit": perda,
            "limite_marginal_compra": limite,
            # ---- 6. valor da peca
            "ganho_esperado": pv[inicios] * Cu * qtd,
            "custo_esperado": (1.0 - pv[inicios]) * perda * qtd,
            "valor_esperado": valor,
            "valor_por_real": valor / custo,
            "custo": custo,
            "nota": valor / (custo * horizonte),
        }))

    if not partes:
        return pd.DataFrame()
    return pd.concat(partes, ignore_index=True)


def valor_esperado_da_compra(df: pd.DataFrame, quantidades, p: Parametros) -> np.ndarray:
    """Valor esperado de comprar `q` unidades de cada item, dada a posicao atual.

    Existe para que qualquer estrategia de compra possa ser medida na mesma
    regua. Comparar estrategias por "quantidade x margem" premia quem compra
    mais, mesmo que a maioria das pecas fique encalhada: aqui cada peca so
    conta a margem na proporcao da chance de ela realmente vender, e paga o
    encalhe na proporcao contraria.
    """
    fora = []
    for r, q in zip(df.itertuples(index=False), quantidades):
        q = int(q)
        if q <= 0 or r.mu_periodo <= 0:
            fora.append(0.0)
            continue
        _, dist, _, _ = ajustar_distribuicao(float(r.mu_periodo), float(r.sd_periodo))
        Cu = float(r.custo_falta_unit)
        perda = float(r.custo_manter_no_periodo) + float(r.custo_unitario) * p.perda_encalhe
        pos = int(max(0, round(float(r.posicao_estoque))))
        k = np.arange(pos + 1, pos + q + 1)
        pv = 1.0 - dist.cdf(k - 1)
        fora.append(float((pv * Cu - (1.0 - pv) * perda).sum()))
    return np.array(fora)


def pecas_com_baixa_chance(df: pd.DataFrame, quantidades, corte: float = 0.5) -> int:
    """Quantas das pecas compradas tem menos de `corte` de chance de vender.

    E a medida direta de empilhamento: peca comprada que provavelmente vai
    ficar parada ate o proximo ciclo."""
    total = 0
    for r, q in zip(df.itertuples(index=False), quantidades):
        q = int(q)
        if q <= 0 or r.mu_periodo <= 0:
            continue
        _, dist, _, _ = ajustar_distribuicao(float(r.mu_periodo), float(r.sd_periodo))
        pos = int(max(0, round(float(r.posicao_estoque))))
        k = np.arange(pos + 1, pos + q + 1)
        total += int(((1.0 - dist.cdf(k - 1)) < corte).sum())
    return total


def alocacao_marginal(df: pd.DataFrame, p: Parametros) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Desce a fila de unidades comprando enquanto o caixa do ciclo aguentar.

    Um bloco que nao cabe no caixa restante e *pulado*, nao encerra a fila -
    assim o troco ainda compra as unidades baratas que vem logo abaixo.

    Devolve (fila, por_item).
    """
    fila = candidatas_marginais(df, p)
    if fila.empty:
        return fila, pd.DataFrame(columns=["sku"])

    fila = fila.sort_values("nota", ascending=False, kind="mergesort").reset_index(drop=True)
    fila["posicao_fila"] = fila.index + 1

    custo = fila.custo.to_numpy(float)
    skus = fila.sku.to_numpy()
    blocos = fila.bloco.to_numpy(int)
    qtds = fila.quantidade.to_numpy(int)
    valores = fila.valor_esperado.to_numpy(float)

    n = len(fila)
    comprado = np.zeros(n, dtype=bool)
    antes = np.zeros(n, dtype=float)        # caixa ja gasto quando a linha e avaliada
    acumulado = np.zeros(n, dtype=float)    # caixa gasto depois de decidir a linha
    sobra = np.zeros(n, dtype=float)
    pecas_ac = np.zeros(n, dtype=int)
    valor_ac = np.zeros(n, dtype=float)
    motivo = np.empty(n, dtype=object)

    ultimo_bloco: dict = {}
    teto = float(p.teto_compra_ciclo)
    restante, gasto, pecas, ganho = teto, 0.0, 0, 0.0

    for i in range(n):
        s, b = skus[i], blocos[i]
        antes[i] = gasto
        # o bloco k so pode ser comprado se o k-1 do mesmo item ja foi -
        # nao da para comprar a 90a peca sem ter comprado as 89 anteriores
        depende = b > 0 and ultimo_bloco.get(s, -1) != b - 1
        cabe = custo[i] <= restante
        if depende and not cabe:
            motivo[i] = "caixa ja esgotado quando chegou a vez dela"
        elif depende:
            motivo[i] = "bloqueada: a peca anterior deste item nao entrou"
        elif not cabe:
            motivo[i] = "nao coube no caixa restante"
        else:
            comprado[i] = True
            restante -= custo[i]
            gasto += custo[i]
            pecas += int(qtds[i])
            ganho += float(valores[i])
            ultimo_bloco[s] = b
            motivo[i] = "comprada"
        acumulado[i] = gasto
        sobra[i] = restante
        pecas_ac[i] = pecas
        valor_ac[i] = ganho

    fila["comprar"] = comprado
    fila["motivo"] = motivo
    fila["caixa_antes"] = antes
    fila["caixa_acumulado"] = acumulado
    fila["caixa_restante"] = sobra
    fila["pecas_acumuladas"] = pecas_ac
    fila["valor_acumulado"] = valor_ac
    fila["teto_ciclo"] = teto

    compradas = fila[fila.comprar]
    por_item = compradas.groupby("sku").agg(
        quantidade_a_comprar=("quantidade", "sum"),
        valor_da_compra=("custo", "sum"),
        margem_esperada=("valor_esperado", "sum"),
        blocos_comprados=("bloco", "count"),
        ultima_unidade=("unidade_ate", "max"),
        p_vender_ultima=("p_vender_ultima", "min"),
    ).reset_index()

    disponiveis = fila.groupby("sku").agg(
        unidades_com_retorno=("quantidade", "sum"),
        melhor_nota=("nota", "max"),
        valor_total_disponivel=("valor_esperado", "sum"),
        custo_total_disponivel=("custo", "sum"),
    ).reset_index()

    return fila, disponiveis.merge(por_item, on="sku", how="left")


def plano_marginal(df: pd.DataFrame, p: Parametros) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Monta o plano de compra por item a partir da alocacao marginal."""
    fila, por_item = alocacao_marginal(df, p)
    plano = df.merge(por_item, on="sku", how="left")

    for col, padrao in [("quantidade_a_comprar", 0), ("valor_da_compra", 0.0),
                        ("margem_esperada", 0.0), ("blocos_comprados", 0),
                        ("unidades_com_retorno", 0), ("valor_total_disponivel", 0.0),
                        ("custo_total_disponivel", 0.0), ("melhor_nota", 0.0)]:
        plano[col] = plano.get(col, padrao)
        plano[col] = plano[col].fillna(padrao)
    plano["quantidade_a_comprar"] = plano.quantidade_a_comprar.astype(int)

    plano["posicao_final"] = plano.posicao_estoque + plano.quantidade_a_comprar
    plano["retorno_por_real"] = np.where(
        plano.valor_da_compra > 0, plano.margem_esperada / plano.valor_da_compra, 0.0)
    plano["retorno_dia"] = np.where(
        plano.valor_da_compra > 0,
        plano.retorno_por_real / plano.periodo_protecao_dias, 0.0)

    plano["decisao"] = np.where(
        plano.quantidade_a_comprar > 0, "COMPRAR AGORA",
        np.where(plano.unidades_com_retorno > 0,
                 "FORA DO TETO", "NAO COMPENSA"))
    plano["precisa_comprar"] = np.where(plano.unidades_com_retorno > 0, "SIM", "NAO")

    # risco de faltar: antes e depois da compra deste ciclo
    def risco(posicoes: pd.Series) -> list[float]:
        fora = []
        for m, s, x in zip(plano.mu_periodo, plano.sd_periodo, posicoes):
            if m <= 0:
                fora.append(0.0)
                continue
            _, dist, _, _ = ajustar_distribuicao(float(m), float(s))
            fora.append(float(1 - dist.cdf(float(x))))
        return fora

    plano["risco_de_faltar"] = risco(plano.posicao_estoque)
    plano["risco_apos_compra"] = risco(plano.posicao_final)
    plano["cobertura_apos_dias"] = np.where(
        plano.demanda_media_dia > 0, plano.posicao_final / plano.demanda_media_dia, 0)
    plano["prioridade"] = plano.melhor_nota.rank(ascending=False, method="min").astype(int)
    return plano.sort_values("melhor_nota", ascending=False).reset_index(drop=True), fila


# ----------------------------------------------------------------------
# 6. Tabela de probabilidade
# ----------------------------------------------------------------------
def tabela_probabilidade(modelo: pd.DataFrame, pontos: int = 30) -> pd.DataFrame:
    """Curva de probabilidade por SKU. Fora do pipeline desde que a fila
    marginal passou a gravar P(vender) de cada peca - que e mais fino e
    custava 6s por execucao aqui. Mantida para uso avulso."""
    linhas = []
    for _, r in modelo.iterrows():
        if r.mu_periodo <= 0:
            continue
        nome, obj, _, _ = ajustar_distribuicao(r.mu_periodo, r.sd_periodo)
        xmax = int(max(4, obj.ppf(0.999)))
        passo = max(1, int(np.ceil(xmax / pontos)))
        for x in range(0, xmax + passo, passo):
            linhas.append(dict(
                sku=r.sku, item=r.item, curva_abc=r.curva_abc, regime=r.regime,
                periodo_protecao_dias=int(r.periodo_protecao_dias), distribuicao=nome,
                x_pecas=x, p_vender_ate_x=float(obj.cdf(x)),
                p_vender_mais_de_x=float(1 - obj.cdf(x))))
    return pd.DataFrame(linhas)


# ----------------------------------------------------------------------
# 7. Orquestracao
# ----------------------------------------------------------------------
def executar(wh: Warehouse, p: Parametros) -> dict:
    """Le os marts, roda o modelo e devolve os quadros prontos para gravar."""
    fin = wh.query(f"select * from {ref('mart_sku_financeiro')}")
    diario = wh.query(
        f"select sku, data, pecas_vendidas, estado_estoque, saldo_final "
        f"from {ref('mart_estoque_diario')}")

    est = estatistica_demanda(diario, p)
    b = fin.merge(est, on="sku", how="left").fillna({"demanda_media_dia": 0.0})

    b["periodo_protecao_dias"] = b.lead_time_dias + p.periodo_revisao_dias
    b["mu_periodo"] = b.demanda_media_dia * b.periodo_protecao_dias
    # raiz(H) supoe dias independentes. Quando a demanda tem reversao a media,
    # essa conta superestima a variacao no horizonte e infla o estoque de
    # seguranca; `fator_desvio_horizonte` permite corrigir com o valor medido
    # em scripts/revisao.py. Fica em 1,00 por padrao - a hipotese conservadora.
    b["sd_periodo"] = (b.desvio_padrao_dia * np.sqrt(b.periodo_protecao_dias)
                       * p.fator_desvio_horizonte)
    b["cv_diario"] = np.where(b.demanda_media_dia > 0,
                              b.desvio_padrao_dia / b.demanda_media_dia, 0)
    b["cv_periodo"] = np.where(b.mu_periodo > 0, b.sd_periodo / b.mu_periodo, 0)
    b["pct_indisponivel"] = b.dias_sem_estoque / b.dias_historico
    b["subestimacao_ingenua_pct"] = np.where(
        b.demanda_media_dia_ingenua > 0,
        b.demanda_media_dia / b.demanda_media_dia_ingenua - 1, 0)
    b["venda_perdida_pecas"] = (b.demanda_media_dia * b.dias_sem_estoque).round(0)
    b["lucro_perdido_ruptura"] = b.venda_perdida_pecas * b.lucro_por_peca
    b["lucro_potencial_periodo"] = b.demanda_media_dia * b.dias_historico * b.lucro_por_peca

    dist = [ajustar_distribuicao(m, s) for m, s in zip(b.mu_periodo, b.sd_periodo)]
    b["distribuicao"] = [d[0] for d in dist]
    for nome, q in [("p50", .5), ("p75", .75), ("p90", .9), ("p95", .95), ("p99", .99)]:
        b[nome] = [float(d[1].ppf(q)) for d in dist]

    b = classificar(b, p)

    lam = resolver_premio_escassez(b, p)
    modelo = modelar(b, p, lam)
    irrestrito = modelar(b, p, 0.0)

    ultimo = diario.data.max()
    posicoes = (diario[diario.data == ultimo][["sku", "saldo_final"]]
                .rename(columns={"saldo_final": "estoque_fisico"}))
    posicoes["em_transito"] = 0.0

    # duas formas de gastar o mesmo caixa do ciclo, para poder comparar:
    #   reposicao  - enche item por item ate o estoque ideal (concentra)
    #   marginal   - compra a melhor proxima peca do catalogo inteiro (espalha)
    reposicao = plano_compra(modelo, posicoes, p)
    plano, fila = plano_marginal(
        reposicao.drop(columns=["decisao", "precisa_comprar", "quantidade_a_comprar",
                                "valor_da_compra", "retorno_por_real", "prioridade",
                                "risco_de_faltar", "cobertura_apos_dias",
                                "capital_acumulado"], errors="ignore"), p)

    def resumo(m: pd.DataFrame, rotulo: str) -> dict:
        return dict(politica=rotulo,
                    capital_imobilizado=float(m.capital_imobilizado.sum()),
                    custo_manter_ano=float(m.custo_manter_ano.sum()),
                    custo_pedir_ano=float(m.custo_pedir_ano.sum()),
                    custo_ruptura_ano=float(m.custo_ruptura_ano.sum()),
                    custo_total_ano=float(m.custo_total_ano.sum()),
                    faltas_esperadas_ano=float(m.faltas_esperadas_ano.sum()),
                    nivel_servico_medio=float(m.nivel_servico.mean()),
                    lucro_liquido_ano=float(m.lucro_liquido_ano.sum()))

    # politica de referencia: compra mensal e folga fixa de 50% da demanda no lead time
    ref_ = b.copy()
    Q_ref = np.maximum(1, ref_.demanda_media_dia * 30)
    es_ref = np.ceil(ref_.demanda_media_dia * ref_.lead_time_dias * 0.5)
    emed_ref = es_ref + Q_ref / 2
    z_ref = np.where(ref_.sd_periodo > 0, es_ref / ref_.sd_periodo, 0)
    G_ref = stats.norm.pdf(z_ref) - z_ref * (1 - stats.norm.cdf(z_ref))
    ciclos_ref = np.where(Q_ref > 0, ref_.demanda_media_dia * p.dias_por_ano / Q_ref, 0)
    falta_ref = ref_.sd_periodo * G_ref * ciclos_ref
    Cu_ref = ref_.lucro_por_peca * p.fator_perda_ruptura
    h_ref = ref_.custo_unitario * p.taxa_manutencao_ano
    atual = dict(politica="Politica atual (compra mensal, folga fixa)",
                 capital_imobilizado=float((emed_ref * ref_.custo_unitario).sum()),
                 custo_manter_ano=float((emed_ref * h_ref).sum()),
                 custo_pedir_ano=float((ciclos_ref * p.custo_por_pedido).sum()),
                 custo_ruptura_ano=float((falta_ref * Cu_ref).sum()),
                 faltas_esperadas_ano=float(falta_ref.sum()),
                 nivel_servico_medio=float(stats.norm.cdf(z_ref).mean()),
                 lucro_liquido_ano=0.0)
    atual["custo_total_ano"] = (atual["custo_manter_ano"] + atual["custo_pedir_ano"]
                                + atual["custo_ruptura_ano"])
    atual["lucro_liquido_ano"] = float(modelo.lucro_bruto_ano.sum()) - atual["custo_total_ano"]

    comparativo = pd.DataFrame([
        atual,
        resumo(irrestrito, "Otima SEM teto de capital"),
        resumo(modelo, "Otima COM teto de capital"),
    ])

    # as duas formas de gastar o mesmo caixa, medidas na mesma regua
    base_cmp = plano.sort_values("sku").reset_index(drop=True)
    q_rep = (reposicao.sort_values("sku").reset_index(drop=True)
             .quantidade_a_comprar.where(
                 reposicao.sort_values("sku").reset_index(drop=True)
                 .decisao.eq("COMPRAR AGORA"), 0).to_numpy())
    q_mar = base_cmp.quantidade_a_comprar.to_numpy()

    def medir(rotulo: str, q) -> dict:
        ve = valor_esperado_da_compra(base_cmp, q, p)
        investido = float((q * base_cmp.custo_unitario).sum())
        itens = int((q > 0).sum())
        # espalhar a compra por mais itens abre mais linhas de pedido, e cada
        # uma custa. Sem isso a comparacao favoreceria a diversificacao de graca.
        custo_pedidos = itens * p.custo_por_pedido
        return dict(
            estrategia=rotulo,
            itens_atendidos=itens,
            pct_catalogo=float(itens / max(len(base_cmp), 1)),
            pecas=int(q.sum()),
            investimento=investido,
            valor_esperado=float(ve.sum()),
            custo_pedidos=custo_pedidos,
            valor_liquido=float(ve.sum()) - custo_pedidos,
            retorno_por_real=(float(ve.sum()) - custo_pedidos) / investido if investido else 0.0,
            pecas_baixa_chance=pecas_com_baixa_chance(base_cmp, q),
        )

    estrategias = pd.DataFrame([
        medir("Repor ate o estoque ideal", q_rep),
        medir("Alocacao marginal peca a peca", q_mar),
    ])
    rep_compra = reposicao[reposicao.decisao.eq("COMPRAR AGORA")]
    mar_compra = plano[plano.quantidade_a_comprar > 0]

    execucao = pd.DataFrame([dict(
        executado_em=pd.Timestamp.now(),
        premio_escassez=lam,
        skus=len(modelo),
        dias_historico=int(b.dias_historico.max()),
        itens_regime_discreto=int((modelo.regime == "Unidade marginal").sum()),
        capital_total=float(modelo.capital_imobilizado.sum()),
        lucro_perdido_ruptura=float(b.lucro_perdido_ruptura.sum()),
        itens_na_compra=int(len(mar_compra)),
        itens_na_compra_reposicao=int(len(rep_compra)),
        unidades_avaliadas=int(fila.quantidade.sum()) if len(fila) else 0,
        **{f"param_{k}": v for k, v in asdict(p).items()},
    )])

    return dict(res_sku_modelo=modelo, res_plano_compra=plano,
                res_fila_marginal=fila, res_plano_reposicao=reposicao,
                res_estrategias=estrategias,
                res_comparativo=comparativo, res_execucao=execucao)


def gravar_resultados(wh: Warehouse, resultados: dict) -> None:
    """Persiste as tabelas de resultado no warehouse (mesmo motor dos marts)."""
    for nome, df in resultados.items():
        wh.gravar(df, nome)
