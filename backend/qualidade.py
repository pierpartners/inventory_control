# -*- coding: utf-8 -*-
"""
Validacao dos dados de origem.

Existe por um motivo concreto e recente. Durante meses o modelo dimensionou a
prateleira do centro de distribuicao (idempresa 26, idlocalestoque 124) contra
a venda de UM canal - a empresa 33, o e-commerce - porque era o unico arquivo
de venda carregado. Medido depois: aquele canal responde por 17% das pecas que
saem daquela posicao, e explica 8,3% da baixa diaria dela. O motor estimava
demanda com um oitavo do sinal, e nenhuma tela do sistema mostrava isso.

Nao foi erro de conta. Todos os testes de identidade passavam, porque a conta
estava certa - o INSUMO estava errado. Um erro de insumo nao aparece em teste
de identidade: aparece em teste de RECONCILIACAO, que confronta duas fontes que
deveriam contar a mesma historia e mede o quanto elas discordam.

E o que este modulo faz. Cada verificacao devolve o mesmo formato, e cada uma
diz o que confronta, o que encontrou e o que fazer com isso.

As checagens estao em seis grupos:

  estoque     a base fecha consigo mesma?
  fluxo       a baixa de estoque casa com a venda?
  venda       a linha de venda e limpa?
  compra      pedido, recebimento e prazo se sustentam?
  cadastro    custo, preco e prazo existem e sao plausiveis?
  parametros  o que esta configurado ainda cabe nos dados?
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Parametros
from .warehouse import Warehouse, ref

# ----------------------------------------------------------------------
# O contrato de uma verificacao
# ----------------------------------------------------------------------
# nivel:  "ok"     nada a fazer
#         "aviso"  e assim mesmo, mas quem le os numeros precisa saber
#         "erro"   compromete decisao; olhar antes de confiar no plano
#
# Nao existe nivel "informativo": se um numero nao muda nada, ele nao merece
# uma linha num painel que alguem vai ler antes de gastar dinheiro.


def _v(grupo, chave, titulo, nivel, valor, unidade, confronta, achado,
       acao="", linhas=None, colunas=None):
    return {
        "grupo": grupo, "chave": chave, "titulo": titulo, "nivel": nivel,
        "valor": valor, "unidade": unidade,
        "confronta": confronta,   # quais duas fontes estao sendo comparadas
        "achado": achado,         # o que a medicao encontrou, em uma frase
        "acao": acao,             # o que fazer, quando ha o que fazer
        "linhas": linhas or [],   # as linhas que exemplificam o problema
        "colunas": colunas or [],
    }


def _reg(df: pd.DataFrame, limite: int = 40) -> list[dict]:
    """Registros prontos para JSON.

    Tres conversoes, e todas as tres derrubaram a rota com 500 antes de
    existirem aqui: NaN nao e JSON valido, `Timestamp` nao serializa, e inteiro
    do numpy nao e `int` do Python.
    """
    if df is None or df.empty:
        return []
    d = df.head(limite).copy()
    for c in d.columns:
        col = d[c]
        if pd.api.types.is_datetime64_any_dtype(col):
            d[c] = col.dt.strftime("%Y-%m-%d").where(col.notna(), None)
        elif pd.api.types.is_float_dtype(col):
            d[c] = col.astype(float).where(np.isfinite(col), None)
        elif pd.api.types.is_integer_dtype(col):
            d[c] = col.astype("Int64").astype(object).where(col.notna(), None)
        elif pd.api.types.is_bool_dtype(col):
            d[c] = col.astype(bool)
        else:
            d[c] = col.astype(object).where(col.notna(), None)
    saida = d.to_dict("records")
    # o que escapou dos dtypes (date puro do DuckDB vem como object)
    for r in saida:
        for k, v in list(r.items()):
            if isinstance(v, (pd.Timestamp,)) or hasattr(v, "isoformat"):
                r[k] = str(v)[:10]
            elif isinstance(v, np.generic):
                r[k] = v.item()
    return saida


def _num(x, casas=0):
    try:
        return f"{float(x):,.{casas}f}".replace(",", "@").replace(".", ",").replace("@", ".")
    except Exception:
        return str(x)


def _pct(x, casas=1):
    try:
        return f"{100 * float(x):.{casas}f}%".replace(".", ",")
    except Exception:
        return "–"


def _existe(wh: Warehouse, tabela: str) -> bool:
    try:
        wh.query(f"select 1 from {ref(tabela)} limit 1")
        return True
    except Exception:
        return False


# ======================================================================
# 1. ESTOQUE: a base fecha consigo mesma?
# ======================================================================
def _estoque(wh: Warehouse) -> list[dict]:
    G = "estoque"
    out = []

    # ---------------------------------------------------------- identidade
    # fisico - reserva = disponivel. Se esta identidade quebra, nao ha como
    # saber qual das tres colunas acreditar - e as tres decidem coisas
    # diferentes no motor.
    r = wh.query(f"""
        select count(*) n,
               sum(case when abs(cast(qtdatualestoque as double)
                                 - cast(qtdreserva as double)
                                 - cast(qtddisponivel as double)) < 0.001
                        then 1 else 0 end) fecham,
               max(abs(cast(qtdatualestoque as double)
                       - cast(qtdreserva as double)
                       - cast(qtddisponivel as double))) pior
        from {ref('raw_estoque_diario_erp')}""").iloc[0]
    ok = int(r.n - r.fecham) == 0
    out.append(_v(G, "identidade_estoque",
                  "físico − reserva = disponível",
                  "ok" if ok else "erro",
                  _pct(r.fecham / r.n) if r.n else "–", "das linhas",
                  "as três colunas de quantidade do próprio retrato diário",
                  f"{_num(r.fecham)} de {_num(r.n)} linhas fecham exatamente"
                  + (f" · pior desvio {r.pior:.4f} peça" if not ok else
                     " · desvio máximo zero"),
                  "" if ok else "As três colunas decidem coisas diferentes no "
                  "motor (o físico é o sinal de demanda, o disponível é a posição "
                  "de compra). Com a identidade quebrada não há como saber em qual "
                  "confiar."))

    # ------------------------------------------------- grade diaria completa
    r = wh.query(f"""
        with g as (select cast(idsubproduto as varchar) sku,
                          cast(dtmovimento as date) dia, count(*) k
                   from {ref('raw_estoque_diario_erp')} group by 1,2)
        select count(*) pares, sum(case when k > 1 then 1 else 0 end) repetidos,
               count(distinct sku) skus, count(distinct dia) dias,
               min(dia) de, max(dia) ate from g""").iloc[0]
    esperado = int(r.skus) * int(r.dias)
    falta = esperado - int(r.pares)
    ok = int(r.repetidos) == 0 and falta == 0
    out.append(_v(G, "grade_diaria",
                  "um retrato por item por dia, sem repetir nem faltar",
                  "ok" if ok else "aviso",
                  _num(r.pares), f"pares item-dia de {_num(esperado)} possíveis",
                  "a grade item × dia contra o produto cartesiano dela",
                  f"{_num(r.skus)} itens × {_num(r.dias)} dias entre {r.de} e {r.ate}"
                  + (f" · {_num(r.repetidos)} pares repetidos" if r.repetidos else "")
                  + (f" · {_num(falta)} pares ausentes" if falta else
                     " · nenhum repetido, nenhum ausente"),
                  "" if ok else "Dia ausente vira buraco na série e o modelo o lê "
                  "como dia sem informação; dia repetido conta a venda duas vezes."))

    # ------------------------------------------------- negativos
    r = wh.query(f"""
        select
          sum(case when cast(qtdatualestoque as double) < 0 then 1 else 0 end) fis_neg,
          sum(case when cast(qtddisponivel as double) < 0 then 1 else 0 end) disp_neg,
          sum(case when cast(qtdreserva as double) < 0 then 1 else 0 end) res_neg,
          count(*) n,
          round(min(cast(qtddisponivel as double)), 1) pior_disp
        from {ref('raw_estoque_diario_erp')}""").iloc[0]
    tot = int(r.fis_neg + r.disp_neg + r.res_neg)
    out.append(_v(G, "estoque_negativo",
                  "quantidade negativa no retrato",
                  "ok" if tot == 0 else "aviso",
                  _num(tot), "linhas",
                  "as três colunas de quantidade contra zero",
                  f"físico negativo em {_num(r.fis_neg)} · disponível negativo em "
                  f"{_num(r.disp_neg)} (pior {_num(r.pior_disp, 1)}) · reserva "
                  f"negativa em {_num(r.res_neg)}, de {_num(r.n)} linhas",
                  "Disponível negativo é reserva acima do físico — o ERP prometeu "
                  "mais do que tem. O modelo trata como zero, o que é a leitura "
                  "conservadora, mas o número em si aponta venda a descoberto."
                  if int(r.disp_neg) else ""))

    # ------------------------------- custo zerado em dia de estoque zero
    # E o reset do ERP: quando o saldo vai a zero o custo medio vai a R$ 1,00.
    # O cadastro ja se protege pegando o ultimo custo de um dia COM estoque;
    # esta linha existe para o tamanho do problema ficar a vista.
    r = wh.query(f"""
        with c as (
          select cast(idsubproduto as varchar) sku,
                 cast(valcustomedio as double) custo,
                 cast(qtdatualestoque as double) q,
                 median(cast(valcustomedio as double))
                   over (partition by idsubproduto) mediana
          from {ref('raw_estoque_diario_erp')} where valcustomedio > 0)
        select count(*) n,
               count(distinct case when q <= 0 and custo < mediana * 0.5
                                   then sku end) skus
        from c where q <= 0 and custo < mediana * 0.5""").iloc[0]
    out.append(_v(G, "custo_resetado",
                  "custo médio lançado em dia de estoque zero",
                  "ok" if int(r.n) == 0 else "aviso",
                  _num(r.n), "lançamentos",
                  "o custo médio do dia contra a mediana histórica do próprio item",
                  f"{_num(r.n)} lançamentos abaixo de metade da mediana em dia de "
                  f"saldo zero, em {_num(r.skus)} itens",
                  "Quando o saldo vai a zero o ERP zera o custo médio e lança "
                  "R$ 1,00. O cadastro já ignora esses dias — foi assim que uma "
                  "prateleira de R$ 1.016 parou de aparecer valendo um real e "
                  "liderando a fila de compra com nota 51× a do segundo."
                  if int(r.n) else ""))
    return out


# ======================================================================
# 2. FLUXO: a baixa de estoque casa com a venda?
# ======================================================================
def _fluxo(wh: Warehouse) -> list[dict]:
    """A reconciliacao que faltava.

    Nenhuma checagem de identidade pegaria a fonte de venda errada, porque
    dentro de cada tabela tudo fechava. O que pega e confrontar as duas: a
    prateleira baixou tanto, a venda registrou tanto, e a diferenca tem de ter
    explicacao.
    """
    G = "fluxo"
    out = []

    # O delta diario vai inline em cada consulta, e nao numa temp table: cada
    # leitura abre uma conexao somente-leitura propria, e a temp table da
    # anterior nao existe mais quando a proxima roda.
    DELTA = f"""
        select sku, data, pecas_vendidas v,
               saldo_final - lag(saldo_final)
                   over (partition by sku order by data) dfis,
               disponivel_final - lag(disponivel_final)
                   over (partition by sku order by data) ddisp
        from {ref('mart_estoque_diario')} qualify dfis is not null"""

    r = wh.query(f"""select
          round(-sum(case when ddisp < 0 then ddisp else 0 end)) baixa_disp,
          round(-sum(case when dfis  < 0 then dfis  else 0 end)) baixa_fis,
          round(sum(v)) vendido,
          round(sum(case when ddisp < 0 then least(-ddisp, v) else 0 end)) casa_disp,
          round(sum(case when dfis  < 0 then least(-dfis,  v) else 0 end)) casa_fis
        from ({DELTA})""").iloc[0]
    cob = float(r.casa_disp) / float(r.baixa_disp) if r.baixa_disp else 0.0
    nivel = "ok" if cob >= 0.7 else ("aviso" if cob >= 0.4 else "erro")
    out.append(_v(G, "venda_explica_baixa",
                  "a venda do dia explica a baixa de estoque do dia",
                  nivel, _pct(cob), "da baixa de disponível",
                  "a queda diária do estoque disponível contra a venda registrada "
                  "no mesmo dia, item por item",
                  f"a prateleira baixou {_num(r.baixa_disp)} peças de disponível "
                  f"({_num(r.baixa_fis)} de físico) e a venda registrou "
                  f"{_num(r.vendido)}; casam no mesmo dia {_num(r.casa_disp)}",
                  "Esta é a verificação que denunciou a fonte de venda errada: com "
                  "só o e-commerte carregado ela dava 8,3%. Abaixo de 40% "
                  "significa que a venda carregada não é a venda que consome este "
                  "estoque."
                  if nivel != "ok" else
                  "A venda casa com a queda do DISPONÍVEL, não do físico: a venda "
                  "reserva a peça na hora e a saída física vem na entrega, dias "
                  "depois. É por isso que o motor usa o disponível como posição de "
                  "compra e o físico como sinal de demanda."))

    # ------------------------------------------ a baixa fisica com defasagem
    r = wh.query(f"""
        with s as (select sku, data, -dfis s from ({DELTA}) where dfis < 0),
             v as (select sku, data, pecas_vendidas v
                   from {ref('mart_estoque_diario')} where pecas_vendidas > 0)
        select round(sum(least(s.s, coalesce(j.v, 0)))) casa, round(sum(s.s)) tot
        from s left join (
            select s2.sku, s2.data, sum(v.v) v
            from s s2 join v on v.sku = s2.sku
                 and v.data between s2.data - INTERVAL 7 DAY
                                and s2.data + INTERVAL 7 DAY
            group by 1,2) j on j.sku = s.sku and j.data = s.data""").iloc[0]
    cob7 = float(r.casa) / float(r.tot) if r.tot else 0.0
    out.append(_v(G, "baixa_fisica_janela",
                  "a saída física acompanha a venda numa janela de ±7 dias",
                  "ok" if cob7 >= 0.8 else "aviso",
                  _pct(cob7), "da baixa física",
                  "a queda do estoque físico contra a venda de uma semana antes "
                  "ou depois",
                  f"{_pct(cob7)} da saída física tem venda correspondente em ±7 "
                  f"dias ({_num(r.casa)} de {_num(r.tot)} peças)",
                  "A mercadoria sai da prateleira dias depois da venda, porque "
                  "material de construção se vende e se entrega. É esse atraso "
                  "que faz a conta do mesmo dia não fechar — e é ele que a janela "
                  "revela."))

    # ------------------------------- venda em dia classificado sem estoque
    r = wh.query(f"""select
          round(sum(pecas_vendidas)) tudo,
          round(sum(case when estado_estoque = 'Sem estoque'
                         then pecas_vendidas else 0 end)) fora
        from {ref('mart_estoque_diario')}""").iloc[0]
    fora = float(r.fora) / float(r.tudo) if r.tudo else 0.0
    det = wh.query(f"""
        with p as (select d.sku, c.item,
              sum(d.pecas_vendidas) tudo,
              sum(case when d.estado_estoque = 'Sem estoque'
                       then d.pecas_vendidas else 0 end) fora,
              sum(case when d.saldo_final > 0 then 1 else 0 end) dias_com_estoque
            from {ref('mart_estoque_diario')} d
            join {ref('stg_catalogo')} c on c.sku = d.sku
            group by 1,2 having fora > 0)
        select sku, item, round(tudo) vendeu, round(fora) fora_de_estoque,
               round(100.0 * fora / tudo) pct, dias_com_estoque
        from p order by fora desc limit 40""")
    nunca = wh.query(f"""
        with p as (select sku, sum(pecas_vendidas) v,
              sum(case when saldo_final > 0 then 1 else 0 end) d
            from {ref('mart_estoque_diario')} group by 1)
        select count(*) skus, round(sum(v)) pecas from p where d = 0 and v > 0
        """).iloc[0]
    out.append(_v(G, "venda_sem_estoque",
                  "venda registrada em dia de prateleira vazia",
                  "aviso" if fora > 0.02 else "ok",
                  _pct(fora), "das peças vendidas",
                  "o estado do estoque no dia contra a venda daquele dia",
                  f"{_num(r.fora)} de {_num(r.tudo)} peças foram vendidas em dia "
                  f"que abriu com saldo zero · {_num(nunca.skus)} itens vendem "
                  f"{_num(nunca.pecas)} peças sem NUNCA ter tido estoque nesta posição",
                  "O modelo descarta esses dias de propósito: dia sem mercadoria "
                  "não diz nada sobre o que o mercado queria. Mas item que vende "
                  "muito e nunca tem estoque aqui é atendido de outra posição — "
                  "ele não pertence a este plano de compra, e enquanto estiver na "
                  "lista ele infla o descarte e some da conta sem explicação.",
                  _reg(det), ["sku", "item", "vendeu", "fora_de_estoque", "pct",
                              "dias_com_estoque"]))

    # ---------------------------------------- a reserva acompanha a venda
    r = wh.query(f"""select
          round(sum(case when ddisp < 0 and dfis >= 0 then -ddisp else 0 end)) so_reserva,
          round(sum(case when ddisp < 0 then -ddisp else 0 end)) baixa_disp
        from ({DELTA})""").iloc[0]
    p = float(r.so_reserva) / float(r.baixa_disp) if r.baixa_disp else 0.0
    out.append(_v(G, "reserva_antes_da_saida",
                  "a peça é reservada antes de sair fisicamente",
                  "ok", _pct(p), "da baixa de disponível",
                  "os dias em que o disponível caiu sem o físico cair",
                  f"{_pct(p)} da queda de disponível acontece sem o físico se "
                  f"mexer: a peça foi prometida e ainda está na prateleira",
                  "É o comportamento esperado e é a razão de o motor usar duas "
                  "colunas: disponível para decidir compra (peça reservada não "
                  "protege a próxima venda) e físico para ler demanda (se havia "
                  "mercadoria em casa, o dia informa)."))
    return out


# ======================================================================
# 3. VENDA: a linha de venda e limpa?
# ======================================================================
def _venda(wh: Warehouse) -> list[dict]:
    G = "venda"
    out = []
    tem_todas = _existe(wh, "raw_vendas_todas")
    fonte = "raw_vendas_todas" if tem_todas else "raw_vendas_ecommerce"

    # ------------------------------------------------------- duplicidade
    r = wh.query(f"""
        with k as (select cast(IDEMPRESA as varchar) e,
                          cast(IDORCAMENTO as varchar) p,
                          cast(NUMSEQUENCIA as varchar) n,
                          cast(IDSUBPRODUTO as varchar) s,
                          cast(DATA as date) d, count(*) k
                   from {ref(fonte)} group by 1,2,3,4,5)
        select count(*) chaves, sum(case when k > 1 then k - 1 else 0 end) excedente,
               (select count(*) from {ref(fonte)}) linhas from k""").iloc[0]
    exemplos = wh.query(f"""
        with k as (select cast(IDEMPRESA as varchar) e, cast(IDORCAMENTO as varchar) p,
                          cast(NUMSEQUENCIA as varchar) n, cast(IDSUBPRODUTO as varchar) s,
                          cast(DATA as date) d, count(*) k
                   from {ref(fonte)} group by 1,2,3,4,5 having k > 1
                   order by k desc limit 20)
        select k.e empresa, k.p pedido, k.n seq, k.s sku, k.d as "data", k.k vezes
        from k""")
    n = int(r.excedente)
    out.append(_v(G, "venda_duplicada",
                  "linha de venda repetida",
                  "ok" if n == 0 else "aviso",
                  _num(n), "linhas excedentes",
                  "empresa + pedido + sequência + produto + data contra si mesma",
                  f"{_num(r.linhas)} linhas para {_num(r.chaves)} chaves distintas",
                  "Cada repetição conta a mesma venda mais de uma vez no sinal de "
                  "demanda. A chave do staging inclui a data justamente porque "
                  "(pedido, sequência) sozinho repete 69 mil vezes — a mesma linha "
                  "de crédito reaparece em datas diferentes."
                  if n else "", _reg(exemplos),
                  ["empresa", "pedido", "seq", "sku", "data", "vezes"]))

    # ------------------------------------------------ quantidade e valor
    r = wh.query(f"""select count(*) n,
          sum(case when cast(QTDPRODUTO as double) < 0 then 1 else 0 end) qtd_neg,
          sum(case when cast(QTDPRODUTO as double) = 0 then 1 else 0 end) qtd_zero,
          round(sum(case when cast(QTDPRODUTO as double) < 0
                    then cast(QTDPRODUTO as double) else 0 end)) pecas_neg,
          sum(case when cast(VALORLIQUIDOVENDA as double) < 0 then 1 else 0 end) val_neg,
          sum(case when cast(QTDPRODUTO as double) < 0
                    and (MOTIVODEVCAN is null or trim(cast(MOTIVODEVCAN as varchar)) = '')
                   then 1 else 0 end) neg_sem_motivo
        from {ref(fonte)}""").iloc[0] if tem_todas else None
    if r is not None:
        out.append(_v(G, "venda_quantidade",
                      "quantidade negativa, zero, e devolução sem motivo",
                      "ok" if int(r.neg_sem_motivo) == 0 else "aviso",
                      _num(r.qtd_neg), "linhas de quantidade negativa",
                      "a quantidade e o valor da linha contra o motivo de "
                      "devolução declarado",
                      f"{_num(r.qtd_neg)} linhas negativas ({_num(-r.pecas_neg)} "
                      f"peças devolvidas) · {_num(r.qtd_zero)} com quantidade zero "
                      f"· {_num(r.val_neg)} com valor líquido negativo · "
                      f"{_num(r.neg_sem_motivo)} negativas SEM motivo declarado",
                      "A devolução é excluída do sinal de demanda de propósito: "
                      "mantê-la inverteria o sinal do dia e o modelo leria "
                      "\"o mercado devolveu\" como \"o mercado não quis\". "
                      "Negativa sem motivo, porém, não se distingue de erro de "
                      "digitação."
                      if int(r.neg_sem_motivo) else
                      "Toda linha negativa tem motivo de devolução declarado."))

    # -------------------------------------- preco unitario fora da faixa
    r = wh.query(f"""
        with l as (select sku, data, pecas_vendidas,
                          receita_liquida / nullif(pecas_vendidas, 0) pu
                   from {ref('stg_vendas')} where pecas_vendidas > 0),
             m as (select sku, median(pu) med from l group by 1)
        select count(*) n,
          sum(case when l.pu > m.med * 5 or l.pu < m.med * 0.2 then 1 else 0 end) fora,
          count(distinct case when l.pu > m.med * 5 or l.pu < m.med * 0.2
                              then l.sku end) skus
        from l join m on m.sku = l.sku""").iloc[0]
    ex = wh.query(f"""
        with l as (select sku, data, pecas_vendidas,
                          receita_liquida / nullif(pecas_vendidas, 0) pu
                   from {ref('stg_vendas')} where pecas_vendidas > 0),
             m as (select sku, median(pu) med from l group by 1)
        select l.sku, c.item, l.data as "data", round(l.pecas_vendidas, 2) pecas,
               round(l.pu, 2) preco_da_linha, round(m.med, 2) preco_mediano,
               round(l.pu / nullif(m.med, 0), 1) razao
        from l join m on m.sku = l.sku
        join {ref('stg_catalogo')} c on c.sku = l.sku
        where l.pu > m.med * 5 or l.pu < m.med * 0.2
        order by abs(l.pu / nullif(m.med, 1) - 1) desc limit 40""")
    frac = float(r.fora) / float(r.n) if r.n else 0.0
    out.append(_v(G, "preco_fora_da_faixa",
                  "preço da linha muito longe do preço mediano do item",
                  "ok" if frac < 0.01 else "aviso",
                  _num(r.fora), "linhas",
                  "o preço unitário de cada linha contra a mediana do próprio item",
                  f"{_num(r.fora)} de {_num(r.n)} linhas ({_pct(frac)}) fora da "
                  f"faixa de 0,2× a 5× a mediana, em {_num(r.skus)} itens",
                  "Preço fora dessa faixa é bonificação, venda casada, erro de "
                  "unidade de medida ou digitação. Entra na margem por peça, que "
                  "é o numerador da nota de compra.",
                  _reg(ex), ["sku", "item", "data", "pecas", "preco_da_linha",
                             "preco_mediano", "razao"]))

    # ------------------------------- venda de item que nao esta no cadastro
    r = wh.query(f"""
        with v as (select distinct sku from {ref('stg_vendas')}),
             c as (select distinct sku from {ref('stg_catalogo')})
        select (select count(*) from v) skus_que_vendem,
               (select count(*) from v anti join c using (sku)) fora_do_plano""").iloc[0]
    ex = wh.query(f"""
        with v as (select sku, sum(pecas_vendidas) p, sum(receita_liquida) r
                   from {ref('stg_vendas')} group by 1),
             c as (select distinct sku from {ref('stg_catalogo')})
        select v.sku, round(v.p) pecas, round(v.r) receita
        from v anti join c using (sku) order by v.p desc limit 40""")
    fora = int(r.fora_do_plano)
    out.append(_v(G, "venda_fora_do_plano",
                  "item que vende e não está no plano de compra",
                  "aviso" if fora else "ok",
                  _num(fora), f"itens de {_num(r.skus_que_vendem)} que vendem",
                  "os itens com venda contra os itens com posição diária de estoque",
                  f"{_num(fora)} itens vendem e não têm retrato de estoque nesta "
                  f"posição, então não entram em nenhum plano",
                  "O plano só cobre o que esta posição estoca. Item vendido sob "
                  "encomenda ou atendido de outro depósito fica de fora por "
                  "definição — mas se um deles vende volume alto, a decisão de não "
                  "estocá-lo merece ser consciente, e não consequência de qual "
                  "arquivo foi carregado.",
                  _reg(ex), ["sku", "pecas", "receita"]))

    # ------------------------- o campo de local de estoque parou de vir
    if tem_todas:
        r = wh.query(f"""select
              date_trunc('quarter', cast(DATA as date)) tri,
              round(100.0 * sum(case when LOCALRETESTOQUE is null then 1 else 0 end)
                    / count(*), 1) pct_vazio
            from {ref('raw_vendas_todas')} group by 1 order by 1 desc limit 6""")
        ult = float(r.pct_vazio.iloc[0]) if len(r) else 0.0
        out.append(_v(G, "local_estoque_morto",
                      "o campo que diz de onde a peça saiu",
                      "aviso" if ult > 50 else "ok",
                      f"{_num(ult, 1)}%", "vazio no trimestre mais recente",
                      "o preenchimento de LOCALRETESTOQUE ao longo do tempo",
                      "o campo era preenchido até meados de 2025 e passou a vir "
                      f"vazio: {_num(ult, 1)}% no trimestre mais recente",
                      "Sem ele não há como separar, na venda recente, o que sai "
                      "deste CD do que sai da prateleira da loja. A demanda usa "
                      "todas as vendas justamente porque este campo não cobre o "
                      "período inteiro — se ele voltar a ser preenchido, dá para "
                      "apertar o recorte.",
                      _reg(r.assign(pct_vazio=r.pct_vazio)), ["tri", "pct_vazio"]))

    # ------------------------- as fontes cobrem o mesmo periodo?
    # O erro mais simples que existe num extrato: cada arquivo tirado num dia
    # diferente. Faltava esta checagem, e a falta custou - a bateria do modelo
    # acusou 67% de divergencia no CMV entre dois caminhos que deveriam ser
    # identicos, e a causa era so que a venda ia quatro dias mais longe que o
    # estoque.
    r = wh.query(f"""select
          (select min(cast(DATA as date)) from {ref(fonte)}) venda_de,
          (select max(cast(DATA as date)) from {ref(fonte)}) venda_ate,
          (select min(cast(dtmovimento as date))
             from {ref('raw_estoque_diario_erp')}) estoque_de,
          (select max(cast(dtmovimento as date))
             from {ref('raw_estoque_diario_erp')}) estoque_ate""").iloc[0]
    sobra = (pd.Timestamp(r.venda_ate) - pd.Timestamp(r.estoque_ate)).days
    resto = wh.query(f"""select count(*) linhas,
          round(sum(pecas_vendidas)) pecas, round(sum(receita_liquida)) receita
        from {ref('stg_vendas')}
        where data > (select max(data) from {ref('mart_estoque_diario')})""").iloc[0]
    out.append(_v(G, "janela_das_fontes",
                  "as duas fontes cobrem o mesmo período",
                  "ok" if abs(sobra) <= 1 else "aviso",
                  f"{_num(abs(sobra))}", "dias de diferença no fim",
                  "a primeira e a última data de cada extrato",
                  f"venda de {r.venda_de} a {r.venda_ate} · estoque de "
                  f"{r.estoque_de} a {r.estoque_ate}"
                  + (f" · {_num(resto.linhas)} linhas de venda "
                     f"({_num(resto.pecas)} peças, R$ {_num(resto.receita)}) "
                     f"caem depois do último retrato de estoque"
                     if int(resto.linhas) else ""),
                  "A grade diária do modelo termina no último dia de ESTOQUE. "
                  "Venda posterior a isso põe peça no numerador da taxa de "
                  "demanda sem pôr dia no denominador, então o agregado "
                  "financeiro corta ali — a venda dos dias sobrantes continua na "
                  "base, só não entra na conta. Extrair os dois arquivos no mesmo "
                  "dia elimina a ressalva."
                  if abs(sobra) > 1 else
                  "Os dois extratos terminam no mesmo dia, então não há venda "
                  "fora da grade."))

    # ---------------------- as duas fontes de venda contam a mesma coisa?
    if tem_todas and _existe(wh, "raw_vendas_ecommerce"):
        r = wh.query(f"""
            select
              (select count(*) from {ref('raw_vendas_todas')}) linhas_todas,
              (select count(*) from {ref('raw_vendas_ecommerce')}) linhas_ecom,
              (select round(sum(cast(QTDPRODUTO as double)))
                 from {ref('raw_vendas_todas')}) pecas_todas,
              (select round(sum(cast(QTDPRODUTO as double)))
                 from {ref('raw_vendas_ecommerce')}) pecas_ecom,
              (select round(sum(cast(QTDPRODUTO as double)))
                 from {ref('raw_vendas_todas')} where IDEMPRESA = 33) pecas_emp33
            """).iloc[0]
        raz = float(r.pecas_todas) / float(r.pecas_ecom) if r.pecas_ecom else 0.0
        out.append(_v(G, "fontes_de_venda",
                      "o arquivo de e-commerce dentro do arquivo completo",
                      "ok", f"{_num(raz, 1)}×", "mais peças no arquivo completo",
                      "os dois arquivos de venda, linha e peça",
                      f"completo {_num(r.linhas_todas)} linhas / "
                      f"{_num(r.pecas_todas)} peças · e-commerce "
                      f"{_num(r.linhas_ecom)} linhas / {_num(r.pecas_ecom)} peças · "
                      f"a empresa 33 dentro do completo vende {_num(r.pecas_emp33)}",
                      "O arquivo de e-commerce é subconjunto do completo (64.331 "
                      "dos 64.433 pedidos dele estão lá). Só o completo alimenta a "
                      "demanda; carregar os dois contaria a mesma venda duas vezes."))
    return out


# ======================================================================
# 4. COMPRA: pedido, recebimento e prazo se sustentam?
# ======================================================================
def _compra(wh: Warehouse) -> list[dict]:
    G = "compra"
    out = []
    if not _existe(wh, "raw_compras"):
        return out

    # --------------------- a tabela de compras contra o que o estoque acusa
    # A tabela de compras salta de escopo em marco de 2026: 18 mil para 322
    # mil pecas por mes, com venda e estoque estaveis. Isto mede o salto.
    r = wh.query(f"""
        with m as (
          select date_trunc('month', cast(dtmovimento as date)) mes,
                 sum(cast(qtdatendida as double)) pecas
          from {ref('raw_compras')} r
          join {ref('stg_catalogo')} c on c.sku = cast(r.idsubproduto as varchar)
          group by 1),
        e as (
          -- a subida de saldo vira coluna antes de ser somada: o DuckDB nao
          -- aceita window function dentro de agregado
          select date_trunc('month', data) mes, sum(greatest(d, 0)) entrou
          from (select data, saldo_final - lag(saldo_final)
                        over (partition by sku order by data) d
                from {ref('mart_estoque_diario')})
          group by 1)
        select m.mes, round(m.pecas) tabela_compras, round(e.entrou) estoque_acusa,
               round(m.pecas / nullif(e.entrou, 0), 1) razao
        from m join e on e.mes = m.mes order by m.mes desc limit 15""")
    pior = float(r.razao.max()) if len(r) else 0.0
    out.append(_v(G, "compras_escopo",
                  "a tabela de compras contra a entrada que o estoque acusa",
                  "erro" if pior > 5 else ("aviso" if pior > 2 else "ok"),
                  f"{_num(pior, 1)}×", "no pior mês",
                  "as peças atendidas na tabela de compras contra a subida de "
                  "saldo no estoque diário, mês a mês",
                  f"no pior mês a tabela de compras registra {_num(pior, 1)}× mais "
                  f"peças do que o estoque acusa de entrada",
                  "Se as compras tivessem crescido tanto com venda e estoque "
                  "estáveis, o estoque teria explodido; ele não explodiu. A tabela "
                  "mudou de escopo. É por isso que o backtest reconstrói o "
                  "recebimento do próprio estoque diário e do ciclo de pagamento, "
                  "e não desta tabela."
                  if pior > 2 else "",
                  _reg(r), ["mes", "tabela_compras", "estoque_acusa", "razao"]))

    # ------------------------------------------- prazo combinado x realizado
    if _existe(wh, "raw_ciclo_pagamento"):
        r = wh.query(f"""select
              round(median(cast(dias_entrega_combinado as double)), 1) combinado,
              round(median(cast(dias_entrega_realizado as double)), 1) realizado,
              round(stddev(cast(dias_entrega_realizado as double)), 1) desvio,
              count(*) pedidos,
              sum(case when dt_entrada_estoque is null then 1 else 0 end) sem_entrada
            from {ref('raw_ciclo_pagamento')}
            where cast(dias_entrega_realizado as double) between 0 and 365""").iloc[0]
        out.append(_v(G, "prazo_entrega",
                      "prazo combinado com o fornecedor contra o realizado",
                      "aviso",
                      f"{_num(r.combinado, 1)} → {_num(r.realizado, 1)}", "dias",
                      "o prazo declarado no pedido contra a data real de entrada "
                      "em estoque",
                      f"mediana combinada {_num(r.combinado, 1)} dias, realizada "
                      f"{_num(r.realizado, 1)} dias, desvio {_num(r.desvio, 1)} dias "
                      f"em {_num(r.pedidos)} pedidos",
                      "O modelo dimensiona pelo REALIZADO: usar o combinado "
                      "imobilizaria capital para uma espera que raramente acontece. "
                      "O desvio entra no estoque de segurança — prazo que varia "
                      "tanto é risco, não detalhe."))
    return out


# ======================================================================
# 5. CADASTRO: custo, preco e prazo existem e sao plausiveis?
# ======================================================================
def _cadastro(wh: Warehouse) -> list[dict]:
    G = "cadastro"
    out = []

    r = wh.query(f"""select count(*) n,
          sum(case when custo_unitario <= 0 then 1 else 0 end) sem_custo,
          sum(case when preco_tabela <= 0 then 1 else 0 end) sem_preco,
          sum(case when coalesce(lead_time_pedidos, 0) < 3 then 1 else 0 end) prazo_fraco,
          sum(case when item like 'SKU %' then 1 else 0 end) sem_descricao
        from {ref('stg_catalogo')}""").iloc[0]
    out.append(_v(G, "cadastro_faltando",
                  "campos que o cadastro não entrega",
                  "aviso" if int(r.sem_custo) else "ok",
                  _num(r.sem_custo), f"itens sem custo, de {_num(r.n)}",
                  "as colunas do cadastro montado contra zero e contra nulo",
                  f"{_num(r.sem_custo)} sem custo · {_num(r.sem_preco)} sem preço · "
                  f"{_num(r.sem_descricao)} sem descrição no cadastro de produtos · "
                  f"{_num(r.prazo_fraco)} com menos de 3 pedidos sustentando o prazo",
                  "Item sem custo não tem denominador na nota e nunca é comprado. "
                  "Prazo apoiado em um ou dois pedidos herda a mediana do catálogo "
                  "— a coluna Prazo na lista de itens marca esses com \"?\"."))

    r = wh.query(f"""select count(*) n,
          sum(case when custo_unitario < custo_mediano * 0.5
                     or custo_unitario > custo_mediano * 2 then 1 else 0 end) fora
        from {ref('stg_catalogo')} where custo_mediano > 0""").iloc[0]
    ex = wh.query(f"""select sku, item, round(custo_unitario, 2) custo_hoje,
          round(custo_ultimo_lancado, 2) ultimo_lancado,
          round(custo_mediano, 2) mediana,
          round(custo_unitario / nullif(custo_mediano, 0), 2) razao
        from {ref('stg_catalogo')} where custo_mediano > 0
          and (custo_unitario < custo_mediano * 0.5
               or custo_unitario > custo_mediano * 2)
        order by abs(custo_unitario / nullif(custo_mediano, 1) - 1) desc limit 40""")
    out.append(_v(G, "custo_fora_da_faixa",
                  "custo de hoje longe da mediana histórica do item",
                  "aviso" if int(r.fora) else "ok",
                  _num(r.fora), f"itens de {_num(r.n)}",
                  "o custo que o modelo usa contra a mediana histórica do próprio item",
                  f"{_num(r.fora)} itens com o custo de hoje fora da faixa de 0,5× "
                  f"a 2× a mediana deles",
                  "Custo de material de construção se move, e a mediana cobre três "
                  "anos: estar fora da faixa não é erro por si. Mas o custo é o "
                  "denominador da nota de compra, e um item fora dessa faixa merece "
                  "conferência antes de subir na fila. A gaveta do item mostra "
                  "lançamento por lançamento.",
                  _reg(ex), ["sku", "item", "custo_hoje", "ultimo_lancado",
                             "mediana", "razao"]))

    # custo do cadastro contra o custo do que foi de fato vendido: duas fontes
    # que nao se falam - o estoque diario e a nota de venda
    r = wh.query(f"""
        with v as (select sku, sum(cmv) / nullif(sum(pecas_vendidas), 0) cv
                   from {ref('stg_vendas')} group by 1 having sum(pecas_vendidas) > 0)
        select count(*) n,
          sum(case when abs(c.custo_unitario / nullif(v.cv, 0) - 1) > 0.25
                   then 1 else 0 end) fora
        from {ref('stg_catalogo')} c join v on v.sku = c.sku where v.cv > 0""").iloc[0]
    out.append(_v(G, "custo_x_vendido",
                  "custo do cadastro contra o custo do que foi vendido",
                  "aviso" if int(r.fora) > int(r.n) * 0.1 else "ok",
                  _num(r.fora), f"itens de {_num(r.n)} divergem mais de 25%",
                  "o custo do cadastro contra o custo médio da mercadoria vendida — "
                  "duas fontes que não se falam, o retrato de estoque e a nota",
                  f"{_num(r.fora)} de {_num(r.n)} itens com divergência acima de 25%",
                  "A média do vendido cobre três anos de custos antigos; o cadastro "
                  "traz o de hoje. Divergir é normal em item cujo preço de compra "
                  "mudou, e a conferência do item separa esse caso do lançamento "
                  "inválido."))
    return out


# ======================================================================
# 6. PARAMETROS: o que esta configurado ainda cabe nos dados?
# ======================================================================
def _parametros(wh: Warehouse, p: Parametros) -> list[dict]:
    G = "parametros"
    out = []

    est = wh.query(f"""select
          round(sum(d.saldo_final * c.custo_unitario)) valor,
          round(sum(d.saldo_final)) pecas
        from {ref('mart_estoque_diario')} d
        join {ref('stg_catalogo')} c on c.sku = d.sku
        where d.data = (select max(data) from {ref('mart_estoque_diario')})""").iloc[0]
    teto = float(getattr(p, "teto_capital", 0) or 0)
    real = float(est.valor or 0)
    raz = real / teto if teto else 0.0
    lam = None
    if _existe(wh, "res_execucao"):
        try:
            lam = float(wh.query(
                f"select premio_escassez from {ref('res_execucao')}").iloc[0, 0])
        except Exception:
            lam = None
    saturou = lam is not None and lam >= 49.9
    out.append(_v(G, "teto_capital",
                  "o teto de capital configurado contra o estoque que existe",
                  "erro" if saturou else ("aviso" if raz > 1.5 else "ok"),
                  f"{_num(raz, 1)}×", "o estoque real sobre o teto",
                  "o parâmetro `teto_capital` contra o valor do estoque de hoje",
                  f"teto R$ {_num(teto)} · estoque real R$ {_num(real)} "
                  f"({_num(est.pecas)} peças)"
                  + (f" · preço-sombra do capital em {_num(lam, 4)}"
                     if lam is not None else ""),
                  "O preço-sombra saturou no limite da busca: o modelo quer muito "
                  "mais estoque do que o teto permite, e o plano sai comprimido "
                  "sem que nenhum número na tela diga isso. O teto foi calibrado "
                  "quando a demanda vinha de um canal só — com todas as lojas a "
                  "demanda é sete vezes maior, e o teto precisa ser revisto para o "
                  "plano voltar a significar algo."
                  if saturou else
                  ("O estoque real está bem acima do teto configurado, então o teto "
                   "está cortando o plano em vez de guiá-lo." if raz > 1.5 else "")))

    dias = wh.query(f"""select count(distinct data) n, min(data) de, max(data) ate
        from {ref('mart_estoque_diario')}""").iloc[0]
    jan = int(getattr(p, "janela_estimacao_dias", 0) or 0)
    out.append(_v(G, "janela_estimacao",
                  "a janela de estimação contra o histórico disponível",
                  "ok" if jan and jan <= int(dias.n) else "aviso",
                  _num(jan), f"dias de {_num(dias.n)} disponíveis",
                  "o parâmetro `janela_estimacao_dias` contra o tamanho da base",
                  f"a base cobre {_num(dias.n)} dias ({dias.de} a {dias.ate}) e a "
                  f"estimativa de demanda usa os últimos {_num(jan)}",
                  "Janela curta acompanha crescimento e perde regularidade; janela "
                  "longa dilui crescimento. Medido em três datas de backtest, 365 "
                  "dias erra menos que 90 — mas isso foi medido com a fonte de "
                  "venda antiga, e merece nova medição agora."))
    return out


# ======================================================================
# 0. UNIVERSO DE SKUS: quanto de cada fonte esta dentro do que rastreamos
# ======================================================================
def _universo(wh: Warehouse) -> list[dict]:
    """O catalogo tem ~162 mil SKUs; o motor planeja 1.190. Este grupo mede o
    tamanho exato dessa diferenca, cruzando as quatro fontes que existem no
    extrato - cadastro (produtos), vendas, estoque diario e compras - em vez
    de aceitar o recorte de 1.190 como dado.

    A pergunta que importa nao e "quantos SKUs existem" (resposta obvia: a
    uniao das quatro). E: quantos aparecem em TODAS, e o que dizer de quem
    aparece em algumas e nao em outras - sobretudo de quem vende, compra ou
    tem estoque sem nunca ter sido cadastrado, porque esse caso nao devia
    existir e existe.
    """
    G = "universo"
    out = []
    if not _existe(wh, "raw_produtos"):
        return out

    UNIVERSO = f"""
        with u as (
            select cast(IDSUBPRODUTO as varchar) sku, 1 in_produtos,
                   0 in_vendas, 0 in_estoque, 0 in_compras
            from {ref('raw_produtos')} group by 1
            union all
            select distinct cast(IDSUBPRODUTO as varchar), 0, 1, 0, 0
            from {ref('raw_vendas_todas')} where IDSUBPRODUTO is not null
            union all
            select distinct cast(idsubproduto as varchar), 0, 0, 1, 0
            from {ref('raw_estoque_diario_erp')}
            union all
            select distinct cast(idsubproduto as varchar), 0, 0, 0, 1
            from {ref('raw_compras')}
        )
        select sku, max(in_produtos) in_produtos, max(in_vendas) in_vendas,
               max(in_estoque) in_estoque, max(in_compras) in_compras
        from u group by 1"""

    r = wh.query(f"""
        with g as ({UNIVERSO})
        select count(*) universo,
               sum(case when in_produtos=1 and in_vendas=1 and in_estoque=1
                        and in_compras=1 then 1 else 0 end) completos,
               sum(case when in_produtos=0 then 1 else 0 end) fora_cadastro,
               sum(case when in_vendas=0 then 1 else 0 end) sem_venda,
               sum(case when in_estoque=0 then 1 else 0 end) sem_estoque,
               sum(case when in_compras=0 then 1 else 0 end) sem_compra,
               sum(in_estoque) planejados,
               sum(case when in_produtos=1 and in_vendas=0 and in_estoque=0
                        and in_compras=0 then 1 else 0 end) so_cadastro
        from g""").iloc[0]
    incompletos = int(r.universo - r.completos)
    out.append(_v(G, "universo_skus",
                  "quantos SKUs existem, por fonte, e quantos tem dado completo",
                  "aviso" if incompletos else "ok",
                  _num(r.completos), f"completos de {_num(r.universo)} no total",
                  "o cadastro de produtos contra vendas, estoque diário e compras, "
                  "todos cruzados pelo SKU",
                  f"{_num(r.universo)} SKUs na união das quatro fontes · "
                  f"{_num(r.completos)} aparecem nas quatro · {_num(incompletos)} "
                  f"faltam em pelo menos uma · {_num(r.so_cadastro)} estão só "
                  f"cadastrados, sem nenhuma venda, estoque ou compra em 3 anos",
                  f"O motor planeja hoje só os {_num(r.planejados)} SKUs com "
                  "retrato diário de estoque — 1.156 deles com as quatro fontes "
                  "completas, e o resto com alguma lacuna tratada nos achados "
                  "abaixo. Todo o restante do universo não entra em nenhum "
                  "cálculo, não por decisão, mas porque falta exatamente a fonte "
                  "de que o motor depende."))

    r2 = wh.query(f"""
        with g as ({UNIVERSO})
        select sku, in_vendas, in_estoque, in_compras
        from g where in_produtos = 0""")
    ex = None
    if len(r2):
        skus = "','".join(r2.sku.tolist())
        ex = wh.query(f"""
            with v as (select cast(IDSUBPRODUTO as varchar) sku,
                             any_value(DESCRICAOPRODUTO) descricao,
                             sum(cast(QTDPRODUTO as double)) pecas,
                             sum(cast(VALORLIQUIDOVENDA as double)) receita,
                             count(distinct IDEMPRESA) lojas
                       from {ref('raw_vendas_todas')}
                       where cast(IDSUBPRODUTO as varchar) in ('{skus}')
                       group by 1),
                 c as (select cast(idsubproduto as varchar) sku, count(*) pedidos
                       from {ref('raw_compras')}
                       where cast(idsubproduto as varchar) in ('{skus}')
                       group by 1)
            select v.sku, v.descricao, v.lojas, round(v.pecas) pecas,
                   round(v.receita) receita, coalesce(c.pedidos, 0) pedidos_compra
            from v left join c on c.sku = v.sku
            order by v.receita desc""")
    fora = int(r.fora_cadastro)
    out.append(_v(G, "fora_do_cadastro",
                  "SKUs com movimento em alguma tabela mas ausentes do cadastro mestre",
                  "aviso" if fora else "ok",
                  _num(fora), "SKUs",
                  "vendas, estoque diário e compras contra o cadastro de produtos "
                  "(a fonte que deveria conter todo SKU que existe)",
                  f"{_num(fora)} SKUs vendem, compram ou têm estoque registrado sem "
                  "existir no cadastro mestre do ERP — descrição, custo e prazo "
                  "para eles existem só nas outras tabelas, quando existem",
                  "Item fora do cadastro não tem nome, família nem custo — não "
                  "pode ser precificado nem avaliado pelo motor. É o caso mais "
                  "grave de lacuna de dado: o produto é real (tem venda e muitas "
                  "vezes pedido de compra) e continua invisível para qualquer "
                  "cálculo por faltar um único registro.",
                  _reg(ex, limite=200) if ex is not None else [],
                  ["sku", "descricao", "lojas", "pecas", "receita", "pedidos_compra"]
                  if ex is not None else []))
    return out


# ======================================================================
# Orquestracao
# ======================================================================
GRUPOS = [
    ("universo", "O universo de SKUs",
     "O cadastro tem ~162 mil produtos; o motor planeja 1.190. Quantos aparecem em "
     "cada fonte, e quantos têm dado incompleto."),
    ("estoque", "O retrato de estoque",
     "As três colunas de quantidade fecham entre si? A grade diária está inteira?"),
    ("fluxo", "Estoque × venda",
     "A prateleira baixou tanto; a venda registrou tanto. A diferença tem explicação? "
     "É esta a verificação que denunciou a fonte de venda errada."),
    ("venda", "A linha de venda",
     "Duplicidade, quantidade negativa, preço fora da faixa, item que vende e não "
     "está no plano."),
    ("compra", "Compra e recebimento",
     "O pedido, o que entrou de fato e o prazo que o fornecedor cumpriu."),
    ("cadastro", "O cadastro montado",
     "No extrato real não existe cadastro pronto: custo vem do estoque, prazo do "
     "ciclo de pagamento, lote do histórico de compras. Cada campo é uma junção."),
    ("parametros", "Os parâmetros contra os dados",
     "Número configurado à mão envelhece quando os dados mudam."),
]


def verificar(wh: Warehouse, p: Parametros | None = None) -> dict:
    """Roda todas as verificacoes e devolve o painel inteiro."""
    p = p or Parametros.carregar()
    checagens: list[dict] = []
    for fn in (_universo, _estoque, _fluxo, _venda, _cadastro, _compra):
        try:
            checagens += fn(wh)
        except Exception as e:                                   # noqa: BLE001
            checagens.append(_v(fn.__name__.strip("_"), "falhou",
                                f"a verificação de {fn.__name__.strip('_')} não rodou",
                                "erro", "–", "", "—", str(e)[:300],
                                "Verificação quebrada é pior que verificação ausente: "
                                "ela some do painel e o problema que ela cobria passa."))
    try:
        checagens += _parametros(wh, p)
    except Exception as e:                                       # noqa: BLE001
        checagens.append(_v("parametros", "falhou", "a verificação de parâmetros "
                            "não rodou", "erro", "–", "", "—", str(e)[:300], ""))

    conta = {"ok": 0, "aviso": 0, "erro": 0}
    for c in checagens:
        conta[c["nivel"]] = conta.get(c["nivel"], 0) + 1

    return {
        "resumo": {
            "total": len(checagens),
            "ok": conta["ok"], "aviso": conta["aviso"], "erro": conta["erro"],
            "veredito": ("erro" if conta["erro"] else
                         ("aviso" if conta["aviso"] else "ok")),
        },
        "grupos": [{"chave": k, "titulo": t, "sobre": s} for k, t, s in GRUPOS],
        "checagens": checagens,
    }
