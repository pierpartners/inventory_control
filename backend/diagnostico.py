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


# ----------------------------------------------------------------------
# leitura
# ----------------------------------------------------------------------
COLUNAS_PLANO = [
    "sku", "item", "familia", "curva_abc", "classe_xyz", "regime", "custo_unitario",
    "demanda_media_dia", "mu_periodo", "ponto_de_pedido", "estoque_maximo", "estoque_medio",
    "estoque_fisico", "em_transito", "posicao_estoque", "quantidade_a_comprar",
    "cobertura_dias", "risco_de_faltar", "lucro_perdido_ruptura", "lucro_por_peca",
    "lead_time_dias", "giro_ano",
]
COLUNAS_POSICAO = [
    "sku", "fornecedor", "comprador", "data_posicao", "saldo_cd", "custo_medio_erp",
    "saldo_lojas", "lojas_com_saldo", "valor_lojas_erp", "ultima_venda", "ultima_entrada",
    "idade_fifo_dias", "entrada_mais_antiga_em_estoque", "entradas_cobrem_saldo",
]
COLUNAS_ITEM = [
    "sku", "item", "familia", "fornecedor", "comprador", "curva_abc", "classe_xyz", "faixa",
    "acao", "estoque_fisico", "em_transito", "saldo_lojas", "lojas_com_saldo",
    "ponto_de_pedido", "estoque_maximo", "cobertura_dias", "risco_de_faltar",
    "demanda_media_dia", "dias_sem_venda", "ultima_venda", "idade_fifo_dias", "faixa_idade",
    "entradas_cobrem_saldo", "custo_unitario", "custo_medio_erp", "capital_modelo",
    "capital_erp", "excesso_pecas", "excesso_valor", "lucro_perdido_ruptura",
    "quantidade_a_comprar",
]


def carregar(wh: Warehouse, dias_sem_giro: int = DIAS_SEM_GIRO_PADRAO,
             ate: str | None = None) -> pd.DataFrame:
    """res_plano_compra (politica) x mart_estoque_posicao (foto), classificado.

    Com `ate`, a foto e congelada naquela data: o motor roda em memoria como
    no backtest (mesmo caminho, nunca um paralelo), a ultima venda e o custo
    contabil sao lidos ate a data, e o que so existe para hoje (idade FIFO,
    saldo das lojas) fica em branco - a pagina avisa.
    """
    posicao = wh.query(f"select {', '.join(COLUNAS_POSICAO)} from {ref('mart_estoque_posicao')}")
    if not ate:
        plano = wh.query(f"select {', '.join(COLUNAS_PLANO)} from {ref('res_plano_compra')}")
    else:
        from .config import Parametros
        from .modelo import executar
        corte = str(pd.Timestamp(ate).date())
        plano = executar(wh, Parametros.carregar(), ate=corte)["res_plano_compra"][COLUNAS_PLANO].copy()
        venda = wh.query(
            f"select sku, max(data) as ultima_venda from {ref('stg_vendas')} "
            f"where pecas_vendidas > 0 and data <= DATE '{corte}' group by sku")
        custo = wh.query(
            f"select sku, custo as custo_medio_erp from ("
            f"  select sku, custo, row_number() over (partition by sku order by data desc) as rn "
            f"  from {ref('stg_custo_medio_erp')} where data <= DATE '{corte}') where rn = 1")
        posicao = (posicao[["sku", "fornecedor", "comprador"]]
                   .merge(venda, on="sku", how="left")
                   .merge(custo, on="sku", how="left"))
        posicao["data_posicao"] = pd.Timestamp(corte)
        posicao["saldo_cd"] = np.nan
        posicao["saldo_lojas"] = 0.0
        posicao["lojas_com_saldo"] = 0
        posicao["valor_lojas_erp"] = 0.0
        posicao["ultima_entrada"] = pd.NaT
        posicao["idade_fifo_dias"] = np.nan
        posicao["entrada_mais_antiga_em_estoque"] = pd.NaT
        posicao["entradas_cobrem_saldo"] = False
    df = plano.merge(posicao, on="sku", how="left")
    df["data_posicao"] = pd.to_datetime(df.data_posicao).fillna(pd.Timestamp(posicao.data_posicao.max()))
    for c in ("saldo_lojas", "lojas_com_saldo", "valor_lojas_erp"):
        df[c] = df[c].fillna(0)
    df["fornecedor"] = df.fornecedor.fillna("Nao informado")
    df["comprador"] = df.comprador.fillna("Nao informado")
    out = classificar_faixas(df, dias_sem_giro)
    out.attrs["congelado"] = bool(ate)
    return out


# ----------------------------------------------------------------------
# leituras agregadas (puras)
# ----------------------------------------------------------------------
def _f(v) -> float:
    v = float(v)
    return v if np.isfinite(v) else 0.0


def _pond(valores: pd.Series, pesos: pd.Series, teto: float = 400.0) -> float:
    p = pesos.fillna(0).clip(lower=0).to_numpy(dtype=float)
    v = valores.clip(0, teto).fillna(0).to_numpy(dtype=float)
    return float(np.average(v, weights=p)) if p.sum() > 0 else 0.0


def resumo_geral(df: pd.DataFrame) -> dict:
    cap = df.capital_modelo
    dem = df.demanda_media_dia.replace(0, np.nan)
    cobertura_real = df.estoque_fisico / dem          # dias de estoque a taxa corrigida
    faixas = []
    for f in FAIXAS:
        s = df[df.faixa == f]
        faixas.append({"faixa": f, "itens": int(len(s)),
                       "capital_modelo": _f(s.capital_modelo.sum()),
                       "capital_erp": _f(s.capital_erp.sum(skipna=True)),
                       "excesso_valor": _f(s.excesso_valor.sum())})
    cob_real = _pond(cobertura_real, cap)
    cob_otima = _pond(df.cobertura_dias, df.capital_otimo)
    return {
        "data_posicao": str(pd.Timestamp(df.data_posicao.max()).date()),
        "congelado": bool(df.attrs.get("congelado", False)),
        "itens": int(len(df)),
        "capital_modelo": _f(cap.sum()),
        "capital_erp": _f(df.capital_erp.sum(skipna=True)),
        "itens_sem_custo_erp": int((df.capital_erp.isna() & (df.estoque_fisico > 0)).sum()),
        "capital_otimo": _f(df.capital_otimo.sum()),
        "diferenca_real_otimo": _f(cap.sum() - df.capital_otimo.sum()),
        "cobertura_real_dias": cob_real,
        "cobertura_otima_dias": cob_otima,
        "giro_real": 365.0 / cob_real if cob_real > 0 else 0.0,
        "giro_otimo": 365.0 / cob_otima if cob_otima > 0 else 0.0,
        "lucro_perdido_ruptura": _f(df.lucro_perdido_ruptura.sum()),
        "faixas": faixas,
    }


def _por_faixa(s: pd.DataFrame) -> dict:
    g = s.groupby("faixa").capital_modelo.sum()
    return {f: _f(g.get(f, 0.0)) for f in FAIXAS}


def por_idade(df: pd.DataFrame) -> list[dict]:
    out = []
    for fi in FAIXAS_IDADE:
        s = df[df.faixa_idade == fi]
        out.append({"faixa_idade": fi, "itens": int(len(s)),
                    "capital_modelo": _f(s.capital_modelo.sum()),
                    "por_faixa": _por_faixa(s)})
    return out


DIMENSOES = ("fornecedor", "comprador", "familia")


def agregar(df: pd.DataFrame, por: str) -> list[dict]:
    if por not in DIMENSOES:
        raise ValueError(f"agregar: `por` deve ser um de {DIMENSOES}, veio {por!r}")
    out = []
    for chave, s in df.groupby(df[por].fillna("Nao informado"), sort=False):
        out.append({
            "chave": str(chave), "itens": int(len(s)),
            "capital_modelo": _f(s.capital_modelo.sum()),
            "capital_erp": _f(s.capital_erp.sum(skipna=True)),
            "capital_otimo": _f(s.capital_otimo.sum()),
            "excesso_valor": _f(s.excesso_valor.sum()),
            "sem_giro_valor": _f(s.loc[s.faixa == "Sem giro", "capital_modelo"].sum()),
            "itens_risco": int((s.faixa == "Risco").sum()),
            "itens_zerados": int((s.faixa == "Zerado com demanda").sum()),
            "lucro_perdido_ruptura": _f(s.lucro_perdido_ruptura.sum()),
            "por_faixa": _por_faixa(s),
        })
    out.sort(key=lambda x: -x["capital_modelo"])
    return out


def _registros(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    from .analitico import registros
    cols = [c for c in cols if c in df.columns]
    d = df[cols].copy()
    if "dias_sem_venda" in d:
        d["dias_sem_venda"] = d.dias_sem_venda.replace(np.inf, np.nan)
    return registros(d)


def rede(df: pd.DataFrame, limite: int = 60) -> dict:
    em_falta = df[df.faixa.isin(FAIXAS[:2]) & (df.saldo_lojas >= df.mu_periodo) & (df.saldo_lojas > 0)]
    em_falta = em_falta.sort_values("lucro_perdido_ruptura", ascending=False).head(limite)
    positivos = df.loc[df.valor_lojas_erp > 0, "valor_lojas_erp"]
    p90 = float(positivos.quantile(0.9)) if len(positivos) else np.inf
    parado = df[(df.valor_lojas_erp >= p90) & df.faixa.isin(["Excesso", "Sem giro"])]
    parado = parado.sort_values("valor_lojas_erp", ascending=False).head(limite)
    cols = COLUNAS_ITEM + ["valor_lojas_erp", "mu_periodo"]
    return {"transferir": _registros(em_falta, cols), "parado_em_loja": _registros(parado, cols),
            "p90_valor_loja": (None if not np.isfinite(p90) else p90)}


def itens(df: pd.DataFrame, faixa: str = "", por: str = "", chave: str = "",
          busca: str = "", limite: int = 5000) -> list[dict]:
    s = df
    if faixa:
        s = s[s.faixa == faixa]
    if por and chave:
        if por not in DIMENSOES:
            raise ValueError(f"itens: `por` deve ser um de {DIMENSOES}")
        s = s[s[por].fillna("Nao informado").astype(str) == chave]
    if busca:
        q = busca.lower()
        s = s[s.sku.astype(str).str.lower().str.contains(q, regex=False)
              | s.item.astype(str).str.lower().str.contains(q, regex=False)]
    s = s.sort_values("capital_modelo", ascending=False).head(limite)
    return _registros(s, COLUNAS_ITEM)


# ----------------------------------------------------------------------
# confianca por bloco
# ----------------------------------------------------------------------
# que grupos da bateria de qualidade sustentam cada bloco da pagina
BLOCOS_QUALIDADE = {
    "geral":    ("estoque", "cadastro"),
    "idade":    ("compra", "estoque"),
    "agregado": ("cadastro",),
    "rede":     ("estoque",),
    "itens":    ("venda", "estoque", "cadastro"),
}
_PESO = {"ok": 0, "aviso": 1, "erro": 2}


def confianca(qualidade: dict | None, sinalizados: pd.DataFrame, df: pd.DataFrame) -> dict:
    """Por bloco: o pior nivel entre as checagens dos grupos que o sustentam,
    e quantos itens com sinal de outlier estao no recorte. `qualidade` e o
    JSON de qualidade.verificar() (ou None se o cache esta frio)."""
    out = {"qualidade_disponivel": qualidade is not None, "blocos": {}}
    por_grupo: dict[str, str] = {}
    if qualidade:
        for c in qualidade.get("checagens", []):
            g = c["grupo"]
            if _PESO.get(c["nivel"], 0) >= _PESO.get(por_grupo.get(g, "ok"), 0):
                por_grupo[g] = c["nivel"]
    n_out = 0
    if sinalizados is not None and len(sinalizados) and "n_motivos" in sinalizados:
        marcados = set(sinalizados.loc[sinalizados.n_motivos > 0, "sku"].astype(str))
        n_out = int(df.sku.astype(str).isin(marcados).sum())
    for bloco, grupos in BLOCOS_QUALIDADE.items():
        niveis = [por_grupo.get(g, "ok") for g in grupos] if qualidade else []
        pior = max(niveis, key=lambda n: _PESO[n]) if niveis else None
        out["blocos"][bloco] = {"nivel": pior, "grupos": list(grupos), "outliers": n_out}
    return out
