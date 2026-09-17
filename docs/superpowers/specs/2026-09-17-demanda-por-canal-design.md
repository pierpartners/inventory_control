# Demanda por canal: e-commerce e lojas

Data: 2026-09-17. Estado: aprovado em conversa, aguardando plano de implementação.

## Problema

O modelo estima a demanda de cada SKU como uma série só, a soma de todas as
lojas do grupo. O e-commerce (empresa 33) e as demais lojas têm dinâmicas de
venda diferentes, e a mistura esconde isso. Além disso o e-commerce tem uma
fatia interna de caixa que hoje o modelo não conhece.

Fatos que definem o desenho:

- Um estoque físico só: o CD Gravataí (empresa 26, local 124). O local 185 da
  empresa 33 é trânsito fiscal.
- Um comprador só: a empresa 26 compra tudo e transfere. O orçamento do
  e-commerce é uma fatia interna, não um pagamento ao fornecedor.
- Universo: SKUs que o e-commerce vendeu nos últimos 12 meses. Não muda.

## Decisão

Dois fluxos de demanda estimados separadamente, uma política de estoque, uma
compra, dois tetos internos de caixa. Descartado: rodar o modelo duas vezes
com o saldo do CD rateado (perde o pooling, compra em dobro, planos podem
discordar) e só estatística por canal com um teto (não dá caixa ao e-commerce).

## 1. Dados (dbt)

- `stg_vendas` ganha `canal`: `ecommerce` quando `loja_id = '33'`, senão
  `lojas`. Nas bases `sintetica` e `exports` não há loja: tudo cai em `lojas`
  e o modelo degenera para um canal sem código especial.
- `int_vendas_sku_dia`: grão SKU × dia × canal.
- `mart_estoque_diario` mantém o grão SKU × dia e as colunas atuais, e ganha
  `pecas_ecommerce` e `pecas_lojas`. Formato largo de propósito: a censura é
  do estoque compartilhado, um dia de ruptura censura os dois canais, e
  `dia_utilizavel`/`dia_censurado` continuam uma por dia. Invariante:
  `pecas_ecommerce + pecas_lojas = pecas_vendidas`.
- `mart_sku_financeiro` ganha peças, receita, lucro e `lucro_por_peca` por
  canal (`_ecommerce`, `_lojas`).
- `mart_demanda_estatistica` ganha as médias ingênua e disponível por canal.

## 2. Estatística por canal (`backend/modelo.py`)

- `estatistica_demanda` roda `em_censurado` três vezes por SKU, com as mesmas
  máscaras de dia: total, e-commerce, lojas. A série total continua estimada
  diretamente, então mu e sigma da política não mudam de método; a
  covariância entre canais já está embutida no sigma da soma.
- Colunas novas por SKU: `demanda_media_dia_ecommerce`, `_lojas`,
  `desvio_padrao_dia_ecommerce`, `_lojas`, `share_ecommerce = mu_e / mu`
  (zero quando mu = 0).
- Economia da peça: `Cu` deixa de ser `lucro_por_peca × fator_perda_ruptura`
  e vira `share_e × lucro_e × fator_e + (1 − share_e) × lucro_l × fator_l`.
  Parâmetros novos `fator_perda_ruptura_ecommerce` e
  `fator_perda_ruptura_lojas`, padrão 0,85 (o valor atual). O parâmetro
  antigo `fator_perda_ruptura` continua existindo como o valor usado quando
  não há canal (bases sem loja) e como padrão dos dois novos.
- `_margem_coerente` refaz o preço da janela por canal.
- Fora de escopo: nível de serviço por canal. Num estoque compartilhado sem
  reserva a probabilidade de faltar é a mesma. Falta esperada por canal =
  falta esperada × participação.

## 3. Alocação com dois caixas

- Parâmetros: `teto_compra_ciclo` continua sendo o total. Entram
  `teto_compra_ecommerce` (R$, a fatia interna) e `fatia_ecommerce_rigida`
  (bool, padrão falso). Lojas ficam com `teto_compra_ciclo −
  teto_compra_ecommerce`. Chave desligada: só o total limita, a fatia é
  leitura. Chave ligada: cada canal para no seu teto.
- `candidatas_marginais`: cada peça ganha `custo_ecommerce = custo × share_e`
  e `custo_lojas = custo − custo_ecommerce`. Nota e ordem da fila não mudam.
- `caminhar`: dois saldos. No modo rígido a peça só entra se cabe nos dois;
  motivos novos: "não coube no caixa do e-commerce", "não coube no caixa das
  lojas". Colunas novas: `caixa_acumulado_ecommerce`, `caixa_acumulado_lojas`,
  `caixa_restante_ecommerce`, `caixa_restante_lojas`.
- Plano por item: `valor_da_compra_ecommerce`, `valor_da_compra_lojas`.
  `analitico.rateio_por_loja` usa a mesma participação, para os dois números
  nunca discordarem.
- Saída: um pedido, da empresa 26, com a fatia de cada canal por linha e no
  total.

## 4. Backtest por canal (`backend/validacao.py`)

- Teto total real da data, como hoje. `fatia_ecommerce_rigida` desligada no
  backtest, porque não existe caixa histórico do e-commerce.
- Por canal e por data: demanda realizada no horizonte, venda perdida
  atribuída pela participação realizada, e quanto do plano cada canal puxou.
- `rodar` e `rodar_intervalo` mantêm a estrutura; as colunas por canal são
  acrescentadas ao relatório.

## 5. Telas

- `/parametros`: `teto_compra_ecommerce`, `fatia_ecommerce_rigida`,
  `fator_perda_ruptura_ecommerce`, `fator_perda_ruptura_lojas` no grupo de
  caixa/ruptura.
- `/plano`: total do ciclo dividido em dois; fatia de cada canal por linha;
  fila com os dois saldos e o motivo de recusa por caixa.
- `/painel`: cartões de demanda e compra com a quebra por canal.
- `/item`: duas séries de venda no gráfico; estatística por canal na tabela.
- `/validacao`: indicadores por canal da seção 4.

## 6. Verificação

- `scripts/revisao.py`: `pecas_ecommerce + pecas_lojas = pecas_vendidas` em
  cada SKU-dia; EM por canal recomputado; `Cu` ponderado recomputado;
  caminhada com dois caixas refeita em numpy; rateio da compra igual à
  participação.
- Bloco 8, diagnósticos novos: correlação entre canais por SKU e
  decomposição da variância (sigma² total vs. sigma²_e + sigma²_l + 2·cov);
  coeficiente de variação por canal; deriva da participação do e-commerce ao
  longo do tempo; sazonalidade semanal por canal.
- `scripts/conferir.py` mostra as colunas por canal.

## Fora de escopo

Venda por encomenda, ordens de compra em aberto e simulação de custo estão
no `to-do.txt` e não entram aqui.
