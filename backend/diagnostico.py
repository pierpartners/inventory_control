# -*- coding: utf-8 -*-
"""
Diagnostico do estoque ATUAL: em que estado cada item esta hoje e quanto
dinheiro ha em cada estado.

O plano de compra responde "o que comprar"; aqui a pergunta e "o que esta
parado, o que esta em risco e quanto isso custa". Nada da politica e
recalculado: ponto de pedido, estoque maximo, estoque medio, cobertura e
risco vem de res_plano_compra. Este modulo so LE e classifica.

Funcoes puras sobre DataFrame (classificar_faixas, resumo_geral, agregar,
por_idade, rede, itens) para scripts/revisao.py poder recompor cada numero
por fora; `carregar()` e o unico ponto que fala com o warehouse.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .warehouse import Warehouse, ref

# ordem = prioridade: um item que esta zerado E acima do maximo (demanda alta,
# maximo pequeno) e ruptura, nao excesso
FAIXAS = ["Zerado com demanda", "Risco", "Sem giro", "Excesso", "Saudável"]
FAIXAS_IDADE = ["até 3 meses", "3 a 6 meses", "6 a 12 meses", "mais de 12 meses", "sem entrada"]
ACOES = {"comprar", "transferir", "revisar", "liquidar", "segurar", "manter"}
DIAS_SEM_GIRO_PADRAO = 180

COLUNAS_ENTRADA = [
    "sku", "estoque_fisico", "posicao_estoque", "demanda_media_dia", "ponto_de_pedido",
    "estoque_maximo", "estoque_medio", "custo_unitario", "quantidade_a_comprar",
    "mu_periodo", "ultima_venda", "data_posicao", "idade_fifo_dias", "custo_medio_erp",
    "saldo_lojas",
]


def _faixa_idade(idade: pd.Series) -> pd.Series:
    i = pd.to_numeric(idade, errors="coerce")
    return pd.Series(
        np.select(
            [i.isna(), i <= 90, i <= 180, i <= 365],
            [FAIXAS_IDADE[4], FAIXAS_IDADE[0], FAIXAS_IDADE[1], FAIXAS_IDADE[2]],
            default=FAIXAS_IDADE[3]),
        index=idade.index)


def classificar_faixas(df: pd.DataFrame, dias_sem_giro: int = DIAS_SEM_GIRO_PADRAO) -> pd.DataFrame:
    """Uma faixa por item, avaliada nesta ordem:

    1. Zerado com demanda  fisico = 0 e demanda corrigida > 0 (ruptura hoje)
    2. Risco               posicao (fisico + transito) <= ponto de pedido > 0
    3. Sem giro            fisico > 0 e sem venda ha mais de `dias_sem_giro`
    4. Excesso             fisico > estoque maximo do modelo
    5. Saudavel            o resto

    Tudo sobre o estoque do CD, que e onde a politica se aplica. `saldo_lojas`
    so entra na acao sugerida (transferir em vez de comprar).
    """
    faltam = [c for c in COLUNAS_ENTRADA if c not in df.columns]
    if faltam:
        raise KeyError(f"classificar_faixas: faltam colunas {faltam}")
    d = df.copy()
    fis = d.estoque_fisico.fillna(0).astype(float)
    pos = d.posicao_estoque.fillna(fis).astype(float)
    dem = d.demanda_media_dia.fillna(0).astype(float)
    rop = d.ponto_de_pedido.fillna(0).astype(float)
    mx = d.estoque_maximo.fillna(0).astype(float)
    custo = d.custo_unitario.fillna(0).astype(float)

    data_pos = pd.to_datetime(d.data_posicao)
    ult = pd.to_datetime(d.ultima_venda, errors="coerce")
    dias = (data_pos - ult).dt.days.astype(float)
    d["dias_sem_venda"] = dias.where(ult.notna(), np.inf)

    zerado = (fis <= 0) & (dem > 0)
    # rop > 0: item sem politica (sem demanda, ROP zero) e sem peca nao esta em
    # risco - sem essa trava o catalogo inteiro que nunca girou cairia aqui
    risco = ~zerado & (pos <= rop) & (rop > 0)
    sem_giro = ~zerado & ~risco & (fis > 0) & (d.dias_sem_venda > dias_sem_giro)
    excesso = ~zerado & ~risco & ~sem_giro & (fis > mx)
    d["faixa"] = np.select([zerado, risco, sem_giro, excesso],
                           FAIXAS[:4], default=FAIXAS[4])

    d["capital_modelo"] = fis * custo
    d["capital_erp"] = fis * pd.to_numeric(d.custo_medio_erp, errors="coerce")
    d["excesso_pecas"] = np.where(excesso, fis - mx, 0.0)
    d["excesso_valor"] = d.excesso_pecas * custo
    d["faixa_idade"] = _faixa_idade(d.idade_fifo_dias)
    d["capital_otimo"] = d.estoque_medio.fillna(0).astype(float) * custo

    comprar = d.quantidade_a_comprar.fillna(0) > 0
    tem_na_rede = d.saldo_lojas.fillna(0) >= d.mu_periodo.fillna(0)
    velho = d.faixa_idade == FAIXAS_IDADE[3]
    d["acao"] = np.select(
        [(zerado | risco) & comprar,
         (zerado | risco) & tem_na_rede,
         (zerado | risco),
         sem_giro & velho,
         sem_giro | excesso],
        ["comprar", "transferir", "revisar", "liquidar", "segurar"],
        default="manter")
    return d
