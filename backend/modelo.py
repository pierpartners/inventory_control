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

    # A venda por canal na MESMA grade (mesmos SKUs, mesmos dias). As mascaras
    # de dia sao as do estoque compartilhado: quando o CD zera, os dois canais
    # ficam censurados juntos - por isso o formato largo, e nao uma serie por
    # canal com censura propria.
    def grade(col: str) -> np.ndarray:
        if col not in diario.columns:
            return np.zeros_like(V)
        t = diario.pivot_table(index="sku", columns="data", values=col, aggfunc="sum")
        return t.reindex(index=g.index, columns=g.columns).fillna(0.0).to_numpy(float)
    VE = grade("pecas_ecommerce")
    VL = V - VE

    OK = (est == "Disponivel").to_numpy()
    CENS = (est == "Ruptura parcial").to_numpy()
    SEM = (est == "Sem estoque").to_numpy()

    if not p.corrigir_censura:
        OK = OK | CENS | SEM        # volta ao metodo ingenuo, de proposito
        CENS = np.zeros_like(CENS)

    piso = int(getattr(p, "dias_utilizaveis_minimo", 0) or 0)
    linhas = []
    for i, sku in enumerate(g.index):
        m, s, n_c, imp = em_censurado(V[i], OK[i], CENS[i], p.imputar_dias_censurados)
        usaveis = int(OK[i].sum() + CENS[i].sum())
        m_em, s_em = m, s
        # Piso de historico. A correcao de censura divide a venda pelos dias
        # utilizaveis; com poucos deles, uma venda isolada vira uma taxa
        # enorme (5 dias, 100 pecas num deles = 20/dia num item que vende
        # duas vezes por ano). Abaixo do piso o item usa a media ingenua e
        # fica marcado, para a tela dizer que o numero e o simples.
        insuficiente = bool(p.corrigir_censura and piso > 0 and usaveis < piso)
        if insuficiente:
            m = float(V[i].mean())
            s = float(V[i].std(ddof=1)) if V.shape[1] > 1 else 0.0

        # os dois canais pelo mesmo caminho do total, com as mesmas mascaras
        m_e, s_e, _, _ = em_censurado(VE[i], OK[i], CENS[i], p.imputar_dias_censurados)
        m_l, s_l, _, _ = em_censurado(VL[i], OK[i], CENS[i], p.imputar_dias_censurados)
        if insuficiente:
            m_e = float(VE[i].mean())
            m_l = float(VL[i].mean())
            s_e = float(VE[i].std(ddof=1)) if V.shape[1] > 1 else 0.0
            s_l = float(VL[i].std(ddof=1)) if V.shape[1] > 1 else 0.0
        soma = m_e + m_l
        share = m_e / soma if soma > 0 else 0.0
        # covariancia entre canais so nos dias em que os dois podiam vender;
        # e o que o bloco 8 da revisao usa para decompor a variancia do total
        dias_ok = OK[i]
        cov = float(np.cov(VE[i][dias_ok], VL[i][dias_ok])[0, 1]) if dias_ok.sum() >= 2 else 0.0

        linhas.append(dict(
            sku=sku,
            demanda_media_dia=m,
            desvio_padrao_dia=s,
            demanda_media_dia_em=m_em,
            historico_insuficiente=insuficiente,
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
            demanda_media_dia_ecommerce=m_e,
            desvio_padrao_dia_ecommerce=s_e,
            demanda_media_dia_lojas=m_l,
            desvio_padrao_dia_lojas=s_l,
            demanda_media_dia_ingenua_ecommerce=float(VE[i].mean()),
            demanda_media_dia_ingenua_lojas=float(VL[i].mean()),
            share_ecommerce=share,
            covariancia_canais=cov,
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

    # --- duas contagens diferentes, que antes eram a mesma ---
    #
    # JANELAS DE RISCO por ano: quantas vezes por ano o item fica exposto a
    # faltar. No regime continuo isso acontece uma vez por reposicao (D/Q);
    # no discreto, a cada revisao em que houve alguma demanda.
    #
    # PEDIDOS por ano: quantas vezes um pedido e efetivamente colocado, que
    # e o que custa o parametro custo_por_pedido. E D/Q nos dois regimes -
    # so nao pode
    # passar do numero de revisoes, porque nao se pede fora da revisao.
    #
    # Contar um pedido por janela de revisao no regime discreto inflava o
    # custo de pedir dos itens de giro baixo em ~4x: o lote minimo do
    # fornecedor cobre varios meses de demanda, e o modelo cobrava como se
    # ele fosse comprado toda semana. Isso jogava 13 itens para lucro
    # negativo que na verdade se pagam.
    with np.errstate(divide="ignore", invalid="ignore"):
        ciclos_n = np.where(Q_n > 0, D / Q_n, 0)
        fisico = np.where(Q > 0, D / Q, 0.0)
    revisoes = p.dias_por_ano / max(p.periodo_revisao_dias, 1) * (
        1 - stats.poisson.cdf(0, b.demanda_media_dia * p.periodo_revisao_dias))
    ciclos = np.where(lento, revisoes, ciclos_n)          # janelas de risco
    pedidos = np.where(lento, np.minimum(fisico, revisoes), ciclos_n)

    Gz = stats.norm.pdf(z) - z * (1 - stats.norm.cdf(z))
    falta_n = sd * Gz
    falta_l = np.array([
        max(0.0, m * (1 - stats.poisson.cdf(max(0, s - 1), m)) - s * (1 - stats.poisson.cdf(s, m)))
        if m > 0 else 0.0 for m, s in zip(mu, rop)])
    faltas_ano = np.where(lento, falta_l, falta_n) * ciclos
    faltas_ano = np.where(sem_hist, 0.0, faltas_ano)

    custo_manter = emed * h_real
    custo_pedir = pedidos * p.custo_por_pedido
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
    out["pedidos_por_ano"] = pedidos
    out["janelas_de_risco_ano"] = ciclos
    out["faltas_esperadas_ano"] = faltas_ano
    out["custo_manter_ano"] = custo_manter
    out["custo_pedir_ano"] = custo_pedir
    out["custo_ruptura_ano"] = custo_falta
    out["custo_total_ano"] = custo_manter + custo_pedir + custo_falta
    out["lucro_bruto_ano"] = lucro_bruto
    out["lucro_liquido_ano"] = lucro_bruto - out["custo_total_ano"]
    return out


def resolver_premio_escassez(base: pd.DataFrame, p: Parametros) -> float:
    """Sobe o preco-sombra do capital ate o estoque caber no teto.

    O capital em funcao de lambda e uma escada: cada degrau e uma peca inteira
    saindo do plano. Nao existe lambda que faca a soma bater exatamente no
    teto, e por isso a busca aqui nao procura a igualdade - procura o menor
    lambda que ja CABE, e devolve esse.

    A diferenca importa. Uma raiz por troca de sinal (brentq) pode parar do
    lado de cima de um degrau e devolver um plano de R$ 2.000.203 para um teto
    de R$ 2.000.000: dentro da tolerancia numerica, fora do caixa.
    """
    if not p.aplicar_teto_capital:
        return 0.0

    def capital(lam: float) -> float:
        return float(modelar(base, p, lam).capital_imobilizado.sum())

    if capital(0.0) <= p.teto_capital:
        return 0.0

    # acha um lambda que cabe, dobrando. O limite de 50 e o mesmo de antes:
    # acima disso o capital ja esta tao caro que o plano e praticamente vazio.
    alto = 0.05
    while alto <= 50.0 and capital(alto) > p.teto_capital:
        alto *= 2.0
    if alto > 50.0:
        return 50.0

    # bisseccao guardando a ponta de cima sempre viavel
    baixo = 0.0
    for _ in range(40):
        meio = 0.5 * (baixo + alto)
        if alto - baixo < 1e-6:
            break
        if capital(meio) > p.teto_capital:
            baixo = meio
        else:
            alto = meio
    return float(alto)


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
            # sem esta coluna nao da para refazer sigma por fora: a
            # variancia do horizonte agora tem dois termos
            "desvio_prazo_dias": float(getattr(r, "sd_lead_time_dias", 0.0) or 0.0),
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
            # a margem que a venda observou, com o custo do dia de cada venda.
            # Fica ao lado da refeita porque a divergencia entre as duas e o
            # sinal de item com custo em movimento - e o que a tela de
            # conferencia mostra.
            "lucro_por_peca_historico": float(
                getattr(r, "lucro_por_peca_historico", r.lucro_por_peca)),
            "preco_liquido_peca": float(getattr(r, "preco_liquido_peca", 0.0)),
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
            # quanto esta linha tira da falta esperada. E exato, nao aproximado:
            #   E[max(0, D - pos)] = soma de P(D >= k) para todo k > pos
            # entao comprar a peca k reduz a falta esperada em exatamente P(D >= k).
            # E o que torna a fronteira de risco uma soma acumulada simples.
            "reducao_falta": np.add.reduceat(pv, inicios)[:corte],
            "reducao_risco": np.add.reduceat(pv, inicios)[:corte] * Cu,
            "valor_esperado": valor,
            "valor_por_real": valor / custo,
            "custo": custo,
            "nota": valor / (custo * horizonte),
        }))

    if not partes:
        return pd.DataFrame()
    return pd.concat(partes, ignore_index=True)


def falta_esperada(df: pd.DataFrame, posicoes=None) -> np.ndarray:
    """Pecas que devem faltar no horizonte, por item, dada uma posicao.

        E[max(0, D - pos)] = soma de P(D >= k) para todo k > pos

    Calculada com a propria distribuicao ajustada (nao com aproximacao
    normal), porque e a base do criterio de parada por risco: se este numero
    nao fechar com a soma das reducoes por peca, a fronteira mente.
    """
    if posicoes is None:
        posicoes = df.posicao_estoque
    fora = []
    for r, pos in zip(df.itertuples(index=False), posicoes):
        if r.mu_periodo <= 0:
            fora.append(0.0)
            continue
        _, dist, _, _ = ajustar_distribuicao(float(r.mu_periodo), float(r.sd_periodo))
        base = int(max(0, round(float(pos))))
        topo = int(max(dist.ppf(0.999999), base + 1))
        k = np.arange(base + 1, topo + 2)
        fora.append(float((1.0 - dist.cdf(k - 1)).sum()))
    return np.array(fora)


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


ORDEM_CRITERIOS = ["caixa", "retorno", "chance", "risco"]


def criterios_ativos(p: Parametros, criterio=None) -> list[str]:
    """Le a lista de criterios ligados, na ordem canonica.

    O campo aceita um so ("caixa") ou vários ("caixa,chance") - a compra e
    cortada pelo primeiro que chegar, que na pratica e o mais restritivo dos
    ligados. Lista vazia volta para o caixa, que e o unico limite fisico.
    """
    if criterio is None:
        criterio = getattr(p, "criterio_parada", "caixa")
    if isinstance(criterio, str):
        pedidos = [x.strip() for x in criterio.split(",")]
    else:
        pedidos = [str(x).strip() for x in criterio]
    ativos = [c for c in ORDEM_CRITERIOS if c in pedidos]
    return ativos or ["caixa"]


def regra_de_parada(p: Parametros, criterio=None) -> dict:
    """Traduz os parametros na regra que corta a fila.

    Todos os criterios rodam sobre a MESMA fila ordenada por retorno por real
    por dia, e podem ser combinados. Dois deles so tornam parte das pecas
    inelegiveis (piso de retorno, piso de chance); um corta pelo dinheiro; e o
    de risco encerra a fila quando a margem exposta desce ao alvo.

    Com mais de um ligado a peca precisa passar por TODOS - o corte acontece
    onde o primeiro deles fecha a porta.
    """
    ativos = criterios_ativos(p, criterio)
    return {
        "criterios": ativos,
        "criterio": ",".join(ativos),
        "piso_retorno": float(p.retorno_minimo_dia) if "retorno" in ativos else 0.0,
        "piso_chance": float(p.chance_minima_peca) if "chance" in ativos else 0.0,
        "alvo_risco": float(p.teto_margem_em_risco) if "risco" in ativos else None,
        "teto": float(p.teto_compra_ciclo),
        # sem o criterio de caixa ligado o dinheiro nao limita: ele e o numero
        # que sai no fim (e o que o criterio por risco faz)
        "caixa_limita": "caixa" in ativos,
    }


def caminhar(fila: pd.DataFrame, regra: dict,
             risco_inicial: float, falta_inicial: float) -> dict:
    """Desce a fila aplicando a regra de parada, peca por peca.

    Um bloco que nao cabe no caixa restante e *pulado*, nao encerra a fila -
    assim o troco ainda compra as unidades baratas que vem logo abaixo.
    """
    custo = fila.custo.to_numpy(float)
    skus = fila.sku.to_numpy()
    blocos = fila.bloco.to_numpy(int)
    qtds = fila.quantidade.to_numpy(int)
    valores = fila.valor_esperado.to_numpy(float)
    red_risco = fila.reducao_risco.to_numpy(float)
    red_falta = fila.reducao_falta.to_numpy(float)

    n = len(fila)
    # cada criterio de piso vira uma mascara; a peca precisa passar em todas
    piso_ret, piso_chc = regra.get("piso_retorno", 0.0), regra.get("piso_chance", 0.0)
    passa_retorno = (fila.nota.to_numpy(float) >= piso_ret if piso_ret > 0
                     else np.ones(n, dtype=bool))
    passa_chance = (fila.p_vender.to_numpy(float) >= piso_chc if piso_chc > 0
                    else np.ones(n, dtype=bool))
    alvo_risco = regra["alvo_risco"]
    teto = regra["teto"]

    comprado = np.zeros(n, dtype=bool)
    antes = np.zeros(n, dtype=float)
    acumulado = np.zeros(n, dtype=float)
    sobra = np.zeros(n, dtype=float)
    pecas_ac = np.zeros(n, dtype=int)
    valor_ac = np.zeros(n, dtype=float)
    risco_ac = np.zeros(n, dtype=float)
    falta_ac = np.zeros(n, dtype=float)
    motivo = np.empty(n, dtype=object)

    ultimo_bloco: dict = {}
    restante = teto if regra["caixa_limita"] else np.inf
    gasto, pecas, ganho = 0.0, 0, 0.0
    risco, falta = risco_inicial, falta_inicial

    for i in range(n):
        s, b = skus[i], blocos[i]
        antes[i] = gasto
        # o bloco k so pode ser comprado se o k-1 do mesmo item ja foi -
        # nao da para comprar a 90a peca sem ter comprado as 89 anteriores
        depende = b > 0 and ultimo_bloco.get(s, -1) != b - 1
        cabe = custo[i] <= restante

        # a ordem de teste e a ordem do relato: o motivo mostrado e o do
        # primeiro criterio que fechou a porta para esta peca
        if alvo_risco is not None and risco <= alvo_risco:
            motivo[i] = "risco assumido ja alcancado"
        elif not passa_retorno[i]:
            motivo[i] = "abaixo do piso de retorno"
        elif not passa_chance[i]:
            motivo[i] = "abaixo do piso de chance de vender"
        elif depende and not cabe:
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
            risco -= float(red_risco[i])
            falta -= float(red_falta[i])
            ultimo_bloco[s] = b
            motivo[i] = "comprada"

        acumulado[i] = gasto
        sobra[i] = teto - gasto        # sempre contra o caixa do ciclo, para leitura
        pecas_ac[i] = pecas
        valor_ac[i] = ganho
        risco_ac[i] = risco
        falta_ac[i] = falta

    return {
        "comprar": comprado, "motivo": motivo,
        "caixa_antes": antes, "caixa_acumulado": acumulado, "caixa_restante": sobra,
        "pecas_acumuladas": pecas_ac, "valor_acumulado": valor_ac,
        "margem_em_risco_restante": risco_ac, "falta_restante": falta_ac,
    }


def fronteira(fila: pd.DataFrame, risco_inicial: float, falta_inicial: float,
              pontos: int = 1500) -> pd.DataFrame:
    """A curva completa: se a fila fosse cortada aqui, com que numeros eu ficaria.

    Calculada SEM nenhuma restricao - e a mesma curva para os quatro criterios,
    e cada criterio e so um ponto sobre ela. Se a curva mudasse de forma ao
    trocar de criterio, nao daria para comparar os cortes.
    """
    f = fila.sort_values("posicao_fila")
    curva = pd.DataFrame({
        "posicao_fila": f.posicao_fila.to_numpy(),
        "caixa": f.custo.cumsum().to_numpy(),
        "pecas": f.quantidade.cumsum().to_numpy(),
        "margem": f.valor_esperado.cumsum().to_numpy(),
        "margem_em_risco": risco_inicial - f.reducao_risco.cumsum().to_numpy(),
        "falta": falta_inicial - f.reducao_falta.cumsum().to_numpy(),
        "nota": f.nota.to_numpy(),
        "p_vender": f.p_vender.to_numpy(),
        "itens": (~f.sku.duplicated()).cumsum().to_numpy(),
    })
    # A amostragem tem de ser fina onde a decisao acontece. Com 1.500 pontos
    # sobre 37 mil pecas o passo fica em ~25 pecas, ou algumas centenas de
    # reais na faixa de caixa que a empresa realmente considera - a previa da
    # tela erra por menos que o arredondamento de um pedido.
    # a origem: nada comprado ainda
    zero = pd.DataFrame([dict(posicao_fila=0, caixa=0.0, pecas=0, margem=0.0,
                              margem_em_risco=risco_inicial, falta=falta_inicial,
                              nota=float(curva.nota.iloc[0]) if len(curva) else 0.0,
                              p_vender=1.0, itens=0)])
    curva = pd.concat([zero, curva], ignore_index=True)
    if len(curva) > pontos:
        passo = int(np.ceil(len(curva) / pontos))
        idx = list(range(0, len(curva), passo))
        if idx[-1] != len(curva) - 1:
            idx.append(len(curva) - 1)
        curva = curva.iloc[idx].reset_index(drop=True)
    return curva


def resumo_criterios(fila: pd.DataFrame, p: Parametros,
                     risco_inicial: float, falta_inicial: float) -> pd.DataFrame:
    """Onde cada criterio cortaria a fila, sozinho, e onde a combinacao corta.

    E o que a tela de criterios mostra: cada regra isolada sobre a mesma
    fronteira, mais a linha `combinado` com o conjunto que esta ligado. So
    assim a escolha e comparada em vez de adivinhada - e da para ver qual das
    regras ligadas e a que realmente esta mordendo.
    """
    from .config import CRITERIOS
    ativos = criterios_ativos(p)

    def medir(regra: dict) -> dict:
        r = caminhar(fila, regra, risco_inicial, falta_inicial)
        ok = r["comprar"]
        n = int(ok.sum())
        caixa = float(fila.custo.to_numpy()[ok].sum()) if n else 0.0
        return dict(
            posicao_corte=int(np.max(np.where(ok)[0]) + 1) if n else 0,
            blocos=n,
            pecas=int(fila.quantidade.to_numpy()[ok].sum()) if n else 0,
            itens=int(pd.unique(fila.sku.to_numpy()[ok]).size) if n else 0,
            caixa=caixa,
            margem=float(fila.valor_esperado.to_numpy()[ok].sum()) if n else 0.0,
            margem_em_risco=float(r["margem_em_risco_restante"][-1]),
            falta=float(r["falta_restante"][-1]),
            estoura_caixa=bool(caixa > p.teto_compra_ciclo + 1e-6),
        )

    linhas = []
    for chave, rotulo, campo, unidade, _t, _d, _q, _a in CRITERIOS:
        linhas.append(dict(
            criterio=chave, rotulo=rotulo, campo=campo, unidade=unidade,
            valor_configurado=float(getattr(p, campo, 0.0)),
            ativo=bool(chave in ativos),
            **medir(regra_de_parada(p, chave))))

    # a linha do conjunto: o corte que a plataforma vai usar de fato
    comb = medir(regra_de_parada(p))
    solos = {l["criterio"]: l for l in linhas}
    # qual das regras ligadas e a que amarra: a que sozinha corta mais baixo
    manda = min(ativos, key=lambda c: solos[c]["caixa"]) if ativos else "caixa"
    linhas.append(dict(
        criterio="combinado",
        rotulo="Combinação em uso" if len(ativos) > 1 else solos[ativos[0]]["rotulo"],
        campo="", unidade="", valor_configurado=0.0, ativo=True,
        criterios_ativos=",".join(ativos), manda=manda, **comb))
    for l in linhas[:-1]:
        l["criterios_ativos"] = ",".join(ativos)
        l["manda"] = manda
    return pd.DataFrame(linhas)


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

    # Ponto de partida do risco: a margem que se perde se NADA for comprado.
    # E a origem da fronteira - cada peca comprada desconta dela.
    falta0 = falta_esperada(df)
    margem_un = (df.lucro_por_peca * p.fator_perda_ruptura).to_numpy(float)
    risco_inicial = float((falta0 * margem_un).sum())
    falta_inicial = float(falta0.sum())

    regra = regra_de_parada(p)
    passo = caminhar(fila, regra, risco_inicial, falta_inicial)

    for coluna, valores in passo.items():
        fila[coluna] = valores
    fila["teto_ciclo"] = float(p.teto_compra_ciclo)
    fila["criterio_parada"] = regra["criterio"]
    fila["piso_retorno"] = regra["piso_retorno"]
    fila["piso_chance"] = regra["piso_chance"]
    fila.attrs["risco_inicial"] = risco_inicial
    fila.attrs["falta_inicial"] = falta_inicial

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
# 6b. Em transito: o que ja foi pedido e ainda nao chegou
# ----------------------------------------------------------------------
def em_transito_por_sku(wh: Warehouse, hoje, protecao: pd.Series,
                        ate: str | None = None, idade_max_dias: int = 180) -> pd.Series:
    """Pecas pedidas ao fornecedor que ainda nao entraram no CD e que chegam
    dentro do periodo de protecao de cada item.

    Entram na posicao de estoque (posicao = disponivel + em transito) porque
    uma peca que chega antes do fim do horizonte protege a demanda do
    horizonte tanto quanto uma na prateleira - e sem ela o modelo mandaria
    comprar de novo o que ja esta comprado. O que chega DEPOIS do horizonte
    fica fora: nao serve a demanda que a compra de hoje precisa cobrir.

    Pedido em aberto = colocado ha no maximo `idade_max_dias` (o que passa
    disso e cancelamento que o ERP nunca fechou) e sem entrada no livro de
    recebimentos do CD (`raw_ciclo_pagamento`) ate `hoje`. A data de chegada
    e a previsao do pedido; previsao vencida conta como chegando agora, a
    mesma leitura da projecao no dossie do item. Na operacao normal a
    quantidade e o que falta atender (solicitado - atendido); no backtest
    (`ate`) a coluna `qtdatendida` e a de hoje, nao a da epoca, entao vale o
    solicitado inteiro e so o livro de entradas diz o que ja tinha chegado.

    `protecao` e uma Series sku -> periodo_protecao_dias. Devolve sku -> pecas.
    Sem as tabelas de compra na base (sintetica/exports) devolve zero para
    todos, que e o comportamento anterior.
    """
    vazio = pd.Series(0.0, index=protecao.index, name="em_transito")
    if not (wh.existe("raw_compras") and wh.existe("raw_ciclo_pagamento")):
        return vazio
    hoje = pd.Timestamp(hoje)
    H = str(hoje)[:10]
    c = wh.query(f"""
        with c as (
            select distinct idpedido, cast(idsubproduto as varchar) as sku,
                   cast(dtmovimento as date)      as pedido_em,
                   cast(previsaoentrega as date)  as previsto_para,
                   cast(diasprevisaoentrega as integer) as prazo_previsto,
                   cast(qtdsolicitada as double)  as solicitado,
                   cast(qtdatendida as double)    as atendido
            from {ref('raw_compras')}
            where cast(dtmovimento as date) <= DATE '{H}'
              and cast(dtmovimento as date) >= DATE '{H}' - INTERVAL {int(idade_max_dias)} DAY)
        select c.* from c
        where not exists (select 1 from {ref('raw_ciclo_pagamento')} k
                          where k.idpedido = c.idpedido
                            and cast(k.idsubproduto as varchar) = c.sku
                            and cast(k.dt_entrada_estoque as date) <= DATE '{H}')""")
    if c.empty:
        return vazio
    c["sku"] = c.sku.astype(str)
    pend = c.solicitado - (c.atendido.fillna(0.0) if ate is None else 0.0)
    prev = pd.to_datetime(c.previsto_para)
    sem_prev = prev.isna()
    prev = prev.where(~sem_prev,
                      pd.to_datetime(c.pedido_em) + pd.to_timedelta(c.prazo_previsto.fillna(0), unit="D"))
    chega = prev.where(prev >= hoje, hoje)
    dias_ate_chegar = (chega - hoje).dt.days
    prot = c.sku.map(protecao.astype(float))
    ok = (pend > 0) & prot.notna() & (dias_ate_chegar <= prot)
    soma = c[ok].assign(pecas=pend[ok]).groupby("sku").pecas.sum()
    return vazio.add(soma.reindex(protecao.index).fillna(0.0), fill_value=0.0).rename("em_transito")

# ----------------------------------------------------------------------
# Espelha mart_sku_financeiro.sql com um corte de data. Existe por causa da
# validacao historica: aquele mart agrega o periodo INTEIRO, e rodar o modelo
# "como se fosse" uma data passada exige recalcular lucro_por_peca, margem_pct,
# cmv e pecas_vendidas so com o que se sabia ate ali. Sem isso o backtest
# adivinha o futuro pelo proprio numero que deveria prever.
#
# Se mart_sku_financeiro.sql mudar, este SQL muda junto - scripts/revisao.py
# cobra a igualdade dos dois com o corte na ultima data.
SQL_FINANCEIRO_ATE = """
with v as (
    select sku,
           sum(pecas_vendidas)       as pecas_vendidas,
           count(*)                  as linhas_de_venda,
           count(distinct pedido)    as pedidos,
           sum(receita_liquida)      as receita_liquida,
           sum(total)                as total_faturado,
           sum(cmv)                  as cmv,
           sum(valor_do_frete)       as frete_custo,
           sum(impostos_sobre_venda) as impostos,
           sum(lucro)                as lucro,
           sum(case when canal_demanda = 'ecommerce' then pecas_vendidas else 0 end) as pecas_ecommerce,
           sum(case when canal_demanda = 'ecommerce' then receita_liquida else 0 end) as receita_ecommerce,
           sum(case when canal_demanda = 'ecommerce' then lucro else 0 end)          as lucro_ecommerce
    from {vendas}
    where data <= DATE '{ate}'
    group by sku
)
select c.sku, c.item, c.familia, c.unidade, c.origem, c.custo_unitario,
       c.preco_tabela, c.lead_time_dias, c.lote_minimo_compra,
       coalesce(v.pecas_vendidas, 0)  as pecas_vendidas,
       coalesce(v.linhas_de_venda, 0) as linhas_de_venda,
       coalesce(v.pedidos, 0)         as pedidos,
       coalesce(v.receita_liquida, 0) as receita_liquida,
       coalesce(v.total_faturado, 0)  as total_faturado,
       coalesce(v.cmv, 0)             as cmv,
       coalesce(v.frete_custo, 0)     as frete_custo,
       coalesce(v.impostos, 0)        as impostos,
       coalesce(v.lucro, 0)           as lucro_observado,
       case when coalesce(v.pecas_vendidas, 0) > 0
            then v.lucro / v.pecas_vendidas else 0 end as lucro_por_peca,
       case when coalesce(v.total_faturado, 0) > 0
            then v.lucro / v.total_faturado else 0 end as margem_pct,
       coalesce(v.pecas_ecommerce, 0)                                  as pecas_vendidas_ecommerce,
       coalesce(v.pecas_vendidas, 0) - coalesce(v.pecas_ecommerce, 0)  as pecas_vendidas_lojas,
       coalesce(v.receita_ecommerce, 0)                                as receita_liquida_ecommerce,
       coalesce(v.receita_liquida, 0) - coalesce(v.receita_ecommerce, 0) as receita_liquida_lojas,
       coalesce(v.lucro_ecommerce, 0)                                  as lucro_observado_ecommerce,
       coalesce(v.lucro, 0) - coalesce(v.lucro_ecommerce, 0)           as lucro_observado_lojas,
       case when coalesce(v.pecas_ecommerce, 0) > 0
            then v.lucro_ecommerce / v.pecas_ecommerce else 0 end      as lucro_por_peca_ecommerce,
       case when coalesce(v.pecas_vendidas, 0) - coalesce(v.pecas_ecommerce, 0) > 0
            then (v.lucro - v.lucro_ecommerce) / (v.pecas_vendidas - v.pecas_ecommerce)
            else 0 end                                                 as lucro_por_peca_lojas
from {catalogo} c
left join v on v.sku = c.sku
"""


def ler_base(wh: Warehouse, ate: str | None = None,
             janela: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """As duas leituras do motor, com duas janelas diferentes de proposito.

    `ate` = None e a operacao normal (a data mais recente do banco);
    `ate` = 'AAAA-MM-DD' e o backtest, que devolve o que o motor teria lido
    naquele dia e nada do que veio depois.

    `janela` limita o historico DIARIO por baixo: so os ultimos N dias entram
    na estimativa de demanda. Nao e um detalhe de desempenho - e a memoria do
    modelo. Com todo o historico (1.097 dias nesta base) a taxa estimada saiu
    mais de 100% abaixo da observada nos ultimos 90 dias, porque a media longa
    dilui crescimento. O agregado financeiro NAO recebe essa janela: lucro por
    peca e economia unitaria, nao taxa, e com 90 dias a margem da maioria do
    catalogo sairia zero.

    `disponivel_final` e a posicao que decide a compra (liquida de reserva);
    `saldo_final` e o estoque fisico, que diz se o dia tem sinal de demanda.
    """
    colunas = ("sku, data, pecas_vendidas, pecas_ecommerce, pecas_lojas, estado_estoque, "
               "saldo_final, disponivel_final")
    fim = (f"where data <= DATE '{str(ate)[:10]}'" if ate else
           f"where data <= (select max(data) from {ref('mart_estoque_diario')})")
    piso = ""
    if janela and janela > 0:
        base = (f"DATE '{str(ate)[:10]}'" if ate
                else f"(select max(data) from {ref('mart_estoque_diario')})")
        piso = f" and data > {base} - INTERVAL {int(janela)} DAY"

    diario = wh.query(f"select {colunas} from {ref('mart_estoque_diario')} {fim}{piso}")
    if ate is None:
        fin = wh.query(f"select * from {ref('mart_sku_financeiro')}")
    else:
        # o corte nunca passa do ultimo dia de ESTOQUE: a grade diaria termina
        # ali, e o extrato de venda vai quatro dias mais longe. Sem este limite
        # o espelho somaria venda de dias que a grade nao tem, e deixaria de
        # reproduzir o mart no ultimo dia - a igualdade que a bateria cobra.
        fim_estoque = str(wh.query(
            f"select max(data) d from {ref('mart_estoque_diario')}").iloc[0, 0])[:10]
        corte = min(str(ate)[:10], fim_estoque)
        fin = wh.query(SQL_FINANCEIRO_ATE.format(
            vendas=ref('stg_vendas'), catalogo=ref('stg_catalogo'),
            ate=corte))
    return _margem_coerente(wh, fin, ate, janela), diario


def _margem_coerente(wh: Warehouse, fin: pd.DataFrame, ate: str | None,
                     janela: int | None) -> pd.DataFrame:
    """Pareia a margem com o custo que o modelo vai pagar.

    A nota divide por `custo_unitario` - o custo de hoje - e multiplica pela
    margem. Mas `lucro_por_peca` vem da venda, e a venda subtrai o custo medio
    do DIA em que ela aconteceu: uma media de tres anos de custos antigos.
    Quando o custo de um item muda, os dois lados da razao falam de precos
    diferentes, e a nota erra - para cima em quem ficou mais caro, para baixo
    em quem ficou mais barato.

    Aqui a margem e refeita como preco praticado menos o custo de hoje. O
    preco sai da MESMA janela finita usada para a demanda, porque preco
    tambem envelhece; item sem venda na janela cai para a media do historico,
    e item sem venda nenhuma fica com zero, como antes.

    A leitura antiga fica gravada em `lucro_por_peca_historico`: a tela de
    conferencia mostra as duas, e e a divergencia entre elas que denuncia
    item com custo em movimento.
    """
    fin = fin.copy()
    fin["lucro_por_peca_historico"] = fin.lucro_por_peca.astype(float)

    q = fin.pecas_vendidas.astype(float).replace(0.0, np.nan)
    preco_tudo = fin.receita_liquida.astype(float) / q

    corte = (f"DATE '{str(ate)[:10]}'" if ate
             else f"(select max(data) from {ref('mart_estoque_diario')})")
    onde = f"where data <= {corte}"
    if janela and janela > 0:
        onde += f" and data > {corte} - INTERVAL {int(janela)} DAY"
    jan = wh.query(f"""
        select sku, sum(receita_liquida) as receita, sum(pecas_vendidas) as pecas
        from {ref('stg_vendas')} {onde} group by sku""")
    jan["preco_janela"] = (jan.receita.astype(float)
                           / jan.pecas.astype(float).replace(0.0, np.nan))
    fin = fin.merge(jan[["sku", "preco_janela"]], on="sku", how="left")

    preco = fin.preco_janela.fillna(preco_tudo)
    fin["preco_liquido_peca"] = preco.fillna(0.0)
    margem = preco - fin.custo_unitario.astype(float)
    # sem venda nenhuma nao ha preco praticado: fica zero, como antes
    fin["lucro_por_peca"] = margem.where(fin.pecas_vendidas.astype(float) > 0,
                                         0.0).fillna(0.0)
    # margem_pct tem de acompanhar, ou os dois numeros na tela se contradizem.
    # A base passa a ser o preco liquido por peca - a mesma base da margem -,
    # e nao o faturamento bruto de antes, que incluia imposto e frete.
    fin["margem_pct"] = np.where(fin.preco_liquido_peca > 0,
                                 fin.lucro_por_peca / fin.preco_liquido_peca,
                                 0.0)
    fin = fin.drop(columns=["preco_janela"])
    return fin


def executar(wh: Warehouse, p: Parametros, ate: str | None = None,
             skus: set[str] | None = None) -> dict:
    """Le os marts, roda o modelo e devolve os quadros prontos para gravar.

    `ate` corta o passado numa data e faz o motor rodar como se fosse aquele
    dia - e o que a tela de validacao historica usa. Todo o resto do fluxo e
    identico, de proposito: se o backtest rodasse por um caminho paralelo,
    validaria outro modelo, nao este.

    `skus` restringe o universo ANTES de tudo o mais. O filtro entra aqui, e
    nao no fim, porque o caixa do ciclo e disputado peca a peca no catalogo
    inteiro: filtrar o plano depois de pronto deixaria o modelo ter gasto
    dinheiro com itens que o recorte exclui, e o que sobrasse na tela seria
    um plano que nunca existiu. Filtrando na entrada, o motor decide dentro do
    recorte com o dinheiro do recorte - que e o unico teste que responde
    "e se a base fosse confiavel?".
    """
    fin, diario = ler_base(wh, ate, getattr(p, "janela_estimacao_dias", None))
    if skus is not None:
        fin = fin[fin.sku.isin(skus)].copy()
        diario = diario[diario.sku.isin(skus)].copy()

    est = estatistica_demanda(diario, p)
    b = fin.merge(est, on="sku", how="left").fillna({"demanda_media_dia": 0.0})

    b["periodo_protecao_dias"] = b.lead_time_dias + p.periodo_revisao_dias
    b["mu_periodo"] = b.demanda_media_dia * b.periodo_protecao_dias
    # raiz(H) supoe dias independentes. Quando a demanda tem reversao a media,
    # essa conta superestima a variacao no horizonte e infla o estoque de
    # seguranca; `fator_desvio_horizonte` permite corrigir com o valor medido
    # em scripts/revisao.py. Fica em 1,00 por padrao - a hipotese conservadora.
    # Variancia da demanda no horizonte, com prazo de entrega VARIAVEL:
    #
    #     Var(D_H) = (E[L] + R) x Var(d)  +  E[d]^2 x Var(L)
    #
    # O primeiro termo e a variacao da demanda diaria acumulada no horizonte -
    # e o unico que a versao anterior tinha. O segundo e a variacao do proprio
    # prazo: se o fornecedor pode atrasar, a janela a cobrir e maior, e um item
    # de giro alto sofre muito mais com isso do que um de giro baixo.
    #
    # No extrato real da Elevato o prazo realizado tem mediana de 18 dias e
    # desvio mediano de 13,2 - um coeficiente de variacao de 0,75. Ignorar esse
    # termo subdimensionaria o estoque de seguranca justamente nos itens que
    # mais vendem. Com `lead_time_desvio_dias` = 0, que e o caso da base
    # sintetica, a conta recai exatamente na anterior.
    sd_lead = b.get("lead_time_desvio_dias", pd.Series(0.0, index=b.index)).fillna(0.0)
    b["sd_lead_time_dias"] = sd_lead
    b["sd_periodo"] = np.sqrt(
        b.periodo_protecao_dias * b.desvio_padrao_dia ** 2
        + (b.demanda_media_dia ** 2) * (sd_lead ** 2)) * p.fator_desvio_horizonte
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
    # a posicao que entra na decisao e a DISPONIVEL, nao o estoque fisico: a
    # peca reservada ja tem dono e nao protege a proxima venda
    posicoes = (diario[diario.data == ultimo][["sku", "disponivel_final"]]
                .rename(columns={"disponivel_final": "estoque_fisico"}))
    # ... mais o que ja foi pedido e chega dentro do periodo de protecao do
    # item: a peca a caminho protege o horizonte tanto quanto a da prateleira
    transito = em_transito_por_sku(
        wh, ultimo, b.set_index("sku").periodo_protecao_dias, ate=ate)
    posicoes["em_transito"] = posicoes.sku.map(transito).fillna(0.0).to_numpy()

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
        criterio_parada=str(p.criterio_parada),
        margem_em_risco=float(fila.margem_em_risco_restante.iloc[-1]) if len(fila) else 0.0,
        margem_em_risco_inicial=float(fila.attrs.get("risco_inicial", 0.0)),
        **{f"param_{k}": v for k, v in asdict(p).items()},
    )])

    # a fronteira e o mapa da decisao: onde cada criterio de parada cortaria
    risco0 = float(fila.attrs.get("risco_inicial", 0.0))
    falta0 = float(fila.attrs.get("falta_inicial", 0.0))
    curva = fronteira(fila, risco0, falta0)
    criterios = resumo_criterios(fila, p, risco0, falta0)
    criterios["risco_inicial"] = risco0
    criterios["falta_inicial"] = falta0
    criterios["teto_ciclo"] = float(p.teto_compra_ciclo)
    criterios["custo_capital_dia"] = p.taxa_manutencao_ano / p.dias_por_ano

    return dict(res_sku_modelo=modelo, res_plano_compra=plano,
                res_fila_marginal=fila, res_plano_reposicao=reposicao,
                res_fronteira=curva, res_criterios=criterios,
                res_estrategias=estrategias,
                res_comparativo=comparativo, res_execucao=execucao)


def gravar_resultados(wh: Warehouse, resultados: dict) -> None:
    """Persiste as tabelas de resultado no warehouse (mesmo motor dos marts)."""
    for nome, df in resultados.items():
        wh.gravar(df, nome)
