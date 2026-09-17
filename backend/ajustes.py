# -*- coding: utf-8 -*-
"""Ajustes manuais por SKU: o que a tela de outliers grava e o motor aplica.

O modelo estima tudo a partir da base - prazo de entrega, custo, demanda. Quando
a base engana (um pedido unico com 130 dias de prazo, uma venda de 100 m2 num
dia so, um custo lancado errado), o numero do item sai errado e o plano compra
errado. Este modulo guarda a correcao que uma pessoa decidiu para aquele item
e a aplica NUM UNICO ponto de `modelo.executar()`, antes de qualquer politica.

Formato de `data/ajustes_sku.json`:

    {"1034796": {"lead_time_dias": 21, "nota": "fornecedor trocou", "em": "2026-09-17"}}

So os campos em CAMPOS sao aceitos. Campo ausente = fica o valor estimado.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
ARQ = RAIZ / "data" / "ajustes_sku.json"

# chave -> (rotulo, unidade, casas, o que corrige)
CAMPOS = {
    "lead_time_dias": ("Prazo de entrega", "dias", 0,
                       "Dias entre o pedido ao fornecedor e a chegada. O modelo mede pelos "
                       "pedidos passados; com poucos pedidos ou um atraso isolado a medida engana."),
    "lead_time_desvio_dias": ("Desvio do prazo", "dias", 1,
                              "Quanto o prazo varia de pedido para pedido. Entra no estoque de "
                              "segurança; um único atraso grande infla este número."),
    "custo_unitario": ("Custo por peça", "R$", 2,
                       "O custo que o modelo paga por peça e usa na nota de retorno. Lançamento "
                       "errado no ERP distorce custo, margem e prioridade."),
    "demanda_media_dia": ("Demanda por dia", "un/dia", 3,
                          "Taxa de venda estimada, já corrigida da ruptura. Um pico único ou uma "
                          "correção de censura extrema podem levá-la longe do que a loja vende."),
    "desvio_padrao_dia": ("Desvio da demanda por dia", "un/dia", 3,
                          "Variabilidade diária da venda. Dita o estoque de segurança; venda errática "
                          "por um evento isolado exagera a proteção."),
}


def carregar() -> dict[str, dict]:
    if not ARQ.exists():
        return {}
    try:
        dados = json.loads(ARQ.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in dados.items() if isinstance(v, dict)}


def _gravar(dados: dict[str, dict]) -> None:
    ARQ.parent.mkdir(parents=True, exist_ok=True)
    ARQ.write_text(json.dumps(dados, indent=2, ensure_ascii=False, sort_keys=True),
                   encoding="utf-8")


def salvar(sku: str, valores: dict, nota: str = "") -> dict | None:
    """Grava o ajuste de um item. Valores vazios/None removem o campo; sem
    nenhum campo restante o item sai do arquivo. Devolve o registro gravado
    (ou None se o item foi removido)."""
    dados = carregar()
    reg = {}
    for chave, bruto in (valores or {}).items():
        if chave not in CAMPOS or bruto is None or bruto == "":
            continue
        try:
            v = float(bruto)
        except (TypeError, ValueError):
            continue
        if not np.isfinite(v) or v < 0:
            continue
        reg[chave] = int(round(v)) if CAMPOS[chave][2] == 0 else v
    if not reg:
        dados.pop(str(sku), None)
        _gravar(dados)
        return None
    reg["nota"] = (nota or "").strip()[:280]
    reg["em"] = date.today().isoformat()
    dados[str(sku)] = reg
    _gravar(dados)
    return reg


def remover(sku: str) -> bool:
    dados = carregar()
    if str(sku) not in dados:
        return False
    dados.pop(str(sku))
    _gravar(dados)
    return True


def aplicar(b: pd.DataFrame, dados: dict[str, dict] | None = None) -> pd.DataFrame:
    """Sobrescreve as colunas ajustadas e marca a linha em `ajustes`.

    Recebe o quadro por SKU ja com financeiro + estatistica de demanda e
    devolve o mesmo quadro com os valores corrigidos. Tudo o que deriva
    destes campos (periodo de protecao, mu, sd, margem, nota, politica) e
    calculado DEPOIS, pelo caminho normal - por isso este e o unico gancho.
    """
    dados = carregar() if dados is None else dados
    b = b.copy()
    b["ajustes"] = ""
    if not dados:
        return b
    idx = b.index[b.sku.astype(str).isin(dados.keys())]
    if len(idx) == 0:
        return b
    for i in idx:
        sku = str(b.at[i, "sku"])
        reg = dados[sku]
        marcas = []
        for chave in CAMPOS:
            if chave not in reg or reg[chave] is None:
                continue
            novo = float(reg[chave])
            antigo = float(b.at[i, chave]) if chave in b.columns and pd.notna(b.at[i, chave]) else np.nan
            if chave == "custo_unitario":
                # a margem foi refeita como preco praticado menos custo de hoje
                # em `_margem_coerente`; custo novo desloca todas as margens
                # pelo mesmo delta, e a margem % acompanha
                delta = (antigo if np.isfinite(antigo) else 0.0) - novo
                vendeu = float(b.at[i, "pecas_vendidas"] or 0) > 0 if "pecas_vendidas" in b.columns else True
                for col in [c for c in b.columns if c.startswith("lucro_por_peca")
                            and "historico" not in c]:
                    if vendeu:
                        b.at[i, col] = float(b.at[i, col]) + delta
                if "preco_liquido_peca" in b.columns and "margem_pct" in b.columns:
                    preco = float(b.at[i, "preco_liquido_peca"] or 0)
                    b.at[i, "margem_pct"] = (b.at[i, "lucro_por_peca"] / preco) if preco > 0 else 0.0
            if chave == "demanda_media_dia" and np.isfinite(antigo) and antigo > 0:
                # os canais acompanham na mesma proporcao: a participacao do
                # e-commerce nao muda por causa de uma correcao de nivel
                razao = novo / antigo
                for col in ("demanda_media_dia_ecommerce", "demanda_media_dia_lojas"):
                    if col in b.columns and pd.notna(b.at[i, col]):
                        b.at[i, col] = float(b.at[i, col]) * razao
            b.at[i, chave] = novo
            marcas.append(chave)
        b.at[i, "ajustes"] = ",".join(marcas)
    return b
