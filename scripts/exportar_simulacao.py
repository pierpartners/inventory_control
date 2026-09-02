# -*- coding: utf-8 -*-
"""
Exporta a base simulada no formato que um ERP entregaria.
Estes tres arquivos sao a FONTE da aplicacao - substitua-os pelo extrato real
do ERP mantendo os mesmos nomes de coluna e todo o resto funciona.
"""
import numpy as np
import pandas as pd
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DEST = RAIZ / "data" / "fonte"
DEST.mkdir(parents=True, exist_ok=True)
ORIG = Path("/home/claude")

vendas = pd.read_pickle(ORIG / "vendas.pkl")
cat = pd.read_pickle(ORIG / "catalogo.pkl")
onh = pd.read_pickle(ORIG / "onhand.pkl")          # saldo no FIM do dia
piv = pd.read_pickle(ORIG / "demanda_diaria.pkl")  # vendas por SKU e dia

# ---------------- catalogo ----------------
catalogo = cat[["SKU", "Item", "Familia", "Unidade", "Origem", "Custo_Unitario",
                "Preco_Tabela", "Peso_Unit_kg", "Lead_Time_dias", "Lote_Minimo_Compra"]].copy()
catalogo.columns = ["sku", "item", "familia", "unidade", "origem", "custo_unitario",
                    "preco_tabela", "peso_unit_kg", "lead_time_dias", "lote_minimo_compra"]
catalogo.to_csv(DEST / "catalogo.csv", index=False)

# ---------------- linhas de venda ----------------
v = vendas.copy()
v = v[["ID_Linha", "Pedido", "Data_Hora_Venda", "Data", "SKU", "Pecas_Vendidas",
       "Valor_da_Peca", "Preco_Tabela_Unit", "Desconto_Pct", "Receita_Bruta",
       "Receita_Liquida", "Custo_Unitario", "CMV", "Valor_do_Frete",
       "Frete_Cobrado_Cliente", "Impostos_sobre_Venda", "Lucro", "Total",
       "Canal", "Tipo_Cliente", "Cliente_ID", "UF", "Regiao", "Modalidade_Frete",
       "Peso_Total_kg"]]
v.columns = ["id_linha", "pedido", "data_hora_venda", "data", "sku", "pecas_vendidas",
             "valor_da_peca", "preco_tabela_unit", "desconto_pct", "receita_bruta",
             "receita_liquida", "custo_unitario", "cmv", "valor_do_frete",
             "frete_cobrado_cliente", "impostos_sobre_venda", "lucro", "total",
             "canal", "tipo_cliente", "cliente_id", "uf", "regiao", "modalidade_frete",
             "peso_total_kg"]
v.to_csv(DEST / "vendas.csv", index=False)

# ---------------- posicao de estoque diaria ----------------
# saldo_inicial = saldo_final + o que saiu no dia (as entradas chegam antes da venda)
skus = list(onh.index)
datas = list(onh.columns)
fim = onh.reindex(index=skus, columns=datas).to_numpy(float)
vend = piv.reindex(index=skus, columns=datas).fillna(0).to_numpy(float)
ini = fim + vend
est = pd.DataFrame({
    "sku": np.repeat(skus, len(datas)),
    "data": np.tile([str(d) for d in datas], len(skus)),
    "saldo_inicial": ini.ravel().astype(int),
    "saldo_final": fim.ravel().astype(int),
    "pecas_vendidas": vend.ravel().astype(int),
})
est.to_csv(DEST / "estoque_diario.csv", index=False)

print("catalogo.csv        ", len(catalogo), "linhas")
print("vendas.csv          ", len(v), "linhas")
print("estoque_diario.csv  ", len(est), "linhas")
print("periodo:", est.data.min(), "a", est.data.max())
print("destino:", DEST)
