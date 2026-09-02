# -*- coding: utf-8 -*-
"""
Conferencia automatica da fila de compra.

Refaz, a partir dos insumos, cada coluna calculada de uma amostra de linhas da
tabela `res_fila_marginal` e compara com o que foi gravado. Depois reconcilia a
soma da fila com o plano por item.

Serve para responder "esses numeros estao certos?" sem abrir o codigo do motor:
o script recalcula por fora, com scipy puro, e so passa se bater.

Uso:
    python scripts/conferir.py
    python scripts/conferir.py --linhas 2000
    python scripts/conferir.py --sku AC-8010-01
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from scipy import stats

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from backend.warehouse import abrir, ref  # noqa: E402

TOLERANCIA = 1e-9

CAMPOS = ["horizonte", "mu_periodo", "sd_periodo", "distribuicao",
          "cdf_ate_k_menos_1", "p_vender", "margem_unit", "custo_obsolescencia",
          "perda_unit", "ganho_esperado", "custo_esperado", "custo",
          "valor_esperado", "valor_por_real", "nota"]


def refazer(r) -> dict:
    """A conta feita do zero, so com os insumos gravados na propria linha."""
    H = r.lead_time_dias + r.periodo_revisao_dias
    mu = r.demanda_dia_corrigida * H
    sd = r.desvio_dia * np.sqrt(H)
    var = sd ** 2

    if var <= mu * 1.05:
        dist, nome = stats.poisson(mu), "Poisson"
    else:
        rr = mu * mu / (var - mu)
        dist, nome = stats.nbinom(rr, rr / (rr + mu)), "Binomial Negativa"

    cdf = float(dist.cdf(r.unidade_de - 1))
    P = 1 - cdf
    M = r.lucro_por_peca * r.fator_perda_ruptura
    obsolescencia = r.custo_unitario * r.perda_encalhe_pct
    L = r.custo_manter_no_periodo + obsolescencia
    q = r.quantidade

    ganho = P * M * q
    perda = (1 - P) * L * q
    valor = ganho - perda
    custo = r.custo_unitario * q

    return dict(horizonte=H, mu_periodo=mu, sd_periodo=sd, distribuicao=nome,
                cdf_ate_k_menos_1=cdf, p_vender=P, margem_unit=M,
                custo_obsolescencia=obsolescencia, perda_unit=L,
                ganho_esperado=ganho, custo_esperado=perda, custo=custo,
                valor_esperado=valor, valor_por_real=valor / custo,
                nota=valor / (custo * H))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--linhas", type=int, default=500, help="tamanho da amostra")
    ap.add_argument("--sku", default="", help="conferir so um produto")
    ap.add_argument("--tudo", action="store_true", help="conferir a fila inteira")
    args = ap.parse_args()

    wh = abrir()
    tabela = ref("res_fila_marginal")
    if args.sku:
        df = wh.query_params(f"select * from {tabela} where sku = ? order by posicao_fila",
                             [args.sku])
    elif args.tudo:
        df = wh.query(f"select * from {tabela}")
    else:
        df = wh.query(f"select * from {tabela} using sample {int(args.linhas)} rows "
                      f"(reservoir, 42)")

    print(f"Conferindo {len(df)} linhas da fila...\n")

    pior = {c: 0.0 for c in CAMPOS}
    falhas = []
    for r in df.itertuples(index=False):
        calc = refazer(r)
        for c in CAMPOS:
            esperado, gravado = calc[c], getattr(r, c)
            if isinstance(esperado, str):
                if esperado != gravado:
                    falhas.append((r.posicao_fila, c, esperado, gravado))
                continue
            desvio = abs(esperado - gravado) / max(abs(gravado), 1e-9)
            pior[c] = max(pior[c], desvio)
            if desvio > TOLERANCIA:
                falhas.append((r.posicao_fila, c, esperado, gravado))

    largura = max(len(c) for c in CAMPOS)
    print("  maior erro relativo por coluna recalculada")
    for c in CAMPOS:
        marca = "ok " if pior[c] <= TOLERANCIA else "!! "
        print(f"    {marca}{c.ljust(largura)}  {pior[c]:.2e}")

    # a fila somada tem de dar exatamente o plano por item
    somas = wh.query(
        f"select round(sum(custo), 2) as inv, round(sum(valor_esperado), 2) as mar, "
        f"sum(quantidade) as pec from {tabela} where comprar").iloc[0]
    plano = wh.query(
        f"select round(sum(valor_da_compra), 2) as inv, "
        f"round(sum(margem_esperada), 2) as mar, "
        f"sum(quantidade_a_comprar) as pec from {ref('res_plano_compra')} "
        f"where quantidade_a_comprar > 0").iloc[0]

    print("\n  soma da fila comprada  x  plano por item")
    for rotulo, a, b in [("investimento", somas.inv, plano.inv),
                         ("margem esperada", somas.mar, plano.mar),
                         ("pecas", somas.pec, plano.pec)]:
        bate = "ok " if abs(float(a) - float(b)) < 0.01 else "!! "
        print(f"    {bate}{rotulo.ljust(16)}  fila {float(a):>14,.2f}   "
              f"plano {float(b):>14,.2f}")

    if falhas:
        print(f"\n{len(falhas)} DIVERGENCIA(S) — primeiras 10:")
        for pos, c, esperado, gravado in falhas[:10]:
            print(f"  linha {pos}  {c}: recalculado {esperado} != gravado {gravado}")
        raise SystemExit(1)

    print("\nTudo confere.")


if __name__ == "__main__":
    main()
