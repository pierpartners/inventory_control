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
    # Perda na ruptura POR CANAL. No site o cliente nao espera e nao ha
    # vendedor para substituir; na loja ha. O custo de ruptura de cada item e
    # a media dos dois, ponderada pela participacao do canal na demanda dele.
    # `fator_perda_ruptura` acima fica para a politica atual de referencia.
    fator_perda_ruptura_ecommerce: float = 0.85
    fator_perda_ruptura_lojas: float = 0.85
    nivel_servico_min: float = 0.80
    nivel_servico_max: float = 0.995
    dias_por_ano: int = 365
    corte_curva_a: float = 0.80
    corte_curva_b: float = 0.95
    corte_xyz_x: float = 1.60
    corte_xyz_y: float = 2.80
    teto_capital: float = 1_500_000.0
    teto_compra_ciclo: float = 250_000.0
    # Fatia INTERNA do e-commerce dentro do caixa do ciclo. A compra e uma so,
    # da empresa 26; a fatia diz quanto desse dinheiro a demanda do e-commerce
    # pode puxar. 0 = sem fatia declarada. Rigida: cada canal para no seu
    # teto; flexivel (padrao): so o total limita e a fatia e leitura.
    teto_compra_ecommerce: float = 0.0
    fatia_ecommerce_rigida: bool = False
    limiar_giro_baixo: float = 20.0
    perda_encalhe: float = 0.04
    # Ciclo financeiro da peca. A nota divide o valor da peca pelos DIAS QUE O
    # DINHEIRO FICA PRESO, e esse prazo nao acaba na venda: acaba quando a
    # venda vira caixa. Cartao parcelado e marketplace (e-commerce) recebem
    # bem depois da venda; a loja recebe mais perto. O prazo de recebimento
    # de cada item e a media dos dois canais ponderada pela participacao do
    # e-commerce nele. O prazo que o fornecedor da para pagar encurta o
    # ciclo. Zero em tudo = o comportamento antigo (dinheiro preso so ate a
    # venda). Nada disto muda a probabilidade de vender: essa continua no
    # periodo de protecao, porque a peca tem de sair antes da reposicao.
    prazo_recebimento_ecommerce_dias: int = 0
    prazo_recebimento_lojas_dias: int = 0
    prazo_pagamento_fornecedor_dias: int = 0
    fator_desvio_horizonte: float = 1.00
    # Dias de historico DIARIO usados para estimar a taxa de demanda. Nao e o
    # tamanho do banco: e a memoria do modelo.
    #
    # 365 nao e chute. Medido no backtest em tres datas, o erro medio absoluto
    # da previsao por janela:
    #     tudo (1.097d)  47%      120d  47%
    #     365d           33%  <--   90d  50%
    #     180d           49%       30d  64%
    # Janela curta parece melhor numa data e pessima em outra: em 07/01/2026 a
    # de 90 dias SUPERESTIMA 112%, porque os 90 dias anteriores pegaram o pico
    # de dezembro. O que a inversao mostra e que falta sazonalidade ao modelo,
    # nao janela mais curta - e um ciclo anual e a escolha honesta enquanto
    # sazonalidade nao for modelada.
    janela_estimacao_dias: int = 365
    # Piso de historico: com menos dias utilizaveis do que isto na janela, a
    # correcao de censura nao tem amostra para ser confiavel e o item cai para
    # a media ingenua (dias sem estoque contam como zero). O caso que motivou:
    # a pastilha 1088006, vendida duas vezes em tres anos sob encomenda, tinha
    # 5 dias utilizaveis e uma venda de 100 m2 num deles - a corrigida dava
    # 25 m2/dia e uma compra de R$ 68 mil. Medido: 30 e 60 dias dao quase o
    # mesmo plano; abaixo de 30 o dano volta. 0 desliga.
    dias_utilizaveis_minimo: int = 30
    # --- critério de parada da compra (ver CRITERIOS abaixo) ---
    # lista separada por virgula: varios criterios valem ao mesmo tempo e
    # a compra e cortada pelo primeiro que chegar
    criterio_parada: str = "caixa"
    retorno_minimo_dia: float = 0.0      # 0 = desligado
    chance_minima_peca: float = 0.0      # 0 = desligado
    teto_margem_em_risco: float = 0.0    # 0 = desligado
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
        campos = {k: v for k, v in dados.items() if k in validos}
        # arquivo antigo, de antes da quebra por canal: a perda na ruptura que
        # ele guardou vale para os dois canais - herdar mantem a hipotese
        # economica do usuario em vez de voltar calado ao default da classe
        if "fator_perda_ruptura" in campos:
            for k in ("fator_perda_ruptura_ecommerce", "fator_perda_ruptura_lojas"):
                campos.setdefault(k, campos["fator_perda_ruptura"])
        return cls(**campos)


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
    ("fator_perda_ruptura_ecommerce", "Perda quando falta no e-commerce", "%", "pct", 0, 1,
     "Fração da margem do e-commerce que se perde quando a peça falta. No site o cliente "
     "não espera nem aceita substituto: tende a ser maior que nas lojas.", "Economia"),
    ("fator_perda_ruptura_lojas", "Perda quando falta nas lojas", "%", "pct", 0, 1,
     "Fração da margem das lojas que se perde na ruptura. O vendedor pode reservar ou "
     "oferecer outro item; costuma ser menor que no e-commerce. O custo de ruptura de cada "
     "item é a média dos dois fatores ponderada pela participação de cada canal nele.", "Economia"),
    ("fator_perda_ruptura", "Perda na ruptura (referência agregada)", "%", "pct", 0, 1,
     "Usado só na política atual de comparação. O modelo usa os dois fatores por canal acima.",
     "Economia"),
    ("perda_encalhe", "Perda se a peça encalhar", "% do custo", "pct", 0, 1,
     "Quanto do custo você perde por uma peça que não vendeu dentro do horizonte: "
     "remarcação, tonalidade fora de linha, descontinuação. Item de linha que sempre "
     "acaba vendendo fica entre 2% e 5%; coleção ou item em fim de vida, entre 30% e 50%. "
     "É o que segura a compra de não virar empilhamento.", "Economia"),
    ("prazo_recebimento_ecommerce_dias", "Prazo de recebimento · e-commerce", "dias", "int", 0, 365,
     "Quantos dias depois da venda o dinheiro do e-commerce entra no caixa (cartão parcelado, "
     "repasse de marketplace). Soma aos dias em que a peça prende o dinheiro: a nota passa a "
     "dividir por horizonte + recebimento − prazo do fornecedor. Zero = o dinheiro volta na venda.",
     "Economia"),
    ("prazo_recebimento_lojas_dias", "Prazo de recebimento · lojas", "dias", "int", 0, 365,
     "O mesmo para as lojas (dinheiro, débito, crediário). O prazo de cada item é a média dos "
     "dois canais ponderada pela participação do e-commerce na demanda dele.", "Economia"),
    ("prazo_pagamento_fornecedor_dias", "Prazo de pagamento ao fornecedor", "dias", "int", 0, 365,
     "Quantos dias depois de receber a mercadoria a empresa paga o fornecedor. Encurta o ciclo "
     "financeiro da peça — dinheiro que ainda não saiu não está preso. Não muda a chance de "
     "vender nem o custo de carregar; só os dias no denominador da nota.", "Economia"),

    ("nivel_servico_min", "Nível de serviço mínimo", "%", "pct", 0.5, 0.99,
     "Piso do nível de serviço no regime EOQ, mesmo para itens de margem magra.",
     "Limites"),
    ("nivel_servico_max", "Nível de serviço máximo", "%", "pct", 0.9, 0.9999,
     "Teto do nível de serviço. Acima disso o estoque de segurança dispara sem ganho "
     "real de atendimento.", "Limites"),
    ("janela_estimacao_dias", "Janela de estimação da demanda", "dias", "int", 14, 1095,
     "Quantos dias de histórico diário o modelo usa para estimar quanto cada item "
     "vende por dia. É a memória do modelo, e não o tamanho do banco: janela longa "
     "dilui crescimento recente, janela curta vira ruído. A economia da peça (custo "
     "e margem) continua vindo do histórico inteiro, porque não é taxa.",
     "Demanda"),

    ("dias_utilizaveis_minimo", "Piso de histórico para corrigir a demanda", "dias", "int", 0, 365,
     "Com menos dias utilizáveis (dias em que havia estoque) do que isto na janela, a "
     "correção de ruptura não tem amostra e o item usa a média simples, contando os dias "
     "sem estoque como zero. Evita que uma venda isolada num item quase sempre zerado "
     "vire uma demanda enorme. O item fica marcado “histórico insuficiente”. 0 desliga.",
     "Demanda"),

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
    ("teto_compra_ecommerce", "Fatia do e-commerce no caixa do ciclo", "R$", "float", 0, 100_000_000,
     "Quanto do caixa do ciclo cabe à demanda do e-commerce. Cada peça comprada é cobrada aos "
     "dois caixas na proporção da participação do e-commerce naquele item. Com a fatia rígida "
     "ligada (abaixo, nos interruptores), o e-commerce para de puxar peças quando a fatia acaba; "
     "as lojas ficam com o resto do caixa.", "Restrições"),

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
    ("fatia_ecommerce_rigida", "Fatia do e-commerce rígida",
     "Ligado: o e-commerce não passa da fatia dele e as lojas não passam do resto do caixa. "
     "Desligado: só o caixa total limita a compra, e a fatia aparece só como leitura de quanto "
     "cada canal puxou."),
]

GRUPOS = ["Operação", "Demanda", "Economia", "Limites", "Restrições",
          "Classificação"]


# ----------------------------------------------------------------------
# Criterios de parada da compra.
#
# Todos operam sobre a MESMA fila de pecas, ja ordenada por retorno por real
# por dia: um criterio de parada nao e um motor diferente, e so a escolha de
# onde cortar a fila. Por isso da para mostrar os quatro na mesma fronteira e
# trocar de um para o outro sem recalcular nada do motor.
#
# (chave, rotulo, campo do parametro, unidade, tipo, o que responde)
# ----------------------------------------------------------------------
CRITERIOS = [
    # chave, rotulo, campo, unidade, tipo, descricao, pergunta, acao
    # `acao` e a frase que fica colada no campo de digitar: tem de ser lida
    # como uma ordem de compra, nao como um conceito.
    ("caixa", "Pelo caixa do ciclo", "teto_compra_ciclo", "R$", "float",
     "Desce a fila comprando até o dinheiro acabar. O risco com que você "
     "termina é consequência, não escolha.",
     "Tenho este dinheiro — onde ele rende mais?",
     "Comprar até gastar, no máximo:"),

    ("retorno", "Por retorno mínimo", "retorno_minimo_dia", "por R$/dia", "float",
     "Corta a fila onde a peça deixa de render este piso por real por dia. "
     "Abaixo do custo de capital da empresa, a peça destrói valor.",
     "Quanto cada real tem de render por dia para valer a compra?",
     "Comprar apenas peças que rendam acima de:"),

    ("chance", "Por chance mínima da peça", "chance_minima_peca", "%", "pct",
     "Freio de encalhe: peça com chance de venda abaixo do piso não entra, "
     "por mais que ela renda.",
     "Qual a chance mínima de venda que eu aceito?",
     "Comprar apenas peças com chance de venda acima de:"),

    ("risco", "Por risco assumido", "teto_margem_em_risco", "R$", "float",
     "Você declara o risco e a plataforma devolve o caixa necessário. "
     "Inverte entrada e saída: o dinheiro passa a ser a resposta.",
     "Quanta margem aceito deixar em risco neste ciclo?",
     "Comprar até a margem em risco cair abaixo de:"),
]
