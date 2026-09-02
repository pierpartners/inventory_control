# -*- coding: utf-8 -*-
"""Camada unica de acesso ao dado.

O resto da aplicacao nunca sabe se esta falando com DuckDB ou BigQuery.
Trocar de motor e mudar WAREHOUSE no .env - o SQL dos modelos dbt e o mesmo.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]


def _env(chave: str, padrao: str = "") -> str:
    return os.environ.get(chave, padrao)


def carregar_env() -> None:
    """Le o .env sem depender de biblioteca externa."""
    arq = RAIZ / ".env"
    if not arq.exists():
        return
    for linha in arq.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip())


class Warehouse:
    """Interface comum. Use `abrir()` em vez de instanciar direto."""

    motor = "?"

    def query(self, sql: str) -> pd.DataFrame:
        raise NotImplementedError

    def query_params(self, sql: str, params: list) -> pd.DataFrame:
        """Consulta com placeholders `?`. Use sempre que o filtro vier da URL:
        interpolar valor de usuario direto no SQL abre injecao."""
        raise NotImplementedError

    def gravar(self, df: pd.DataFrame, tabela: str) -> None:
        raise NotImplementedError

    def existe(self, tabela: str) -> bool:
        raise NotImplementedError

    def tabelas(self) -> list[str]:
        raise NotImplementedError


class DuckDBWarehouse(Warehouse):
    motor = "duckdb"

    def __init__(self, caminho: str):
        self.caminho = str((RAIZ / caminho).resolve()) if not os.path.isabs(caminho) else caminho
        Path(self.caminho).parent.mkdir(parents=True, exist_ok=True)

    def _con(self):
        import duckdb
        return duckdb.connect(self.caminho)

    def query(self, sql: str) -> pd.DataFrame:
        with self._con() as con:
            return con.execute(sql).fetch_df()

    def query_params(self, sql: str, params: list) -> pd.DataFrame:
        with self._con() as con:
            return con.execute(sql, params).fetch_df()

    def gravar(self, df: pd.DataFrame, tabela: str) -> None:
        with self._con() as con:
            con.register("_tmp_df", df)
            con.execute(f'create or replace table "{tabela}" as select * from _tmp_df')
            con.unregister("_tmp_df")

    def carregar_csv(self, caminho, tabela: str) -> int:
        caminho_p = Path(caminho)
        with self._con() as con:
            con.execute(
                f'create or replace table "{tabela}" as '
                f"select * from read_csv_auto('{caminho_p.as_posix()}', header=true)"
            )
            return con.execute(f'select count(*) from "{tabela}"').fetchone()[0]

    def existe(self, tabela: str) -> bool:
        with self._con() as con:
            r = con.execute(
                "select count(*) from information_schema.tables where table_name = ?", [tabela]
            ).fetchone()
            return r[0] > 0

    def tabelas(self) -> list[str]:
        with self._con() as con:
            return [r[0] for r in con.execute(
                "select table_name from information_schema.tables "
                "where table_schema='main' order by table_name").fetchall()]


class BigQueryWarehouse(Warehouse):
    motor = "bigquery"

    def __init__(self, projeto: str, dataset: str, keyfile: str = "", location: str = ""):
        from google.cloud import bigquery
        from google.oauth2 import service_account

        self.projeto, self.dataset, self.location = projeto, dataset, location or None
        cred = None
        if keyfile:
            cred = service_account.Credentials.from_service_account_file(keyfile)
        self.client = bigquery.Client(project=projeto, credentials=cred, location=self.location)

    def _fqn(self, tabela: str) -> str:
        return f"`{self.projeto}.{self.dataset}.{tabela}`"

    def query(self, sql: str) -> pd.DataFrame:
        return self.client.query(sql).to_dataframe()

    def query_params(self, sql: str, params: list) -> pd.DataFrame:
        from google.cloud import bigquery
        tipos = {int: "INT64", float: "FLOAT64", bool: "BOOL"}
        cfg = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter(None, tipos.get(type(v), "STRING"), v)
            for v in params])
        return self.client.query(sql, job_config=cfg).to_dataframe()

    def gravar(self, df: pd.DataFrame, tabela: str) -> None:
        from google.cloud import bigquery
        cfg = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
        destino = f"{self.projeto}.{self.dataset}.{tabela}"
        self.client.load_table_from_dataframe(df, destino, job_config=cfg).result()

    def carregar_csv(self, caminho: Path, tabela: str) -> int:
        from google.cloud import bigquery
        cfg = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.CSV, skip_leading_rows=1,
            autodetect=True, write_disposition="WRITE_TRUNCATE")
        destino = f"{self.projeto}.{os.environ.get('RAW_SCHEMA', self.dataset)}.{tabela}"
        with caminho.open("rb") as fh:
            self.client.load_table_from_file(fh, destino, job_config=cfg).result()
        return int(self.client.get_table(destino).num_rows)

    def existe(self, tabela: str) -> bool:
        from google.cloud.exceptions import NotFound
        try:
            self.client.get_table(f"{self.projeto}.{self.dataset}.{tabela}")
            return True
        except NotFound:
            return False

    def tabelas(self) -> list[str]:
        return sorted(t.table_id for t in self.client.list_tables(self.dataset))


def abrir() -> Warehouse:
    carregar_env()
    motor = _env("WAREHOUSE", "duckdb").lower()
    if motor == "bigquery":
        return BigQueryWarehouse(
            projeto=_env("GCP_PROJECT"), dataset=_env("BQ_DATASET", "elevato"),
            keyfile=_env("GCP_KEYFILE"), location=_env("BQ_LOCATION"))
    return DuckDBWarehouse(_env("DUCKDB_PATH", "./data/elevato.duckdb"))


def ref(tabela: str) -> str:
    """Nome qualificado da tabela para usar dentro de um SELECT."""
    carregar_env()
    if _env("WAREHOUSE", "duckdb").lower() == "bigquery":
        return f"`{_env('GCP_PROJECT')}.{_env('BQ_DATASET', 'elevato')}.{tabela}`"
    return f'"{tabela}"'
