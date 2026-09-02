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

Abra `http://localhost:8000`. Depois disso, para reprocessar só o modelo (após mudar um
parâmetro), use **Salvar e recalcular** na tela de Parâmetros — não é preciso voltar ao terminal.
O `dbt build` só é necessário quando os CSVs de origem mudam.

---

## As telas

| Tela | O que resolve |
|---|---|
| **Painel** | O que decidir hoje: quanto liberar de compra, o que está furando agora, onde o capital está e onde está vazando, e a comparação entre a política atual e as duas ótimas. |
| **Plano de compra** | A fila operacional, alocada **peça a peça**: a corrida das unidades (100 / 500 / 2.000 / todas as do ciclo, um quadrado por peça, clicável), em que peça cada produto entra, onde o caixa acaba, e a comparação contra a reposição ao estoque ideal. Exporta CSV. |
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
plano de compra é sempre o `saldo_final` do último dia carregado — não um "estoque atual"
separado.

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
2. **Distribuição no horizonte.** Poisson quando a variância acompanha a média, Binomial
   Negativa quando a supera — a cauda gorda muda tudo. Horizonte = lead time + intervalo entre
   revisões: o período que **esta** compra precisa cobrir sozinha.

**Decidir a compra — peça a peça, não produto a produto**

3. **Valor de cada peça.** Para a k-ésima unidade de um item, contando o estoque que já existe:

   ```
   P     = P(demanda no horizonte ≥ k)          chance de essa peça vender
   valor = P × margem − (1 − P) × perda
   nota  = valor / custo unitário / horizonte   retorno por real por dia
   ```

   `margem` é o lucro por peça descontado pelo fator de perda na ruptura. `perda` é o custo de
   carregar a peça pelo horizonte mais a fração do custo que se perde no encalhe.

4. **Alocação marginal.** Todas as peças candidatas do catálogo inteiro entram numa fila única
   ordenada pela nota. O motor desce a fila comprando; um bloco que não cabe no caixa restante é
   pulado, não encerra a fila. Como `P` cai a cada peça, o retorno marginal de um item decresce
   sozinho — **o dinheiro se espalha por muitos produtos sem nenhuma regra de diversificação**.

**Camada de política (referência, não decide a compra)**

5. **EOQ, ponto de pedido e estoque de segurança** continuam sendo calculados por item: servem
   de alarme ("olhe este item") e de referência de nível ideal. O nível de serviço sai da
   economia do próprio item (`1 − lote·h / (D·Cu)`), não de uma meta arbitrária.
6. **Preço-sombra do capital (λ).** Soma um prêmio de escassez ao custo de manter e sobe λ até o
   estoque ideal total caber no teto — o corte sai de onde menos paga, não proporcionalmente.

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
