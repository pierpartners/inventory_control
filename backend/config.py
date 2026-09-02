# -*- coding: utf-8 -*-
"""Parametros do modelo: leitura, escrita e validacao.

Tudo que a pagina de Parametros edita passa por aqui. Os defaults sao os
mesmos que foram usados no estudo, e cada um carrega a explicacao que
aparece na tela.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, fields
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
ARQ = RAIZ / "data" / "parametros.json"


@dataclass
class Parametros:
    periodo_revisao_dias: int = 7
    taxa_manutencao_ano: float = 0.25
    custo_por_pedido: float = 185.0
    fator_perda_ruptura: float = 0.85
    nivel_servico_min: float = 0.80
    nivel_servico_max: float = 0.995
    dias_por_ano: int = 365
    corte_curva_a: float = 0.80
    corte_curva_b: float = 0.95
    corte_xyz_x: float = 1.60
    corte_xyz_y: float = 2.80
    teto_capital: float = 1_500_000.0
    teto_compra_ciclo: float = 250_000.0
    limiar_giro_baixo: float = 20.0
    perda_encalhe: float = 0.04
    fator_desvio_horizonte: float = 1.00
    aplicar_teto_capital: bool = True
    corrigir_censura: bool = True
    imputar_dias_censurados: bool = True
    respeitar_lote_minimo: bool = False

    def salvar(self) -> None:
        ARQ.parent.mkdir(parents=True, exist_ok=True)
        ARQ.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def carregar(cls) -> "Parametros":
        if not ARQ.exists():
            p = cls()
            p.salvar()
            return p
        dados = json.loads(ARQ.read_text(encoding="utf-8"))
        validos = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in dados.items() if k in validos})


# Metadados da tela de parametros: rotulo, unidade, tipo, limites e grupo.
# So texto de interface e faixa de validacao - os defaults do modelo estao
# na dataclass acima e nao mudam aqui.
CAMPOS = [
    ("periodo_revisao_dias", "Período de revisão de compras", "dias", "int", 1, 90,
     "De quantos em quantos dias alguém olha a lista de compras. Soma ao prazo do "
     "fornecedor e forma o período de proteção — a janela em que você fica exposto.",
     "Operação"),
    ("custo_por_pedido", "Custo por pedido de compra", "R$", "float", 0, 10000,
     "Quanto custa colocar e receber um pedido: tempo de compras, recebimento, "
     "conferência. Quando é alto, puxa o lote econômico para cima.", "Operação"),
    ("dias_por_ano", "Dias por ano", "dias", "int", 300, 366,
     "Base de anualização da demanda.", "Operação"),

    ("taxa_manutencao_ano", "Custo de manter estoque", "% a.a.", "pct", 0, 2,
     "Capital parado + armazenagem + seguro + obsolescência, sobre o custo do item. "
     "É um dos três números que mais mexem no resultado.", "Economia"),
    ("fator_perda_ruptura", "Perda quando falta o item", "%", "pct", 0, 1,
     "Fração da margem que se perde na ruptura. 0% = o cliente sempre espera; "
     "100% = a venda está sempre perdida. Em item de obra, tende a ser alto.", "Economia"),
    ("perda_encalhe", "Perda se a peça encalhar", "% do custo", "pct", 0, 1,
     "Quanto do custo você perde por uma peça que não vendeu dentro do horizonte: "
     "remarcação, tonalidade fora de linha, descontinuação. Item de linha que sempre "
     "acaba vendendo fica entre 2% e 5%; coleção ou item em fim de vida, entre 30% e 50%. "
     "É o que segura a compra de não virar empilhamento.", "Economia"),

    ("nivel_servico_min", "Nível de serviço mínimo", "%", "pct", 0.5, 0.99,
     "Piso do nível de serviço no regime EOQ, mesmo para itens de margem magra.",
     "Limites"),
    ("nivel_servico_max", "Nível de serviço máximo", "%", "pct", 0.9, 0.9999,
     "Teto do nível de serviço. Acima disso o estoque de segurança dispara sem ganho "
     "real de atendimento.", "Limites"),
    ("fator_desvio_horizonte", "Ajuste do desvio no horizonte", "×", "float", 0.4, 1.5,
     "O modelo calcula o desvio da demanda no horizonte como desvio diário × raiz(H), "
     "o que supõe que um dia não influencia o outro. Se a demanda tem reversão à média, "
     "essa conta superestima a variação e infla o estoque de segurança. Deixe em 1,00 "
     "para a hipótese conservadora; rode scripts/revisao.py para ver o fator medido "
     "nos seus dados.", "Limites"),

    ("limiar_giro_baixo", "Limiar do regime discreto", "peças", "float", 0, 200,
     "Quando a demanda esperada no período de proteção fica abaixo disso, o item troca "
     "a curva normal pelo teste da unidade marginal.", "Limites"),

    ("teto_capital", "Teto de capital em estoque", "R$", "float", 0, 100_000_000,
     "Quanto a empresa aceita ter parado em estoque, somando todos os itens. O modelo "
     "encarece o dinheiro internamente até caber aqui.", "Restrições"),
    ("teto_compra_ciclo", "Teto de compra por ciclo", "R$", "float", 0, 100_000_000,
     "Caixa liberado para UMA rodada de compras. Corta a fila do plano de compra pelo "
     "retorno por real investido.", "Restrições"),

    ("corte_curva_a", "Corte da curva A", "% do lucro", "pct", 0.5, 0.95,
     "Itens até este acumulado de lucro potencial são classe A.", "Classificação"),
    ("corte_curva_b", "Corte da curva B", "% do lucro", "pct", 0.8, 0.999,
     "Até este acumulado, classe B. O resto, classe C.", "Classificação"),
    ("corte_xyz_x", "Corte XYZ — X", "CV diário", "float", 0.1, 10,
     "Coeficiente de variação abaixo do qual a demanda é considerada regular (X).",
     "Classificação"),
    ("corte_xyz_y", "Corte XYZ — Y", "CV diário", "float", 0.1, 20,
     "Entre X e este valor, demanda moderada (Y). Acima, errática (Z).",
     "Classificação"),
]

CHAVES = [
    ("corrigir_censura", "Corrigir a ruptura no cálculo da demanda",
     "Ligado: os dias sem estoque ficam fora da conta. Desligado: o modelo volta a "
     "tratar falta como demanda zero — útil só para medir o tamanho do erro."),
    ("imputar_dias_censurados", "Imputar os dias que acabaram no meio",
     "Ligado: o dia em que o estoque acabou vira “vendeu pelo menos X” e recebe um "
     "valor estimado. Desligado: esse dia é simplesmente excluído."),
    ("aplicar_teto_capital", "Aplicar o teto de capital",
     "Ligado: o modelo encolhe o estoque até caber no teto. Desligado: calcula o ótimo "
     "irrestrito, que é a referência de quanto o teto está custando."),
    ("respeitar_lote_minimo", "Respeitar o lote mínimo do fornecedor",
     "Ligado: a primeira compra de um item é avaliada em bloco, do tamanho do lote mínimo — "
     "é o que dá para executar de verdade. Desligado: o motor compra peça a peça, sem "
     "arredondar, mostrando a alocação teoricamente ideal."),
]

GRUPOS = ["Operação", "Economia", "Limites", "Restrições", "Classificação"]
