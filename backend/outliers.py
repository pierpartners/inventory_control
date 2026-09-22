# -*- coding: utf-8 -*-
"""Outliers do catalogo: itens cujo numero de entrada merece um olhar humano.

Nao e metodologia nova: cada regra le colunas que `modelo.executar()` ja
gravou em res_sku_modelo / res_plano_compra e pergunta "isto e plausivel para
este catalogo?". O limiar de cada regra e relativo (percentil do proprio
catalogo) com um piso absoluto, para nao sinalizar metade dos itens numa base
onde todos os prazos sao longos - nem deixar passar um prazo de 130 dias so
porque a mediana e alta.

Cada regra aponta o campo de `ajustes.CAMPOS` que corrige o problema. A tela
de outliers usa isso para abrir o formulario ja no campo certo.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .warehouse import Warehouse, ref
from .config import Parametros
from . import ajustes, modelo

# chave, rotulo, gravidade (cr=vermelho, am=ambar), campo(s) a ajustar separados
# por virgula, explicacao
REGRAS = [
    ("cv_alto", "Venda errática", "am", "desvio_padrao_dia",
     "Coeficiente de variação diário no topo do catálogo com demanda relevante: a "
     "proteção contra a variabilidade domina o estoque deste item."),
    ("pico_unico", "Um dia domina a história", "cr", "demanda_media_dia",
     "O maior dia de venda vale mais da metade de tudo que o item vendeu: a média está "
     "puxada por um evento isolado (venda sob encomenda, transferência, lançamento errado)."),
    ("censura_extrema", "Correção de ruptura extrema", "cr", "demanda_media_dia",
     "A demanda corrigida ficou muito acima da observada: o item passou quase o tempo todo "
     "sem estoque e a correção está extrapolando de poucos dias."),
    ("prazo_longo", "Prazo de entrega muito longo", "am", "lead_time_dias",
     "Prazo medido no topo do catálogo. Prazo longo infla o período de proteção e, com ele, "
     "o ponto de pedido e o capital parado."),
    ("prazo_incerto", "Prazo mal medido", "am", "lead_time_dias,lead_time_desvio_dias",
     "Prazo estimado com menos de 3 pedidos, ou com desvio maior que a própria média: um "
     "pedido atrasado sozinho está definindo o estoque de segurança."),
    ("custo_movimento", "Custo fora do padrão histórico", "am", "custo_unitario",
     "Custo de hoje muito diferente do custo mediano lançado: pode ser reajuste real ou "
     "lançamento errado — nos dois casos margem e nota mudam."),
    ("margem_negativa", "Vende abaixo do custo", "cr", "custo_unitario",
     "Preço praticado na janela menor que o custo de hoje. Ou o custo está errado, ou o item "
     "não deveria ser reposto."),
    ("margem_atipica", "Margem fora do padrão da família", "am", "custo_unitario",
     "Custo e preço médio praticado não conversam: a margem deste item está a mais de 20 pontos "
     "da margem típica da família. Ou o custo está errado, ou o preço — nos dois casos a nota "
     "de retorno sai distorcida."),
    ("compra_desproporcional", "Compra desproporcional à venda", "cr", "demanda_media_dia,lead_time_dias",
     "O plano compra mais que meio ano de demanda de uma vez, ou um valor no topo do "
     "catálogo: sinal de que a entrada (demanda ou prazo) está errada."),
    ("historico_curto", "Histórico insuficiente", "am", "demanda_media_dia",
     "Poucos dias utilizáveis na janela: o modelo caiu para a média ingênua e o número "
     "é frágil."),
]
CHAVES = {r[0]: r for r in REGRAS}


def _q(s: pd.Series, q: float, piso: float) -> float:
    s = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if s.empty:
        return piso
    return float(max(np.quantile(s, q), piso))


def limiares(m: pd.DataFrame) -> dict:
    """Os cortes efetivos desta base - a tela mostra, para o leitor saber por
    que um item foi ou nao sinalizado."""
    com_venda = m[m.demanda_media_dia > 0]
    return {
        "cv_alto": _q(com_venda.cv_diario, 0.95, 5.0),
        "demanda_relevante": _q(com_venda.demanda_media_dia, 0.50, 0.05),
        "pico_unico": 0.5,
        "censura_extrema": 1.0,
        "prazo_longo": _q(m.lead_time_dias, 0.95, 60),
        "prazo_pedidos_min": 3,
        "custo_movimento": 0.5,
        "compra_dias": 180,
        "margem_desvio": 0.20,
        "compra_valor": _q(m.valor_da_compra[m.valor_da_compra > 0], 0.99, 50_000)
        if "valor_da_compra" in m.columns else float("inf"),
    }


def carregar(wh: Warehouse) -> pd.DataFrame:
    m = wh.query(f"select * from {ref('res_sku_modelo')}")
    pl = wh.query(
        f"select sku, posicao_estoque, quantidade_a_comprar, valor_da_compra, decisao, "
        f"cobertura_apos_dias, risco_de_faltar from {ref('res_plano_compra')}")
    m = m.merge(pl, on="sku", how="left")
    for c in ("quantidade_a_comprar", "valor_da_compra"):
        m[c] = pd.to_numeric(m[c], errors="coerce").fillna(0.0)
    if "ajustes" not in m.columns:
        m["ajustes"] = ""
    m["ajustes"] = m["ajustes"].fillna("")
    return m


def sinalizar(m: pd.DataFrame, lim: dict | None = None) -> pd.DataFrame:
    """Uma coluna booleana por regra + `motivos` (lista) + `gravidade`."""
    lim = lim or limiares(m)
    m = m.copy()
    dem = m.demanda_media_dia.fillna(0.0)
    total_vendido = (dem * m.dias_historico.fillna(0)).replace(0, np.nan)
    lead = m.lead_time_dias.fillna(0.0)
    sd_lead = m.lead_time_desvio_dias.fillna(0.0) if "lead_time_desvio_dias" in m.columns else 0.0
    pedidos_lt = m.lead_time_pedidos.fillna(0) if "lead_time_pedidos" in m.columns else 99
    custo_med = m.custo_mediano.replace(0, np.nan) if "custo_mediano" in m.columns else np.nan
    vendeu = m.pecas_vendidas.fillna(0) > 0

    # So vale sinalizar onde o modelo AGE: item que esta na compra, tem ponto
    # de pedido positivo ou e curva A/B. Nos outros 12 mil itens do catalogo o
    # numero pode estar errado sem consequencia - e sinalizar metade da base
    # e o mesmo que nao sinalizar nada.
    relevante = ((m.quantidade_a_comprar > 0) | (m.ponto_de_pedido.fillna(0) > 0)
                 | m.curva_abc.isin(["A", "B"]))
    com_estoque = relevante | (m.posicao_estoque.fillna(0) > 0)
    mediana_lead = float(lead[lead > 0].median()) if (lead > 0).any() else 0.0
    m["relevante"] = relevante

    m["f_cv_alto"] = relevante & (dem >= lim["demanda_relevante"]) & (m.cv_diario >= lim["cv_alto"])
    m["f_pico_unico"] = relevante & vendeu & (m.demanda_max_dia / total_vendido >= lim["pico_unico"]) \
        & (m.dias_com_venda.fillna(0) >= 3)
    m["f_censura_extrema"] = relevante & (dem > 0) & (m.subestimacao_ingenua_pct >= lim["censura_extrema"])
    m["f_prazo_longo"] = relevante & (lead >= lim["prazo_longo"])
    # prazo com poucos pedidos so preocupa quando saiu longo: um prazo curto
    # mal medido erra pouco. Desvio maior que a media preocupa sempre.
    m["f_prazo_incerto"] = relevante & (lead > 0) & (
        ((pedidos_lt < lim["prazo_pedidos_min"]) & (lead > 2 * mediana_lead)) | (sd_lead > lead))
    m["f_custo_movimento"] = com_estoque & ((m.custo_unitario / custo_med - 1).abs() >= lim["custo_movimento"])
    m["f_margem_negativa"] = com_estoque & vendeu & (m.lucro_por_peca <= 0)
    # custo amarrado ao preco medio: a margem (preco praticado - custo) / preco
    # de cada item e comparada com a MEDIANA da familia dele, nao com um numero
    # fixo - piso porcelanato e acessorio de pintura nao praticam a mesma margem.
    # O custo que devolveria a margem tipica fica gravado como sugestao.
    preco = m.preco_liquido_peca.where(m.preco_liquido_peca > 0)
    m["margem_item"] = (preco - m.custo_unitario) / preco
    base_fam = m[relevante & vendeu & preco.notna()]
    tipica_fam = base_fam.groupby("familia").margem_item.median()
    tipica_geral = float(base_fam.margem_item.median()) if len(base_fam) else 0.3
    m["margem_tipica"] = m.familia.map(tipica_fam).fillna(tipica_geral)
    m["custo_sugerido"] = (preco * (1 - m.margem_tipica)).round(2)
    m["f_margem_atipica"] = com_estoque & vendeu & preco.notna() & (m.lucro_por_peca > 0) & (
        (m.margem_item - m.margem_tipica).abs() >= lim["margem_desvio"])
    m["f_compra_desproporcional"] = (m.quantidade_a_comprar > 0) & (
        (m.quantidade_a_comprar > lim["compra_dias"] * dem.replace(0, np.nan))
        | (m.valor_da_compra >= lim["compra_valor"]))
    # historico fragil so importa quando o modelo esta gastando dinheiro nele
    m["f_historico_curto"] = (m.quantidade_a_comprar > 0) & (
        m.historico_insuficiente.fillna(False).astype(bool)
        if "historico_insuficiente" in m.columns else False)
    m["impacto"] = m.capital_imobilizado.fillna(0) + m.valor_da_compra.fillna(0)

    cols = [f"f_{r[0]}" for r in REGRAS]
    for c in cols:
        m[c] = m[c].fillna(False).astype(bool)
    m["motivos"] = [[r[0] for r in REGRAS if row[f"f_{r[0]}"]] for _, row in m[cols].iterrows()]
    m["n_motivos"] = m.motivos.map(len)
    m["gravidade"] = m.motivos.map(
        lambda ms: "cr" if any(CHAVES[k][2] == "cr" for k in ms) else ("am" if ms else ""))
    return m


# ----------------------------------------------------------------------
# custo do erro
# ----------------------------------------------------------------------
# Um motivo diz que o numero de entrada parece errado; nao diz quanto isso
# custa. A conta abaixo responde "se a suspeita estiver certa, quanto eu perco
# comprando o que o plano manda?". E um arrependimento, na regua do plano:
#
#     V'(q) = soma, peca a peca, de  P(vender) x Cu - P(encalhar) x perda
#                                    - corte x custo x dias_capital
#
# O ultimo termo e o custo de oportunidade do caixa: o real gasto neste item
# deixa de comprar a ultima peca que o plano comprou (a `nota` de corte da
# fila). Com ele, a quantidade que maximiza V' e exatamente a que a fila
# compraria com o dado corrigido - e o arrependimento nunca e negativo:
#
#     custo do erro = V'_certo(q_certo) - V'_certo(q_atual)
#
# avaliado no mundo corrigido. A mesma conta no mundo atual e o custo de
# corrigir sem precisar - se a suspeita estiver errada e o ajuste for aplicado.
# As duas saem por ciclo de compra (o horizonte de protecao do item) e,
# para a fila, por mes: um ciclo de 12 dias e um de 250 nao se comparam, e o
# item de ciclo curto repete o erro muitas vezes no mesmo mes. O erro se
# repete a cada compra de quem usa o numero errado: o custo do erro, no ciclo
# que o plano enxerga HOJE (e nele que ele recompra); o custo de corrigir sem
# precisar, no ciclo CORRIGIDO. A leitura supoe que o erro persiste enquanto
# nao for corrigido - exata para a compra a menos (a margem perdida volta a
# cada compra), generosa para a compra a mais (parte do excedente e custo de
# uma vez so).
DIAS_MES = 30.0

def nota_de_corte(fila: pd.DataFrame) -> float:
    """A nota da ultima peca que o plano comprou. Se a fila inteira coube no
    caixa, o corte e zero: toda peca de valor positivo seria comprada."""
    if fila is None or fila.empty or "comprar" not in fila.columns:
        return 0.0
    comprou = fila.comprar.fillna(False).astype(bool)
    if comprou.all() or not comprou.any():
        return 0.0
    return float(fila.nota[comprou].min())


def hipoteses(s: pd.DataFrame, lim: dict) -> dict[str, dict]:
    """O valor corrigido que cada motivo sugere, no formato de `ajustes`.

    Nao e a correcao certa - e a hipotese que o proprio motivo carrega (o
    pico isolado nao existiu, o prazo e o da familia, o custo e o mediano).
    Varios motivos no mesmo campo: fica o valor mais conservador (o menor).
    `_limitar` marca o item em que so a compra desproporcional disparou: sem
    campo a corrigir, a hipotese e a propria compra limitada ao corte de dias.
    """
    lead = s.lead_time_dias.fillna(0.0)
    com_lead = s[lead > 0]
    med_fam = com_lead.groupby("familia").lead_time_dias.median()
    med_ger = float(com_lead.lead_time_dias.median()) if len(com_lead) else 0.0

    def num(v, padrao=0.0):
        v = float(v) if v is not None and pd.notna(v) else padrao
        return v if np.isfinite(v) else padrao

    fora: dict[str, dict] = {}
    for r in s[s.n_motivos > 0].itertuples(index=False):
        ms = set(r.motivos)
        dem, sd = num(r.demanda_media_dia), num(r.desvio_padrao_dia)
        lt = num(r.lead_time_dias)
        sd_lt = num(getattr(r, "lead_time_desvio_dias", 0.0))
        dems, sds = [], []
        if "pico_unico" in ms:
            n = max(num(r.dias_historico), 2.0)
            pico = num(r.demanda_max_dia)
            dems.append(max(0.0, (dem * n - pico) / n))
            sds.append(float(np.sqrt(max(0.0, (sd ** 2 * n - (pico - dem) ** 2) / (n - 1)))))
        if "censura_extrema" in ms:
            teto = num(r.demanda_media_dia_ingenua) * (1 + lim["censura_extrema"])
            if 0 < teto < dem:
                dems.append(teto)
                sds.append(sd * teto / dem)
        if "cv_alto" in ms:
            sds.append(dem * lim["cv_alto"])
        h: dict = {}
        if dems and min(dems) < dem:
            h["demanda_media_dia"] = min(dems)
        if sds and min(sds) < sd:
            h["desvio_padrao_dia"] = min(sds)

        tipico = num(med_fam.get(r.familia, med_ger), med_ger)
        pedidos = num(getattr(r, "lead_time_pedidos", 0.0))
        if ("prazo_longo" in ms or ("prazo_incerto" in ms and pedidos < lim["prazo_pedidos_min"])) \
                and 0 < tipico < lt:
            h["lead_time_dias"] = tipico
        if "prazo_incerto" in ms and sd_lt > h.get("lead_time_dias", lt):
            h["lead_time_desvio_dias"] = h.get("lead_time_dias", lt)

        custo = num(r.custo_unitario)
        if "custo_movimento" in ms:
            novo = num(r.custo_mediano)
        elif ms & {"margem_negativa", "margem_atipica"}:
            novo = num(r.custo_sugerido)
        else:
            novo = 0.0
        if novo > 0 and abs(novo - custo) > 0.005:
            h["custo_unitario"] = novo

        if not h and "compra_desproporcional" in ms:
            h["_limitar"] = True
        if h:
            fora[str(r.sku)] = h
    return fora


def _valor(df: pd.DataFrame, q: np.ndarray, p: Parametros, corte: float) -> np.ndarray:
    """V'(q): valor esperado da compra menos o custo de oportunidade do caixa."""
    ve = modelo.valor_esperado_da_compra(df, q, p)
    return ve - corte * q * df.custo_unitario.to_numpy(float) * df.dias_capital.to_numpy(float)


def custo_do_erro(s: pd.DataFrame, lim: dict, p: Parametros, corte: float) -> pd.DataFrame:
    """Uma linha por item sinalizado com hipotese: quanto o erro custa por ciclo.

    O item corrigido passa pelo mesmo caminho do motor - `ajustes.aplicar`
    (o custo novo desloca a margem, a demanda nova leva os canais junto),
    `derivar_horizonte`, `dias_capital` e `modelar` com o premio de escassez
    desta rodada - e a quantidade certa sai de `candidatas_marginais`, a
    mesma fila do plano. Nenhuma conta do modelo e refeita aqui.
    """
    cols = ["sku", "hipotese", "q_certo", "custo_erro", "custo_corrigir", "sentido",
            "custo_erro_mes", "custo_corrigir_mes", "ciclo_certo_dias"]
    hip = hipoteses(s, lim)
    if not hip:
        return pd.DataFrame(columns=cols)
    atual = s[s.sku.astype(str).isin(hip.keys())].reset_index(drop=True)
    campos = {k: {c: v for c, v in h.items() if c in ajustes.CAMPOS} for k, h in hip.items()}

    certo = ajustes.aplicar(atual, campos)
    certo = modelo.derivar_horizonte(certo, p)
    certo["dias_capital"] = modelo.dias_capital(certo)
    lam = float(atual.premio_escassez.iloc[0]) if "premio_escassez" in atual.columns else 0.0
    certo = modelo.modelar(certo, p, lam)

    fila = modelo.candidatas_marginais(certo, p)
    q_fila = (fila[fila.nota >= corte].groupby("sku").quantidade.sum()
              if not fila.empty else pd.Series(dtype=float))
    q_atual = atual.quantidade_a_comprar.fillna(0).to_numpy(float)
    q_certo = atual.sku.map(q_fila).fillna(0).to_numpy(float)
    limitar = atual.sku.astype(str).map(lambda k: bool(hip[k].get("_limitar"))).to_numpy()
    teto = np.ceil(lim["compra_dias"] * atual.demanda_media_dia.fillna(0).to_numpy(float))
    q_certo = np.where(limitar, np.minimum(q_atual, teto), q_certo)

    erro = _valor(certo, q_certo, p, corte) - _valor(certo, q_atual, p, corte)
    corrigir = _valor(atual, q_atual, p, corte) - _valor(atual, q_certo, p, corte)
    # cada lado no ciclo de quem decide com o numero: o erro se repete a cada
    # compra do plano de hoje, a correcao indevida a cada compra do corrigido
    por_mes_atual = DIAS_MES / atual.periodo_protecao_dias.to_numpy(float)
    por_mes_certo = DIAS_MES / certo.periodo_protecao_dias.to_numpy(float)
    return pd.DataFrame({
        "sku": atual.sku,
        "hipotese": [{c: v for c, v in hip[str(k)].items() if c in ajustes.CAMPOS}
                     for k in atual.sku],
        "q_certo": q_certo,
        # arredondamento do lote pode deixar -0,01; o arrependimento e >= 0
        "custo_erro": np.maximum(erro, 0.0),
        "custo_corrigir": np.maximum(corrigir, 0.0),
        "custo_erro_mes": np.maximum(erro, 0.0) * por_mes_atual,
        "custo_corrigir_mes": np.maximum(corrigir, 0.0) * por_mes_certo,
        "ciclo_certo_dias": certo.periodo_protecao_dias.to_numpy(float),
        "sentido": np.where(q_atual > q_certo, "a_mais",
                            np.where(q_atual < q_certo, "a_menos", "igual")),
    })


COLUNAS_TELA = [
    "sku", "item", "familia", "curva_abc", "classificacao", "regime", "motivos", "n_motivos",
    "gravidade", "ajustes", "demanda_media_dia", "demanda_media_dia_ingenua", "desvio_padrao_dia",
    "cv_diario", "demanda_max_dia", "dias_com_venda", "dias_historico", "dias_utilizaveis",
    "dias_sem_estoque", "subestimacao_ingenua_pct", "lead_time_dias", "lead_time_desvio_dias",
    "lead_time_pedidos", "custo_unitario", "custo_mediano", "custo_ultimo_lancado",
    "lucro_por_peca", "preco_liquido_peca", "margem_pct", "margem_item", "margem_tipica",
    "custo_sugerido", "pecas_vendidas", "posicao_estoque",
    "ponto_de_pedido", "estoque_seguranca", "capital_imobilizado", "quantidade_a_comprar",
    "valor_da_compra", "cobertura_apos_dias", "risco_de_faltar", "decisao",
    "lucro_potencial_periodo", "impacto",
    "hipotese", "q_certo", "custo_erro", "custo_corrigir", "sentido",
    "custo_erro_mes", "custo_corrigir_mes", "periodo_protecao_dias", "ciclo_certo_dias",
]


# As dimensoes que a nuvem de dispersao aceita nos eixos. Uma lista so, no
# servidor, para a tela nao carregar uma copia: chave -> rotulo, unidade,
# casas decimais e escala padrao ("log" para o que varia em ordens de
# grandeza - demanda, dinheiro, dias de cobertura; "linear" para %, risco e
# contagens curtas). Em escala log o ponto com valor <= 0 e omitido, e a
# tela diz quantos ficaram de fora.
DIMENSOES = [
    ("demanda_media_dia", "Demanda por dia", "un/dia", 3, "log"),
    ("cv_diario", "Coeficiente de variação diário", "", 2, "linear"),
    ("demanda_max_dia", "Maior venda num dia", "un", 0, "log"),
    ("dias_com_venda", "Dias com venda", "dias", 0, "linear"),
    ("dias_sem_estoque", "Dias sem estoque", "dias", 0, "linear"),
    ("subestimacao_ingenua_pct", "Correção de censura", "%", 0, "linear"),
    ("lead_time_dias", "Prazo de entrega", "dias", 0, "linear"),
    ("lead_time_desvio_dias", "Desvio do prazo", "dias", 0, "linear"),
    ("custo_unitario", "Custo unitário", "R$", 2, "log"),
    ("lucro_por_peca", "Lucro por peça", "R$", 2, "log"),
    ("margem_item", "Margem do item", "%", 0, "linear"),
    ("pecas_vendidas", "Peças vendidas na janela", "un", 0, "log"),
    ("posicao_estoque", "Posição de estoque", "un", 0, "log"),
    ("cobertura_apos_dias", "Cobertura após a compra", "dias", 0, "log"),
    ("risco_de_faltar", "Risco de faltar", "%", 0, "linear"),
    ("quantidade_a_comprar", "Quantidade a comprar", "un", 0, "log"),
    ("valor_da_compra", "Valor da compra", "R$", 0, "log"),
    ("capital_imobilizado", "Capital imobilizado", "R$", 0, "log"),
]


def painel(wh: Warehouse) -> dict:
    """Tudo o que a tela /outliers precisa, num JSON so."""
    from .analitico import registros
    m = carregar(wh)
    lim = limiares(m)
    s = sinalizar(m, lim)
    fila = wh.query(f"select comprar, nota from {ref('res_fila_marginal')}")
    corte = nota_de_corte(fila)
    s = s.merge(custo_do_erro(s, lim, Parametros.carregar(), corte), on="sku", how="left")
    aj = ajustes.carregar()
    # itens ajustados aparecem sempre, mesmo que a correcao ja os tenha
    # tirado dos criterios - e assim que se ve o que foi mexido
    alvo = s[(s.n_motivos > 0) | s.sku.astype(str).isin(aj.keys())].copy()
    alvo["ajuste"] = alvo.sku.astype(str).map(aj)
    # a fila da tela e a do dinheiro em jogo por mes, depois quantos motivos e
    # a exposicao (item sem hipotese fica no fim do seu grupo)
    alvo = alvo.sort_values(["custo_erro_mes", "n_motivos", "impacto"],
                            ascending=[False, False, False], na_position="last")
    cols = [c for c in COLUNAS_TELA if c in alvo.columns] + ["ajuste"]
    contagem = {r[0]: int(s[f"f_{r[0]}"].sum()) for r in REGRAS}
    # nuvem do catalogo inteiro, para o item sinalizado ter fundo. Leva todas
    # as dimensoes que a tela pode por nos eixos (DIMENSOES), nao so a dupla
    # padrao demanda x CV.
    dims = [d[0] for d in DIMENSOES if d[0] in s.columns]
    nuvem = s[s.demanda_media_dia > 0][["sku", "item", "n_motivos", "gravidade"] + dims]
    return {
        "regras": [dict(chave=r[0], rotulo=r[1], gravidade=r[2], campo=r[3], explicacao=r[4],
                        n=contagem[r[0]]) for r in REGRAS],
        "limiares": {k: (None if v == float("inf") else float(v)) for k, v in lim.items()},
        "total": int(len(s)),
        "relevantes": int(s.relevante.sum()),
        "sinalizados": int((s.n_motivos > 0).sum()),
        "ajustados": int(s.sku.astype(str).isin(aj.keys()).sum()),
        "nota_de_corte": corte,
        "custo_erro_mes": float(s.custo_erro_mes.fillna(0).sum()),
        "custo_erro_mes_a_mais": float(s.custo_erro_mes[s.sentido.eq("a_mais")].sum()),
        "custo_erro_mes_a_menos": float(s.custo_erro_mes[s.sentido.eq("a_menos")].sum()),
        "campos": {k: dict(rotulo=v[0], unidade=v[1], casas=v[2], ajuda=v[3])
                   for k, v in ajustes.CAMPOS.items()},
        "itens": registros(alvo[cols]),
        "nuvem": registros(nuvem),
        "dimensoes": [dict(chave=d[0], rotulo=d[1], unidade=d[2], casas=d[3], escala=d[4])
                      for d in DIMENSOES if d[0] in s.columns],
    }
