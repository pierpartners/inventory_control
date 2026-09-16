# Mapeamento do DW real — estoque e vendas

Pesquisa exploratória feita em 2026-09-14 nos dois Postgres (`dwelevato` e
`dwanalitico`) para descobrir que tabelas sustentam "estoque de ontem − vendas
de ontem = estoque de hoje" com exatidão, antes de integrar de verdade no
pipeline (`dbt_elevato/models/staging`). **Nada foi integrado ainda** — isto é
só o esqueleto e os exemplos que embasam a integração futura.

Consultas rodadas com `scripts/consultar_dw.py`. Todos os exemplos abaixo são
reprodutíveis com esse script.


## 1. Qual conexão usar

Descobri que as duas instâncias hospedam variações do mesmo modelo (ERP CISS),
em arquitetura tipo bronze/prata/ouro:

| Instância | Schemas relevantes | Acesso do usuário `renan.pier` | Frescor |
|---|---|---|---|
| `dwelevato` | `ciss` (bruto, ERP) | **sem USAGE** — permission denied | — |
| `dwelevato` | `ciss1`, `db2dbez` (réplicas do bruto) | com USAGE | `db2dbez.estoque_sintetico` para 12/5/2195: última data **2026-04-02** (~5 meses atrasado) |
| `dwelevato` | `gold.estoque` (snapshot analítico) | com USAGE | só **agosto/2024** — carga única, não é atualizado |
| `dwanalitico` | `db2`, `gold`, `silver` | com USAGE, tudo liberado | `db2.estoque_sintetico` → última data **2026-09-14** (hoje) |

**Recomendação: usar `dwanalitico` (schemas `db2`, `gold`, `silver`) como fonte
única.** É o mais completo, o único onde temos acesso a tudo que precisamos, e
o único atualizado até hoje. `dwelevato` só seria necessário se algum dia
precisarmos de algo que exista unicamente lá (schemas de apps: `chamados`,
`hub_marketplace`, `atas_grupo_elevato` etc. — fora do escopo de estoque).


## 2. Esqueleto de tabelas (grão, chaves, papel)

### Fato — posição diária de estoque: `db2.estoque_sintetico`
Uma linha por **(idempresa, idlocalestoque, idsubproduto, dtmovimento)**, só
nos dias em que algo se moveu (dias sem movimento não geram linha — o saldo
do dia anterior vale até a próxima linha existente).

Colunas centrais:
- `qtdsaldoinicial`, `qtdatualestoque` — saldo no início e no fim do dia
- `qtdentraestoque`, `qtdsaidaestoque` — total que entrou/saiu naquele dia
  (agregado, todas as causas)
- `qtdcompra`, `qtdvenda`, `qtddevcompra`, `qtddevvenda`,
  `qtdentradatransfer`, `qtdsaidatransfer`, `qtdbonificacao`,
  `qtdajustebalanco` — **subcategorias parciais** dos totais acima (ver §4 —
  não cobrem 100% dos casos)
- `valcustomedio` — custo médio do dia

### Fato — movimento transacional: `db2.estoque_analitico`
Grão bem mais fino: uma linha por **documento/operação** que mexeu estoque
(não por dia). Chave útil: `(idempresa, idlocalestoque, idsubproduto,
dtmovimento, idoperacao, numpedido)`. Tem `idoperacao` (código) mas não a
descrição — junta com `db2.operacao_interna` para saber o que cada código
significa e se ele de fato mexe físico (`flagmovprodutos`,
`flagcalculogiro`, `flagmovfiscal`).

Somando `qtdproduto` por dia (com sinal certo por tipo de operação) esse é o
jeito de **provar** o que compõe `qtdentraestoque`/`qtdsaidaestoque` do fato
diário — é o próximo passo antes de confiar cegamente no agregado.

### Fato — vendas (documento fiscal/pedido): `gold.vendas` (curado) ou `public.vendas`/`db2`(?) (bruto)
Grão: uma linha por **item de pedido/nota** (`idorcamento`/`numsequencia`).
`gold.vendas` já vem com margem calculada e nomes em minúsculo; `public.vendas`
no `dwelevato` é a fonte crua (bate exatamente com o CSV que já usamos em
`data/fonte_verdadeira/vendas_todas_3anos.csv`). Chave para juntar com
estoque: `(idempresa, idsubproduto, data)`. **Cuidado:** `IDEMPRESA` aqui é a
empresa que vendeu — não necessariamente a mesma que baixou o estoque
fisicamente (ver §5).

### Dimensão — produto: `db2.produto` (+ `ciss1.produto` no dwelevato)
Cadastro por `idsubproduto`: divisão/seção/grupo/subgrupo, fabricante,
modelo. É de onde vem `raw_produtos` (bate com
`data/fonte_verdadeira/produtos.csv`, que parece ser um join de produto +
divisão/seção/grupo/clifor/grupo econômico — ainda não localizei a tabela de
`clifor`/`grupo_economico` usada nesse join específico).

### Dimensão — local de estoque: `ciss1.estoque_cadastro_local` (dwelevato)
`idlocalestoque → descrlocal`, e `idempresabaixaest` (empresa "dona" daquele
local). Um `idempresabaixaest` pode ter vários `idlocalestoque` (loja normal,
loja-avaria, loja-mostruário, loja-em-balanço etc. — só o principal tem
`flagdisponvenda='T'`). Não achei essa tabela ainda em `dwanalitico`; se for
usar só o `dwanalitico`, precisamos confirmar se existe lá ou trazer via
`dwelevato`.

### Dimensão — tipo de operação: `db2.operacao_interna`
`idoperacao → descroperacao` + flags (`flagmovprodutos`, `flagmovfiscal`,
`flagcalculogiro`, `tipomovimento`). Essencial para classificar cada linha de
`estoque_analitico` em "é venda de verdade", "é transferência", "é
devolução" etc.


## 3. O que bate com exatidão (validado)

A identidade contábil do dia bate, sempre, nas amostras testadas:

```
qtdatualestoque = qtdsaldoinicial + qtdentraestoque - qtdsaidaestoque + qtdajustebalanco
```

E o encadeamento entre dias também bate — o saldo final de um dia é o saldo
inicial da próxima linha existente (mesmo pulando dias sem movimento, ex.
domingos):

```sql
select dtmovimento, qtdsaldoinicial, qtdentraestoque, qtdsaidaestoque, qtdatualestoque
from db2.estoque_sintetico
where idempresa=26 and idlocalestoque=124 and idsubproduto=2195
  and dtmovimento between '2025-11-25' and '2025-12-10'
order by dtmovimento;
```

```
dtmovimento  saldoinicial  entra  saida  atual
2025-11-26   103           0      3      100     -- transferência p/ loja
2025-11-27   100           1      0      101     -- transferência recebida
2025-11-28   101           0      0      101     -- (venda gerencial registrada, mas 0 saída física)
2025-11-29   101           0      0      101
2025-12-01   101           0      1      100     -- (pula 11-30, domingo: 101 carrega certo)
2025-12-02   100           0      0      100
2025-12-04   100           0      4      96      -- (pula 12-03: 100 carrega certo)
2025-12-09   96            0      3      93
```

**Conclusão do §3: para "estoque bate", o fato diário (`estoque_sintetico`)
já é confiável como livro-razão.** O que NÃO é trivial é decompor
`qtdsaidaestoque` em "quanto foi venda de verdade" — é o achado do §4.


## 4. O que NÃO bate direto: `qtdvenda` ≠ baixa física do dia

Achado central da pesquisa. Peguei um SKU numa loja normal (empresa 12 —
Nilo, local 5, sku 2195) e um dia onde `qtdvenda=1` mas `qtdsaidaestoque=0`:

```sql
select a.dtmovimento, a.idoperacao, o.descroperacao, a.qtdproduto
from db2.estoque_analitico a
left join db2.operacao_interna o on o.idoperacao = a.idoperacao
where a.idempresa=12 and a.idlocalestoque=5 and a.idsubproduto=2195
  and a.dtmovimento = '2026-02-25';
```

```
dtmovimento  idoperacao  descroperacao              qtdproduto
2026-02-25   3000        Pedido de Venda Gerencial  1.000
```

Olhando os 12 meses anteriores desse mesmo SKU/loja, os códigos de operação
que aparecem são:

```
idoperacao  descroperacao                              flagmovprodutos  n   qtd
3001        Vendas de Mercadorias Faturamento Logística      T          85  107   <- baixa física real da venda
85          Entrada Avulsa Automática de Outra Empresa       T          77   97   <- reposição automática vinda do CD
43          TRANSF REC AUTOMATICA ENTRE EMPRESAS             T          76   96
980         TRANSF RECEB ENTRE LOJAS                         T          10   12
3000        Pedido de Venda Gerencial                        T           4    4   <- só registra o pedido, não decrementa nesse dia
969         Devolução de Venda com Nota Própria - Elevato    T           4    5
9980        TRANSF EMIT ENTRE LOJAS                          T           3    4
```

**Interpretação:** o ERP separa o *pedido de venda* (`3000`, cai na coluna
`qtdvenda` do fato diário) do *faturamento logístico* (`3001`, que é quem de
fato tira a peça do estoque físico — mas cai só no agregado genérico
`qtdsaidaestoque`, sem coluna própria em `estoque_sintetico`). Os dois quase
sempre acontecem em dias próximos mas não necessariamente no mesmo dia.

**Implicação prática:** para reconciliar "estoque bate com vendas" com
exatidão, **não dá para usar a coluna `qtdvenda` de `estoque_sintetico`** como
proxy de baixa física. É preciso somar `estoque_analitico` filtrando pelos
`idoperacao` de faturamento/venda real (ex. `3001`, "Venda de Mercadoria ECF",
"Vendas de Mercadorias - Tele-vendas" — a lista completa de códigos ainda
precisa ser levantada e classificada com `operacao_interna`).


## 5. CD × loja: quem vende e quem só transfere

A empresa 26 (CD, Gravataí — `idlocalestoque=124`) **nunca aparece em
`gold.vendas`/`public.vendas`** — zero linhas. Isso bate com o comentário que
já existia em `dbt_elevato/models/sources.yml`: o estoque diário é do CD, e
só o e-commerce (empresa 33) consome ~17% da baixa dele diretamente. O resto
da baixa do CD é **transferência para lojas** (`qtdsaidatransfer`), não venda:

```sql
select dtmovimento, qtdsaidaestoque, qtdsaidatransfer, qtdvenda
from db2.estoque_sintetico
where idempresa=26 and idlocalestoque=124 and idsubproduto=2195
  and dtmovimento='2025-12-04';
-- saida=4, saida_transfer=4, venda=0  →  toda a baixa foi transferência p/ loja
```

Ou seja: **o mesmo teste de reconciliação não pode usar a mesma fórmula no CD
e na loja.** No CD, `saída física ≈ transferências + baixa e-commerce`. Na
loja, `saída física ≈ faturamento (idoperacao 3001-like) + devoluções −
transferências recebidas`.


## 6.1. O dado no `dwanalitico` basta? (checagem feita em 2026-09-14)

Perguntas que precisavam de resposta antes de dizer "sim, basta":

- **Dimensão de local existe lá?** Sim — `db2.estoque_cadastro_local` existe
  em `dwanalitico`. Não precisamos do `dwelevato` pra isso.
- **Profundidade histórica é suficiente?** Sim, e sobra: `db2.estoque_sintetico`
  vai de **2020-07-16** até hoje (2.7M linhas), `db2.estoque_analitico` de
  **2020-07-16** até hoje (7.6M linhas), `gold.vendas` de **2015-01-02** até
  hoje (1.9M linhas). A janela que o modelo usa hoje é de 365 dias
  (`janela_estimacao_dias`) — tem quase 5x mais histórico do que precisa.
- **Cancelamento/devolução de venda é rastreável?** Sim, dos dois lados:
  em `gold.vendas` a devolução/cancelamento já vem como linha com
  `qtdproduto` **negativo** (não precisa calcular por fora); no estoque,
  aparece como operação própria — ex. achei `idoperacao=500` =
  "CANCELAMENTO DE ENTREGA NA LOGISTICA" estornando exatamente a quantidade
  cancelada, no mesmo dia. Os dois lados batem.
- **O catálogo cobre todos os SKUs que já tiveram movimento?** Sim, 100% —
  testei: 59.658 SKUs distintos apareceram em `estoque_sintetico`, e as
  59.658 estão em `db2.produto_grade` (que tem 163k SKUs no total —
  o resto nunca teve estoque, provavelmente descontinuado antes do
  histórico começar). Zero SKU órfão.
- Achado colateral, irrelevante: 3 linhas (de 7,6 milhões) em
  `estoque_analitico` têm `dtmovimento` quebrado (2050) — ruído desprezível,
  só filtrar com `dtmovimento <= current_date`.

**Veredito: o dado que já está no DW é suficiente para rastrear venda e
estoque com exatidão.** Não falta nenhuma tabela nova. O que falta é
**trabalho de classificação** sobre o dado que já temos — mapear os ~120
códigos de `idoperacao` em categorias (venda-fatura, pedido gerencial,
transferência, devolução, ajuste, avaria, bonificação) pra poder somar
`estoque_analitico` corretamente. Isso é o item 1 do §6 abaixo.

Ressalva operacional (não é dado faltante): a conexão com `dwanalitico`
(host em `DWANALITICO_HOST`, no `.env`) deu timeout de conexão duas vezes nesta sessão, em
consultas de 2-3s. Provavelmente rede/VPN, não o Postgres em si — vale
monitorar estabilidade antes de depender disso num pipeline agendado.


## 6.2. Escala real do trabalho de classificação de `idoperacao`

Correção do que eu tinha estimado antes ("~120 códigos"): esse número era só
os códigos usados por *uma empresa* em 12 meses. Olhando a base toda:

- `db2.operacao_interna` tem **1.098 códigos cadastrados** no total.
- Só **643** desses aparecem de fato em algum movimento de estoque
  (`db2.estoque_analitico`, 7.625.106 linhas ao todo).
- A coluna `tipomovimento` (que poderia servir de classificação pronta) **não
  ajuda**: 83% dos 643 códigos usados (79% das linhas) estão com esse campo
  em branco. E quando está preenchido, mistura coisas diferentes — o código
  3000 ("Pedido de Venda Gerencial", que NÃO é baixa física) está marcado
  como `V` (venda), igual ao código que É a baixa física de verdade. Não dá
  pra confiar nessa coluna sem validar caso a caso.
- **Mas a concentração é enorme**: os 25 códigos mais frequentes já somam
  ~98,4% de todas as linhas. Não é preciso classificar os 643 com cuidado —
  are uns 25-30 que importam.

Top códigos por volume, já agrupados por família (nomes conforme
`descroperacao`):

| Família | `idoperacao` (exemplos) | Linhas | % do total |
|---|---|---|---|
| Transferência (CD↔loja, empresa↔empresa) | 1045, 85, 1043, 43, 9980, 980, 88, 1088 | ~4.167.000 | 54,6% |
| Pedido de venda (registro — **não** é baixa física) | 3000 | 1.152.277 | 15,1% |
| Venda real (baixa física — faturamento/ECF/tele-venda) | 3001, 1001, 1300, 1202, 1203 | ~1.250.000 | 16,4% |
| Produção interna | 1081, 81 | ~221.600 | 2,9% |
| Ajuste manual avulso | 1080, 80 | ~232.900 | 3,1% |
| Compra (entrada de fornecedor) | 1, 196 | ~190.400 | 2,5% |
| Balanço de estoque | 2000 | 142.517 | 1,9% |
| Cancelamento/devolução de venda | 500, 969 | ~117.700 | 1,5% |
| Outros (frete, uso/consumo) | 60, 46 | ~25.500 | 0,3% |

**Achado de escala**: mais da metade (54,6%) de tudo que mexe estoque nesta
base é **transferência interna**, não venda. Se a "demanda" do modelo fosse
calculada em cima da saída bruta de estoque sem filtrar por família, mais da
metade do sinal seria ruído logístico (CD mandando pra loja), não cliente
comprando.

O par 3000/3001 sozinho é ~30% do volume da base inteira, com contagens
quase idênticas (1.152.277 vs 1.074.981) — forte indício de que são o mesmo
evento comercial em dois estágios (pedido → fatura), como já demonstrado no
§4.

**Trabalho restante, escopado**: classificar com cuidado os ~25-30 códigos
da tabela acima (cobre 98,4%); os ~600 códigos da cauda longa (1,6% do
volume) podem ficar num bucket "outros/baixo impacto" e não bloqueiam a
integração.


## 6.3. Cuidado: uma transferência física gera várias linhas em `estoque_analitico`

Achado ao tentar confirmar se as colunas `idlocalestoqueorigemtransf` /
`idempresaorigemtransf` (que dizem de onde veio uma transferência) estavam
preenchidas. Peguei uma transferência real do CD (empresa 26, local 124)
pra uma loja, 1 unidade, no mesmo dia (2026-09-14):

```
idempresa  idlocalestoque  idoperacao  descroperacao                          origem_local  origem_empresa
26         124             1043        TRANSF EMIT AUTOMATICA ENTRE EMPRESAS  (vazio)       (vazio)
26         124             1045        Transferência Automática Entre Empresas (vazio)      (vazio)
32         177             43          TRANSF REC AUTOMATICA ENTRE EMPRESAS   (vazio)        (vazio)
32         177             85          Entrada Avulsa Automática de Outra Empresa  124        26
```

**Uma única movimentação física de 1 peça gera 4 linhas**, em 4 códigos de
operação diferentes (dois do lado de quem emite, dois do lado de quem
recebe) — só uma delas (`85`) preenche a origem. Isso quer dizer que **somar
`qtdproduto` de todo código "parecido com transferência" em
`estoque_analitico` conta a mesma peça 3-4 vezes.**

**Implicação prática**: para o total diário de transferência, usar sempre
`db2.estoque_sintetico.qtdentradatransfer` / `qtdsaidatransfer` — essa
coluna já vem agregada corretamente pelo ERP (é por isso que a identidade do
§3 bate). `estoque_analitico` serve para *auditoria* (explicar por que o
saldo de um dia específico mudou), não para *recalcular* o total somando
códigos — o mesmo cuidado provavelmente vale para as outras famílias
(compra, produção etc.), não só transferência.

Isso também responde a pergunta "a coluna `descroperacao` já é a
classificação que precisamos?" — **não sozinha**. Ela dá o nome de cada
código (matéria-prima necessária), mas não avisa quando vários códigos são
o mesmo evento físico visto de ângulos contábeis diferentes, nem quando dois
nomes parecidos (`3000` vs `3001`, ambos "venda") têm comportamento
diferente. A classificação por nome tem que vir acompanhada de checagem
empírica (comparar com o agregado diário), como fizemos até aqui.


## 6.4. Virada: `gold`/`silver` já resolvem a separação pedido × baixa física — não precisa classificar `idoperacao`

Depois de listar TODAS as tabelas de `gold` e `silver` no `dwanalitico` (não só
as que eu já tinha espiado), achei que o trabalho de classificação dos §6.2 e
§6.3 **já foi feito por quem construiu essas camadas**. Existem tabelas
dedicadas, uma pra cada estágio do fluxo comercial:

| Tabela | Papel | Mexe estoque? |
|---|---|---|
| `silver.pedidos` / `gold.vendas` | Pedido comercial (= `idoperacao 3000`-like) | Não |
| `silver.faturamento` / `gold.faturamento` | Nota fiscal emitida, tem `numnota` (= `idoperacao 3001`-like) | **Sim — é a baixa física real** |
| `silver.devolucao_logistica` | Devolução física de venda já faturada | Sim (entrada) |
| `silver.cancelamentos` | Pedido cancelado | Só se já tinha sido faturado (aí vira devolução) |

Validei reproduzindo os dois casos que já tinha usado como prova:

1. **Caso "pedido sem baixa" (empresa 12, sku 2195, 2026-02-25)**: `silver.pedidos`
   tem 2 linhas de 1 unidade nesse dia; `silver.faturamento` tem **zero**
   linhas. Bate exatamente com o que eu tinha visto manualmente em
   `estoque_analitico` (só o código 3000 disparou, sem 3001).
2. **Caso "cancelado antes de faturar" (empresa 9, sku 1018898, 2026-09-14)**:
   `silver.cancelamentos` tem 1 linha (`qtdproduto=-1`, motivo "DESISTÊNCIA");
   `silver.pedidos` e `silver.faturamento` **não têm nenhuma linha** nesse
   dia — a venda nunca chegou a existir fisicamente, então não precisa de
   estorno em lugar nenhum.
3. **Faturamento mensal bate com a saída de estoque**: somando
   `silver.faturamento.qtdproduto` por mês pro mesmo sku/loja, os números
   batem exatamente com o `qtdsaidaestoque` mensal de `estoque_sintetico`
   em vários meses (ex. set/2025: 14 vs 14; out/2025: 6 vs 6; jan/2026: 12
   vs 12), com pequenas diferenças nos outros meses — esperado, porque
   `qtdsaidaestoque` inclui também avaria/ajuste, não só venda.

**Conclusão: os §6.2/6.3 (mapear ~25-30 códigos de `idoperacao`) deixam de
ser necessários para chegar em "vendas".** `silver.faturamento` já é
exatamente esse número, pronto, sem precisar tocar em
`estoque_analitico`/`operacao_interna`. Essa investigação continuaria
relevante só se um dia precisarmos auditar transferência em detalhe — não
achei tabela dedicada de transferência em `gold`/`silver`, só em
`db2.estoque_sintetico` (`qtdentradatransfer`/`qtdsaidatransfer`, já
confiável) — mas isso não bloqueia nada hoje.

**Modelo final recomendado, atualizado:**
- `stg_vendas` ← `silver.faturamento` (venda real, física)
- `stg_devolucao` ← `silver.devolucao_logistica` (se for preciso separar)
- `stg_estoque_diario` ← `db2.estoque_sintetico` (ledger completo, valida a
  identidade do §3) ou `silver.estoque_disponivel_historico` (mais leve, já
  tem `flag_ruptura`)
- `silver.pedidos`/`gold.vendas` e `silver.cancelamentos` ficam de fora do
  cálculo de estoque — são sinal comercial, não movimento físico.


## 6.5. Transferência CD↔loja, recebimento no CD e compras — o que já dá pra rastrear

Pergunta testada: já rastreamos transferência entre CD e lojas, o que foi
recebido no CD, e as compras? Resposta com evidência:

**Transferência — parcial.** O total diário (`db2.estoque_sintetico.
qtdentradatransfer`/`qtdsaidatransfer`) é confiável como número agregado.
Mas ao testar o CD (empresa 26, local 124) mais a fundo, achei uma
sobreposição que **não** existia no exemplo da loja: no dia 2026-08-26, a
entrada total foi 1 unidade, mas tanto `qtddevvenda` quanto
`qtdentradatransfer` mostram 1 — as duas colunas "reivindicam" a mesma
unidade. Isso **invalida a afirmação anterior** (§6.2/6.4) de que a
decomposição de entrada em `estoque_sintetico` seria limpa — aquilo só
valeu por coincidência no exemplo simples da loja (onde só havia um tipo de
entrada por vez). No CD, com mais tipos de entrada acontecendo,
as colunas nomeadas se sobrepõem. **Total geral continua batendo — a
decomposição por categoria, não.**

**Recebido no CD — sim, com uma pegada importante.** Testei um pedido real
(`idpedido=162959`): feito em 2026-07-23, previsão de chegada 2026-08-22,
mas o estoque só registrou a entrada em **2026-08-06** — data diferente das
duas outras. `silver.compras` **não tem a data real de chegada**, só data do
pedido e previsão. A data de chegada de verdade só existe no ledger de
estoque (`estoque_sintetico.qtdcompra` ou `estoque_analitico` com
`idoperacao=1`, "Compra de Mercadoria"). Nesse caso específico o valor bateu
exatamente (300 unidades, mesma quantidade do pedido) — `qtdcompra` parece
mais confiável que `qtddevvenda`/`qtdentradatransfer` para esse propósito
específico, mas é uma amostra pequena.

**Compras — sim, mas `silver.compras` duplica linha por parcela de
pagamento.** Achado: o pedido `165738` (5 parcelas) aparece **5 vezes
idênticas** na tabela, cada uma repetindo a mesma `qtdsolicitada`/
`qtdatendida`; o pedido `162959` (3 parcelas) aparece 3 vezes. Somar
`qtdatendida` direto **infla o total pelo número de parcelas**. Para usar
certo: `select distinct on (idpedido, idsubproduto) ...` ou filtrar
`numsequencia = 1` (a coluna que varia por parcela).

**Resumo do estado real**: temos os três rastreios, mas nenhum dos três está
pronto pra usar direto sem tratamento — cada um tem uma armadilha específica
que só apareceu testando com dado real, não visível só olhando o schema.


## 6.6. Foco em vendas online: o CD não é exclusivo do e-commerce

Mudança de escopo pedida: a análise vai focar em compras/vendas **online**,
mas o estoque é compartilhado entre e-commerce e distribuição para lojas
físicas. Testei três perguntas:

**O e-commerce tem depósito próprio, separado do CD.** Descobri um local
dedicado: `idlocalestoque=185`, `descrlocal='E-COMMERCE'`,
`idempresaproprietaria=33`. O fluxo real é CD (124, "GRAVATAI CD") →
transferência automática → depósito e-commerce (185) → só aí a baixa física
de venda (`idoperacao=3001`) acontece de fato. O "Pedido de Venda Gerencial"
(3000) registrado em 124 é só o pedido comercial, não a baixa. Confirmei
contando operações por local pra `idempresa=33`: em 124, quase só código
3000 (4.991 linhas); em 185, o par 3001/85/43 (transferência recebida +
faturamento) domina, quase 1-pra-1.

**Não dá pra tratar toda transferência do CD como ruído a descartar.** Uma
fatia relevante da saída de transferência do CD **é** o abastecimento do
depósito de e-commerce — é o próprio sinal que queremos medir, não
distribuição pra loja física. A separação certa é por **empresa** (33 =
e-commerce), não por local: `silver.faturamento` filtrado em `idempresa=33`
já dá a venda online líquida sem precisar decidir se uma transferência
específica era pra loja ou pro e-commerce.

**A tabela de comparação funciona, mas precisa da reposição na fórmula.**
Isolei certo (depósito 185 + `idempresa=33`) e mesmo assim a comparação
simples (`diferença = -vendas`) não bate na maioria dos dias — pelo mesmo
motivo já visto na loja (§ anterior): esse depósito também opera com
estoque quase zerado e reposição automática quase diária, **não é
característica exclusiva do CD compartilhado**. A fórmula que bate de
verdade, validada 100% num teste de 15 dias:

```
Diferença de estoque = − Vendas (dia anterior) + Entrada/reposição (dia anterior)
```

**Modelo recomendado para o foco em vendas online:**
- Vendas online = `silver.faturamento` filtrado `idempresa=33` (não precisa
  saber de qual local saiu)
- Estoque relevante = `db2.estoque_sintetico` filtrado
  `idempresa=33 and idlocalestoque=185` (o depósito dedicado — já valida a
  identidade completa)
- Comparação (Comparação) = conferir a identidade completa
  (`saldo_inicial + entrada − saída = saldo_final`), não só vendas vs
  diferença — inclui a coluna de reposição/entrada como parte do "bate"


## 6.7. Anúncio retirado do ar quando zera, e primeira tabela de conferência validada

**Verificação 1 — anúncio some quando o estoque no depósito 185 zera?** Sim,
quase perfeitamente. Nos últimos 180 dias, 89,6% das linhas do depósito
e-commerce começam o dia já com estoque zero (confirma o padrão de operação
enxuta), mas só 19 linhas ficaram zeradas **o dia inteiro sem nenhuma
reposição**. Dessas 19, só **1** teve venda faturada mesmo assim (SKU
1035537, 2026-04-13, venda normal — `idoperacao=3001` — não foi pré-venda,
o saldo foi a -2). Isso é ~1 em 15.766 linhas: exceção isolada, não padrão.
O site retira o produto do ar quando some o estoque.

**Verificação 2 — tabela de conferência (venda × variação de estoque),
formato final.** Rodei pra 3 SKUs de peso do e-commerce (depósito 185,
últimos 30 dias, 90 linhas). Usando a fórmula completa
(`diferença = entrada − venda`, não só `-venda`):

- **86% das linhas comparáveis bateram exatamente** (75 de 87, excluindo o
  primeiro dia de cada SKU, que não tem "dia anterior" pra comparar — isso
  conta como falso "não bate" só por estar na borda da janela).
- Os ~14% que não bateram são gaps pequenos (1 a 4 unidades). Uma pista já
  aparece nos dados: no dia com `vendas_dia_anterior = -1` (devolução
  líquida), não houve entrada correspondente — sugere que devolução física
  às vezes retorna para outro local (provavelmente o CD 124), não para o
  depósito 185, então não aparece na conta local. Fica como próximo item a
  investigar (não bloqueia o uso da tabela como está).

Consulta usada (adaptável pra qualquer lista de SKUs): junta
`db2.estoque_sintetico` (estoque + entrada, filtrado
`idempresa=33 and idlocalestoque=185`) com `silver.faturamento` (vendas,
filtrado `idempresa=33`) por SKU e data, com "estoque neste dia" resolvido
como o último saldo conhecido até aquela data (preenche os dias sem
movimento). Arquivo de exemplo com o resultado completo entregue ao usuário
(CSV com as 90 linhas).


## 6. Próximos passos (ainda não feitos)

1. Levantar a lista completa de `idoperacao` (via `db2.operacao_interna`,
   ~120 códigos distintos vistos) e classificar cada um em categorias:
   venda-fatura, transferência, devolução, ajuste/balanço, avaria,
   bonificação, produção.
2. Com essa classificação, somar `estoque_analitico` por dia/categoria e
   provar que bate 100% com os totais de `estoque_sintetico`
   (`qtdentraestoque`/`qtdsaidaestoque`) numa amostra grande de SKUs — não só
   os dois exemplos manuais deste documento.
3. Confirmar se `ciss1.estoque_cadastro_local` (mapa de `idlocalestoque`)
   também existe em `dwanalitico`, ou se vamos precisar das duas conexões.
4. Só depois disso desenhar os modelos de staging novos (`stg_estoque_diario`,
   `stg_vendas`) apontando pra `dwanalitico`, no lugar dos CSVs de
   `data/fonte_verdadeira/`.
