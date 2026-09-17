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
from . import ajustes

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


COLUNAS_TELA = [
    "sku", "item", "familia", "curva_abc", "classificacao", "regime", "motivos", "n_motivos",
    "gravidade", "ajustes", "demanda_media_dia", "demanda_media_dia_ingenua", "desvio_padrao_dia",
    "cv_diario", "demanda_max_dia", "dias_com_venda", "dias_historico", "dias_utilizaveis",
    "dias_sem_estoque", "subestimacao_ingenua_pct", "lead_time_dias", "lead_time_desvio_dias",
    "lead_time_pedidos", "custo_unitario", "custo_mediano", "custo_ultimo_lancado",
    "lucro_por_peca", "preco_liquido_peca", "margem_pct", "pecas_vendidas", "posicao_estoque",
    "ponto_de_pedido", "estoque_seguranca", "capital_imobilizado", "quantidade_a_comprar",
    "valor_da_compra", "cobertura_apos_dias", "risco_de_faltar", "decisao",
    "lucro_potencial_periodo", "impacto",
]


def painel(wh: Warehouse) -> dict:
    """Tudo o que a tela /outliers precisa, num JSON so."""
    from .analitico import registros
    m = carregar(wh)
    lim = limiares(m)
    s = sinalizar(m, lim)
    aj = ajustes.carregar()
    # itens ajustados aparecem sempre, mesmo que a correcao ja os tenha
    # tirado dos criterios - e assim que se ve o que foi mexido
    alvo = s[(s.n_motivos > 0) | s.sku.astype(str).isin(aj.keys())].copy()
    alvo["ajuste"] = alvo.sku.astype(str).map(aj)
    alvo = alvo.sort_values(["n_motivos", "impacto"], ascending=[False, False])
    cols = [c for c in COLUNAS_TELA if c in alvo.columns] + ["ajuste"]
    contagem = {r[0]: int(s[f"f_{r[0]}"].sum()) for r in REGRAS}
    # nuvem demanda x CV do catalogo inteiro, para o item sinalizado ter fundo
    nuvem = s[s.demanda_media_dia > 0][["sku", "item", "demanda_media_dia", "cv_diario",
                                        "lead_time_dias", "n_motivos", "gravidade"]]
    return {
        "regras": [dict(chave=r[0], rotulo=r[1], gravidade=r[2], campo=r[3], explicacao=r[4],
                        n=contagem[r[0]]) for r in REGRAS],
        "limiares": {k: (None if v == float("inf") else float(v)) for k, v in lim.items()},
        "total": int(len(s)),
        "relevantes": int(s.relevante.sum()),
        "sinalizados": int((s.n_motivos > 0).sum()),
        "ajustados": int(s.sku.astype(str).isin(aj.keys()).sum()),
        "campos": {k: dict(rotulo=v[0], unidade=v[1], casas=v[2], ajuda=v[3])
                   for k, v in ajustes.CAMPOS.items()},
        "itens": registros(alvo[cols]),
        "nuvem": registros(nuvem),
    }
