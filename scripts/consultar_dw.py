# -*- coding: utf-8 -*-
"""
Consulta ad-hoc aos data warehouses Postgres (dwelevato, dwanalitico).

Nao faz parte do pipeline do app - e so uma forma rapida de rodar SQL nos
dois DWs a partir da linha de comando, usando as credenciais do .env.

Uso:
    python scripts/consultar_dw.py dwelevato "select count(*) from information_schema.tables"
    python scripts/consultar_dw.py dwanalitico --arquivo minha_query.sql
    python scripts/consultar_dw.py dwelevato --tabelas
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

DWS = ("dwelevato", "dwanalitico")


def _carregar_env() -> None:
    """Le o .env sem depender de pandas/dotenv (script e standalone)."""
    arq = RAIZ / ".env"
    if not arq.exists():
        return
    for linha in arq.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip())


def conectar(nome: str):
    import psycopg

    _carregar_env()
    prefixo = nome.upper()
    host = os.environ.get(f"{prefixo}_HOST", "")
    porta = int(os.environ.get(f"{prefixo}_PORT", "5432"))
    dbname = os.environ.get(f"{prefixo}_DBNAME", "")
    usuario = os.environ.get("DW_USER", "")
    senha = os.environ.get("DW_PASSWORD", "")
    if not host or not usuario:
        raise SystemExit(
            f"Faltam variaveis de ambiente para {nome} "
            f"({prefixo}_HOST / DW_USER / DW_PASSWORD). Confira o .env."
        )
    return psycopg.connect(
        host=host, port=porta, dbname=dbname, user=usuario, password=senha,
        connect_timeout=10,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dw", choices=DWS, help="qual data warehouse consultar")
    ap.add_argument("sql", nargs="?", help="SQL a executar")
    ap.add_argument("--arquivo", help="le o SQL de um arquivo em vez de passar inline")
    ap.add_argument("--tabelas", action="store_true", help="lista as tabelas do schema public")
    args = ap.parse_args()

    if args.tabelas:
        sql = (
            "select table_schema, table_name from information_schema.tables "
            "where table_schema not in ('pg_catalog', 'information_schema') "
            "order by table_schema, table_name"
        )
    elif args.arquivo:
        sql = Path(args.arquivo).read_text(encoding="utf-8")
    elif args.sql:
        sql = args.sql
    else:
        ap.error("informe um SQL, --arquivo ou --tabelas")

    with conectar(args.dw) as con:
        with con.cursor() as cur:
            cur.execute(sql)
            if cur.description is None:
                print(f"OK ({cur.rowcount} linha(s) afetada(s))")
                return
            colunas = [c.name for c in cur.description]
            linhas = cur.fetchall()
            print("\t".join(colunas))
            for linha in linhas:
                print("\t".join("" if v is None else str(v) for v in linha))
            print(f"\n({len(linhas)} linha(s))")


if __name__ == "__main__":
    main()
