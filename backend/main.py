# -*- coding: utf-8 -*-
"""
NUCLEO - aplicacao web do planejamento de estoque da Elevato.

Telas:
  /painel       sala de controle: o que decidir hoje e por que
  /plano        fila de compra priorizada por retorno de capital
  /itens        catalogo com o resultado do modelo item a item
  /metodologia  o caminho de um item pelo modelo, com os numeros dele
  /parametros   as premissas economicas e os interruptores metodologicos
  /outliers     itens com entrada suspeita (venda, prazo, custo) e o ajuste manual por SKU
  /dados        navegacao pelas tabelas do warehouse

Rodar:
  uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import Body, FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from backend import acompanhamento, ajustes, analitico, outliers, qualidade  # noqa: E402
from backend import modelo as motor  # noqa: E402
from backend import validacao  # noqa: E402
from backend.config import (Parametros, CAMPOS, CHAVES,  # noqa: E402
                            CRITERIOS, GRUPOS)
from backend.warehouse import (BigQueryWarehouse, DuckDBWarehouse,  # noqa: E402
                               carregar_env, ref)

carregar_env()

app = FastAPI(title="Nucleo - Planejamento de Estoque Elevato")
# a corrida completa e a fila passam de 1 MB de JSON; sem compressao a tela
# de plano fica lenta so pelo transporte
app.add_middleware(GZipMiddleware, minimum_size=2048)
app.mount("/static", StaticFiles(directory=str(RAIZ / "static")), name="static")
tpl = Jinja2Templates(directory=str(RAIZ / "templates"))


# ----------------------------------------------------------------------
# formatacao pt-BR nos templates
# ----------------------------------------------------------------------
def f_num(v, casas: int = 0) -> str:
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return "–"
    inteiro, _, frac = f"{float(v):,.{casas}f}".partition(".")
    inteiro = inteiro.replace(",", ".")
    return f"{inteiro},{frac}" if frac else inteiro


def f_curto(v) -> str:
    """1.234.567 -> 1,23 mi. Para numeros grandes em espaco apertado."""
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return "–"
    v = float(v)
    a, s = abs(v), "-" if v < 0 else ""
    if a >= 1e9:
        return f"{s}{f_num(a / 1e9, 2)} bi"
    if a >= 1e6:
        return f"{s}{f_num(a / 1e6, 2)} mi"
    if a >= 1e3:
        return f"{s}{f_num(a / 1e3, 0 if a >= 1e5 else 1)} mil"
    return f"{s}{f_num(a, 0)}"


def f_pct(v, casas: int = 1) -> str:
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return "–"
    return f_num(float(v) * 100, casas) + "%"


tpl.env.filters["n"] = f_num
tpl.env.filters["curto"] = f_curto
tpl.env.filters["pct"] = f_pct


def est(nome: str) -> str:
    """URL de um arquivo estatico com a data de modificacao como versao.

    Sem isso o navegador segura o CSS/JS antigo depois de cada alteracao, o que
    em desenvolvimento leva a depurar um bug que ja foi corrigido em disco.
    """
    arq = RAIZ / "static" / nome
    marca = int(arq.stat().st_mtime) if arq.exists() else 0
    return f"/static/{nome}?v={marca}"


tpl.env.globals["est"] = est


# ----------------------------------------------------------------------
# infraestrutura
# ----------------------------------------------------------------------
def wh():
    if os.environ.get("WAREHOUSE", "duckdb") == "bigquery":
        return BigQueryWarehouse(
            projeto=os.environ.get("GCP_PROJECT", ""),
            dataset=os.environ.get("BQ_DATASET", "elevato"),
            keyfile=os.environ.get("GCP_KEYFILE", ""),
            location=os.environ.get("BQ_LOCATION", "southamerica-east1"))
    return DuckDBWarehouse(os.environ.get("DUCKDB_PATH", "./data/elevato.duckdb"))


def pronto(w) -> bool:
    try:
        return w.existe("res_sku_modelo") and w.existe("res_plano_compra")
    except Exception:  # noqa: BLE001
        return False


def contexto(request: Request, pagina: str, **extra) -> dict:
    """Base de todo template: pagina ativa + o cabecalho de execucao."""
    base = {"request": request, "pagina": pagina,
            "motor_dados": os.environ.get("WAREHOUSE", "duckdb")}
    # o contador de achados no menu sai do cache, nunca da bateria: rodar as 21
    # verificacoes em cada abertura de pagina custaria 13 segundos por clique.
    # Enquanto o cache estiver frio o menu fica sem numero, e e isso mesmo -
    # um numero errado ali seria pior que nenhum.
    q = _cache_qualidade.get("d")
    if q:
        n = q["resumo"]["erro"] + q["resumo"]["aviso"]
        if n:
            base["qualidade_alerta"] = n
    try:
        w = wh()
        if pronto(w):
            e = analitico.execucao(w)
            m = analitico.modelo_df(w)
            ultimo = w.query(
                f"select max(data) as d from {ref('mart_estoque_diario')}").iloc[0]["d"]
            base["exec"] = {
                "em": str(e.executado_em)[:16],
                "skus": int(e.skus),
                "dias": int(e.dias_historico),
                "lam": float(e.premio_escassez),
                "capital": float(m.capital_imobilizado.sum()),
                "posicao": str(ultimo)[:10],
                "unidades": int(getattr(e, "unidades_avaliadas", 0) or 0),
                "itens_compra": int(getattr(e, "itens_na_compra", 0) or 0),
            }
    except Exception:  # noqa: BLE001
        pass
    base.update(extra)
    return base


# a avaliacao roda o motor inteiro e a janela de validacao: ~2s por data. Cara
# demais para refazer a cada clique, barata demais para gravar no warehouse.
_cache_validacao: dict = {}
# a bateria de qualidade varre 1,3 milhao de linhas de estoque contra 790 mil
# de venda: 13 segundos. Fica em cache e cai quando o pipeline roda.
_cache_qualidade: dict = {}
# a mesma reconciliacao dia a dia, de novo: 1,3 milhao de linhas viram
# item-dia com pandas. Fica em cache pelo mesmo motivo.
_cache_ranking: dict = {}


def invalidar_caches() -> None:
    """Derruba tudo o que depende de parametro ou de resultado do motor.

    A validacao historica guarda a avaliacao de cada data de corte porque cada
    uma custa ~2s. Se os parametros mudam, aquelas avaliacoes passam a descrever
    um modelo que nao existe mais - e o cache tem de cair junto com o de
    analitico, num lugar so, para nao sobrar cache orfao.
    """
    analitico.invalidar_cache()
    _cache_validacao.clear()
    _cache_qualidade.clear()
    _cache_ranking.clear()


def sem_dados(request: Request):
    return tpl.TemplateResponse(request, "vazio.html",
                                contexto(request, "painel"), status_code=200)


@app.get("/")
def home():
    return RedirectResponse(url="/painel")


# ======================================================================
# PAINEL
# ======================================================================
@app.get("/painel")
def painel(request: Request):
    w = wh()
    if not pronto(w):
        return sem_dados(request)

    p = Parametros.carregar()
    m = analitico.modelo_df(w)
    plano = analitico.plano_df(w)
    comp = w.query(f"select * from {ref('res_comparativo')}")
    e = analitico.execucao(w)

    hoje = w.query(
        f"select count(*) as n from {ref('mart_estoque_diario')} "
        f"where data = (select max(data) from {ref('mart_estoque_diario')}) "
        f"and estado_estoque = 'Sem estoque'").iloc[0]["n"]

    est = analitico.estrategias_df(w)
    marginal = est[est.estrategia.str.contains("marginal")].iloc[0]
    reposicao = est[est.estrategia.str.contains("ideal")].iloc[0]

    comprar = plano[plano.quantidade_a_comprar > 0]
    segurar = plano[(plano.quantidade_a_comprar == 0) & (plano.unidades_com_retorno > 0)]
    abaixo_rop = plano[plano.posicao_estoque <= plano.ponto_de_pedido]

    livre = comp[comp.politica.str.contains("SEM teto")]
    presa = comp[comp.politica.str.contains("COM teto")]
    custo_do_teto = (float(livre.lucro_liquido_ano.iloc[0]) -
                     float(presa.lucro_liquido_ano.iloc[0])) if not livre.empty else 0.0

    k = {
        "comprar_itens": int(len(comprar)),
        "comprar_valor": float(comprar.valor_da_compra.sum()),
        "comprar_valor_ecommerce": float(comprar.get("valor_da_compra_ecommerce", pd.Series(dtype=float)).sum()),
        "comprar_valor_lojas": float(comprar.get("valor_da_compra_lojas", pd.Series(dtype=float)).sum()),
        "teto_ecommerce": p.teto_compra_ecommerce,
        "teto_ciclo": p.teto_compra_ciclo,
        "segurar_itens": int(len(segurar)),
        "segurar_valor": float(segurar.custo_total_disponivel.sum()),
        "segurar_ve": float(segurar.valor_total_disponivel.sum()),
        "familias_compra": int(comprar.familia.nunique()),
        "pecas_compra": int(comprar.quantidade_a_comprar.sum()),
        "margem_esperada": float(marginal.valor_liquido),
        "baixa_chance": int(marginal.pecas_baixa_chance),
        "rep_itens": int(reposicao.itens_atendidos),
        "rep_ve": float(reposicao.valor_liquido),
        "rep_baixa": int(reposicao.pecas_baixa_chance),
        "abaixo_rop": int(len(abaixo_rop)),
        "sem_estoque_hoje": int(hoje),
        "skus": int(len(m)),
        "capital": float(m.capital_imobilizado.sum()),
        "teto_capital": p.teto_capital,
        "lam": float(e.premio_escassez),
        "custo_do_teto": custo_do_teto,
        "lucro_liquido": float(m.lucro_liquido_ano.sum()),
        "lucro_perdido": float(m.lucro_perdido_ruptura.sum()),
        "faltas_ano": float(m.faltas_esperadas_ano.sum()),
        "nivel_servico": float(m.nivel_servico.mean()),
        "giro": float(np.average(m.giro_ano, weights=m.capital_imobilizado.clip(lower=1e-9))),
        "cobertura": float(np.average(m.cobertura_dias.clip(0, 400),
                                      weights=m.capital_imobilizado.clip(lower=1e-9))),
        "risco_medio": float(abaixo_rop.risco_de_faltar.mean()) if len(abaixo_rop) else 0.0,
        "itens_discreto": int((m.regime == "Unidade marginal").sum()),
        "subestimacao": float(m[m.demanda_media_dia_ingenua > 0].subestimacao_ingenua_pct.mean()),
    }
    return tpl.TemplateResponse(request, "painel.html", contexto(request, "painel", k=k))


@app.get("/api/painel/serie")
def api_serie():
    w = wh()
    df = w.query(f"select * from {ref('mart_vendas_diarias')} order by data")
    return JSONResponse({"dias": analitico.registros(df)})


@app.get("/api/painel/abc")
def api_abc():
    w = wh()
    df = w.query(
        f"select sku, item, familia, curva_abc, classe_xyz, lucro_potencial_periodo, "
        f"lucro_acumulado_pct, capital_imobilizado, cv_diario, mu_periodo, giro_ano "
        f"from {ref('res_sku_modelo')} order by lucro_acumulado_pct")
    df = df.reset_index(drop=True)
    df["ordem"] = (df.index + 1) / max(len(df), 1)
    return JSONResponse({"itens": analitico.registros(df)})


@app.get("/api/painel/matriz")
def api_matriz():
    w = wh()
    df = w.query(
        f"select curva_abc, classe_xyz, count(*) as skus, "
        f"sum(lucro_potencial_periodo) as lucro, sum(capital_imobilizado) as capital, "
        f"sum(lucro_perdido_ruptura) as perdido "
        f"from {ref('res_sku_modelo')} group by 1, 2")
    return JSONResponse({"celulas": analitico.registros(df)})


@app.get("/api/painel/abc_lojas")
def api_abc_lojas():
    """ABC x XYZ de cada loja, so com a venda dela, e o cruzamento com a classe do CD."""
    return JSONResponse(analitico.abc_xyz_por_loja(wh(), Parametros.carregar()))


@app.get("/api/painel/familias")
def api_familias():
    return JSONResponse({"familias": analitico.cobertura_familias(wh())})


@app.get("/api/painel/alertas")
def api_alertas(limite: int = 22):
    return JSONResponse({"itens": analitico.alerta_ruptura(wh(), limite)})


@app.get("/api/painel/politicas")
def api_politicas():
    w = wh()
    return JSONResponse({"politicas": analitico.registros(
        w.query(f"select * from {ref('res_comparativo')}"))})


@app.get("/api/painel/perdas")
def api_perdas(limite: int = 14):
    w = wh()
    df = w.query(
        f"select sku, item, familia, curva_abc, dias_sem_estoque, dias_ruptura_parcial, "
        f"pct_indisponivel, venda_perdida_pecas, lucro_perdido_ruptura, "
        f"subestimacao_ingenua_pct, demanda_media_dia, demanda_media_dia_ingenua "
        f"from {ref('res_sku_modelo')} where lucro_perdido_ruptura > 0 "
        f"order by lucro_perdido_ruptura desc limit {int(limite)}")
    return JSONResponse({"itens": analitico.registros(df)})


# ======================================================================
# PLANO DE COMPRA - alocacao marginal
# ======================================================================
@app.get("/plano")
def plano(request: Request):
    w = wh()
    if not pronto(w):
        return sem_dados(request)
    p = Parametros.carregar()
    df = analitico.plano_df(w)
    est = analitico.estrategias_df(w)

    comprados = df[df.quantidade_a_comprar > 0].sort_values(
        "margem_esperada", ascending=False)
    # em que peca do ciclo cada produto entra na compra (ordem da fila
    # marginal) - antes era uma tabela a parte, agora e uma coluna da fila
    entradas = pd.DataFrame(analitico.entradas_na_compra(w))
    if not entradas.empty:
        comprados = comprados.merge(
            entradas[["sku", "entra_na_peca", "chance_primeira"]], on="sku", how="left")
    fora = df[(df.quantidade_a_comprar == 0) & (df.unidades_com_retorno > 0)].sort_values(
        "melhor_nota", ascending=False)
    sem_retorno = df[df.unidades_com_retorno == 0]

    marginal = est[est.estrategia.str.contains("marginal")].iloc[0]
    reposicao = est[est.estrategia.str.contains("ideal")].iloc[0]

    resumo = {
        "teto": p.teto_compra_ciclo,
        "itens": int(len(comprados)),
        "catalogo": int(len(df)),
        "pecas": int(comprados.quantidade_a_comprar.sum()),
        "investimento": float(comprados.valor_da_compra.sum()),
        "sobra": float(p.teto_compra_ciclo - comprados.valor_da_compra.sum()),
        "investimento_ecommerce": float(comprados.get("valor_da_compra_ecommerce", pd.Series(dtype=float)).sum()),
        "investimento_lojas": float(comprados.get("valor_da_compra_lojas", pd.Series(dtype=float)).sum()),
        "teto_ecommerce": float(p.teto_compra_ecommerce),
        "fatia_rigida": bool(p.fatia_ecommerce_rigida),
        "valor_esperado": float(marginal.valor_esperado),
        "valor_liquido": float(marginal.valor_liquido),
        "retorno": float(marginal.retorno_por_real),
        "familias": int(comprados.familia.nunique()),
        "baixa_chance": int(marginal.pecas_baixa_chance),
        "fora_itens": int(len(fora)),
        "fora_valor": float(fora.custo_total_disponivel.sum()),
        "fora_ve": float(fora.valor_total_disponivel.sum()),
        "sem_retorno": int(len(sem_retorno)),
        "rep_itens": int(reposicao.itens_atendidos),
        "rep_ve": float(reposicao.valor_liquido),
        "rep_baixa": int(reposicao.pecas_baixa_chance),
        "lote_minimo": bool(p.respeitar_lote_minimo),
    }

    cols = ["dias_utilizaveis", "historico_insuficiente",
            "sku", "item", "familia", "origem", "classificacao", "curva_abc",
            "posicao_estoque", "quantidade_a_comprar", "ultima_unidade",
            "demanda_media_dia", "entra_na_peca", "chance_primeira",
            "p_vender_ultima", "custo_unitario", "valor_da_compra",
            "valor_da_compra_ecommerce", "valor_da_compra_lojas",
            "margem_esperada", "retorno_por_real", "melhor_nota",
            "unidades_com_retorno", "periodo_protecao_dias", "mu_periodo",
            "cobertura_dias", "cobertura_apos_dias", "risco_de_faltar",
            "risco_apos_compra", "ponto_de_pedido", "lote_minimo_compra"]
    cols = [c for c in cols if c in comprados.columns]
    fora_cols = [c for c in ["sku", "item", "familia", "origem", "classificacao", "curva_abc",
                             "posicao_estoque", "unidades_com_retorno", "melhor_nota",
                             "custo_total_disponivel", "valor_total_disponivel",
                             "risco_de_faltar", "cobertura_dias"] if c in df.columns]

    # marcas (coluna `origem` do catalogo) presentes no pedido ou na fila de
    # espera, para o filtro da tela - as que nao tem nenhuma peca nem
    # candidata ficam de fora da lista
    presentes = pd.concat([comprados.origem, fora.origem]) if "origem" in df.columns else pd.Series(dtype=str)
    marcas = sorted(presentes.dropna().astype(str).unique(), key=str.casefold)

    return tpl.TemplateResponse(request, "plano.html", contexto(
        request, "plano", resumo=resumo, marcas=marcas,
        linhas=analitico.registros(comprados[cols]),
        fora=analitico.registros(fora[fora_cols].head(40)),
        estrategias=analitico.registros(est)))


@app.get("/api/plano/fila")
def api_fila(limite: int = 2200):
    """A fila de blocos, do melhor retorno por real por dia para o pior."""
    w = wh()
    p = Parametros.carregar()
    f = analitico.fila_df(w)
    cols = ["posicao_fila", "sku", "item", "familia", "bloco", "unidade_de",
            "unidade_ate", "quantidade", "p_vender", "p_vender_ultima", "custo",
            "custo_unitario", "valor_esperado", "nota", "comprar", "caixa_acumulado"]
    cols = [c for c in cols if c in f.columns]
    return JSONResponse({
        "fila": analitico.registros(f[cols].head(int(limite))),
        "total_blocos": int(len(f)),
        "comprados": int(f.comprar.sum()),
        "teto": p.teto_compra_ciclo,
    })


@app.get("/api/plano/corrida")
def api_corrida(n: int = 100, todas: bool = False):
    """As primeiras N pecas compradas, uma a uma - a 'corrida' entre os itens.

    Com `todas`, devolve o ciclo inteiro; ai o payload e enxuto de proposito,
    porque sao milhares de pecas e o detalhe de cada uma vem sob demanda em
    /api/conferencia/{posicao}.
    """
    w = wh()
    limite = 10_000_000 if todas else int(n)
    pecas = analitico.corrida(w, limite, so_compradas=True)
    return JSONResponse({"pecas": pecas, "total": len(pecas)})


# ----------------------------------------------------------------------
# Conferencia: a fila inteira, linha a linha, com toda a cadeia de calculo
# ----------------------------------------------------------------------
@app.get("/conferencia")
def conferencia(request: Request, sku: str = "", decisao: str = "", q: str = "",
                ordem: str = "posicao_fila", sentido: str = "asc", pg: int = 1):
    w = wh()
    if not pronto(w) or not w.existe("res_fila_marginal"):
        return sem_dados(request)

    fila = analitico.fila_pagina(
        w, sku=sku, motivo=decisao, busca=q,
        ordem=ordem, desc=(sentido == "desc"), pg=pg)

    tot = w.query(
        f"select count(*) as linhas, sum(quantidade) as pecas, "
        f"count(distinct sku) as skus, "
        f"sum(case when comprar then quantidade else 0 end) as pecas_compradas, "
        f"sum(case when comprar then custo else 0 end) as investido, "
        f"sum(case when comprar then valor_esperado else 0 end) as margem "
        f"from {ref('res_fila_marginal')}").iloc[0]

    itens = w.query(
        f"select sku, min(item) as item, count(*) as linhas "
        f"from {ref('res_fila_marginal')} group by sku order by min(item)")

    return tpl.TemplateResponse(request, "conferencia.html", contexto(
        request, "conferencia",
        fila=fila, filtros={"sku": sku, "decisao": decisao, "q": q,
                                "ordem": ordem, "sentido": sentido},
        etapas=analitico.ETAPAS_FILA,
        itens=analitico.registros(itens),
        tot={k: (float(v) if v is not None else 0) for k, v in tot.items()}))


@app.get("/api/etapas")
def api_etapas():
    """Como agrupar e rotular os campos da fila. O dossie de uma peca usa isto
    para montar a trilha, em qualquer tela."""
    return JSONResponse({"etapas": analitico.ETAPAS_FILA})


@app.get("/api/conferencia/{posicao}")
def api_conferencia_linha(posicao: int):
    """Uma linha da fila com todos os campos, para a trilha de auditoria."""
    w = wh()
    df = w.query_params(
        f"select * from {ref('res_fila_marginal')} where posicao_fila = ?", [int(posicao)])
    if df.empty:
        return JSONResponse({"erro": "linha nao encontrada"}, status_code=404)
    return JSONResponse({"linha": analitico.registros(df)[0]})


@app.get("/conferencia.csv")
def conferencia_csv(sku: str = "", decisao: str = "", q: str = ""):
    """A fila inteira em CSV, com todas as colunas - para conferir no Excel."""
    from fastapi.responses import StreamingResponse

    w = wh()
    pagina = analitico.fila_pagina(w, sku=sku, motivo=decisao, busca=q,
                                   pg=1, tam=1_000_000)
    df = pd.DataFrame(pagina["linhas"])

    def gerar():
        yield "﻿"                      # BOM: o Excel abre acentos certo
        yield ";".join(df.columns) + "\n"
        for linha in df.itertuples(index=False):
            campos = []
            for v in linha:
                if v is None:
                    campos.append("")
                elif isinstance(v, bool):
                    campos.append("sim" if v else "nao")
                elif isinstance(v, (int, float)):
                    campos.append(str(v).replace(".", ","))
                else:
                    campos.append('"' + str(v).replace('"', '""') + '"')
            yield ";".join(campos) + "\n"

    nome = f"conferencia-fila{'-' + sku if sku else ''}.csv"
    return StreamingResponse(gerar(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{nome}"'})


@app.get("/api/plano/rateio")
def api_rateio():
    """Quanto da compra do ciclo cabe a cada empresa, pela demanda dela em cada item."""
    p = Parametros.carregar()
    # a janela da participacao observada e a mesma que estimou a demanda -
    # senao a fatia por loja fala de um periodo e o share_ecommerce de outro
    d = analitico.rateio_por_loja(wh(), janela=int(p.janela_estimacao_dias))
    d["teto_ecommerce"] = float(p.teto_compra_ecommerce)
    d["fatia_rigida"] = bool(p.fatia_ecommerce_rigida)
    return JSONResponse(d)


@app.get("/api/plano/entradas")
def api_entradas():
    """Em que peca do ciclo cada produto entra na compra."""
    return JSONResponse({"entradas": analitico.entradas_na_compra(wh())})


@app.get("/api/plano/estrategias")
def api_estrategias():
    return JSONResponse({"estrategias": analitico.registros(analitico.estrategias_df(wh()))})


# ======================================================================
# ITENS
# ======================================================================
def _itens_tabela(w) -> pd.DataFrame:
    """As colunas da tabela de /itens, item a item, ja ordenadas por lucro
    potencial. Servida como JSON: com ~19 mil SKUs, renderizar a tabela no
    HTML passava de 38 MB e travava o navegador."""
    df = analitico.modelo_df(w).merge(
        analitico.plano_df(w)[["sku", "posicao_estoque", "quantidade_a_comprar",
                               "valor_da_compra", "decisao", "risco_de_faltar"]],
        on="sku", how="left")
    df = df.sort_values("lucro_potencial_periodo", ascending=False)
    # preco praticado por peca, refeito da venda: e a unica forma de conferir a
    # margem na propria linha. `lucro_por_peca` ja e o lucro observado medio, e
    # custo + lucro tem de dar o preco - se nao der, a conta esta errada e a
    # tela mostra isso em vez de escondor.
    df["preco_por_peca"] = df.custo_unitario + df.lucro_por_peca
    df["margem_por_peca_pct"] = np.where(
        df.preco_por_peca > 0, df.lucro_por_peca / df.preco_por_peca, np.nan)
    cols = ["sku", "item", "familia", "classificacao", "curva_abc", "classe_xyz", "regime",
            "demanda_media_dia", "cv_diario", "posicao_estoque", "ponto_de_pedido",
            "estoque_maximo", "lote_compra", "estoque_seguranca", "nivel_servico",
            "cobertura_dias", "giro_ano", "capital_imobilizado", "lucro_bruto_ano",
            "lucro_perdido_ruptura", "faltas_esperadas_ano", "risco_de_faltar",
            "decisao", "lead_time_dias", "custo_unitario", "distribuicao",
            "dias_sem_estoque", "dias_ruptura_parcial", "subestimacao_ingenua_pct",
            "preco_por_peca", "lucro_por_peca", "margem_por_peca_pct",
            "pecas_vendidas", "quantidade_a_comprar", "valor_da_compra",
            "lead_time_desvio_dias", "lead_time_pedidos", "mu_periodo"]
    return df[[c for c in cols if c in df.columns]]


@app.get("/itens")
def itens(request: Request):
    w = wh()
    if not pronto(w):
        return sem_dados(request)
    fam = w.query(f"select distinct familia from {ref('res_sku_modelo')} "
                  f"where familia is not null order by 1")
    total = w.query(f"select count(*) as n from {ref('res_sku_modelo')}").n.iloc[0]
    return tpl.TemplateResponse(request, "itens.html", contexto(
        request, "itens", total=int(total), familias=fam.familia.tolist()))


@app.get("/api/itens/tabela")
def api_itens_tabela():
    """Todas as linhas da tabela de /itens; a pagina filtra, ordena e pagina
    no navegador."""
    return JSONResponse({"itens": analitico.registros(_itens_tabela(wh()))})


@app.get("/api/item/{sku}/conferencia")
def api_item_conferencia(sku: str, limite: int = 400):
    """Toda venda, toda compra e todo custo lancado do item, sem passar pelo
    modelo. E a trilha de conferencia a mao."""
    d = analitico.conferencia_item(wh(), sku, limite)
    if not d:
        return JSONResponse({"erro": "item nao encontrado"}, status_code=404)
    return JSONResponse(d)


@app.get("/api/item/{sku}")
def api_item(sku: str):
    d = analitico.dossie(wh(), Parametros.carregar(), sku)
    if not d:
        return JSONResponse({"erro": "item nao encontrado"}, status_code=404)
    return JSONResponse(d)


@app.get("/api/itens/lista")
def api_lista():
    w = wh()
    df = w.query(
        f"select sku, item, familia, regime, curva_abc, classificacao, "
        f"mu_periodo, demanda_media_dia, dias_sem_estoque, dias_ruptura_parcial, "
        f"lucro_potencial_periodo from {ref('res_sku_modelo')} order by item")
    return JSONResponse({"itens": analitico.registros(df)})


# ======================================================================
# METODOLOGIA
# ======================================================================
@app.get("/metodologia")
def metodologia(request: Request, sku: str = ""):
    w = wh()
    if not pronto(w):
        return sem_dados(request)
    p = Parametros.carregar()
    m = analitico.modelo_df(w)

    # sugestoes de partida: o item onde cada efeito aparece com mais forca
    sug = {
        "censura": m.sort_values(["dias_ruptura_parcial", "dias_sem_estoque"],
                                 ascending=False).iloc[0],
        "marginal": (m[m.regime == "Unidade marginal"]
                     .sort_values("lucro_por_peca", ascending=False).iloc[0]
                     if (m.regime == "Unidade marginal").any() else m.iloc[0]),
        "giro": m[m.regime == "EOQ + normal"].sort_values(
            "demanda_media_dia", ascending=False).iloc[0],
    }
    escolhido = sku or str(sug["censura"].sku)
    if escolhido not in set(m.sku):
        escolhido = str(sug["censura"].sku)

    lista = m[["sku", "item", "familia", "regime", "curva_abc"]].sort_values("item")
    return tpl.TemplateResponse(request, "metodologia.html", contexto(
        request, "metodologia", sku=escolhido, p=p,
        lista=analitico.registros(lista),
        sugestoes={k: {"sku": str(v.sku), "item": str(v.item)} for k, v in sug.items()}))


@app.get("/api/metodologia/prazo")
def api_metodologia_prazo():
    """Distribuicao do prazo de recebimento das compras no catalogo inteiro:
    realizado contra combinado, pedido a pedido. Evidencia da parcela de
    desvio do prazo no estoque de seguranca."""
    w = wh()
    d = analitico.distribuicao_prazo(w)
    if d:
        p = Parametros.carregar()
        d["pagamento_parametro"] = p.prazo_pagamento_fornecedor_dias
        d["recebimento_parametro"] = {"ecommerce": p.prazo_recebimento_ecommerce_dias,
                                      "lojas": p.prazo_recebimento_lojas_dias}
        # a ponta de entrada do ciclo (venda -> caixa) e o que o motor usou
        d["recebimento"] = analitico.distribuicao_recebimento(w)
        d["modelo"] = analitico.prazos_usados(w)
    return JSONResponse(d)


@app.get("/api/comparar")
def api_comparar():
    """Produtos bem diferentes entre si, medidos na mesma regua."""
    return JSONResponse({
        "produtos": analitico.comparar_produtos(wh(), Parametros.carregar())})


@app.get("/api/capital")
def api_capital():
    w = wh()
    p = Parametros.carregar()
    e = analitico.execucao(w)
    return JSONResponse({
        "curva": analitico.curva_capital(w, p),
        "lam": float(e.premio_escassez),
        "teto": p.teto_capital,
    })


# ======================================================================
# BACKTEST
# ======================================================================
@app.get("/validacao")
def validacao_pagina(request: Request):
    w = wh()
    if not pronto(w):
        return sem_dados(request)
    p = Parametros.carregar()
    lim = validacao.datas_disponiveis(w)
    # a data padrao: seis meses antes do fim, que deixa horizonte suficiente
    # para quase todo o catalogo ser cobrado
    padrao = str((pd.Timestamp(lim["ultimo"]) - pd.Timedelta(days=184)).date())
    return tpl.TemplateResponse(request, "validacao.html", contexto(
        request, "validacao", limites=lim, padrao=padrao,
        campos=[dict(chave=c[0], rotulo=c[1], unidade=c[2], tipo=c[3], ajuda=c[4],
                     valor=getattr(p, c[0]))
                for c in validacao.PARAMETROS_BACKTEST]))


# Uma rodada roda o motor inteiro sobre 1.190 itens mais a janela futura:
# ~10 s. Cara demais para refazer a cada clique de filtro, barata demais para
# gravar no warehouse.
_cache_validacao: dict = {}


def _ajustes_da_url(request: Request) -> dict:
    """Os parametros que vierem na URL, com o nome do proprio campo do modelo."""
    validos = {c[0]: c[3] for c in validacao.PARAMETROS_BACKTEST}
    fora = {}
    for k, v in request.query_params.items():
        if k not in validos or v == "":
            continue
        try:
            fora[k] = float(v) / 100.0 if validos[k] == "pct" else float(v)
        except ValueError:
            pass
    return fora


@app.get("/api/validacao")
def api_validacao(request: Request, corte: str = "", reais: bool = True):
    """O backtest de uma data exata."""
    w = wh()
    aj = _ajustes_da_url(request)
    chave = "bt|" + corte + "|" + str(reais) + "|" + repr(sorted(aj.items()))
    if chave not in _cache_validacao:
        _cache_validacao[chave] = validacao.rodar(w, corte, aj, reais)
    r = _cache_validacao[chave]
    if "erro" in r:
        return JSONResponse(r, status_code=400)
    return JSONResponse(r)


@app.get("/api/validacao/intervalo")
def api_validacao_intervalo(request: Request, de: str, ate: str,
                            reais: bool = True, passo: int = 0):
    """O mesmo backtest em varias datas, uma por ciclo de revisao."""
    w = wh()
    aj = _ajustes_da_url(request)
    chave = f"bti|{de}|{ate}|{reais}|{passo}|" + repr(sorted(aj.items()))
    if chave not in _cache_validacao:
        _cache_validacao[chave] = validacao.rodar_intervalo(
            w, de, ate, aj, reais, passo or None)
    r = _cache_validacao[chave]
    if "erro" in r:
        return JSONResponse(r, status_code=400)
    return JSONResponse(r)


@app.get("/api/validacao/contexto")
def api_validacao_contexto(corte: str, revisao: int = 0):
    """O caixa e o estoque reais da data, para a tela preencher os campos."""
    p = Parametros.carregar()
    return JSONResponse(validacao.contexto_da_data(
        wh(), corte, int(revisao or p.periodo_revisao_dias)))


# ======================================================================
# RETORNO DO DINHEIRO
# ======================================================================
@app.get("/retorno")
def retorno(request: Request):
    w = wh()
    if not pronto(w):
        return sem_dados(request)
    return tpl.TemplateResponse(request, "retorno.html", contexto(request, "retorno"))


@app.get("/qualidade")
def pagina_qualidade(request: Request):
    w = wh()
    if not pronto(w):
        return sem_dados(request)
    return tpl.TemplateResponse(request, "qualidade.html",
                                contexto(request, "qualidade"))


@app.get("/api/qualidade")
def api_qualidade(recarregar: bool = False):
    """As 21 verificacoes de dados, agrupadas.

    Em cache porque a bateria confronta as duas maiores tabelas da base linha a
    linha. `recarregar=true` forca, para quando alguem quer conferir depois de
    mexer no ERP sem rodar o pipeline inteiro.
    """
    if recarregar:
        _cache_qualidade.clear()
    if "d" not in _cache_qualidade:
        _cache_qualidade["d"] = qualidade.verificar(wh(), Parametros.carregar())
    return JSONResponse(_cache_qualidade["d"])


@app.get("/api/retorno")
def api_retorno():
    """Retorno sobre o capital: total, cascata, curva marginal e por item."""
    return JSONResponse(analitico.retorno_do_capital(wh(), Parametros.carregar()))


# ======================================================================
# ACOMPANHAMENTO DE VENDAS (conferencia)
# ======================================================================
@app.get("/acompanhamento")
def pagina_acompanhamento(request: Request):
    w = wh()
    if not pronto(w):
        return sem_dados(request)
    return tpl.TemplateResponse(request, "acompanhamento.html",
                                contexto(request, "acompanhamento"))


@app.get("/api/acompanhamento/serie")
def api_acompanhamento_serie(sku: str):
    """A serie diaria de um item: venda por canal + estoque disponivel e
    reservado. Sem isso o grafico principal da pagina nao tem o que desenhar."""
    d = acompanhamento.serie_item(wh(), sku)
    if not d:
        return JSONResponse({"erro": "item sem posicao de estoque nesta base"},
                            status_code=404)
    return JSONResponse(d)


@app.get("/api/acompanhamento/ranking")
def api_acompanhamento_ranking(recarregar: bool = False):
    """Os itens ordenados por dias em que estoque, venda e reserva nao batem
    entre si. Em cache pelo mesmo motivo da tela de qualidade: refaz a conta
    de 1,3 milhao de linhas item-dia."""
    if recarregar:
        _cache_ranking.clear()
    if "d" not in _cache_ranking:
        _cache_ranking["d"] = acompanhamento.ranking_dias_nao_ok(wh())
    return JSONResponse(_cache_ranking["d"])


@app.get("/api/acompanhamento/item/{sku}")
def api_acompanhamento_item(sku: str, so_nao_ok: bool = False, limite: int = 400):
    """A tabela dia a dia que sustenta o numero do item na lista: estoque,
    venda, reserva e o veredito, linha por linha."""
    return JSONResponse(acompanhamento.detalhe_item(wh(), sku, so_nao_ok, limite))


# ======================================================================
# CRITERIO DE PARADA
# ======================================================================
@app.get("/criterios")
def criterios(request: Request, msg: str = "", erro: str = ""):
    w = wh()
    if not pronto(w) or not w.existe("res_criterios"):
        return sem_dados(request)
    p = Parametros.carregar()
    cr = w.query(f"select * from {ref('res_criterios')}")
    ctx = cr.iloc[0]
    return tpl.TemplateResponse(request, "criterios.html", contexto(
        request, "criterios", p=p, criterios=analitico.registros(cr),
        meta=[dict(chave=c[0], rotulo=c[1], campo=c[2], unidade=c[3], tipo=c[4],
                   descricao=c[5], pergunta=c[6], acao=c[7]) for c in CRITERIOS],
        ativos=motor.criterios_ativos(p),
        base={"risco_inicial": float(ctx.risco_inicial),
              "falta_inicial": float(ctx.falta_inicial),
              "teto_ciclo": float(ctx.teto_ciclo),
              "custo_capital_dia": float(ctx.custo_capital_dia)},
        msg=msg, erro=erro))


@app.get("/api/criterios")
def api_criterios():
    """A fronteira inteira mais o corte de cada criterio.

    A tela simula os quatro cortes em cima desta curva, sem ida ao servidor -
    todo criterio de parada e apenas um ponto sobre a mesma fila.
    """
    w = wh()
    p = Parametros.carregar()
    return JSONResponse({
        "fronteira": analitico.registros(
            w.query(f"select * from {ref('res_fronteira')} order by posicao_fila")),
        "criterios": analitico.registros(w.query(f"select * from {ref('res_criterios')}")),
        "atual": motor.criterios_ativos(p),
    })


@app.get("/api/criterios/previa")
def api_criterios_previa(request: Request, ativos: str = ""):
    """Corta a fila com um conjunto de criterios e valores hipoteticos.

    Os valores chegam como parametros com o nome do proprio campo do modelo
    (teto_compra_ciclo=..., chance_minima_peca=...), o que deixa a tela mandar
    o formulario inteiro sem tradutor no meio.
    """
    campos = {c[2] for c in CRITERIOS}
    valores = {k: v for k, v in request.query_params.items() if k in campos}
    r = analitico.simular_criterios(wh(), Parametros.carregar(), ativos, valores)
    return JSONResponse(r)


@app.post("/criterios")
async def criterios_salvar(request: Request):
    form = await request.form()
    atual = Parametros.carregar()
    dados = asdict(atual)

    # varios criterios podem vir marcados: a compra e cortada pelo primeiro
    validos = [c[0] for c in CRITERIOS]
    marcados = [x for x in form.getlist("criterio_parada") if x in validos]
    escolhidos = [c for c in validos if c in marcados] or ["caixa"]
    dados["criterio_parada"] = ",".join(escolhidos)

    for _chave, _rot, campo, _un, tipo, _d, _q, _a in CRITERIOS:
        bruto = form.get(campo)
        if bruto is None or bruto == "":
            continue
        try:
            dados[campo] = float(bruto) / 100.0 if tipo == "pct" else float(bruto)
        except ValueError:
            pass

    p = Parametros(**dados)
    p.salvar()
    invalidar_caches()
    try:
        res = motor.executar(wh(), p)
        motor.gravar_resultados(wh(), res)
        invalidar_caches()
        cr = res["res_criterios"]
        linha = cr[cr.criterio == "combinado"].iloc[0]
        msg = (f"Critério aplicado: {linha.rotulo}. "
               f"{int(linha.pecas):,} peças de {int(linha.itens)} produtos, "
               f"R$ {linha.caixa:,.0f} de compra, "
               f"R$ {linha.margem_em_risco:,.0f} de margem ainda em risco."
               ).replace(",", ".")
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(url=f"/criterios?erro={str(exc)[:280]}", status_code=303)
    return RedirectResponse(url=f"/criterios?msg={msg}", status_code=303)


# ======================================================================
# PARAMETROS
# ======================================================================
@app.get("/parametros")
def parametros(request: Request, msg: str = "", erro: str = ""):
    p = Parametros.carregar()
    # o grupo e o ultimo elemento da tupla de CAMPOS (indice 7), nao o 6 - o 6
    # e o texto de ajuda, e filtrar por ele deixava a tela sem nenhum campo
    grupos = {g: [c for c in CAMPOS if c[7] == g] for g in GRUPOS}
    return tpl.TemplateResponse(request, "parametros.html", contexto(
        request, "parametros", p=p, grupos=grupos, ordem_grupos=GRUPOS,
        chaves=CHAVES, msg=msg, erro=erro))


@app.post("/parametros")
async def parametros_salvar(request: Request):
    form = await request.form()
    atual = Parametros.carregar()
    # parte dos valores atuais: campos que esta tela nao edita (criterio de
    # parada, pisos) tem de sobreviver ao salvamento
    dados = asdict(atual)
    for chave, _r, _u, tipo, _mi, _ma, _a, _g in CAMPOS:
        bruto = form.get(chave)
        if bruto is None or bruto == "":
            dados[chave] = getattr(atual, chave)
            continue
        try:
            if tipo == "int":
                dados[chave] = int(float(bruto))
            elif tipo == "pct":
                dados[chave] = float(bruto) / 100.0
            else:
                dados[chave] = float(bruto)
        except ValueError:
            dados[chave] = getattr(atual, chave)
    for chave, _r, _a in CHAVES:
        dados[chave] = form.get(chave) == "on"

    p = Parametros(**dados)
    p.salvar()
    invalidar_caches()

    acao = form.get("acao", "salvar")
    msg = "Parametros salvos. Recalcule para aplicar."
    try:
        if acao in ("recalcular", "pipeline"):
            w = wh()
            if acao == "pipeline":
                dbt = RAIZ / "dbt_elevato"
                env = os.environ.copy()
                env["DBT_PROFILES_DIR"] = str(dbt)
                env["DUCKDB_PATH"] = "../data/elevato.duckdb"
                r = subprocess.run(["dbt", "build"], cwd=dbt, env=env,
                                   capture_output=True, text=True, timeout=300)
                if r.returncode != 0:
                    cauda = (r.stderr or r.stdout or "")[-260:]
                    return RedirectResponse(url=f"/parametros?erro=dbt build falhou: {cauda}",
                                            status_code=303)
            res = motor.executar(w, p)
            motor.gravar_resultados(w, res)
            invalidar_caches()
            e = res["res_execucao"].iloc[0]
            msg = (f"Modelo recalculado - premio de escassez {e.premio_escassez:.3f}, "
                   f"{int(e.itens_regime_discreto)} itens no regime discreto, "
                   f"capital R$ {e.capital_total:,.0f}".replace(",", "."))
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(url=f"/parametros?erro={str(exc)[:280]}", status_code=303)
    return RedirectResponse(url=f"/parametros?msg={msg}", status_code=303)


@app.post("/recalcular")
def recalcular():
    try:
        w = wh()
        p = Parametros.carregar()
        res = motor.executar(w, p)
        motor.gravar_resultados(w, res)
        invalidar_caches()
        e = res["res_execucao"].iloc[0]
        return JSONResponse({"ok": True, "lam": float(e.premio_escassez),
                             "capital": float(e.capital_total)})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "erro": str(exc)[:300]}, status_code=500)


# ======================================================================
# OUTLIERS - itens cuja entrada merece correcao manual
# ======================================================================
@app.get("/outliers")
def outliers_pagina(request: Request):
    w = wh()
    if not pronto(w):
        return sem_dados(request)
    return tpl.TemplateResponse(request, "outliers.html", contexto(
        request, "outliers", regras=outliers.REGRAS, campos=ajustes.CAMPOS))


@app.get("/api/outliers")
def api_outliers():
    return JSONResponse(outliers.painel(wh()))


@app.post("/api/outliers/ajuste")
def api_outliers_ajuste(corpo: dict = Body(...)):
    """Grava a correcao de UM item. Nao recalcula: o botao de recalcular da
    tela chama /recalcular depois, para varios ajustes entrarem numa rodada."""
    sku = str(corpo.get("sku", "")).strip()
    if not sku:
        return JSONResponse({"ok": False, "erro": "sku vazio"}, status_code=400)
    reg = ajustes.salvar(sku, corpo.get("valores") or {}, corpo.get("nota", ""))
    invalidar_caches()
    return JSONResponse({"ok": True, "sku": sku, "ajuste": reg,
                         "pendente": True})


@app.delete("/api/outliers/ajuste/{sku}")
def api_outliers_remover(sku: str):
    ok = ajustes.remover(sku)
    invalidar_caches()
    return JSONResponse({"ok": ok, "sku": sku})


# ======================================================================
# DADOS
# ======================================================================
FAMILIAS_TABELA = {
    "raw_": ("Origem", "O que o ERP entrega, sem nenhuma transformação."),
    "stg_": ("Staging", "Tipagem e a regra dos três estados do dia."),
    "int_": ("Intermediário", "Grade item × dia, já com o financeiro junto."),
    "mart_": ("Marts", "Agregações prontas para consumo."),
    "res_": ("Resultados", "Saída do modelo em Python."),
}


def familia_tabela(nome: str):
    for pre, (rot, desc) in FAMILIAS_TABELA.items():
        if nome.startswith(pre):
            return rot, desc
    return "Outra", ""


@app.get("/dados")
def dados(request: Request):
    w = wh()
    linhas = []
    for n in sorted(w.tabelas()):
        try:
            cont = int(w.query(f'select count(*) as n from "{n}"').iloc[0]["n"])
        except Exception:  # noqa: BLE001
            cont = None
        rot, desc = familia_tabela(n)
        linhas.append({"nome": n, "linhas": cont, "familia": rot, "desc": desc})
    ordem = ["Origem", "Staging", "Intermediário", "Marts", "Resultados", "Outra"]
    grupos = [(f, [x for x in linhas if x["familia"] == f]) for f in ordem]
    grupos = [(f, xs) for f, xs in grupos if xs]
    return tpl.TemplateResponse(request, "dados.html", contexto(
        request, "dados", grupos=grupos, total=len(linhas)))


@app.get("/dados/{nome}")
def dados_tabela(request: Request, nome: str, pg: int = 1):
    w = wh()
    if nome not in w.tabelas():
        return RedirectResponse(url="/dados", status_code=303)
    tam = 80
    total = int(w.query(f'select count(*) as n from "{nome}"').iloc[0]["n"])
    pg = max(1, pg)
    df = w.query(f'select * from "{nome}" limit {tam} offset {(pg - 1) * tam}')
    rot, desc = familia_tabela(nome)
    numericas = {c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])}
    return tpl.TemplateResponse(request, "dados_tabela.html", contexto(
        request, "dados", nome=nome, familia=rot, desc=desc,
        colunas=list(df.columns), numericas=numericas,
        linhas=df.replace({np.nan: None}).values.tolist(),
        total=total, pg=pg, paginas=max(1, (total + tam - 1) // tam)))
