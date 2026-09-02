# -*- coding: utf-8 -*-
"""
Pipeline ponta a ponta:

  1. Carrega os CSVs de origem (o "ERP") nas tabelas raw_* do warehouse.
  2. Roda o dbt (staging -> intermediate -> marts).
  3. Roda o motor de calculo em Python (backend/modelo.py).
  4. Grava as tabelas de resultado de volta no warehouse.

Uso:
    python scripts/rodar_pipeline.py
    python scripts/rodar_pipeline.py --pular-dbt   # so recalcula o modelo Python
    python scripts/rodar_pipeline.py --pular-carga --pular-dbt
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from backend.config import Parametros  # noqa: E402
from backend.warehouse import DuckDBWarehouse, carregar_env  # noqa: E402
from backend import modelo  # noqa: E402


def carregar_fontes(wh) -> None:
    fonte = RAIZ / "data" / "fonte"
    print("  - carregando CSVs de", fonte)
    n1 = wh.carregar_csv(fonte / "catalogo.csv", "raw_catalogo")
    n2 = wh.carregar_csv(fonte / "vendas.csv", "raw_vendas")
    n3 = wh.carregar_csv(fonte / "estoque_diario.csv", "raw_estoque_diario")
    print(f"    raw_catalogo={n1}  raw_vendas={n2}  raw_estoque_diario={n3}")


def rodar_dbt() -> None:
    dbt_dir = RAIZ / "dbt_elevato"
    env = os.environ.copy()
    env["DBT_PROFILES_DIR"] = str(dbt_dir)
    # o dbt roda com cwd=dbt_elevato/, entao o caminho relativo do warehouse
    # precisa apontar um nivel acima - independente do que o .env define para
    # o resto da aplicacao (que roda com cwd=app/).
    env["DUCKDB_PATH"] = "../data/elevato.duckdb"
    print("  - dbt build em", dbt_dir)
    r = subprocess.run(["dbt", "build"], cwd=dbt_dir, env=env,
                       capture_output=True, text=True)
    print(r.stdout[-4000:])
    if r.returncode != 0:
        print(r.stderr[-4000:])
        raise SystemExit("dbt build falhou - veja o log acima")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pular-carga", action="store_true", help="nao recarrega os CSVs")
    ap.add_argument("--pular-dbt", action="store_true", help="nao roda o dbt build")
    args = ap.parse_args()

    carregar_env()
    caminho = os.environ.get("DUCKDB_PATH", "./data/elevato.duckdb")
    wh = DuckDBWarehouse(caminho)

    t0 = time.time()
    if not args.pular_carga:
        print("[1/4] carregando fontes...")
        carregar_fontes(wh)
    else:
        print("[1/4] carga pulada")

    if not args.pular_dbt:
        print("[2/4] rodando dbt...")
        rodar_dbt()
    else:
        print("[2/4] dbt pulado")

    print("[3/4] rodando o motor de calculo (Python)...")
    p = Parametros.carregar()
    resultados = modelo.executar(wh, p)
    for nome, df in resultados.items():
        print(f"    {nome}: {df.shape[0]} linhas x {df.shape[1]} colunas")

    print("[4/4] gravando resultados no warehouse...")
    modelo.gravar_resultados(wh, resultados)

    print(f"\nPipeline concluido em {time.time() - t0:.1f}s")
    exe = resultados["res_execucao"].iloc[0]
    print(f"  premio de escassez do capital: {exe.premio_escassez:.4f}")
    print(f"  itens no regime discreto (unidade marginal): {exe.itens_regime_discreto}")
    print(f"  capital total imobilizado: R$ {exe.capital_total:,.2f}")


if __name__ == "__main__":
    main()
