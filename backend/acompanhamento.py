# -*- coding: utf-8 -*-
"""
Acompanhamento de vendas — pagina de conferencia.

Duas coisas, e so isso: um grafico diario por item (venda por canal em barra
empilhada, estoque disponivel + reservado em linha empilhada) e uma lista de
produtos ordenada por quantos dias a conta do estoque nao bate com a venda do
dia. Nao ha analise autonoma nem texto explicativo por produto - a tela existe
para alguem OLHAR o dado cru, nao para o sistema opinar sobre ele.

O "dia nao ok" usa a mesma regua que a tela de qualidade dos dados ja mede no
agregado (a venda do dia explica 65,5% da baixa de disponivel): aqui ela desce
para o nivel de item-dia, e o resultado e contado por SKU. Fica ao lado o
delta de reserva do dia, porque e o primeiro lugar a olhar quando a conta nao
fecha - reserva antecipada e a explicacao mais comum para a queda de
disponivel sem venda correspondente.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .warehouse import Warehouse, ref
from .analitico import registros

TOLERANCIA_PECAS = 0.5  # abaixo disso a divergencia e arredondamento, nao erro


def serie_item(wh: Warehouse, sku: str) -> dict:
    """A serie diaria de um item: venda por canal (barra) e estoque disponivel
    + reservado (linha), dia a dia, do primeiro ao ultimo dia da base.

    `reservado` sai de saldo_final - disponivel_final: o mart nao carrega a
    reserva do ERP como coluna propria, e essa subtracao e exatamente a
    identidade que a tela de qualidade confirma fechar em 100% das linhas
    (fisico - reserva = disponivel), entao reconstrui-la aqui nao introduz
    erro novo.
    """
    est = wh.query(f"""
        select data, saldo_final as fisico, disponivel_final as disponivel,
               saldo_final - disponivel_final as reservado
        from {ref('mart_estoque_diario')}
        where sku = '{sku}' order by data""")
    if est.empty:
        return {}

    ven = wh.query(f"""
        select data,
               sum(case when loja_id = '33' then pecas_vendidas else 0 end) as ecommerce,
               sum(case when loja_id <> '33' or loja_id is null
                        then pecas_vendidas else 0 end) as lojas
        from {ref('stg_vendas')} where sku = '{sku}' group by 1""")

    d = est.merge(ven, on="data", how="left")
    d[["ecommerce", "lojas"]] = d[["ecommerce", "lojas"]].fillna(0.0)
    d["data"] = pd.to_datetime(d.data).dt.strftime("%Y-%m-%d")
    return {
        "dias": d.data.tolist(),
        "ecommerce": d.ecommerce.round(2).tolist(),
        "lojas": d.lojas.round(2).tolist(),
        "disponivel": d.disponivel.round(2).tolist(),
        "reservado": d.reservado.round(2).tolist(),
        "fisico": d.fisico.round(2).tolist(),
    }


def _base_reconciliacao(wh: Warehouse, sku: str | None = None) -> pd.DataFrame:
    """Uma linha por item-dia: a queda de disponivel do dia contra a venda do
    dia, com o delta de reserva ao lado para contexto.

    E a mesma comparacao que sustenta o achado "a venda do dia explica 65,5%
    da baixa de disponivel" na tela de qualidade dos dados - so que aqui nao
    para no agregado: cada dia de cada item vira uma linha, "ok" ou "nao ok",
    e o "nao ok" e contado por SKU.

    `sku` filtra no banco antes de trazer para o pandas - um drill-down de um
    item so nao tem por que puxar as 1,3 milhao de linhas da base inteira.
    """
    seguro = str(sku).replace("'", "''") if sku else None
    onde = f"where sku = '{seguro}'" if seguro else ""
    d = wh.query(f"""
        select sku, data, saldo_final as fisico, disponivel_final as disponivel,
               pecas_vendidas as vendas
        from {ref('mart_estoque_diario')} {onde} order by sku, data""")
    d["disp_ant"] = d.groupby("sku")["disponivel"].shift(1)
    d["fis_ant"] = d.groupby("sku")["fisico"].shift(1)
    d = d[d.disp_ant.notna()].copy()

    d["delta_estoque"] = d.fisico - d.fis_ant
    d["queda_disponivel"] = (d.disp_ant - d.disponivel).clip(lower=0)
    d["reserva"] = d.fisico - d.disponivel
    d["reserva_ant"] = d.fis_ant - d.disp_ant
    d["delta_reserva"] = d.reserva - d.reserva_ant
    d["diferenca"] = d.queda_disponivel - d.vendas
    d["ok"] = d.diferenca.abs() <= TOLERANCIA_PECAS
    return d


def ranking_dias_nao_ok(wh: Warehouse, limite: int = 400) -> dict:
    """Os itens com mais dias em que estoque, venda e reserva nao batem entre
    si, do pior para o melhor."""
    d = _base_reconciliacao(wh)
    cat = wh.query(f"select sku, item, familia, classificacao from {ref('res_sku_modelo')}")

    d["dif_abs"] = d.diferenca.abs()
    geral = d.groupby("sku").agg(
        dias_historico=("ok", "size"),
        dias_nao_ok=("ok", lambda s: int((~s).sum())),
        pecas_nao_explicadas=("dif_abs", lambda s: float(s[s > TOLERANCIA_PECAS].sum())),
    ).reset_index()
    # o ultimo dia ruim, separado: max() sobre um grupo vazio (item sem
    # nenhum dia "nao ok") tem de sobrar None, nao quebrar o agrupamento acima
    ultimo = (d[~d.ok].groupby("sku")["data"].max()
              .rename("ultimo_dia_nao_ok").reset_index())

    g = geral.merge(ultimo, on="sku", how="left")
    g["pct_dias_nao_ok"] = np.where(g.dias_historico > 0,
                                    g.dias_nao_ok / g.dias_historico, 0.0)
    g = g.merge(cat, on="sku", how="left")
    g = g.sort_values(["dias_nao_ok", "pecas_nao_explicadas"], ascending=False)
    return {
        "total_itens": int(len(g)),
        "itens": registros(g.head(limite)),
    }


def detalhe_item(wh: Warehouse, sku: str, so_nao_ok: bool = False,
                 limite: int = 400) -> dict:
    """A tabela dia a dia de um item: estoque, venda, reserva e o veredito -
    o que sustenta o numero da lista acima, aberto para conferencia."""
    d = _base_reconciliacao(wh, sku=sku).sort_values("data", ascending=False)
    if so_nao_ok:
        d = d[~d.ok]
    d = d.head(limite)
    d["data"] = pd.to_datetime(d.data).dt.strftime("%Y-%m-%d")
    cols = ["data", "fisico", "disponivel", "vendas", "reserva",
            "delta_estoque", "delta_reserva", "queda_disponivel", "diferenca", "ok"]
    return {"linhas": registros(d[cols])}
