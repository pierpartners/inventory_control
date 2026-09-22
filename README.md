# Núcleo — planejamento de estoque da Elevato

Aplicação web (dbt + Python + FastAPI) que calcula, para cada SKU do catálogo, o **ponto de
pedido**, o **lote de compra** e o **plano de compra priorizado por retorno de capital** —
corrigindo o viés de estoque zerado na estimativa de demanda e tratando separadamente os itens
de giro baixíssimo e alto valor (regime de "unidade marginal").

---

## Como rodar

```bash
cd app
pip install -r requirements.txt

# 1) roda o pipeline inteiro uma vez:
#    carrega os CSVs -> dbt (staging/intermediário/marts) -> modelo em Python -> tabelas res_*
python scripts/rodar_pipeline.py

# 2) sobe o servidor
uvicorn backend.main:app --reload --port 8000
```

Para usar os dados reais direto do DW da Elevato (Postgres `dwanalitico`), preencha as
variáveis `DW_*` / `DWANALITICO_*` no `.env` (modelo em `.env.example`) e rode antes do pipeline:

```bash
python scripts/extrair_dw.py        # 3 anos de venda, estoque diário, compras e entradas -> data/fonte_dw/
python scripts/rodar_pipeline.py    # detecta a pasta e usa a base `dw`
```

O que cada consulta busca, e por quê, está em `docs/estudo_dados_faltantes_dw.md`.

Abra `http://localhost:8000`. Depois disso, para reprocessar só o modelo (após mudar um
parâmetro), use **Salvar e recalcular** na tela de Parâmetros — não é preciso voltar ao terminal.
O `dbt build` só é necessário quando os CSVs de origem mudam.

---

## As telas

| Tela | O que resolve |
|---|---|
| **Painel** | O que decidir hoje: quanto liberar de compra, o que está furando agora, onde o capital está e onde está vazando, e a comparação entre a política atual e as duas ótimas. |
| **Plano de compra** | A fila operacional, alocada **peça a peça**: a corrida das unidades (100 / 500 / 2.000 / todas as do ciclo, um quadrado por peça, clicável), em que peça cada produto entra, onde o caixa acaba, e a comparação contra a reposição ao estoque ideal. Exporta CSV. |
| **Critério de parada** | A fronteira risco × caixa e os quatro critérios que decidem até onde comprar — pelo caixa, por retorno mínimo, por chance mínima da peça, ou **por risco assumido** (você declara o risco que aceita e a plataforma devolve o caixa necessário). Podem ser **combinados**: a compra para na primeira regra que fecha a porta, e a tela mostra qual delas está amarrando. A prévia roda o motor de verdade a cada tecla digitada. |
| **Retorno do dinheiro** | Quanto volta por real aplicado, do total ao item: retorno sobre o capital em estoque, GMROI, payback, a cascata do lucro bruto até o líquido (por onde ele escapa), o retorno **marginal** de cada faixa de caixa (onde o próximo real deixa de pagar) e o mapa capital × retorno em quatro grupos — motor de lucro, joia pequena, dinheiro preso, cauda longa. |
| **Itens** | O catálogo com o resultado do modelo item a item, com filtros (família, ABC, regime, risco) e agregados que recalculam conforme a seleção. Clicar em qualquer linha abre o dossiê do item. |
| **Conferência** | A fila inteira, **uma linha por peça candidata**, com todos os valores intermediários — μ, σ, F(k−1), P, M, L, ganho, custo, V, nota, caixa acumulado e o motivo da decisão. Clicar numa linha mostra a conta refeita passo a passo. Exporta o CSV completo (todas as colunas, todas as linhas) para conferir no Excel. |
| **Metodologia** | Cinco perguntas em linguagem simples, com **os números de um produto de verdade** (escolhido no seletor). Cada fórmula aparece como uma sequência de caixas — nome em português, valor do produto e o símbolo usado —, mais um glossário de todos os símbolos e letras gregas. Feita para quem não conhece o modelo. |
| **Parâmetros** | As premissas econômicas, agrupadas e explicadas, mais três interruptores de diagnóstico que desligam cada correção metodológica para medir, em reais, o que ela entrega. |
| **Dados** | Navegação por todas as tabelas do warehouse, da extração bruta do ERP até a saída do modelo. |

O **dossiê do item** (gaveta lateral, aberta de qualquer tabela) reúne situação atual, histórico
diário classificado, distribuição da demanda, a derivação da política e a economia anual.

---

## O modelo de dados do ERP

Toda madrugada o ERP fecha o estoque do dia anterior e grava, por SKU: `saldo_inicial`,
`pecas_vendidas` e `saldo_final`. A partir disso o dbt classifica cada dia em três estados:

- **Sem estoque** — saldo inicial já zerado. O dia não diz nada sobre demanda e sai da conta.
- **Ruptura parcial** — esgotou no meio do dia. A venda registrada é um *piso*, não o valor real.
- **Disponível** — saldo positivo o dia inteiro. Dado limpo.

Essa distinção é a base da correção de censura feita em Python. A posição de estoque usada no
plano de compra é o **estoque físico** do último dia carregado (`saldo_final`) **mais o em
trânsito**: pedidos ao fornecedor ainda sem entrada no CD, com no máximo
180 dias e previsão de chegada dentro do período de proteção do item (previsão vencida conta
como chegando agora). O que chega depois do horizonte fica fora, porque não serve a demanda
que a compra de hoje precisa cobrir. Não há um "estoque atual" separado. A reserva do ERP
(`disponivel_final` = físico menos reserva) **não** é descontada da posição: ela é a fila de
pedidos que vai faturar pela mesma venda que alimenta a demanda, e descontá-la contaria o
mesmo pedido duas vezes (no último ano houve venda em 2,0% dos dias com disponível zero e
físico positivo, contra 0,1% dos dias com físico zero).

> Para funcionar com dados reais, o ponto crítico é o ERP manter o **histórico diário** de
> estoque (não só o saldo de hoje). É dele que vem a correção.

---

## Estrutura

```
app/
  data/fonte/            CSVs de origem no formato que o ERP exportaria
  data/elevato.duckdb    banco local (criado no primeiro run)
  data/parametros.json   parâmetros do modelo (editados pela tela /parametros)
  dbt_elevato/           projeto dbt (staging -> intermediate -> marts)

  backend/
    warehouse.py         acesso a dados (DuckDB agora, BigQuery quando quiser)
    config.py            parâmetros do modelo + metadados da tela de configuração
    modelo.py            MOTOR DE CÁLCULO — censura/EM, distribuição, alocação
                         marginal do caixa (peça a peça), EOQ e ponto de pedido,
                         preço-sombra do capital
    analitico.py         recortes para as telas; reaproveita as funções de modelo.py
                         para que a explicação na tela seja o mesmo cálculo que gerou
                         o número, e não uma reimplementação paralela
    main.py              aplicação FastAPI (6 telas + APIs)

  templates/             páginas Jinja2
  static/
    nucleo.css           sistema de design
    nucleo.js            formatadores pt-BR, tema de gráficos, motor de tabela, gaveta
                         e a cor de cada produto (hash do SKU — a mesma em toda tela)
    item.js              o dossiê de um SKU
    peca.js              o dossiê de UMA peça da fila, com a conta refeita

  scripts/
    exportar_simulacao.py  gera os CSVs de origem a partir da simulação
    rodar_pipeline.py      roda tudo: carga -> dbt -> modelo -> grava resultados
    conferir.py            refaz por fora, com scipy puro, cada coluna calculada
                           da fila e reconcilia a soma com o plano por item
```

## Revisar o modelo

```bash
python scripts/revisao.py
```

81 verificações independentes em 8 blocos: integridade dos dados, correção de censura,
distribuição, política por item, alocação marginal, a conta de cada peça refeita do zero,
sanidade econômica e — o mais importante — **as hipóteses do modelo confrontadas com o dado**.
Cada teste recalcula por fora, com numpy/scipy puros, e falha se o número não bater.

O bloco 8 mede o que o modelo *assume* contra o que os dados *mostram*:

| Hipótese | O que os dados dizem |
|---|---|
| Demanda com superdispersão (Binomial Negativa) | Razão variância/média mediana de **16** e máxima de **239**. Só 1 dos 142 itens cabe em Poisson. Confirmado: forçar Poisson subestimaria a cauda por larga margem. |
| Dias independentes (`σ_H = σ_dia × √H`) | Autocorrelação somada nos lags 1–25 = **−0,34**; razão de variância medida em 397 blocos independentes = **0,62**. Há reversão à média: a raiz de H **superestima** o desvio do horizonte em ~27%. Erra para o lado seguro, mas erra — vale ~R$ 190 mil de estoque de segurança. Ajustável em `fator_desvio_horizonte`. |
| Sem sazonalidade semanal | Autocorrelação de **+0,05** nos lags 7/14/21 contra −0,02 nos outros: há efeito de dia da semana, que o modelo ignora. |
| Prazo do fornecedor fixo | A base não traz prazo prometido vs. realizado, então atraso de entrega não tem folga própria. |

## Conferir os números

A tabela `res_fila_marginal` guarda **uma linha por peça candidata** (dezenas de milhares) com
todos os insumos e resultados intermediários. Qualquer coluna calculada pode ser refeita a partir
das anteriores — a tela de Conferência mostra essa cadeia, e o script confere sozinho:

```bash
python scripts/conferir.py --linhas 800
```

Ele recalcula μ, σ, a escolha da distribuição, F(k−1), P, M, L, ganho, custo, V e a nota por fora
do motor, compara com o que foi gravado (tolerância 1e-9) e verifica que a soma das linhas
compradas bate exatamente com o plano por item. Use `--sku` para um produto ou `--tudo` para a
fila inteira.

---

## O que o modelo faz (resumo)

**Estimar a demanda de verdade**

1. **Correção de censura (EM).** O dia em que o estoque acabou no meio não é uma observação de
   demanda, é um piso. Os dias totalmente sem estoque saem da conta; os de ruptura parcial
   recebem `E[D | D ≥ observado]`, reestimado em ciclo até convergir.
   **Piso de histórico** (`dias_utilizaveis_minimo`, 30 dias): com menos dias utilizáveis do
   que isso na janela, a correção não tem amostra e o item usa a média simples, com os dias sem
   estoque valendo zero, marcado como *histórico insuficiente*. Sem o piso, uma venda isolada
   num item quase sempre zerado vira uma taxa enorme (5 dias utilizáveis, 100 m² num deles =
   25 m²/dia, R$ 68 mil de compra para um item vendido duas vezes em três anos).
2. **Distribuição no horizonte.** Poisson quando a variância acompanha a média, Binomial
   Negativa quando a supera — a cauda gorda muda tudo. Horizonte = lead time + intervalo entre
   revisões: o período que **esta** compra precisa cobrir sozinha.

**Decidir a compra — peça a peça, não produto a produto**

3. **Valor de cada peça.** Para a k-ésima unidade de um item, contando o estoque que já existe:

   ```
   P     = P(demanda no horizonte ≥ k)          chance de essa peça vender
   valor = P × margem − (1 − P) × perda
   D     = max(1, horizonte + prazo de recebimento − prazo ao fornecedor)
   nota  = valor / custo unitário / D           retorno por real por dia preso
   ```

   `margem` é o lucro por peça descontado pelo fator de perda na ruptura. `perda` é o custo de
   carregar a peça pelo horizonte mais a fração do custo que se perde no encalhe.

   **Ciclo financeiro.** O dinheiro não volta na venda: volta quando a venda vira caixa, e só
   sai quando o fornecedor é pago. `D` conta esses dias, item a item:
   - *recebimento* — mediana de `dias_recebimento` dos títulos a receber dos pedidos em que o
     item apareceu (`raw_ciclo_recebimento`: títulos de caixa, origem PRE, com as parcelas do
     cartão somadas como mensais), separada em e-commerce e lojas porque o meio de pagamento é
     do canal (gateway em D+30 contra à vista, débito e parcelado). Item com menos de 3 títulos
     num canal herda a mediana do canal; sem a tabela vale o parâmetro
     (`prazo_recebimento_*_dias`). A média dos dois canais é ponderada pela participação do
     e-commerce no item;
   - *pagamento* — mediana de `prazo_titulo_dias` das notas do item (`raw_ciclo_pagamento`),
     com 3 notas ou mais; senão a mediana do catálogo; sem dado, `prazo_pagamento_fornecedor_dias`.

   `prazo_recebimento_origem` e `prazo_pagamento_origem` em `res_sku_modelo` dizem de qual
   degrau cada número veio. Só a nota usa `D`: μ, σ e a chance de vender continuam no horizonte,
   porque a peça tem de sair antes da reposição independente de quando o dinheiro entra.

   **Dois canais, um estoque.** A venda de cada SKU é separada em e-commerce (empresa 33) e
   lojas (as demais). Os dois fluxos são estimados com as mesmas máscaras de ruptura do CD,
   porque o estoque é um só. A política usa a soma; a participação do e-commerce
   (μ_e/(μ_e+μ_l)) pondera o custo de ruptura (`fator_perda_ruptura_ecommerce`/`_lojas` ×
   margem do canal) e reparte o custo de cada peça da fila entre dois caixas: a fatia do
   e-commerce (`teto_compra_ecommerce`) e o resto. Com `fatia_ecommerce_rigida` ligada cada
   canal para no seu teto; desligada, só o total limita e a fatia é leitura. Nível de serviço
   por canal não existe: num estoque compartilhado sem reserva a probabilidade de faltar é a
   mesma.

4. **Alocação marginal.** Todas as peças candidatas do catálogo inteiro entram numa fila única
   ordenada pela nota. O motor desce a fila comprando; um bloco que não cabe no caixa restante é
   pulado, não encerra a fila. Como `P` cai a cada peça, o retorno marginal de um item decresce
   sozinho — **o dinheiro se espalha por muitos produtos sem nenhuma regra de diversificação**.

**Onde parar de comprar**

5. **Critério de parada.** Os quatro critérios operam na mesma fila ordenada — um critério
   não é um motor diferente, é só *onde a fila é cortada*. Vários podem estar ligados ao
   mesmo tempo (`criterio_parada = "caixa,chance"`): a peça precisa passar por todos, então
   **o corte acontece na primeira regra que fecha a porta**. `res_criterios` traz uma linha
   por regra isolada mais a linha `combinado`, e a coluna `manda` diz qual das ligadas está
   realmente amarrando — afrouxar as outras não move nada:

   | Critério | Entrada | Saída |
   |---|---|---|
   | Pelo caixa do ciclo | quanto tenho | com que risco eu fico |
   | Por retorno mínimo | piso de retorno por real por dia | onde a fila deixa de pagar |
   | Por chance mínima da peça | piso de chance de vender | quanta prateleira parada eu aceito |
   | **Por risco assumido** | quanto risco eu aceito | **quanto caixa preciso** |

   A **fronteira** (`res_fronteira`) é a curva completa: para cada ponto de corte, o caixa
   aplicado, a margem capturada e a margem que ainda sobra em risco. Ela usa uma identidade
   exata — `E[max(0, D − posição)] = Σ P(D ≥ k)` — então comprar a peça *k* reduz a falta
   esperada em exatamente `P(D ≥ k)`, e a curva de risco é uma soma acumulada, sem aproximação.

**Medir o retorno do dinheiro**

6. **Retorno sobre o capital.** O denominador é sempre capital *imobilizado* (estoque médio ao
   custo), nunca faturamento — margem sobre venda mede preço, retorno sobre capital mede o
   negócio. Daí saem `retorno_capital = lucro_líquido_ano / capital`, o `GMROI` (margem bruta
   por real de estoque), o payback em dias e a divisão da carteira em quatro grupos pelas
   **medianas da própria carteira** (usar a taxa de carregamento como piso não separa nada
   quando o retorno passa de 400% ao ano).
7. **Retorno marginal por faixa de caixa.** A inclinação da fronteira, em faixas: quanto de
   margem o próximo real compra. Os valores são **por ciclo de compra**, não anuais — por isso
   a tela não desenha o custo de capital anual como piso: comparar as duas coisas misturaria
   escalas de tempo.

**Camada de política (referência, não decide a compra)**

8. **EOQ, ponto de pedido e estoque de segurança** continuam sendo calculados por item: servem
   de alarme ("olhe este item") e de referência de nível ideal. O nível de serviço sai da
   economia do próprio item (`1 − lote·h / (D·Cu)`), não de uma meta arbitrária.
9. **Preço-sombra do capital (λ).** Soma um prêmio de escassez ao custo de manter e sobe λ até o
   estoque ideal total caber no teto — o corte sai de onde menos paga, não proporcionalmente.
10. **Pedidos por ano ≠ janelas de risco por ano.** As duas contagens já foram a mesma
    variável, e isso cobrava um pedido a cada revisão nos itens de giro baixo — 43 pedidos/ano
    onde o lote mínimo do fornecedor só permite 1,6. Inflava o custo de pedir em R$ 162 mil/ano
    e jogava 13 itens para lucro negativo. Hoje `pedidos_por_ano = min(D/Q, revisões com
    demanda)` e `janelas_de_risco_ano` é a contagem de exposição; `scripts/revisao.py` tem
    quatro verificações só para essa separação.

> **Por que não repor até o estoque ideal?** Porque encher item por item até o nível ideal gasta
> o caixa em poucos produtos e compra muitas peças cuja chance de vender no horizonte já é
> baixa. Com os dados atuais, as duas estratégias gastam o mesmo dinheiro — e a reposição compra
> 9.953 peças das quais **90% têm menos de 50% de chance de vender**, contra 0,6% na alocação
> marginal. A tela de Plano de compra mostra essa comparação lado a lado, medida na mesma régua.

---

## Trocar para BigQuery

Tudo já está preparado, só não está ligado:

1. Preencha `GCP_PROJECT`, `GCP_KEYFILE`, `BQ_DATASET` e `BQ_LOCATION` no `.env`
   (as linhas existem, comentadas).
2. Troque `WAREHOUSE=duckdb` para `bigquery` e `DBT_TARGET=duckdb` para `bigquery`.
3. Rode `python scripts/rodar_pipeline.py` de novo.

O mesmo SQL do dbt (`profiles.yml` tem os dois targets) e o mesmo código Python funcionam sem
alteração, porque toda leitura e escrita passa por `backend/warehouse.py`.

---

## Dados

Os CSVs em `data/fonte/` vêm da simulação de vendas da Elevato — **não são dados reais**. A
entrega é o laboratório (metodologia + aplicação), pronto para receber a extração do ERP no mesmo
formato (`catalogo.csv`, `vendas.csv`, `estoque_diario.csv`).

## Diagnóstico do estoque atual

A página `/diagnostico` responde "o que está parado, o que está em risco e quanto isso custa",
sem recalcular a política. Cada SKU cai numa faixa, avaliada nesta ordem: **zerado com demanda**
(sem peça e com demanda corrigida positiva), **risco** (posição ≤ ponto de pedido, com ponto de
pedido positivo), **sem giro** (há peça e não vende há mais de N dias, padrão 180), **excesso**
(acima do estoque máximo do modelo), **saudável**. O estoque é valorizado duas vezes: ao custo
que o modelo usa e ao custo médio contábil do ERP. A **idade FIFO** aproxima há quanto tempo o
saldo está no CD percorrendo as entradas da mais recente para trás até cobrir o saldo. A foto
das lojas (uma linha por SKU e local, sem histórico) aponta transferência em vez de compra.
Os cortes são quatro — fornecedor, comprador, família e **marca** (a coluna `origem` do cadastro,
com o fabricante de reserva) — e a página ainda cruza dois deles numa **matriz comprador × marca
(ou família)**, com capital, capital parado, capital em risco, lucro perdido e nº de itens em cada
célula; clicar numa célula filtra a lista de itens pelos dois recortes ao mesmo tempo.
`scripts/revisao.py` (bloco 9) recompõe faixas, idade, somas e a matriz por fora.
