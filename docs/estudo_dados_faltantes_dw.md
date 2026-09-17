# Estudo — de onde vem cada dado do modelo no DW da Elevato

Data: 2026-09-16. Fonte: `dwanalitico` (Postgres, host em `DWANALITICO_HOST` no `.env`),
schemas `db2` (espelho do ERP CISS), `silver` e `gold` (camadas do dbt-elevato).
Todos os números abaixo foram medidos nesta data, salvo indicação.

O extrator que materializa estas decisões é `scripts/extrair_dw.py`; ele grava seis
tabelas em `data/fonte_dw/` no formato que o staging `real` do dbt já conhecia. Este
documento explica **por que cada consulta é do jeito que é** e o que ficou de fora.

Complementa `docs/dw_mapeamento_estoque.md`, que investigou o livro de estoque e a
separação pedido × baixa física. Aqui o foco é o que o **modelo de compra** precisa e a
exportação manual (`dbt-elevato/exports`) não trazia.

---

## 0. Universo e demanda — as duas decisões de escopo

**Universo** (desde 2026-09-17): **todo SKU que o CD Gravataí (empresa 26, local 124)
movimentou na janela de 3 anos** — qualquer linha em `db2.estoque_sintetico` nesse local.
Medido em 2026-09-17: **19.139 SKUs**. É a mesma definição que o staging já usava
(`stg_catalogo` só planeja o que tem posição no CD), agora aplicada também na extração.

Até então o universo era o recorte da exportação manual: SKUs que o e-commerce (empresa
33) vendeu nos últimos 12 meses — 3.278 SKUs, 3.231 com posição no CD. Ficavam de fora
os itens que o CD estoca mas só as lojas físicas vendem. Contagens de referência do dia
da troca, para dimensionar alternativas:

| Recorte | SKUs | Com posição no CD |
|---|---|---|
| E-commerce, 12 meses (antigo) | 3.279 | 3.230 |
| Qualquer loja, 12 meses | 17.459 | 11.809 |
| Movimento no CD, 3 anos (atual) | 19.139 | 19.139 |
| Saldo > 0 no CD hoje | 7.214 | 7.214 |

Os números das seções seguintes foram medidos ainda no universo antigo.

**Demanda**: venda de **todas as lojas** desses SKUs, não só do e-commerce. O estoque
dimensionado é o do CD Gravataí (empresa 26, local 124), que abastece o grupo. Medido
na janela de 2025-09-17 a 2026-09-16, nos SKUs do universo:

| Origem da venda | Empresas | Peças | Receita líquida |
|---|---|---|---|
| E-commerce (33) | 1 | 114.882 | R$ 37,2 mi |
| Outras lojas | 30 | 1.241.701 | R$ 120,0 mi |

Dimensionar o CD só pelo e-commerce usaria um décimo do sinal. A saída física do CD no
mesmo período foi 1,29 milhão de peças, coerente com a venda do grupo e não com a do
canal online.

---

## 1. Custo unitário

**Faltava** na exportação. **Fonte**: `db2.estoque_sintetico.valcustomedio`, CD 26/124.

- Cobertura: 2.654 de 2.698 SKUs da exportação têm custo em algum dia com estoque. Os 44
  restantes nunca tiveram saldo no CD (venda sob encomenda ou de outro local).
- **Armadilha**: quando o saldo zera, o ERP zera o custo médio junto e passa a lançar
  R$ 1,00. Por isso o custo válido é o do **último dia com `qtdatualestoque > 0`**, e o
  staging ainda trava contra a mediana (fora de 0,25× a 4× vira a mediana). O caso
  documentado no staging (prateleira de R$ 1.016 lida como R$ 1,00) é desse tipo.
- O extrator carrega o custo dia a dia, preenchido para frente nos dias sem movimento,
  para que o lucro de cada venda use o custo **do dia da venda**, não o de hoje.

## 2. Prazo de entrega do fornecedor

**Faltava**. A exportação tinha só o prazo **combinado** (`silver.compras.diasprevisaoentrega`).

**Fonte do realizado**: `db2.estoque_analitico` com `idoperacao = 1` ("Compra de
Mercadoria"), CD 26/124. Essa operação traz `numpedido` em 41.312 de 41.315 linhas do
último ano; juntando com `db2.pedido_compra` (mesma empresa) obtém-se pedido → entrada.

| Medida | Último ano | Três anos |
|---|---|---|
| Pares pedido × SKU com entrada | 39.998 | 45.894 |
| Prazo realizado, mediana | 12 dias | 14 dias |
| Prazo realizado, p25 – p75 | 7 – 17 dias | — |
| Prazo combinado, mediana | 30 dias | 35 dias |

O combinado superestima o realizado em mais de duas vezes, o que confirma a medição do
extrato antigo (40,6 contra 17,9). Dimensionar o estoque de segurança pelo combinado
imobiliza capital para uma espera que não acontece. O staging usa a mediana do realizado
por SKU e o desvio dele.

- 3 pares com prazo negativo e 21 acima de 365 dias em três anos; o staging já filtra
  `between 0 and 365`.
- `silver.auxiliar_notas` e `silver.notas_fiscais` (dbt-elevato) também têm data de entrada
  da nota, mas apontam para o schema antigo `ciss`/`dwelevato`; o livro de estoque é a
  fonte mais direta e já está no `dwanalitico`.
- `db2.produto_fornecedor.diasentregaprod` e `qtdpedidominimo` estão **zerados** para
  todos os 2.698 SKUs testados. O cadastro de fornecedor não serve.

## 3. Lote mínimo de compra

**Faltava**. **Fonte**: `silver.compras.qtdsolicitada`, menor pedido aceito por SKU.

- **Armadilha**: `silver.compras` repete a linha do item **uma vez por parcela de
  pagamento** (`numsequencia` varia; um pedido em 5 parcelas aparece 5 vezes). O extrator
  faz `distinct on (empresa, pedido, SKU, quantidade, valor unitário)`. Sem isso qualquer
  soma de `qtdatendida` fica multiplicada pelo número de parcelas.
- Cobertura: 3.096 dos 3.278 SKUs têm pedido de compra em três anos. Os demais recebem
  lote 1 e prazo mediano do catálogo, com `lead_time_pedidos = 0` denunciando quais são.
- 654 linhas têm `qtdatendida > qtdsolicitada` (entregas a mais); não afetam o lote mínimo.

## 4. Reserva e disponível

**Faltava**. A exportação só tinha o físico.

**Fonte**: `db2.reserva_sintetico` (`qtdreserva` por empresa, local, SKU e dia), com
histórico desde 2022-02-08 no CD. Como o `estoque_sintetico`, só tem linha em dia com
mudança; o extrator preenche para frente. `qtddisponivel = físico − reserva`.

O staging usa as duas colunas de propósito: o **físico** governa o sinal de demanda (peça
reservada na prateleira ainda diz que houve procura) e o **disponível** governa a
decisão de compra (peça reservada não protege a próxima venda).

## 5. Grade diária completa

`db2.estoque_sintetico` **só tem linha em dia com movimento**. O modelo precisa de todos
os dias: são os dias de venda zero com estoque que puxam a demanda estimada para baixo.
O extrator gera a grade SKU × dia e reconstrói o saldo por soma acumulada de entradas
menos saídas, a partir do último saldo anterior à janela, exatamente como o SQL da
exportação manual (conciliado em 100% das linhas na origem). A coluna `saldo_erp` guarda
o saldo lançado pelo ERP nos dias com movimento, para conferência.

Ajustes de balanço e eventuais quebras de encadeamento entre dias (`qtdsaldoinicial`
diferente do `qtdatualestoque` anterior) entram como entrada ou saída conforme o sinal,
para a identidade fechar por construção.

## 6. Preço de tabela

Não existe no DW para o universo. O staging usa o **preço praticado mediano** de todas as
lojas como rótulo. Não entra na decisão: o motor usa lucro por peça, que é venda líquida
menos custo do dia.

## 7. Devoluções e cancelamentos

`gold.vendas` já traz devolução e cancelamento como linhas com quantidade negativa,
identificadas em `cte`. Em três anos, no universo:

| `cte` | Linhas | Peças |
|---|---|---|
| pedidos | 504.167 | +4.068.075 |
| devolucao_logistica | 27.540 | −155.623 |
| cancelamentos | 23.504 | −291.789 |
| venda_direta | 3.222 | +3.222 |
| devolucao_direta | 230 | −1.463 |

O staging tira as linhas negativas do sinal de demanda (devolução não é demanda negativa)
e as expõe como `VALOR_DEV` / `VALOR_CAN` para a tela de qualidade. Cancelamento antes do
faturamento é demanda que existiu e não se realizou; fica no sinal de propósito.

## 8. O que continua sem fonte

- **Cidade da venda** (`DESCRCIDADE`) e **local de retirada** (`LOCALRETESTOQUE`): não
  existem em `gold.vendas`; vêm nulos. Nenhum dos dois entra no cálculo.
- **Prazo de pagamento ao fornecedor** (`prazo_titulo_dias`): existe em
  `db2.pedido_compra_vcto`, ainda não juntado. Vem nulo; só a tela analítica o mostra.
- **Peso unitário**: não há no cadastro. Zero.
- **Número da nota de entrada**: obtido via `db2.notas.idplanilha`; cobre 100% das entradas.

## 9. Armadilhas de leitura do DW

1. `gold.vendas.idsubproduto` é texto e contém valores não numéricos ("CANCELAMENTO
   CISS"). Filtrar com `~ '^[0-9]+$'` antes do cast.
2. `gold.vendas.idorcamento` é texto: venda direta começa com `VD`, Revest com `R`. O
   staging antigo fazia cast para número e quebraria; passou a tratar como texto.
3. `numeric` do Postgres chega como `Decimal` no Python; o DuckDB infere a precisão pela
   primeira linha e estoura nas seguintes. O extrator converte para float antes de gravar.
4. Uma transferência física gera 3 a 4 linhas em `estoque_analitico` com operações
   diferentes; para totais diários use as colunas agregadas do `estoque_sintetico`.
5. Três linhas de `estoque_analitico` têm data em 2050; a janela do extrator as exclui.
6. O `dwelevato` tem cópias defasadas (estoque parado em 2026-04). Só `dwanalitico`.

## 10. Como rodar

```bash
python scripts/extrair_dw.py            # 3 anos, universo = tudo que o CD movimentou
python scripts/rodar_pipeline.py        # detecta data/fonte_dw e usa base=dw
python scripts/revisao.py               # ~90 conferências independentes
```

Tempos medidos em 2026-09-16 (rede do escritório):

| Tabela | Linhas | Tempo |
|---|---|---|
| raw_produtos | 3.278 | 2 s |
| raw_compras | 44.727 | 8 s |
| raw_ciclo_pagamento | 45.894 | 99 s |
| raw_vendas_todas | 557.970 | ~3 min |
| raw_vendas_ecommerce | 82.538 | ~1 min |
| raw_estoque_diario_erp | 3.537.945 (3.231 SKUs × 1.095 dias) | 88 s |

O pipeline (carga + dbt + motor) leva 13 s. `scripts/revisao.py` passou com 86 verificações
ok, 12 alertas e 0 falhas. Registro por tabela em `data/fonte_dw/_extracao.txt`.

Ainda não há agendamento nem carga incremental: cada rodada apaga e regrava a pasta da
tabela. Para uma carga diária, o caminho natural é `--anos 3` uma vez por semana e, no
meio, só `--so raw_estoque_diario_erp raw_vendas_todas` com `--anos 1`.

## 11. Comparação com a exportação manual (base `exports`)

Mesma data, mesmos parâmetros:

| | `exports` | `dw` |
|---|---|---|
| SKUs planejados | 2.698 | 3.231 |
| Histórico | 365 dias | 1.095 dias |
| Demanda | só e-commerce | todas as lojas |
| Prazo de fornecedor | previsto (mediana 35 d) | realizado (mediana 15 d, p90 35 d) |
| Itens no regime de unidade marginal | 2.127 | 2.301 |
| Capital imobilizado | R$ 4,75 mi | R$ 8,03 mi |
| Prêmio de escassez do capital | 0,00 | 4,30 |

O capital sobe porque a demanda do grupo é dez vezes a do e-commerce: com mais itens
merecendo compra, o caixa passa a ser a restrição ativa e o prêmio de escassez deixa de
ser zero. Venda em dia sem estoque no CD caiu de 11% para 7,7% das peças; o resto é
venda sob encomenda ou atendida por outro local.

## 12. Bugs encontrados e corrigidos no caminho

- A grade de estoque é extraída um ano por vez e o universo usava o fim do chunk: para
  2023 e 2024 o universo ficava vazio. O universo passou a receber a janela global.
- Uma subconsulta correlacionada por linha (para achar `idproduto`) deixava a grade em
  10 minutos por lote de 200 mil linhas; virou um join agregado e o ano inteiro caiu
  para 40 segundos.
- Um SKU que só apareceu no CD em 2026 não tinha grade em 2023–2025, e o motor
  esperava 365 dias por item. A grade passou a cobrir a janela inteira para todo SKU,
  com saldo zero antes da primeira entrada (dias que o modelo já descarta).
