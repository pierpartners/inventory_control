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

    # No extrato real o retrato de estoque e de um local (124) e a venda e do
    # e-commerce (empresa 33): sao entidades diferentes, e a venda pode ser
    # atendida de outro deposito. Alem disso o recebimento e lancado na virada
    # do dia. Entao a identidade nao fecha sempre - o que importa e o TAMANHO
    # da quebra, nao a existencia dela.
    excede = dia.pecas_vendidas > dia.saldo_inicial + 1e-9
    pecas_ex = float((dia.pecas_vendidas - dia.saldo_inicial)[excede].sum())
    total_v = float(dia.pecas_vendidas.sum())
    frac = pecas_ex / total_v if total_v else 0.0
    r.afirma(B, "venda do dia cabe no saldo inicial (ou quebra pouco)",
             frac < 0.01,
             f"{int(excede.sum()):,} dias-item de {len(dia):,} ({excede.mean():.2%}) · "
             f"{pecas_ex:,.0f} pecas de {total_v:,.0f} ({frac:.2%}) · "
             f"estoque de um local contra venda de outro canal",
             alerta_em_vez=True)

    # A posicao que decide a compra e o DISPONIVEL do ultimo dia, nao o estoque
    # fisico: a peca reservada ja tem dono e nao protege a proxima venda. As
    # duas colunas existem justamente porque confundi-las foi um erro real
    # desta migracao - `saldo_final` responde "havia mercadoria?" (sinal de
    # demanda) e `disponivel_final` responde "posso contar com ela?" (compra).
    ultimo = dia.data.max()
    col_pos = "disponivel_final" if "disponivel_final" in dia.columns else "saldo_final"
    pos_real = (dia[dia.data == ultimo][["sku", col_pos]]
                .set_index("sku")[col_pos])
    pl_i = plano.set_index("sku")
    fisico = (pl_i.estoque_fisico if "estoque_fisico" in pl_i.columns
              else pl_i.posicao_estoque).reindex(pos_real.index)
    r.compara(B, f"estoque fisico da posicao = {col_pos} do ultimo dia", pos_real.to_numpy(),
              fisico.to_numpy(), contexto=f"posicao de {str(ultimo)[:10]}")
    # ... e a posicao que decide soma o em transito: pedido colocado, ainda nao
    # entrado no CD, chegando dentro do periodo de protecao do item
    if "em_transito" in pl_i.columns:
        transito = pl_i.em_transito.fillna(0.0)
        r.afirma(B, "em transito nao e negativo", bool((transito >= 0).all()),
                 f"{float(transito.sum()):,.0f} pecas a caminho em "
                 f"{int((transito > 0).sum())} itens entram na posicao")
        r.compara(B, "posicao = disponivel + em transito",
                  (pl_i.estoque_fisico.fillna(0) + transito).to_numpy(),
                  pl_i.posicao_estoque.to_numpy(), contexto="soma das duas colunas")
    if col_pos == "disponivel_final":
        reservado = float((dia[dia.data == ultimo].saldo_final
                           - dia[dia.data == ultimo].disponivel_final).sum())
        r.afirma(B, "a reserva reduz a posicao de compra, e nao o sinal de demanda",
                 reservado >= 0,
                 f"{reservado:,.0f} pecas reservadas no ultimo dia ficam fora da "
                 f"posicao de compra mas continuam contando como prateleira cheia")

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

    # ------------------------------------------------------------------
    # a procedencia do custo unitario
    #
    # O custo e o denominador da nota. Errado, ele nao gera numero absurdo em
    # coluna nenhuma - gera nota alta, e nota alta manda comprar. Quando o
    # saldo de um item vai a zero o ERP zera o custo medio junto e lanca
    # R$ 1,00; uma prateleira de R$ 1.016 apareceu valendo um real e subiu ao
    # topo da fila com nota 51x a do segundo. Nenhum teste de identidade pega
    # isso, porque a conta estava certa e o insumo errado.
    cat = wh.query(f"""
        select sku, item, custo_unitario, custo_ultimo_lancado, custo_mediano
        from {ref('stg_catalogo')}""")

    # 1. nenhum custo saiu de um dia sem estoque. A consulta refaz a leitura
    #    crua: o ultimo lancamento de custo, com e sem a exigencia de estoque.
    try:
        cru = wh.query(f"""
            with t as (
                select cast(idsubproduto as varchar) sku,
                       cast(dtmovimento as date) dia,
                       cast(valcustomedio as double) c,
                       cast(qtdatualestoque as double) q
                from {ref('raw_estoque_diario_erp')} where valcustomedio > 0),
            qualquer as (
                select sku, c from (select sku, c,
                    row_number() over (partition by sku order by dia desc) rn
                    from t) where rn = 1),
            com_estoque as (
                select sku, c from (select sku, c,
                    row_number() over (partition by sku order by dia desc) rn
                    from t where q > 0) where rn = 1)
            select q.sku, q.c custo_qualquer_dia, e.c custo_dia_com_estoque
            from qualquer q join com_estoque e on e.sku = q.sku""")
    except Exception:
        cru = pd.DataFrame()

    if len(cru):
        j = cat.merge(cru, on="sku", how="inner")
        # o que o cadastro publica tem de ser a leitura COM estoque
        bate = np.isclose(j.custo_ultimo_lancado, j.custo_dia_com_estoque,
                          rtol=1e-6, atol=1e-6)
        contaminados = int((~np.isclose(j.custo_qualquer_dia,
                                        j.custo_dia_com_estoque,
                                        rtol=1e-6, atol=1e-6)).sum())
        r.afirma(B, "custo unitario vem de um dia COM estoque",
                 bool(bate.all()),
                 f"{int(bate.sum())} de {len(j)} itens conferem com a leitura "
                 f"crua de dia com estoque - {contaminados} deles teriam custo "
                 f"diferente se a exigencia caisse (o reset do ERP)")
    else:
        r.alerta(B, "custo unitario vem de um dia COM estoque",
                 "base sintetica: nao ha lancamento diario de custo para conferir")

    # 2. o custo publicado esta na faixa da mediana historica do proprio item.
    #    Custo sobe com o tempo, entao a faixa e larga de proposito: o que se
    #    procura e a ordem de grandeza, nao a variacao.
    v = cat[cat.custo_mediano > 0].copy()
    v["razao"] = v.custo_unitario / v.custo_mediano
    fora = v[(v.razao < 0.25) | (v.razao > 4.0)]
    r.afirma(B, "custo publicado na faixa da mediana do item",
             len(fora) == 0,
             f"{len(v)} itens com mediana · razao mediana {v.razao.median():.2f} · "
             f"faixa 0,25x a 4x" +
             ("" if not len(fora) else " · fora: " +
              str(fora.nsmallest(3, 'razao')[['item', 'custo_unitario',
                                              'custo_mediano']].to_dict('records'))))

    # 3. o cruzamento independente: o custo do cadastro contra o custo medio
    #    do que foi de fato vendido. Sao duas fontes que nao se falam - o
    #    estoque diario e a nota de venda -, e por isso a divergencia grande
    #    aponta insumo errado, nao arredondamento.
    cmv = wh.query(f"""
        select sku, sum(cmv) / nullif(sum(pecas_vendidas), 0) custo_vendido,
               sum(pecas_vendidas) q
        from {ref('stg_vendas')} group by 1 having sum(pecas_vendidas) > 0""")
    k = cat.merge(cmv, on="sku", how="inner")
    k = k[k.custo_vendido > 0].copy()
    k["dif"] = k.custo_unitario / k.custo_vendido - 1
    grave = k[k.dif.abs() > 2.0]
    r.afirma(B, "custo do cadastro proximo do custo do que foi vendido",
             len(grave) == 0,
             f"{len(k)} itens com venda · |diferenca| mediana "
             f"{k.dif.abs().median():.1%} · p95 {k.dif.abs().quantile(.95):.1%} · "
             f"{int((k.dif.abs() > 0.25).sum())} acima de 25% (custo sobe com o "
             f"tempo, e a media do vendido cobre 3 anos)" +
             ("" if not len(grave) else " · fora de 3x: " +
              str(grave.reindex(grave.dif.abs().sort_values(ascending=False).index)
                  [['item', 'custo_unitario', 'custo_vendido']].head(3)
                  .to_dict('records'))))

    # --- os dois canais fecham com o total, dia a dia e no agregado ---
    # A grade traz a venda por canal em formato LARGO porque a censura e do
    # estoque compartilhado: um dia de ruptura censura os dois canais. A
    # identidade abaixo vale por construcao (int_demanda_diaria) e e o que
    # permite estimar cada canal com as mesmas mascaras de dia do total.
    if {"pecas_ecommerce", "pecas_lojas"} <= set(dia.columns):
        r.compara(B, "pecas e-commerce + lojas = pecas vendidas (dia a dia)",
                  (dia.pecas_ecommerce + dia.pecas_lojas).to_numpy(),
                  dia.pecas_vendidas.to_numpy(), contexto="grade SKU x dia")
        r.afirma(B, "pecas por canal nao negativas",
                 bool((dia.pecas_ecommerce >= 0).all() and (dia.pecas_lojas >= 0).all()),
                 f"e-commerce {int(dia.pecas_ecommerce.sum()):,} pecas · "
                 f"lojas {int(dia.pecas_lojas.sum()):,} pecas na janela")
    else:
        r.falha(B, "grade diaria traz pecas por canal",
                "faltam pecas_ecommerce / pecas_lojas em mart_estoque_diario")
    fin = ctx["financeiro"]
    if {"pecas_vendidas_ecommerce", "lucro_observado_ecommerce"} <= set(fin.columns):
        r.compara(B, "financeiro: pecas por canal fecham com o total",
                  (fin.pecas_vendidas_ecommerce + fin.pecas_vendidas_lojas).to_numpy(),
                  fin.pecas_vendidas.to_numpy())
        r.compara(B, "financeiro: lucro por canal fecha com o total",
                  (fin.lucro_observado_ecommerce + fin.lucro_observado_lojas).to_numpy(),
                  fin.lucro_observado.to_numpy(), tol=TOL_FROUXA)
    else:
        r.falha(B, "financeiro traz colunas por canal",
                "faltam *_ecommerce / *_lojas em mart_sku_financeiro")


# ----------------------------------------------------------------------
# 2. correcao de censura (EM)
# ----------------------------------------------------------------------
def bloco2(r: Relatorio, wh, p, ctx) -> None:
    B = "2. Correcao de censura (EM)"
    m, dia = ctx["modelo"], ctx["dia"]

    r.afirma(B, "pecas imputadas nunca negativas",
             bool((m.pecas_imputadas >= -1e-9).all()),
             "imputar so pode aumentar a venda observada, nunca reduzir")

    # A media corrigida deveria ficar acima da media dos dias disponiveis: e a
    # mesma amostra mais os dias censurados, que valem >= o observado. Mas o EM
    # tem um TETO - a peca imputada nao passa do percentil 95 da distribuicao,
    # para que um dia de ruptura nao puxe a media sem limite (em_censurado,
    # cap_q=0,95). Esse teto pode deixar a corrigida um fio abaixo, e e
    # proposital. O que nao pode e o desvio ser grande: na base real o gap
    # maximo medido e de 0,009 un/dia, 14% em termos relativos num item de
    # demanda quase nula.
    # abaixo do piso de historico o item usa a media ingenua de proposito, e
    # por isso fica fora das duas comparacoes com a corrigida
    insuf = (m.historico_insuficiente.astype(bool) if "historico_insuficiente" in m.columns
             else pd.Series(False, index=m.index))
    piso = int(getattr(p, "dias_utilizaveis_minimo", 0) or 0)
    if piso > 0:
        abaixo = m[m.dias_utilizaveis < piso]
        r.afirma(B, f"abaixo do piso de {piso} dias utilizaveis, demanda = media ingenua",
                 bool((abaixo.historico_insuficiente.astype(bool)).all()
                      and np.allclose(abaixo.demanda_media_dia, abaixo.demanda_media_dia_ingenua)),
                 f"{len(abaixo)} itens abaixo do piso usam a media simples · "
                 f"a corrigida (EM) fica guardada em demanda_media_dia_em")
        r.afirma(B, "acima do piso, ninguem esta marcado como historico insuficiente",
                 bool((~insuf[m.dias_utilizaveis >= piso]).all()),
                 f"{int(insuf.sum())} marcados no total")
    comp = m[(m.dias_ruptura_parcial > 0) & (m.demanda_media_dia_disponivel > 0) & ~insuf]
    if len(comp):
        gap = comp.demanda_media_dia_disponivel - comp.demanda_media_dia
        rel = (gap / comp.demanda_media_dia_disponivel).abs()
        pior_rel = float(rel.max())
        r.afirma(B, "corrigida >= media dos dias disponiveis (fora do teto do EM)",
                 pior_rel < 0.20,
                 f"{int((gap > 1e-9).sum())} de {len(comp)} itens um fio abaixo pelo "
                 f"teto cap_q=0,95 · gap maximo {float(gap.max()):.4f} un/dia "
                 f"({pior_rel:.1%} relativo)",
                 alerta_em_vez=True)

    # e nao pode ficar abaixo da ingenua quando houve dia sem estoque - mesma
    # ressalva do teto acima
    comp2 = m[(m.dias_sem_estoque > 0) & ~insuf]
    if len(comp2):
        gap2 = comp2.demanda_media_dia_ingenua - comp2.demanda_media_dia
        r.afirma(B, "corrigida >= ingenua quando faltou estoque (fora do teto do EM)",
                 float(gap2.max()) < 0.10,
                 f"{len(comp2)} itens com dia zerado · maior gap "
                 f"{float(gap2.max()):.4f} un/dia",
                 alerta_em_vez=True)

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

    # --- os dois canais, estimados com as mesmas mascaras de dia ---
    cols = {"demanda_media_dia_ecommerce", "demanda_media_dia_lojas", "share_ecommerce",
            "demanda_media_dia_ingenua_ecommerce", "demanda_media_dia_ingenua_lojas"}
    if cols <= set(m.columns) and "pecas_ecommerce" in dia.columns:
        g_e = dia.groupby("sku").pecas_ecommerce.mean()
        g_l = dia.groupby("sku").pecas_lojas.mean()
        mi = m.set_index("sku")
        idx = g_e.index.intersection(mi.index)
        r.compara(B, "media ingenua do e-commerce = media da coluna na grade",
                  g_e.reindex(idx).to_numpy(), mi.demanda_media_dia_ingenua_ecommerce.reindex(idx).to_numpy(),
                  tol=TOL_FROUXA)
        r.compara(B, "media ingenua das lojas = media da coluna na grade",
                  g_l.reindex(idx).to_numpy(), mi.demanda_media_dia_ingenua_lojas.reindex(idx).to_numpy(),
                  tol=TOL_FROUXA)
        soma = m.demanda_media_dia_ecommerce + m.demanda_media_dia_lojas
        esperado = np.where(soma > 0, m.demanda_media_dia_ecommerce / soma.replace(0, np.nan), 0.0)
        r.compara(B, "participacao do e-commerce = mu_e / (mu_e + mu_l)",
                  np.nan_to_num(esperado), m.share_ecommerce.to_numpy(), tol=TOL_FROUXA)
        r.afirma(B, "participacao do e-commerce em [0, 1]",
                 bool(((m.share_ecommerce >= 0) & (m.share_ecommerce <= 1)).all()),
                 f"mediana {float(m.share_ecommerce.median()):.1%} · "
                 f"{int((m.share_ecommerce > 0.5).sum())} itens com o e-commerce majoritario")
        # A correcao de censura e nao linear: a soma das medias corrigidas por
        # canal nao precisa bater com a media corrigida do total. O tamanho da
        # diferenca e diagnostico, nao erro.
        com = m.demanda_media_dia > 0
        gap = ((soma - m.demanda_media_dia) / m.demanda_media_dia)[com]
        r.alerta(B, "soma dos canais corrigidos vs. total corrigido",
                 f"diferenca relativa mediana {float(gap.median()):+.2%} · "
                 f"p95 {float(gap.abs().quantile(.95)):.2%} · a EM por canal e a EM do total "
                 f"nao somam exatamente, e o modelo usa o total para a politica")
    else:
        r.falha(B, "modelo traz a demanda por canal",
                "faltam demanda_media_dia_ecommerce / _lojas / share_ecommerce em res_sku_modelo")


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
    # Var(D_H) = (E[L]+R) x Var(d) + E[d]^2 x Var(L). O segundo termo e a
    # variancia do PRAZO DE ENTREGA, que a base real traz e a sintetica nao.
    # Com desvio de prazo zero a conta recai exatamente na antiga,
    # sigma_dia x raiz(H) - e foi assim que este teste ficou desatualizado sem
    # ninguem notar quando o motor passou a somar o segundo termo.
    sd_lead = (m.lead_time_desvio_dias.fillna(0.0)
               if "lead_time_desvio_dias" in m.columns
               else pd.Series(0.0, index=m.index))
    r.compara(B, "sigma do horizonte = raiz(H x Var(d) + d^2 x Var(L))",
              np.sqrt(m.periodo_protecao_dias * m.desvio_padrao_dia ** 2
                      + m.demanda_media_dia ** 2 * sd_lead ** 2).to_numpy(),
              m.sd_periodo.to_numpy())

    # a escolha Poisson x NB e exatamente o teste de dispersao
    razao = np.where(m.mu_periodo > 0, m.sd_periodo ** 2 / m.mu_periodo, np.nan)
    escolha = np.where(m.mu_periodo <= 0, "Sem historico",
                       np.where(razao <= 1.05, "Poisson", "Binomial Negativa"))
    r.afirma(B, "escolha da distribuicao segue o teste variancia/media",
             bool((escolha == m.distribuicao.to_numpy()).all()),
             f"{int((escolha=='Poisson').sum())} Poisson · "
             f"{int((escolha=='Binomial Negativa').sum())} Binomial Negativa")

    # Com prazo FIXO, somar dias nao cria nem destroi superdispersao e a razao
    # variancia/media e a mesma no dia e no horizonte. Com prazo VARIAVEL deixa
    # de ser: o termo d^2 x Var(L) soma variancia sem somar media, e a razao no
    # horizonte sobe de proposito. O teste passa a cobrar a identidade so onde
    # ela vale, e a medir o efeito onde nao vale.
    razao_dia = np.where(m.demanda_media_dia > 0,
                         m.desvio_padrao_dia ** 2 / m.demanda_media_dia, np.nan)
    fixo = (m.lead_time_desvio_dias.fillna(0.0).to_numpy() == 0
            if "lead_time_desvio_dias" in m.columns
            else np.ones(len(m), dtype=bool))
    if fixo.any():
        r.compara(B, "razao no horizonte = razao no dia (onde o prazo e fixo)",
                  razao_dia[fixo], razao[fixo], tol=TOL_FROUXA,
                  contexto=f"{int(fixo.sum())} itens de prazo fixo · "
                           f"somar dias nao cria nem destroi superdispersao")
    if (~fixo).any():
        v = np.where(m.demanda_media_dia > 0, razao / razao_dia, np.nan)[~fixo]
        v = v[np.isfinite(v)]
        r.alerta(B, "prazo variavel infla a superdispersao no horizonte",
                 f"{int((~fixo).sum())} itens com prazo variavel · a razao "
                 f"variancia/media no horizonte fica {np.nanmedian(v):.1f}x a do "
                 f"dia (mediana) · e o termo d^2 x Var(L), nao um erro de conta")

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
    # o custo de ruptura e a media por canal: a margem e a perda do e-commerce
    # sao outras, e a participacao dele no item pondera as duas
    share = (m.share_ecommerce.fillna(0.0) if "share_ecommerce" in m.columns
             else pd.Series(0.0, index=m.index))
    le = m.lucro_por_peca_ecommerce if "lucro_por_peca_ecommerce" in m.columns else m.lucro_por_peca
    ll = m.lucro_por_peca_lojas if "lucro_por_peca_lojas" in m.columns else m.lucro_por_peca
    Cu = (share * le * getattr(p, "fator_perda_ruptura_ecommerce", p.fator_perda_ruptura)
          + (1 - share) * ll * getattr(p, "fator_perda_ruptura_lojas", p.fator_perda_ruptura))
    Co = c * (p.taxa_manutencao_ano + lam) * m.periodo_protecao_dias / p.dias_por_ano

    r.compara(B, "custo de manter na decisao = c x (taxa + lambda)",
              h_dec.to_numpy(), m.custo_manter_unit_decisao.to_numpy())
    r.compara(B, "custo de manter real = c x taxa",
              (c * p.taxa_manutencao_ano).to_numpy(), m.custo_manter_unit_real.to_numpy())
    r.compara(B, "margem perdida na ruptura = media por canal de lucro x fator",
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

    # --- pedidos por ano vs. janelas de risco por ano ---
    # As duas contagens ja foram a mesma variavel, e isso cobrava um pedido
    # por revisao nos itens de giro baixo: 43 pedidos/ano onde o lote minimo
    # do fornecedor so permite 1,6. Inflava o custo de pedir em R$ 162 mil e
    # jogava 13 itens para lucro negativo. Estes tres testes existem para
    # que a confusao nao volte.
    Q = np.where(lento, np.maximum(1, m.lote_minimo_compra), Q_n)
    with np.errstate(divide="ignore", invalid="ignore"):
        fisico = np.where(Q > 0, D / Q, 0.0)
    revisoes = (p.dias_por_ano / max(p.periodo_revisao_dias, 1) *
                (1 - stats.poisson.cdf(0, m.demanda_media_dia * p.periodo_revisao_dias)))
    r.compara(B, "pedidos por ano = min(D/Q, revisoes com demanda)",
              np.where(lento, np.minimum(fisico, revisoes), fisico),
              m.pedidos_por_ano.to_numpy())
    r.compara(B, "janelas de risco por ano (revisoes no discreto, D/Q no continuo)",
              np.where(lento, revisoes, fisico),
              m.janelas_de_risco_ano.to_numpy())
    r.afirma(B, "nenhum item pede mais vezes do que o lote permite",
             bool((m.pedidos_por_ano.to_numpy() <= fisico + 1e-6).all()),
             f"maior excesso: "
             f"{float(np.max(m.pedidos_por_ano.to_numpy() - fisico)):.4f} pedidos/ano")
    r.compara(B, "custo de pedir = pedidos x custo por pedido",
              (m.pedidos_por_ano * p.custo_por_pedido).to_numpy(),
              m.custo_pedir_ano.to_numpy())

    r.compara(B, "capital imobilizado = estoque medio x custo",
              (m.estoque_medio * m.custo_unitario).to_numpy(),
              m.capital_imobilizado.to_numpy())
    r.compara(B, "custo total = manter + pedir + ruptura",
              (m.custo_manter_ano + m.custo_pedir_ano + m.custo_ruptura_ano).to_numpy(),
              m.custo_total_ano.to_numpy())
    r.compara(B, "lucro liquido = bruto - custo total",
              (m.lucro_bruto_ano - m.custo_total_ano).to_numpy(),
              m.lucro_liquido_ano.to_numpy())

    # --- o espelho do corte historico ---
    # backend/modelo.py mantem uma copia em SQL de mart_sku_financeiro com um
    # filtro de data, para a validacao historica poder rodar o motor "como se
    # fosse" um dia passado. Duas copias da mesma regra sempre divergem com o
    # tempo; este teste e o que impede. O comentario do motor prometia esta
    # verificacao antes de ela existir - foi uma auditoria que apontou.
    from backend.modelo import ler_base
    ultimo = str(wh.query(
        f"select max(data) d from {ref('mart_estoque_diario')}").d.iloc[0])[:10]
    espelho, _ = ler_base(wh, ultimo)
    oficial, _ = ler_base(wh, None)
    cols = sorted(set(espelho.columns) & set(oficial.columns))
    a = espelho.sort_values("sku").reset_index(drop=True)[cols]
    b_ = oficial.sort_values("sku").reset_index(drop=True)[cols]
    # erro RELATIVO, nao absoluto: um ULP de um valor de R$ 1 milhao ja passa
    # de 1e-9 em termos absolutos, e a soma de uma coluna de receita muda de
    # ULP so por ordem de somatorio. O que se cobra aqui e que as duas copias
    # da regra calculem a mesma coisa, e isso se mede em escala relativa.
    pior, onde = 0.0, ""
    for c in cols:
        if pd.api.types.is_numeric_dtype(b_[c]):
            x = a[c].astype(float).to_numpy()
            y = b_[c].astype(float).to_numpy()
            val = np.isfinite(x) & np.isfinite(y)
            if not val.any():
                continue
            dd = float((np.abs(x[val] - y[val])
                        / np.maximum(np.abs(y[val]), 1.0)).max())
            if dd > pior:
                pior, onde = dd, c
        elif not a[c].equals(b_[c]):
            pior, onde = float("inf"), c
    r.afirma(B, "o corte historico na ultima data reproduz o mart do dbt",
             pior < TOL,
             f"maior divergencia relativa {pior:.2e}"
             + (f" na coluna {onde}" if onde else "")
             + f" · {len(cols)} colunas conferidas em {ultimo}")

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
        # ARREDONDA, nao trunca: candidatas_marginais faz
        # int(max(0, round(posicao))), e no dado real a posicao e fracionaria -
        # laminado e vendido em metro quadrado, e um item com 2,965 m2 tem a
        # primeira candidata na unidade 4, nao na 3. O teste truncava e
        # acusava furo onde o motor esta certo.
        base = int(max(0, round(pos)))
        if u[0] != base + 1 or not np.array_equal(u, np.arange(u[0], u[0] + len(u))):
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
        # dois termos: a demanda que varia ao longo de H, e o proprio H que
        # varia porque o fornecedor atrasa
        sd_prazo = float(getattr(x, "desvio_prazo_dias", 0.0) or 0.0)
        sd = np.sqrt(H * x.desvio_dia ** 2
                     + x.demanda_dia_corrigida ** 2 * sd_prazo ** 2)
        var = sd ** 2
        if var <= mu * 1.05:
            d, nome = stats.poisson(mu), "Poisson"
        else:
            rr = mu * mu / (var - mu)
            d, nome = stats.nbinom(rr, rr / (rr + mu)), "Binomial Negativa"
        cdf = float(d.cdf(x.unidade_de - 1))
        P = 1 - cdf
        M = (x.share_ecommerce * x.lucro_por_peca_ecommerce * x.fator_perda_ruptura_ecommerce
             + (1 - x.share_ecommerce) * x.lucro_por_peca_lojas * x.fator_perda_ruptura_lojas)
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
        ("sigma = raiz(H x Var(d) + d^2 x Var(L))", "sd", "sd_periodo"),
        ("F(k-1) da distribuicao", "cdf", "cdf_ate_k_menos_1"),
        ("P = 1 - F(k-1)", "P", "p_vender"),
        ("M = s x lucro_e x fator_e + (1-s) x lucro_l x fator_l", "M", "margem_unit"),
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

    # O extrato real nao tem preco de cadastro; stg_catalogo usa o preco
    # praticado MEDIANO como referencia. O lucro por peca vem da media
    # ponderada de tres anos. Duas agregacoes sobre a mesma serie: em item cujo
    # preco subiu, o lucro pode passar a mediana. Medido: 2 itens de 1.190.
    acima = m.lucro_por_peca > m.preco_tabela + 1e-9
    r.afirma(B, "margem por peca menor que a referencia de preco",
             float(acima.mean()) < 0.02,
             f"{int(acima.sum())} de {len(m)} itens acima · a referencia e o preco "
             f"praticado mediano, nao um preco de tabela de cadastro",
             alerta_em_vez=True)

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
    # A comparacao de gasto so e justa quando o CAIXA e o que limita as duas.
    # Com um criterio de piso ligado (retorno ou chance), a alocacao marginal
    # para antes por decisao, nao por falta de dinheiro - na base real o piso
    # de chance de 50% corta 1.356 de 1.779 pecas candidatas.
    from backend.modelo import criterios_ativos
    ativos = criterios_ativos(p)
    so_caixa = set(ativos) <= {"caixa"}
    if so_caixa:
        r.afirma(B, "as duas estrategias gastam caixa comparavel",
                 abs(mar.investimento - rep.investimento) / max(rep.investimento, 1) < 0.35,
                 f"marginal R$ {mar.investimento:,.0f} vs reposicao "
                 f"R$ {rep.investimento:,.0f} · as duas usam a mesma regra de corte")
    else:
        r.alerta(B, "gasto das estrategias nao e comparavel com piso ligado",
                 f"criterios ativos: {','.join(ativos)} · marginal "
                 f"R$ {mar.investimento:,.0f} vs reposicao R$ {rep.investimento:,.0f} · "
                 f"a diferenca e o piso cortando, nao o caixa")

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

    # (c) o prazo de entrega: fixo ou medido?
    if "lead_time_desvio_dias" in m.columns and m.lead_time_desvio_dias.fillna(0).max() > 0:
        sd_lead = m.lead_time_desvio_dias.fillna(0.0)
        sem = m.desvio_padrao_dia * np.sqrt(m.periodo_protecao_dias)
        efeito = float(m.sd_periodo.sum() / sem.sum() - 1) if sem.sum() else 0.0
        var_prazo = (m.demanda_media_dia ** 2) * (sd_lead ** 2)
        var_dem = m.periodo_protecao_dias * m.desvio_padrao_dia ** 2
        r.afirma(B, "prazo do fornecedor medido, com a variacao dele no estoque",
                 True,
                 f"prazo realizado mediano {m.lead_time_dias.median():.0f} dias com "
                 f"desvio mediano {sd_lead.median():.1f} · o termo d^2 x Var(L) "
                 f"aumenta sigma do horizonte em {efeito:+.1%} no agregado e domina a "
                 f"variancia em {int((var_prazo > var_dem).sum())} de {len(m)} itens")
        poucos = int((m.lead_time_pedidos < 3).sum()) if "lead_time_pedidos" in m.columns else 0
        if poucos:
            r.alerta(B, "prazo estimado com poucos pedidos em parte do catalogo",
                     f"{poucos} itens com menos de 3 pedidos de compra no periodo · "
                     f"a mediana e o desvio do prazo desses itens repousam em uma ou "
                     f"duas observacoes, ou herdam a mediana do catalogo")
    else:
        r.alerta(B, "prazo do fornecedor tratado como fixo",
                 f"lead time entra como constante por item ({m.lead_time_dias.min():.0f} a "
                 f"{m.lead_time_dias.max():.0f} dias). Atraso de entrega nao tem folga "
                 f"propria no modelo — esta base nao traz prazo prometido vs. realizado")

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
        # A grade diaria que os testes usam tem de ser a MESMA que o motor leu:
        # ele agora estima a demanda numa janela finita (janela_estimacao_dias),
        # e recomputar sobre o historico inteiro compararia dois modelos
        # diferentes. Foi exatamente esse descuido que fez quatro testes
        # falharem quando a janela entrou.
        # `ler_base` traz so as colunas do motor; os testes precisam tambem de
        # saldo_inicial. Mesma janela, colunas completas.
        "dia": wh.query(
            f"select * from {ref('mart_estoque_diario')} "
            f"where data > (select max(data) from {ref('mart_estoque_diario')})"
            f" - INTERVAL {int(getattr(p, 'janela_estimacao_dias', 0) or 99999)} DAY "
            f"order by sku, data"),
        "dia_completo": wh.query(
            f"select * from {ref('mart_estoque_diario')} order by sku, data"),
        "financeiro": wh.query(f"select * from {ref('mart_sku_financeiro')}"),
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
