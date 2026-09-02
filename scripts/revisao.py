# -*- coding: utf-8 -*-
"""
Revisao completa do motor de calculo.

Bateria de verificacoes independentes: cada teste refaz a conta por fora
(numpy/scipy puros, a partir dos insumos gravados) e compara com o resultado
do modelo, ou checa uma propriedade que tem de valer sempre.

Nao e um teste de "roda sem erro" - e um teste de "o numero esta certo".

Niveis:
  OK     a propriedade vale
  ALERTA vale, mas ha algo que merece decisao humana
  FALHA  o numero esta errado

Uso:
    python scripts/revisao.py
    python scripts/revisao.py --amostra 3000
    python scripts/revisao.py --so 5          # roda so o bloco 5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from backend.config import Parametros  # noqa: E402
from backend.modelo import ajustar_distribuicao, modelar  # noqa: E402
from backend.warehouse import abrir, ref  # noqa: E402

TOL = 1e-9          # igualdade numerica exata (mesma conta, mesma ordem)
TOL_FROUXA = 1e-6   # quando ha arredondamento pelo caminho


# ----------------------------------------------------------------------
# relatorio
# ----------------------------------------------------------------------
class Relatorio:
    def __init__(self) -> None:
        self.itens: list[tuple[str, str, str, str]] = []

    def registrar(self, nivel: str, bloco: str, nome: str, detalhe: str = "") -> None:
        self.itens.append((nivel, bloco, nome, detalhe))

    def ok(self, bloco, nome, detalhe=""):
        self.registrar("OK", bloco, nome, detalhe)

    def alerta(self, bloco, nome, detalhe=""):
        self.registrar("ALERTA", bloco, nome, detalhe)

    def falha(self, bloco, nome, detalhe=""):
        self.registrar("FALHA", bloco, nome, detalhe)

    def compara(self, bloco, nome, calculado, gravado, tol=TOL, contexto=""):
        """Compara dois vetores numericos e registra o pior desvio relativo."""
        a = np.asarray(calculado, dtype=float)
        b = np.asarray(gravado, dtype=float)
        val = np.isfinite(a) & np.isfinite(b)
        if not val.any():
            self.alerta(bloco, nome, "nada comparavel (tudo NaN)")
            return
        desvio = np.abs(a[val] - b[val]) / np.maximum(np.abs(b[val]), 1e-12)
        pior = float(desvio.max())
        n_ruim = int((desvio > tol).sum())
        det = f"pior desvio {pior:.2e} em {val.sum()} valores"
        if contexto:
            det += f" · {contexto}"
        if n_ruim:
            i = int(np.argmax(desvio))
            det += f" · {n_ruim} fora da tolerancia (ex.: {a[val][i]:.10g} vs {b[val][i]:.10g})"
            self.falha(bloco, nome, det)
        else:
            self.ok(bloco, nome, det)

    def afirma(self, bloco, nome, condicao: bool, detalhe="", alerta_em_vez=False):
        if condicao:
            self.ok(bloco, nome, detalhe)
        elif alerta_em_vez:
            self.alerta(bloco, nome, detalhe)
        else:
            self.falha(bloco, nome, detalhe)

    def imprimir(self) -> int:
        larg = max(len(n) for _, _, n, _ in self.itens) if self.itens else 10
        bloco_atual = None
        for nivel, bloco, nome, det in self.itens:
            if bloco != bloco_atual:
                print(f"\n{bloco}")
                print("-" * min(len(bloco), 78))
                bloco_atual = bloco
            marca = {"OK": "  ok  ", "ALERTA": " ~~~~ ", "FALHA": " FALHA"}[nivel]
            print(f"{marca} {nome.ljust(larg)}  {det}")

        falhas = sum(1 for n, _, _, _ in self.itens if n == "FALHA")
        alertas = sum(1 for n, _, _, _ in self.itens if n == "ALERTA")
        oks = sum(1 for n, _, _, _ in self.itens if n == "OK")
        print("\n" + "=" * 78)
        print(f"{oks} verificacoes ok · {alertas} alerta(s) · {falhas} falha(s)")
        if alertas:
            print("\nALERTAS (nao sao erro de conta, mas pedem decisao):")
            for nivel, _, nome, det in self.itens:
                if nivel == "ALERTA":
                    print(f"  · {nome}: {det}")
        if falhas:
            print("\nFALHAS:")
            for nivel, _, nome, det in self.itens:
                if nivel == "FALHA":
                    print(f"  · {nome}: {det}")
        print("=" * 78)
        return falhas


# ----------------------------------------------------------------------
# 1. dados de entrada e integridade
# ----------------------------------------------------------------------
def bloco1(r: Relatorio, wh, p, ctx) -> None:
    B = "1. Dados de entrada e integridade"
    dia, m, plano = ctx["dia"], ctx["modelo"], ctx["plano"]

    # a regra dos tres estados e a base de toda a correcao de censura
    esperado = np.where(dia.saldo_inicial <= 0, "Sem estoque",
                        np.where(dia.saldo_final <= 0, "Ruptura parcial", "Disponivel"))
    r.afirma(B, "regra dos tres estados do dia",
             bool((esperado == dia.estado_estoque.to_numpy()).all()),
             f"{len(dia)} dias · "
             f"{int((dia.estado_estoque=='Disponivel').sum())} disponiveis / "
             f"{int((dia.estado_estoque=='Ruptura parcial').sum())} parciais / "
             f"{int((dia.estado_estoque=='Sem estoque').sum())} sem estoque")

    r.afirma(B, "venda do dia nao excede o saldo inicial",
             bool((dia.pecas_vendidas <= dia.saldo_inicial + 1e-9).all()),
             "nao da para vender mais do que havia na prateleira")

    # a posicao usada na compra tem de ser o saldo do ultimo dia carregado
    ultimo = dia.data.max()
    pos_real = (dia[dia.data == ultimo][["sku", "saldo_final"]]
                .set_index("sku").saldo_final)
    junto = plano.set_index("sku").posicao_estoque.reindex(pos_real.index)
    r.compara(B, "posicao de estoque = saldo do ultimo dia", pos_real.to_numpy(),
              junto.to_numpy(), contexto=f"posicao de {str(ultimo)[:10]}")

    # NaN / infinito: duas colunas tem NaN por definicao (nao se aplicam), e o
    # teste tem de saber disso - senao ou ele grita a cada execucao ou, pior,
    # alguem "conserta" preenchendo com zero um campo que nao existe.
    esperado_nulo = {
        "res_plano_compra": {
            "ultima_unidade": plano.quantidade_a_comprar == 0,
            "p_vender_ultima": plano.quantidade_a_comprar == 0,
        },
        "res_fila_marginal": {
            "nb_r": ctx["fila"].distribuicao == "Poisson",
            "nb_p": ctx["fila"].distribuicao == "Poisson",
        },
    }
    for nome, df in [("res_sku_modelo", m), ("res_plano_compra", plano),
                     ("res_fila_marginal", ctx["fila"])]:
        num = df.select_dtypes(include=[np.number])
        permitido = esperado_nulo.get(nome, {})
        ruins = {}
        for c in num.columns:
            invalido = ~np.isfinite(num[c].to_numpy(float))
            if not invalido.any():
                continue
            if c in permitido:
                # NaN e permitido, mas SO nas linhas em que nao se aplica
                fora = invalido & ~permitido[c].to_numpy()
                if fora.any():
                    ruins[c] = f"{int(fora.sum())} fora do caso previsto"
            else:
                ruins[c] = int(invalido.sum())
        r.afirma(B, f"NaN/infinito so onde e previsto em {nome}", not ruins,
                 "limpo" if not ruins else f"colunas com valor invalido: {ruins}")

    r.afirma(B, "'ultima unidade' nula exatamente nos itens sem compra",
             bool((plano.ultima_unidade.isna() ==
                   (plano.quantidade_a_comprar == 0)).all()),
             f"{int(plano.ultima_unidade.isna().sum())} itens sem compra neste ciclo")
    r.afirma(B, "parametros r,p nulos exatamente nas linhas Poisson",
             bool((ctx["fila"].nb_r.isna() ==
                   (ctx["fila"].distribuicao == "Poisson")).all()),
             "a Poisson nao tem r nem p - so a Binomial Negativa tem")

    r.afirma(B, "dias utilizaveis + dias sem estoque = historico",
             bool(((m.dias_utilizaveis + m.dias_sem_estoque) == m.dias_historico).all()),
             f"historico de {int(m.dias_historico.max())} dias")


# ----------------------------------------------------------------------
# 2. correcao de censura (EM)
# ----------------------------------------------------------------------
def bloco2(r: Relatorio, wh, p, ctx) -> None:
    B = "2. Correcao de censura (EM)"
    m, dia = ctx["modelo"], ctx["dia"]

    r.afirma(B, "pecas imputadas nunca negativas",
             bool((m.pecas_imputadas >= -1e-9).all()),
             "imputar so pode aumentar a venda observada, nunca reduzir")

    # a media corrigida nao pode ficar abaixo da media dos dias disponiveis:
    # ela e a mesma amostra mais os dias censurados, que valem >= o observado
    comp = m[(m.dias_ruptura_parcial > 0) & (m.demanda_media_dia_disponivel > 0)]
    if len(comp):
        r.afirma(B, "corrigida >= media dos dias disponiveis",
                 bool((comp.demanda_media_dia >= comp.demanda_media_dia_disponivel - 1e-9).all()),
                 f"{len(comp)} itens com dia de ruptura parcial")

    # e nao pode ficar abaixo da ingenua quando houve dia sem estoque
    comp2 = m[m.dias_sem_estoque > 0]
    if len(comp2):
        pior = float((comp2.demanda_media_dia_ingenua - comp2.demanda_media_dia).max())
        r.afirma(B, "corrigida >= ingenua quando faltou estoque", pior <= 1e-9,
                 f"{len(comp2)} itens com dia zerado · maior violacao {pior:.3e}")

    # media dos dias disponiveis, refeita direto do diario
    disp = (dia[dia.estado_estoque == "Disponivel"]
            .groupby("sku").pecas_vendidas.mean())
    alvo = m.set_index("sku").demanda_media_dia_disponivel.reindex(disp.index)
    r.compara(B, "media dos dias disponiveis refeita", disp.to_numpy(), alvo.to_numpy(),
              tol=TOL_FROUXA)

    # media ingenua = media de todos os dias
    ing = dia.groupby("sku").pecas_vendidas.mean()
    alvo = m.set_index("sku").demanda_media_dia_ingenua.reindex(ing.index)
    r.compara(B, "media ingenua refeita", ing.to_numpy(), alvo.to_numpy(), tol=TOL_FROUXA)

    # o EM tem de ser estavel: rodar de novo nos mesmos dados da o mesmo numero
    from backend.modelo import estatistica_demanda
    novo = estatistica_demanda(dia, p).set_index("sku")
    velho = m.set_index("sku")
    r.compara(B, "EM reproduzivel (rodar 2x da o mesmo)",
              novo.demanda_media_dia.reindex(velho.index).to_numpy(),
              velho.demanda_media_dia.to_numpy(), tol=TOL_FROUXA)
    r.compara(B, "desvio do EM reproduzivel",
              novo.desvio_padrao_dia.reindex(velho.index).to_numpy(),
              velho.desvio_padrao_dia.to_numpy(), tol=TOL_FROUXA)

    quanto = float(m[m.demanda_media_dia_ingenua > 0].subestimacao_ingenua_pct.mean())
    r.alerta(B, "tamanho da correcao",
             f"sem corrigir, a demanda media do catalogo sairia {quanto:.1%} menor")


# ----------------------------------------------------------------------
# 3. distribuicao da demanda
# ----------------------------------------------------------------------
def bloco3(r: Relatorio, wh, p, ctx) -> None:
    B = "3. Distribuicao da demanda no horizonte"
    m, dia = ctx["modelo"], ctx["dia"]

    # a agregacao diaria -> horizonte
    r.compara(B, "mu do horizonte = demanda diaria x H",
              (m.demanda_media_dia * m.periodo_protecao_dias).to_numpy(),
              m.mu_periodo.to_numpy())
    r.compara(B, "sigma do horizonte = desvio diario x raiz(H)",
              (m.desvio_padrao_dia * np.sqrt(m.periodo_protecao_dias)).to_numpy(),
              m.sd_periodo.to_numpy())

    # a escolha Poisson x NB e exatamente o teste de dispersao
    razao = np.where(m.mu_periodo > 0, m.sd_periodo ** 2 / m.mu_periodo, np.nan)
    escolha = np.where(m.mu_periodo <= 0, "Sem historico",
                       np.where(razao <= 1.05, "Poisson", "Binomial Negativa"))
    r.afirma(B, "escolha da distribuicao segue o teste variancia/media",
             bool((escolha == m.distribuicao.to_numpy()).all()),
             f"{int((escolha=='Poisson').sum())} Poisson · "
             f"{int((escolha=='Binomial Negativa').sum())} Binomial Negativa")

    # a razao e invariante ao horizonte: e o mesmo teste feito no dado diario
    razao_dia = np.where(m.demanda_media_dia > 0,
                         m.desvio_padrao_dia ** 2 / m.demanda_media_dia, np.nan)
    r.compara(B, "razao no horizonte = razao no dia (invariante a H)",
              razao_dia, razao, tol=TOL_FROUXA,
              contexto="somar dias nao cria nem destroi superdispersao")

    # a NB ajustada reproduz exatamente a media e a variancia pedidas
    erros_mu, erros_var, r_pequeno = [], [], 0
    for _, x in m.iterrows():
        if x.mu_periodo <= 0:
            continue
        nome, dist, rr, pp = ajustar_distribuicao(x.mu_periodo, x.sd_periodo)
        erros_mu.append(abs(dist.mean() - x.mu_periodo) / max(x.mu_periodo, 1e-12))
        if nome == "Binomial Negativa":
            erros_var.append(abs(dist.var() - x.sd_periodo ** 2) / max(x.sd_periodo ** 2, 1e-12))
            if rr < 1:
                r_pequeno += 1
    r.afirma(B, "distribuicao ajustada reproduz a media", max(erros_mu) < TOL_FROUXA,
             f"pior desvio {max(erros_mu):.2e}")
    r.afirma(B, "NB reproduz a variancia", max(erros_var) < TOL_FROUXA,
             f"pior desvio {max(erros_var):.2e} em {len(erros_var)} itens")
    if r_pequeno:
        r.alerta(B, "itens com parametro r < 1",
                 f"{r_pequeno} itens · cauda muito gorda, a NB fica quase geometrica")

    # percentis coerentes e monotonicos
    q = m[["p50", "p75", "p90", "p95", "p99"]].to_numpy()
    r.afirma(B, "percentis monotonicos (p50<=p75<=p90<=p95<=p99)",
             bool((np.diff(q, axis=1) >= -1e-9).all()))

    # aderencia ao dado real: a NB ajustada nos momentos DIARIOS descreve
    # bem a venda diaria observada? (teste qui-quadrado por faixas)
    reprovados, testados = [], 0
    for sku, g in dia[dia.estado_estoque == "Disponivel"].groupby("sku"):
        v = g.pecas_vendidas.to_numpy(float)
        if len(v) < 40 or v.mean() <= 0:
            continue
        testados += 1
        _, d, _, _ = ajustar_distribuicao(float(v.mean()), float(v.std(ddof=1)))
        cortes = np.unique(np.floor(d.ppf([0, .2, .4, .6, .8, 1.0])).astype(int))
        if len(cortes) < 3:
            continue
        obs, esp = [], []
        for i in range(len(cortes) - 1):
            lo, hi = cortes[i], cortes[i + 1]
            obs.append(int(((v >= lo) & (v < hi)).sum()))
            esp.append(float((d.cdf(hi - 1) - d.cdf(lo - 1)) * len(v)))
        obs.append(int((v >= cortes[-1]).sum()))
        esp.append(float((1 - d.cdf(cortes[-1] - 1)) * len(v)))
        obs, esp = np.array(obs, float), np.array(esp, float)
        manter = esp > 3
        if manter.sum() < 3:
            continue
        chi = float(((obs[manter] - esp[manter]) ** 2 / esp[manter]).sum())
        gl = int(manter.sum()) - 1
        if 1 - stats.chi2.cdf(chi, gl) < 0.01:
            reprovados.append(sku)
    if testados:
        frac = len(reprovados) / testados
        r.afirma(B, "aderencia da distribuicao ao dado diario", frac <= 0.25,
                 f"{len(reprovados)} de {testados} itens rejeitados a 1% "
                 f"({frac:.0%}) · esperado ~1% se o ajuste fosse perfeito",
                 alerta_em_vez=True)


# ----------------------------------------------------------------------
# 4. politica de estoque (EOQ, ponto de pedido, regime discreto)
# ----------------------------------------------------------------------
def bloco4(r: Relatorio, wh, p, ctx) -> None:
    B = "4. Politica de estoque por item"
    m = ctx["modelo"]
    lam = float(m.premio_escassez.iloc[0])

    D = m.demanda_media_dia * p.dias_por_ano
    c = m.custo_unitario
    h_dec = c * (p.taxa_manutencao_ano + lam)
    Cu = m.lucro_por_peca * p.fator_perda_ruptura
    Co = c * (p.taxa_manutencao_ano + lam) * m.periodo_protecao_dias / p.dias_por_ano

    r.compara(B, "custo de manter na decisao = c x (taxa + lambda)",
              h_dec.to_numpy(), m.custo_manter_unit_decisao.to_numpy())
    r.compara(B, "custo de manter real = c x taxa",
              (c * p.taxa_manutencao_ano).to_numpy(), m.custo_manter_unit_real.to_numpy())
    r.compara(B, "margem perdida na ruptura = lucro x fator",
              Cu.to_numpy(), m.custo_falta_unit.to_numpy())
    r.compara(B, "custo de manter no horizonte", Co.to_numpy(),
              m.custo_manter_no_periodo.to_numpy())

    with np.errstate(divide="ignore", invalid="ignore"):
        eoq = np.sqrt(2 * D * p.custo_por_pedido / h_dec.replace(0, np.nan))
    eoq = np.nan_to_num(eoq, nan=0.0, posinf=0.0)
    r.compara(B, "EOQ = raiz(2 D S / h)", eoq, m.eoq.to_numpy())

    Q_n = np.ceil(np.maximum(eoq, m.lote_minimo_compra))
    with np.errstate(divide="ignore", invalid="ignore"):
        ns_n = 1 - (Q_n * h_dec) / (D * Cu)
    ns_n = np.clip(np.nan_to_num(ns_n, nan=p.nivel_servico_min),
                   p.nivel_servico_min, p.nivel_servico_max)
    z = stats.norm.ppf(ns_n)
    r.compara(B, "fator z do nivel de servico", z, m.z.to_numpy())

    continuo = m.regime.eq("EOQ + normal").to_numpy()
    rop_n = np.ceil(m.mu_periodo.to_numpy() + np.ceil(z * m.sd_periodo.to_numpy()))
    sem_hist = m.pecas_vendidas.to_numpy() <= 0
    esperado = np.where(sem_hist, 0, rop_n)
    r.compara(B, "ponto de pedido no regime continuo",
              esperado[continuo & ~sem_hist],
              m.ponto_de_pedido.to_numpy()[continuo & ~sem_hist],
              contexto=f"{int((continuo & ~sem_hist).sum())} itens")

    r.afirma(B, "nivel de servico dentro dos limites",
             bool(((m.nivel_servico >= p.nivel_servico_min - 1e-9) &
                   (m.nivel_servico <= p.nivel_servico_max + 1e-9)).all() or
                  ((m.nivel_servico[continuo] >= p.nivel_servico_min - 1e-9) &
                   (m.nivel_servico[continuo] <= p.nivel_servico_max + 1e-9)).all()),
             f"piso {p.nivel_servico_min:.0%} · teto {p.nivel_servico_max:.0%} "
             f"(o regime discreto tem nivel proprio, fora desses limites)")

    # regime discreto: s = quantas pecas passam o teste da unidade marginal
    limite = np.where((Cu + Co) > 0, Co / (Cu + Co), 1.0)
    r.compara(B, "limite do teste marginal = Co/(Cu+Co)", limite,
              m.limite_marginal.to_numpy())
    K = np.arange(1, 61)
    s_disc = np.array([
        int((1 - stats.poisson.cdf(K - 1, mu) > lim).sum()) if mu > 0 else 0
        for mu, lim in zip(m.mu_periodo, limite)])
    r.compara(B, "unidades marginais do regime discreto", s_disc,
              m.unidades_marginais.to_numpy())

    lento = (m.mu_periodo < p.limiar_giro_baixo) & (m.mu_periodo > 0)
    r.afirma(B, "regime escolhido pelo limiar de giro",
             bool((np.where(lento, "Unidade marginal", "EOQ + normal") ==
                   m.regime.to_numpy()).all()),
             f"limiar {p.limiar_giro_baixo:g} pecas no horizonte · "
             f"{int(lento.sum())} itens no regime discreto")

    r.compara(B, "capital imobilizado = estoque medio x custo",
              (m.estoque_medio * m.custo_unitario).to_numpy(),
              m.capital_imobilizado.to_numpy())
    r.compara(B, "custo total = manter + pedir + ruptura",
              (m.custo_manter_ano + m.custo_pedir_ano + m.custo_ruptura_ano).to_numpy(),
              m.custo_total_ano.to_numpy())
    r.compara(B, "lucro liquido = bruto - custo total",
              (m.lucro_bruto_ano - m.custo_total_ano).to_numpy(),
              m.lucro_liquido_ano.to_numpy())

    # o preco-sombra resolve mesmo a restricao de capital?
    if p.aplicar_teto_capital:
        cap = float(modelar(m, p, lam).capital_imobilizado.sum())
        folga = abs(cap - p.teto_capital) / p.teto_capital
        r.afirma(B, "lambda faz o capital caber no teto", folga < 1e-4 or cap < p.teto_capital,
                 f"capital {cap:,.0f} vs teto {p.teto_capital:,.0f} "
                 f"(lambda = {lam:.4f})")
        cap0 = float(modelar(m, p, 0.0).capital_imobilizado.sum())
        r.afirma(B, "sem lambda o capital estouraria o teto", cap0 >= cap - 1e-6,
                 f"irrestrito {cap0:,.0f} · com teto {cap:,.0f}")


# ----------------------------------------------------------------------
# 5. alocacao marginal do caixa
# ----------------------------------------------------------------------
def bloco5(r: Relatorio, wh, p, ctx) -> None:
    B = "5. Alocacao marginal do caixa"
    f, plano = ctx["fila"], ctx["plano"]

    r.afirma(B, "nenhuma peca na fila com valor negativo",
             bool((f.valor_esperado > 0).all()),
             f"{len(f)} pecas candidatas · menor valor R$ {f.valor_esperado.min():.4f}")

    r.afirma(B, "nenhuma peca comprada com valor negativo",
             bool((f[f.comprar].valor_esperado > 0).all()),
             f"{int(f.comprar.sum())} pecas compradas")

    r.afirma(B, "fila ordenada por nota decrescente",
             bool((f.sort_values("posicao_fila").nota.diff().dropna() <= 1e-12).all()),
             "a fila e a ordem em que o caixa decide")

    # dentro de cada item, a chance e o valor tem de cair peca a peca
    quebras_p, quebras_v = [], []
    for sku, g in f.groupby("sku"):
        g = g.sort_values("unidade_de")
        if (g.p_vender.diff().dropna() > 1e-12).any():
            quebras_p.append(sku)
        if (g.valor_esperado.diff().dropna() > 1e-9).any():
            quebras_v.append(sku)
    r.afirma(B, "chance de vender cai a cada peca", not quebras_p,
             "e o que faz o caixa trocar de produto sozinho"
             if not quebras_p else f"quebra em {quebras_p[:5]}")
    r.afirma(B, "valor da peca cai a cada peca", not quebras_v,
             "retorno marginal decrescente"
             if not quebras_v else f"quebra em {quebras_v[:5]}")

    gasto = float(f[f.comprar].custo.sum())
    r.afirma(B, "compra nao passa do caixa do ciclo", gasto <= p.teto_compra_ciclo + 1e-6,
             f"gasto {gasto:,.2f} de {p.teto_compra_ciclo:,.2f} "
             f"(sobra {p.teto_compra_ciclo - gasto:,.2f})")

    # dependencia: nao da para comprar a k-esima peca sem as anteriores
    furos = []
    for sku, g in f[f.comprar].groupby("sku"):
        u = np.sort(g.unidade_de.to_numpy())
        pos = float(plano.loc[plano.sku == sku, "posicao_estoque"].iloc[0])
        if u[0] != int(pos) + 1 or not np.array_equal(u, np.arange(u[0], u[0] + len(u))):
            furos.append(sku)
    r.afirma(B, "unidades compradas sao contiguas a partir da posicao", not furos,
             "sem buraco: compra da posicao+1 para cima"
             if not furos else f"furo em {furos[:5]}")

    # o guloso e coerente: peca nao comprada so pode ter sido barrada por caixa
    incoerentes = f[(~f.comprar) & (f.motivo == "comprada")]
    r.afirma(B, "motivo da decisao coerente com a compra", len(incoerentes) == 0,
             f"motivos: {dict(f.motivo.value_counts())}")

    # nenhuma peca comprada depois de uma nao comprada do mesmo item
    ordem_ruim = []
    for sku, g in f.groupby("sku"):
        g = g.sort_values("unidade_de")
        comprou = g.comprar.to_numpy()
        if comprou.any() and (~comprou).any():
            # tudo que foi comprado tem de vir antes de tudo que nao foi
            if comprou[np.argmax(~comprou):].any():
                ordem_ruim.append(sku)
    r.afirma(B, "compras de um item param e nao voltam", not ordem_ruim,
             "" if not ordem_ruim else f"reinicio em {ordem_ruim[:5]}")

    # reconciliacao com o plano por item
    agg = f[f.comprar].groupby("sku").agg(
        q=("quantidade", "sum"), inv=("custo", "sum"), mar=("valor_esperado", "sum"))
    pl = plano.set_index("sku")
    idx = agg.index
    r.compara(B, "quantidade por item = pecas compradas na fila",
              agg.q.to_numpy(), pl.quantidade_a_comprar.reindex(idx).to_numpy())
    r.compara(B, "investimento por item = soma dos custos",
              agg.inv.to_numpy(), pl.valor_da_compra.reindex(idx).to_numpy(),
              tol=TOL_FROUXA)
    r.compara(B, "margem por item = soma dos valores",
              agg.mar.to_numpy(), pl.margem_esperada.reindex(idx).to_numpy(),
              tol=TOL_FROUXA)

    nao_comprados = pl.index.difference(idx)
    r.afirma(B, "item fora da fila tem quantidade zero",
             bool((pl.loc[nao_comprados, "quantidade_a_comprar"] == 0).all()),
             f"{len(nao_comprados)} itens sem compra neste ciclo")

    # caixa acumulado tem de ser a soma corrida dos custos comprados
    fo = f.sort_values("posicao_fila")
    esperado = (fo.custo * fo.comprar).cumsum().to_numpy()
    r.compara(B, "caixa acumulado = soma corrida do que foi comprado",
              esperado, fo.caixa_acumulado.to_numpy(), tol=TOL_FROUXA)
    r.compara(B, "caixa restante = teto - acumulado",
              (p.teto_compra_ciclo - esperado), fo.caixa_restante.to_numpy(),
              tol=TOL_FROUXA)


# ----------------------------------------------------------------------
# 6. a conta de cada peca, refeita do zero
# ----------------------------------------------------------------------
def bloco6(r: Relatorio, wh, p, ctx, amostra: int) -> None:
    B = "6. A conta de cada peca, refeita do zero"
    f = ctx["fila"]
    if len(f) > amostra:
        f = f.sample(amostra, random_state=7)

    calc = {k: [] for k in ("H", "mu", "sd", "dist", "cdf", "P", "M", "obs", "L",
                            "lim", "ganho", "custo_esp", "V", "custo", "vpr", "nota")}
    for x in f.itertuples(index=False):
        H = x.lead_time_dias + x.periodo_revisao_dias
        mu = x.demanda_dia_corrigida * H
        sd = x.desvio_dia * np.sqrt(H)
        var = sd ** 2
        if var <= mu * 1.05:
            d, nome = stats.poisson(mu), "Poisson"
        else:
            rr = mu * mu / (var - mu)
            d, nome = stats.nbinom(rr, rr / (rr + mu)), "Binomial Negativa"
        cdf = float(d.cdf(x.unidade_de - 1))
        P = 1 - cdf
        M = x.lucro_por_peca * x.fator_perda_ruptura
        obs = x.custo_unitario * x.perda_encalhe_pct
        L = x.custo_manter_no_periodo + obs
        q = x.quantidade
        ganho = P * M * q
        custo_esp = (1 - P) * L * q
        V = ganho - custo_esp
        custo = x.custo_unitario * q
        for k, v in [("H", H), ("mu", mu), ("sd", sd), ("dist", nome), ("cdf", cdf),
                     ("P", P), ("M", M), ("obs", obs), ("L", L),
                     ("lim", L / (M + L)), ("ganho", ganho), ("custo_esp", custo_esp),
                     ("V", V), ("custo", custo), ("vpr", V / custo),
                     ("nota", V / (custo * H))]:
            calc[k].append(v)

    r.afirma(B, "distribuicao escolhida por peca",
             bool((np.array(calc["dist"]) == f.distribuicao.to_numpy()).all()),
             f"{len(f)} pecas conferidas")
    for nome, chave, col in [
        ("horizonte H = prazo + revisao", "H", "horizonte"),
        ("mu = demanda diaria x H", "mu", "mu_periodo"),
        ("sigma = desvio diario x raiz(H)", "sd", "sd_periodo"),
        ("F(k-1) da distribuicao", "cdf", "cdf_ate_k_menos_1"),
        ("P = 1 - F(k-1)", "P", "p_vender"),
        ("M = lucro x fator de ruptura", "M", "margem_unit"),
        ("obsolescencia = c x % encalhe", "obs", "custo_obsolescencia"),
        ("L = carregar + obsolescencia", "L", "perda_unit"),
        ("limite = L/(M+L)", "lim", "limite_marginal_compra"),
        ("ganho = P x M x pecas", "ganho", "ganho_esperado"),
        ("custo esperado = (1-P) x L x pecas", "custo_esp", "custo_esperado"),
        ("V = ganho - custo esperado", "V", "valor_esperado"),
        ("investimento = c x pecas", "custo", "custo"),
        ("retorno por real = V / investimento", "vpr", "valor_por_real"),
        ("nota = V / investimento / H", "nota", "nota"),
    ]:
        r.compara(B, nome, calc[chave], f[col].to_numpy())


# ----------------------------------------------------------------------
# 7. sanidade economica
# ----------------------------------------------------------------------
def bloco7(r: Relatorio, wh, p, ctx) -> None:
    B = "7. Sanidade economica"
    m, f, plano, est = ctx["modelo"], ctx["fila"], ctx["plano"], ctx["estrategias"]

    neg = m[m.lucro_por_peca <= 0]
    r.afirma(B, "todo item tem margem positiva por peca", len(neg) == 0,
             "nenhum item vende no prejuizo" if len(neg) == 0 else
             f"{len(neg)} item(ns) com margem <= 0: "
             f"{neg[['sku','lucro_por_peca']].to_dict('records')[:5]}",
             alerta_em_vez=True)

    r.afirma(B, "margem por peca menor que o preco de tabela",
             bool((m.lucro_por_peca <= m.preco_tabela + 1e-6).all()),
             "sanidade de unidade: margem nao pode passar do preco")

    sem_venda = m[m.pecas_vendidas <= 0]
    r.afirma(B, "item sem venda no historico nao entra na compra",
             bool((plano.set_index("sku").loc[sem_venda.sku, "quantidade_a_comprar"] == 0).all())
             if len(sem_venda) else True,
             f"{len(sem_venda)} item(ns) sem venda no periodo", alerta_em_vez=True)

    # a comparacao entre estrategias tem de usar a mesma regua
    mar = est[est.estrategia.str.contains("marginal")].iloc[0]
    rep = est[est.estrategia.str.contains("ideal")].iloc[0]
    r.afirma(B, "alocacao marginal ganha da reposicao na propria regua",
             mar.valor_esperado >= rep.valor_esperado,
             f"marginal R$ {mar.valor_esperado:,.0f} vs reposicao R$ {rep.valor_esperado:,.0f} "
             f"· e esperado: o guloso maximiza exatamente essa soma")
    r.afirma(B, "as duas estrategias gastam caixa comparavel",
             abs(mar.investimento - rep.investimento) / max(rep.investimento, 1) < 0.05,
             f"marginal R$ {mar.investimento:,.0f} vs reposicao R$ {rep.investimento:,.0f} "
             f"· as duas usam a mesma regra de corte (pula o que nao cabe), "
             f"senao a comparacao seria injusta")

    baixa = int(mar.pecas_baixa_chance)
    r.afirma(B, "poucas pecas compradas com chance abaixo de 50%",
             baixa <= mar.pecas * 0.10,
             f"{baixa} de {int(mar.pecas)} ({baixa/max(mar.pecas,1):.1%})",
             alerta_em_vez=True)

    # cobertura: comprar nao pode deixar o item com cobertura absurda
    comprados = plano[plano.quantidade_a_comprar > 0]
    exagero = comprados[comprados.cobertura_apos_dias > 365]
    r.afirma(B, "nenhum item fica com mais de um ano de cobertura",
             len(exagero) == 0,
             "" if len(exagero) == 0 else
             f"{len(exagero)} item(ns) acima de 365 dias: "
             f"{exagero.nlargest(3,'cobertura_apos_dias')[['sku','cobertura_apos_dias']].to_dict('records')}",
             alerta_em_vez=True)

    # risco depois da compra tem de ser menor que antes
    piorou = comprados[comprados.risco_apos_compra > comprados.risco_de_faltar + 1e-9]
    r.afirma(B, "comprar reduz o risco de faltar", len(piorou) == 0,
             f"risco medio {comprados.risco_de_faltar.mean():.1%} -> "
             f"{comprados.risco_apos_compra.mean():.1%}")

    # a fila cobre todo item que tinha peca com retorno
    r.afirma(B, "fila cobre todos os itens com peca lucrativa",
             set(f.sku) == set(plano[plano.unidades_com_retorno > 0].sku),
             f"{f.sku.nunique()} itens na fila · "
             f"{int((plano.unidades_com_retorno>0).sum())} com peca lucrativa")


# ----------------------------------------------------------------------
# 8. as hipoteses do modelo confrontadas com o dado
# ----------------------------------------------------------------------
def _trechos_contiguos(g: pd.DataFrame) -> list[np.ndarray]:
    """Sequencias de dias seguidos COM estoque.

    Nao da para colar pedacos separados por uma ruptura: a serie do meio nao
    existe, e emendar cria uma correlacao que nunca houve.
    """
    ok = (g.estado_estoque == "Disponivel").to_numpy()
    v = g.pecas_vendidas.to_numpy(float)
    fora, atual = [], []
    for i in range(len(ok)):
        if ok[i]:
            atual.append(v[i])
        else:
            if len(atual) > 1:
                fora.append(np.array(atual))
            atual = []
    if len(atual) > 1:
        fora.append(np.array(atual))
    return fora


def bloco8(r: Relatorio, wh, p, ctx) -> None:
    B = "8. Hipoteses do modelo vs. o dado observado"
    m, dia = ctx["modelo"], ctx["dia"]
    horizonte = m.set_index("sku").periodo_protecao_dias

    # (a) superdispersao: a Binomial Negativa e escolhida por medicao, nao por gosto
    razao = np.where(m.demanda_media_dia > 0,
                     m.desvio_padrao_dia ** 2 / m.demanda_media_dia, np.nan)
    r.alerta(B, "superdispersao da demanda diaria",
             f"razao variancia/media: mediana {np.nanmedian(razao):.1f} · "
             f"minima {np.nanmin(razao):.2f} · maxima {np.nanmax(razao):.0f} — "
             f"so {int((razao <= 1.05).sum())} item cabe em Poisson (razao <= 1,05). "
             f"A demanda chega em pedidos grandes com muitos dias de zero: "
             f"forcar Poisson subestimaria a cauda por larga margem")

    # (b) independencia entre dias: e o que justifica sd_H = sd_dia x raiz(H)
    acf, blocos_z = {k: [0.0, 0] for k in range(1, 26)}, []
    for sku, g in dia.groupby("sku"):
        tr = _trechos_contiguos(g)
        if not tr:
            continue
        v = np.concatenate(tr)
        if len(v) < 40 or v.var() == 0:
            continue
        mu, var = v.mean(), v.var()
        for lag in range(1, 26):
            num = den = 0.0
            for t in tr:
                if len(t) <= lag:
                    continue
                num += ((t[:-lag] - mu) * (t[lag:] - mu)).sum()
                den += len(t) - lag
            if den > 0:
                acf[lag][0] += num / den / var
                acf[lag][1] += 1
        H = int(horizonte.get(sku, 0))
        if H >= 2:
            esc = np.sqrt(H * v.var(ddof=1))
            for t in tr:
                for i in range(len(t) // H):
                    blocos_z.append((t[i * H:(i + 1) * H].sum() - H * mu) / esc)

    curva = np.array([acf[k][0] / max(acf[k][1], 1) for k in range(1, 26)])
    # Var(soma de H) / (H x Var diaria) previsto pela autocorrelacao, em H=20
    prev = 1 + 2 * sum(curva[k - 1] * (1 - k / 20) for k in range(1, 20))
    z = np.array(blocos_z)
    obs = float(z.var(ddof=1)) if len(z) > 30 else np.nan

    r.afirma(B, "dias independentes (o que a raiz de H supoe)",
             abs(prev - 1) < 0.10 and (np.isnan(obs) or abs(obs - 1) < 0.10),
             f"soma da autocorrelacao nos lags 1..25 = {curva.sum():+.3f} "
             f"(zero se independente) · razao de variancia prevista {prev:.2f}, "
             f"medida em {len(z)} blocos independentes {obs:.2f} — "
             f"a demanda tem leve reversao a media, entao sd_dia x raiz(H) "
             f"SUPERESTIMA o desvio do horizonte em ~{100*(1/np.sqrt(obs)-1):.0f}%. "
             f"Erra para o lado seguro (mais folga), mas erra",
             alerta_em_vez=True)

    # sazonalidade semanal aparece como pico da autocorrelacao em 7, 14, 21
    semanal = float(np.mean([curva[6], curva[13], curva[20]]))
    outros = float(np.mean([curva[k] for k in range(25)
                            if (k + 1) % 7 != 0]))
    r.afirma(B, "sem sazonalidade semanal relevante", semanal - outros < 0.08,
             f"autocorrelacao media nos lags 7/14/21 = {semanal:+.3f} contra "
             f"{outros:+.3f} nos outros lags — ha efeito de dia da semana. "
             f"O modelo trata todos os dias como iguais",
             alerta_em_vez=True)

    # (c) o horizonte e tratado como fixo
    r.alerta(B, "prazo do fornecedor tratado como fixo",
             f"lead time entra como constante por item ({m.lead_time_dias.min():.0f} a "
             f"{m.lead_time_dias.max():.0f} dias). Atraso de entrega nao tem folga "
             f"propria no modelo — a base de dados nao traz prazo prometido vs. realizado")

    # (d) o horizonte cobre apenas um ciclo
    r.alerta(B, "peca que nao vende no horizonte nao e perda total",
             f"quem nao vende dentro do horizonte paga so carregamento + "
             f"{p.perda_encalhe:.0%} de encalhe, nao a margem inteira — e o que impede "
             f"o modelo de comprar so o que gira em dias. Se o item for de colecao ou "
             f"fim de linha, esse percentual precisa subir (30-50%)")


# ----------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--amostra", type=int, default=1500,
                    help="pecas conferidas peca a peca no bloco 6")
    ap.add_argument("--so", type=int, default=0, help="rodar so um bloco (1..8)")
    args = ap.parse_args()

    wh = abrir()
    p = Parametros.carregar()
    ctx = {
        "modelo": wh.query(f"select * from {ref('res_sku_modelo')}"),
        "plano": wh.query(f"select * from {ref('res_plano_compra')}"),
        "fila": wh.query(f"select * from {ref('res_fila_marginal')}"),
        "estrategias": wh.query(f"select * from {ref('res_estrategias')}"),
        "dia": wh.query(
            f"select sku, data, saldo_inicial, saldo_final, pecas_vendidas, "
            f"estado_estoque from {ref('mart_estoque_diario')} order by sku, data"),
    }

    print("=" * 78)
    print("REVISAO DO MOTOR DE CALCULO")
    print("=" * 78)
    print(f"  {len(ctx['modelo'])} itens · {len(ctx['fila']):,} pecas candidatas · "
          f"{len(ctx['dia']):,} dias-item")
    print(f"  lambda = {float(ctx['modelo'].premio_escassez.iloc[0]):.4f} · "
          f"teto de capital R$ {p.teto_capital:,.0f} · "
          f"caixa do ciclo R$ {p.teto_compra_ciclo:,.0f}")
    print(f"  perda por encalhe {p.perda_encalhe:.1%} · "
          f"lote minimo {'ligado' if p.respeitar_lote_minimo else 'desligado'}")

    r = Relatorio()
    blocos = [bloco1, bloco2, bloco3, bloco4, bloco5, None, bloco7, bloco8]
    for i, fn in enumerate(blocos, 1):
        if args.so and args.so != i:
            continue
        if i == 6:
            bloco6(r, wh, p, ctx, args.amostra)
        else:
            fn(r, wh, p, ctx)

    falhas = r.imprimir()
    raise SystemExit(1 if falhas else 0)


if __name__ == "__main__":
    main()
