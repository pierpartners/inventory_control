/* =====================================================================
   GLOSSARIO - a definicao de cada rotulo do sistema, num lugar so.

   Cada chave e o rotulo normalizado: minusculo, sem acento, sem pontuacao.
   `N.pinar()` percorre cabecalho de tabela, titulo de painel, rotulo de
   metrica, pergunta de resposta direta e formula, normaliza o texto e pendura
   o "!" onde encontra verbete.

   Por que aqui e nao no HTML de cada tela: o mesmo rotulo aparece em varias
   paginas - "Classe" esta em quatro, "Investimento" em tres, "Cobertura" em
   duas -, e boa parte das tabelas e montada por JS em tempo de execucao. Uma
   definicao por arquivo divergiria na primeira mudanca de metodologia.

   Regra de escrita: dizer O QUE E o numero e DE ONDE ele vem, na ordem. Onde
   houver armadilha de leitura - prazo combinado contra realizado, demanda
   corrigida contra venda registrada -, a armadilha vai no verbete, porque e
   ali que o leitor esta olhando quando a duvida aparece.
   ===================================================================== */
(function (raiz) {
  "use strict";
  var G = {

  /* ---------------------------------------------------- identificacao */
  "produto": "Nome do produto no cadastro, com o código (SKU) e a família embaixo. Clique na linha para abrir o dossiê completo do item.",
  "item": "Nome do produto no cadastro, com o código (SKU) e a família embaixo. Clique na linha para abrir o dossiê completo do item.",
  "classe": "Classificação ABC × XYZ. ABC ordena pelo lucro que o item gera (A = os que concentram 80% do lucro). XYZ mede a regularidade da demanda (X = regular, Z = errática). AZ, por exemplo, é um item que dá muito lucro e vende de forma imprevisível.",
  "familia": "Seção do cadastro do ERP a que o produto pertence. Serve de filtro e de agrupamento; não entra em nenhuma conta.",
  "fornecedor": "Quem vendeu a mercadoria, como consta no pedido de compra do ERP.",
  "unid no": "Número da unidade dentro do item: a 1ª peça, a 2ª, a 3ª. O modelo avalia peça por peça, e cada linha aqui é uma peça.",
  "regime": "Como a política deste item foi calculada. <b>EOQ + normal</b> para item de giro relevante: lote econômico com estoque de segurança pela curva normal. <b>Unidade marginal</b> para item caro que vende pouco: o modelo testa peça por peça se vale carregar a próxima unidade, porque com demanda de poucas peças a curva normal não descreve nada.",

  /* -------------------------------------------------------- dinheiro */
  "custo unitario": "O que uma peça custa hoje, do custo médio de estoque do ERP. O modelo usa o último lançamento de um dia COM estoque: quando o saldo vai a zero o ERP zera o custo médio junto e lança R$ 1,00, e um lançamento desses contaminaria toda a economia do item.",
  "custo peca": "O que uma peça custa hoje, do custo médio de estoque do ERP — o último lançamento de um dia com estoque em casa.",
  "custo un": "Custo médio do estoque no dia daquela venda. Varia ao longo do tempo, e é por isso que o lucro de uma venda antiga não usa o custo de hoje.",
  "custa": "O que a peça custa: o custo unitário do item.",
  "preco peca": "Preço líquido médio praticado por peça na janela de estimação — receita líquida dividida por peças vendidas. É preço realizado, não preço de tabela.",
  "preco un": "Preço unitário daquela venda, como saiu na nota.",
  "preco medio praticado": "Receita líquida dividida por peças vendidas, em todo o histórico do item. É o preço que o mercado de fato pagou, não o de tabela.",
  "lucro peca": "Preço líquido praticado menos o custo de hoje. É esta margem que decide a compra: dividir por um custo e multiplicar por outro faria a nota errar nos dois sentidos quando o custo do item se move.",
  "lucro por peca": "Preço líquido praticado menos o custo de hoje — a margem que entra na decisão de compra.",
  "lucro": "Receita líquida menos o custo da mercadoria vendida. Não é a margem do ERP: aquele campo carrega rateio de despesa e por isso não serve para decidir compra.",
  "lucro observado": "O lucro que as vendas passadas registraram, com o custo do dia de cada venda. Difere da margem que decide a compra quando o custo do item se moveu.",
  "margem": "Lucro por peça dividido pelo preço líquido por peça. Base líquida, a mesma do lucro por peça ao lado.",
  "cmv": "Custo da mercadoria vendida: peças × custo médio do estoque no dia da venda.",
  "custo do vendido cmv": "Soma do custo da mercadoria vendida em todo o histórico: peças × custo médio do dia de cada venda.",
  "custo medio do vendido": "CMV dividido pelas peças vendidas. É a média dos custos históricos, e por isso pode ficar longe do custo de hoje num item cujo preço de compra mudou.",
  "custo que o modelo usa": "O custo unitário que entra na decisão de compra: o último lançamento de um dia com estoque. Se ele estiver longe do custo médio do vendido, o preço do item se moveu.",
  "custo lancado": "O custo médio que o ERP registrou naquele dia. A coluna do estoque ao lado é o que denuncia lançamento inválido: em dia de saldo zero o ERP reseta o custo médio para R$ 1,00.",
  "receita liquida": "Faturamento menos impostos e frete — o dinheiro que de fato fica.",
  "receita liq": "Faturamento da linha menos impostos e frete.",
  "valor": "Valor em reais.",
  "valor un": "Valor unitário da linha do pedido de compra, como consta na nota.",
  "valor ao custo": "Peças × custo unitário do item.",
  "total": "Valor total da linha.",
  "investimento": "O que a compra sugerida custa: peças × custo unitário.",
  "investimento do modelo": "O que a compra sugerida pela plataforma custaria: peças × custo unitário do dia.",
  "capital": "Dinheiro imobilizado em estoque neste item: estoque médio × custo unitário.",
  "capital imobilizado": "Dinheiro parado em estoque: estoque médio × custo unitário, somado na seleção.",
  "capital parado": "Dinheiro imobilizado neste item: estoque médio × custo unitário. É o que se deixa de aplicar em outra coisa enquanto a peça espera na prateleira.",
  "capital preso": "Dinheiro imobilizado em estoque neste item ao longo do ano.",
  "capital em estoque": "Valor total do estoque, ao custo, no dia da execução.",
  "dinheiro parado": "As peças que sobraram, ao custo delas. É quanto de capital ficou imobilizado sem que o horizonte que justificou a compra tivesse pedido.",
  "dinheiro parado sem necessidade": "O custo das peças compradas que a demanda do horizonte não pediu. Não é o estoque inteiro — é só a parte da compra que o próprio prazo que a justificou não consumiu.",
  "pagamento": "Forma e prazo de pagamento do pedido, do ciclo de pagamento do ERP. Prazo de fornecedor financia estoque, e hoje isso não entra no custo de capital do modelo.",

  /* ------------------------------------------------------- quantidade */
  "pecas": "Número de peças.",
  "unidades": "Número de peças que o modelo avaliou uma a uma neste item.",
  "produtos": "Número de produtos distintos (SKUs).",
  "vendidas": "Peças vendidas no histórico do item.",
  "pecas vendidas": "Peças vendidas no período.",
  "pecas ano": "Demanda anual estimada: demanda média por dia × 365.",
  "comprar": "Peças que o modelo manda comprar agora, depois de avaliar peça por peça se a próxima unidade se paga e de respeitar o caixa do ciclo.",
  "comprar agora": "Peças a comprar neste ciclo e o que isso custa. Vazio significa que a próxima peça não se pagaria — não que o item esteja bem de estoque.",
  "leva": "Quantas peças deste produto entraram no pedido deste ciclo.",
  "lote": "Tamanho do lote de compra depois de arredondar para o lote mínimo do fornecedor.",
  "lote de compra": "Quantidade por pedido, já arredondada para o múltiplo mínimo que o fornecedor aceita.",
  "lote minimo compra": "O menor pedido que o fornecedor de fato aceitou no histórico. Usar a mediana do pedido inflaria o lote e obrigaria a comprar mais do que se precisa.",
  "pedido atendido": "Quanto foi pedido e quanto o fornecedor de fato atendeu naquela linha.",
  "pedido": "Número do pedido no ERP.",
  "entra na peca no": "A partir de qual peça da fila global este produto começa a levar unidades. Mostra a ordem em que o caixa se espalha.",
  "pecas que se pagariam": "Peças que passariam no teste marginal se houvesse caixa. É a fila que ficou esperando o próximo ciclo.",
  "custariam": "O que as peças que ficaram de fora custariam, se o caixa alcançasse.",
  "pecas compradas": "Total de peças que entraram no pedido.",

  /* -------------------------------------------------------- demanda */
  "demanda dia": "Peças por dia que o item vende, corrigido: dia em que a prateleira estava vazia não conta como dia de demanda zero, porque ali a venda registrada é um piso e não uma observação.",
  "demanda media corrigida": "Peças por dia, tratando o dia de ruptura como observação censurada: o que se vendeu naquele dia é um piso do que o mercado pediu, e imputar pela taxa dos dias com estoque evita subestimar a demanda.",
  "se contasse falta como zero": "A mesma média sem a correção de censura — contando o dia sem estoque como dia de demanda zero. A diferença ao lado é o tamanho do erro que essa leitura ingênua causaria.",
  "subestimacao evitada": "Quanto a demanda sairia menor se o dia sem estoque fosse contado como demanda zero. É o ganho da correção de censura.",
  "demanda real": "Quantas peças o mercado pediu dentro do horizonte deste produto. Não é só o que foi vendido: nos dias em que a prateleira estava vazia a venda registrada é um piso, e a demanda é reconstruída pela taxa dos dias com estoque.",
  "demanda no horizonte": "Peças que o mercado pediu no horizonte cobrado, com os dias de prateleira vazia corrigidos.",
  "demanda esperada nesse prazo": "Demanda média esperada dentro do período de proteção, com o desvio ao lado. É sobre essa distribuição que o estoque de segurança é dimensionado.",
  "previa vender": "Quantas peças o modelo estimava que sairiam dentro do horizonte deste produto. Compare com a demanda real para ver o erro de previsão.",
  "demanda": "Quanto o item vende por dia e com que regularidade — a base de todo o dimensionamento.",
  "cv": "Coeficiente de variação da demanda diária: desvio padrão dividido pela média. Acima de 1 a demanda chega em pedidos grandes com muitos dias de zero, e a cauda pesa mais que a média.",
  "variabilidade cv diario": "Desvio padrão da demanda diária dividido pela média. Quanto maior, mais o estoque de segurança tem de crescer para o mesmo nível de serviço.",
  "distribuicao ajustada": "Qual distribuição descreve a demanda do período. <b>Poisson</b> quando variância ≈ média; <b>Binomial Negativa</b> quando a variância é maior, que é o caso da maior parte do catálogo — demanda que chega em pedidos grandes com muitos dias de zero.",
  "chance": "Probabilidade de a peça ser vendida dentro do horizonte, pela distribuição ajustada ao item.",
  "chance da 1a peca": "Probabilidade de vender a primeira peça do lote dentro do horizonte. É quase sempre alta: é a peça mais fácil de justificar.",
  "chance da ultima": "Probabilidade de vender a última peça do lote dentro do horizonte. É a peça mais frágil da compra, e é ela que define se o lote foi longe demais.",

  /* -------------------------------------------------------- horizonte */
  "horizonte": "Quantos dias esta compra precisa cobrir sozinha: o prazo de entrega do fornecedor mais o intervalo até alguém olhar a lista de novo. É a janela em que o resultado é cobrado, e é diferente para cada produto.",
  "periodo de protecao": "Prazo do fornecedor mais o intervalo de revisão — a janela em que a compra de hoje tem de segurar o item sozinha.",
  "prazo": "Prazo de entrega do fornecedor, a mediana do REALIZADO (data do pedido até a entrada em estoque), não a do combinado. No extrato os dois divergem muito: 40,6 dias combinados contra 17,9 realizados, e dimensionar pelo combinado imobilizaria capital para uma espera que raramente acontece. O \"?\" ao lado aparece quando há menos de 3 pedidos sustentando a estimativa.",
  "prazo combinado": "O prazo que o cadastro do pedido diz. Não é o que o modelo usa.",
  "prazo realizado": "Dias entre o pedido e a entrada em estoque. É este que o modelo usa para dimensionar, e é ele que costuma ser bem menor que o combinado.",
  "entrou em": "Data em que a mercadoria de fato entrou no estoque, do ciclo de pagamento do ERP.",
  "dias presos": "Quantos dias o dinheiro daquela peça fica imobilizado antes de ela virar venda.",
  "data": "Data do lançamento.",
  "dia da decisao": "O dia do passado em que o modelo foi posto a decidir. Tudo o que veio depois dessa data é usado só para cobrar o resultado, nunca para decidir.",

  /* ---------------------------------------------------- politica */
  "ponto de pedido": "Nível de estoque em que o pedido tem de sair: demanda esperada no período de proteção mais o estoque de segurança. Abaixo dele, esperar é assumir risco de faltar.",
  "posicao vs ponto de pedido": "Onde o estoque disponível está em relação ao ponto de pedido e ao estoque máximo. Barra dentro da faixa amarela significa que o pedido já deveria ter saído.",
  "posicao de estoque": "Peças disponíveis hoje: o estoque físico menos o que já está reservado para outro pedido. É desta posição que a decisão de compra parte.",
  "posicao hoje": "Peças disponíveis no dia da execução — físico menos reservado.",
  "estoque no dia": "Peças disponíveis no dia da decisão: o estoque físico menos o que já estava reservado para outro pedido.",
  "estoque maximo": "Onde o estoque chega logo depois de a compra entrar: ponto de pedido mais o lote.",
  "seguranca": "Peças carregadas só para absorver variação — de demanda e de prazo de entrega. É o preço do nível de serviço.",
  "estoque de seguranca": "Peças carregadas acima da demanda esperada, para absorver a variação da demanda e do prazo de entrega. Sem elas o nível de serviço cairia para cerca de 50%.",
  "nivel de servico": "Probabilidade de atravessar o período de proteção sem faltar. 97% significa que, em cem ciclos, três terminariam com alguma peça sem atender.",
  "nivel de servico medio": "Média do nível de serviço do catálogo, ponderada pelo que cada item representa.",
  "cobertura": "Por quantos dias o estoque atual aguenta, na demanda média do item.",
  "cobertura atual": "Por quantos dias o estoque de hoje aguenta, na demanda média corrigida.",
  "cobertura hoje": "Dias de estoque antes da compra.",
  "cobertura depois": "Dias de estoque depois de a compra entrar.",
  "cobertura apos a compra": "Por quantos dias o estoque aguenta depois de a compra chegar.",
  "giro": "Quantas vezes o estoque se renova por ano: demanda anual dividida pelo estoque médio. Giro baixo é capital dormindo.",
  "giro anual": "Demanda do ano dividida pelo estoque médio. Quantas vezes o estoque se renova.",
  "cobertura giro": "Dias de cobertura e giro anual do catálogo, lado a lado.",
  "pedidos por ano": "Quantos pedidos de compra o item gera por ano: demanda anual dividida pelo lote, limitado pelo número de revisões. Um item não pode gerar mais pedidos que revisões — contar revisão como pedido inflaria o custo de pedir.",
  "risco": "Probabilidade de faltar peça antes de a reposição chegar, pela distribuição ajustada ao item.",
  "risco de faltar": "Probabilidade de o estoque não atravessar o período de proteção. É calculado da posição atual, não do estoque cheio.",
  "risco de faltar ate repor": "Probabilidade de faltar peça entre hoje e a chegada da reposição, na posição de estoque atual.",
  "risco antes depois": "Risco de faltar antes da compra e depois dela. É a redução de risco que o dinheiro deste item comprou.",
  "risco que sobra": "Risco de ruptura que continua na mesa depois de o critério de parada cortar a compra. Todo critério deixa risco; a questão é quanto e em que itens.",
  "risco que sobra na mesa": "Soma da margem exposta a ruptura nos itens que o critério deixou de fora.",
  "decisao": "O que fazer com este item neste ciclo: comprar, esperar, ou ficou fora do caixa.",

  /* -------------------------------------------------- nota e formula */
  "nota": "Retorno por real investido por dia: V ÷ (c × D), com D os dias em que o dinheiro fica preso. É a régua que ordena a fila de compra — ela põe na mesma escala um item barato de giro rápido e um item caro de prazo longo ou vendido a prazo.",
  "d": "D: dias com o dinheiro preso — o horizonte H mais o prazo até a venda virar caixa (cartão parcelado, marketplace), menos o prazo que o fornecedor dá para pagar. Só a nota divide por D; a chance de vender continua medida em H.",
  "dias de capital": "Quantos dias o dinheiro de uma peça fica fora do caixa: da data em que o fornecedor é pago até a venda ser recebida. É o denominador da nota.",
  "dias com o dinheiro preso": "O mesmo que dias de capital: horizonte + prazo de recebimento − prazo de pagamento ao fornecedor.",
  "vale": "O valor esperado da peça: o que ela devolve, em média, descontado o risco de não vender.",
  "f k 1": "F(k−1): probabilidade acumulada de a demanda no horizonte ficar abaixo de k−1 peças — ou seja, a chance de a k-ésima peça encalhar.",
  "p": "P: probabilidade de vender esta peça dentro do horizonte. É 1 − F(k−1).",
  "m": "M: a margem que a peça devolve se for vendida — lucro por peça, ajustado pelo fator de perda por ruptura.",
  "l": "L: a perda se a peça não vender no horizonte — carregamento do capital no período mais o percentual de encalhe. Não é a margem inteira: peça que não vendeu neste horizonte continua vendável no próximo.",
  "p m": "P × M: o ganho esperado da peça.",
  "1 p l": "(1−P) × L: a perda esperada da peça.",
  "v": "V = P×M − (1−P)×L: o valor esperado da peça em reais. Positivo significa que carregar esta unidade se paga.",
  "c": "c: o custo unitário da peça — o denominador da nota.",
  "h": "H: o horizonte em dias — prazo do fornecedor mais o intervalo de revisão. É a janela em que a peça tem de vender; os dias que o dinheiro fica preso (D) partem dele.",
  "caixa acum": "Soma acumulada do custo das peças, na ordem da fila. Onde essa coluna cruza o caixa do ciclo é onde a compra é cortada.",
  "retorno r": "Quanto de margem esperada cada real investido neste item devolve.",
  "por real aplicado": "Margem esperada dividida pelo investimento — quanto volta por real aplicado.",
  "margem esperada": "Margem que a compra deve devolver, ponderada pela probabilidade de cada peça vender no horizonte.",
  "margem de volta": "Margem esperada que a compra devolve sob aquele critério de parada.",
  "margem esperada de volta": "Soma da margem esperada das peças que entraram na compra.",
  "margem esperada do ciclo": "Margem que a compra deste ciclo deve devolver, somando a expectativa peça por peça.",
  "margem em risco": "Margem que se perde se a ruptura acontecer: peças que devem faltar × lucro por peça.",
  "margem perdida": "Peças que o mercado pediu e não levou × lucro por peça. É venda que não aconteceu.",
  "limite marginal": "A probabilidade de venda em que carregar a próxima peça deixa de se pagar — o ponto em que o custo de mantê-la parada empata com a margem que se perde se ela faltar.",

  /* ------------------------------------------------- economia anual */
  "lucro ano": "Lucro líquido projetado para 12 meses: lucro bruto menos custo de manter, de pedir e de ruptura.",
  "lucro bruto": "Demanda anual × lucro por peça, antes de qualquer custo de estoque.",
  "lucro bruto ano": "Demanda anual × lucro por peça, sem descontar custo de estoque.",
  "lucro bruto anual": "Demanda anual × lucro por peça, somado na seleção.",
  "lucro liquido": "Lucro bruto menos o custo de manter, o de pedir e o de ruptura.",
  "lucro liquido ano": "Lucro bruto do ano menos custo de manter, de pedir e de ruptura.",
  "custo de manter": "Custo de carregar o estoque médio por um ano: capital imobilizado × taxa de manutenção.",
  "custo de carregar": "Capital imobilizado × taxa de manutenção ao ano. É o aluguel do dinheiro que está na prateleira.",
  "custo de pedir": "Custo administrativo dos pedidos do ano: pedidos por ano × custo por pedido.",
  "custo de ruptura": "Margem perdida esperada por falta de estoque no ano.",
  "custo ano": "Soma do custo de manter, de pedir e de ruptura no ano.",
  "custo lucro bruto": "Quanto do lucro bruto é consumido pelo custo de estoque. Acima de 100% o item destrói valor.",
  "retorno s capital": "Retorno sobre o capital imobilizado: lucro líquido do ano dividido pelo capital preso.",
  "paga se em": "Em quantos meses o lucro do item devolve o capital que ele imobiliza.",
  "faltas ano": "Peças que se espera faltar no ano, na política atual.",
  "faltas esperadas": "Peças que devem faltar por ano com esta política. Zero exigiria estoque infinito; o número aceito é o que o nível de serviço escolheu.",
  "perdido": "Margem perdida no ano por ruptura: peças que faltam × lucro por peça.",
  "lucro perdido por ruptura": "Margem que se perde por prateleira vazia, somada na seleção.",
  "repor": "Peças a repor para o item voltar ao estoque máximo.",

  /* ------------------------------------------------- backtest */
  "o que aconteceu": "OBSERVADO. Lido direto do banco, sem simulação: inclui TODAS as compras da empresa dentro do horizonte — a ordem daquela semana, as entregas de ordens anteriores e todos os ciclos de compra seguintes. Por isso não se compara com as colunas simuladas, que são de uma ordem de compra só.",
  "o pedido daquele ciclo": "A EMPRESA. A ordem de compra que ela colocou na mesma janela de revisão que termina no dia da decisão, chegando nas datas reais de entrada em estoque. Simulada sozinha no horizonte, sem compra posterior — a mesma restrição que o modelo tem.",
  "a compra do modelo": "O MODELO. A ordem que a plataforma teria colocado naquele dia, chegando no prazo do fornecedor. Simulada sozinha no mesmo horizonte, com o mesmo dinheiro do pedido real.",
  "diferenca": "Modelo menos empresa, comparando as duas colunas simuladas.",
  "mandaria comprar": "Peças que a plataforma teria mandado comprar naquele instante. Não é o que a empresa comprou.",
  "o modelo compraria": "Peças que a plataforma teria mandado comprar naquele instante, avaliando peça por peça se a próxima unidade se paga.",
  "a empresa pediu": "Peças que a empresa colocou em ordem de compra na mesma janela de revisão. É a decisão dela, a que compete com a do modelo.",
  "valor do pedido": "Valor da nota do que a empresa pediu. É esse total, somado no catálogo, que serve de teto de caixa para o modelo — os dois lados decidem com o mesmo dinheiro.",
  "recebido de fato": "Valor da mercadoria que de fato entrou no estoque na janela.",
  "usou da compra": "Das peças compradas, quantas a demanda do horizonte de fato consumiu. O resto sobrou.",
  "sobrou": "Peças da compra que a demanda do horizonte não pediu. É a conta de excesso: comprou 36, a demanda pediu 12 e o estoque já tinha 0, então sobraram 24.",
  "faltou": "Peças que a demanda pediu e não havia na prateleira, simulando dia a dia com a compra do modelo chegando no prazo do fornecedor.",
  "faltaria": "Peças que faltariam com a compra do modelo, na simulação dia a dia.",
  "faltaria com o pedido real": "Peças que faltariam se a empresa contasse apenas com a ordem daquele ciclo, chegando nas datas reais. Mesma simulação, mesma demanda, mesma posição inicial — só muda quanta mercadoria chega e quando.",
  "faltou de verdade": "OBSERVADO, sem simulação. Peças que o mercado pediu no horizonte e não levou: demanda corrigida menos venda registrada. Inclui todas as compras da empresa, e por isso é sempre menor que as colunas simuladas.",
  "falta evitada": "Peças que o modelo deixaria de perder em relação ao pedido real do ciclo. Positivo: o modelo atenderia mais.",
  "pecas nao atendidas": "Peças que o mercado pediu dentro do horizonte e não levou.",
  "dias zerado": "Em quantos dias do horizonte o estoque ficou em zero. Pode não haver pedido nesses dias, então este número é maior que o de produtos em que faltou peça.",
  "itens com ruptura": "Quantos produtos ficaram sem estoque em algum dia do horizonte.",
  "o contexto real daquele dia": "Os tetos que o backtest usa não são escolhidos à mão: o caixa é o que a empresa de fato pôs em ordem de compra naquele ciclo, e o teto de capital é o valor do estoque naquele dia.",

  /* ------------------------------------------------- criterios */
  "regra": "O critério de parada: a condição que decide quando parar de comprar.",
  "compra": "O que a compra fica valendo sob aquela regra.",
  "compra no criterio em uso": "Valor da compra sob a combinação de critérios ativa. Com mais de um critério ativo, corta o que chegar primeiro.",
  "compra deste ciclo": "Valor total do pedido deste ciclo, depois do corte pelo caixa.",
  "se nada for comprado": "O que se perde de margem se o ciclo passar sem compra. É o piso contra o qual todo critério é medido.",
  "liberado para comprar hoje": "Caixa disponível para este ciclo de compra.",
  "o que o teto custa": "Margem que se deixa na mesa por causa do limite de caixa: o que as peças que ficaram de fora devolveriam.",
  "exposicao imediata": "Margem em risco nos itens que já estão abaixo do ponto de pedido — a perda que acontece se nada for reposto.",
  "abaixo do ponto de pedido": "Quantos produtos já estão em nível de estoque que exigia pedido.",
  "quem levou as pecas": "Como o caixa do ciclo se dividiu entre os produtos.",
  "produtos atendidos": "Quantos produtos receberam ao menos uma peça neste ciclo.",
  "ficaram fora do caixa": "Produtos cujas peças passariam no teste marginal mas não couberam no caixa deste ciclo.",
  "nao compensam agora": "Produtos em que a próxima peça não se paga: o valor esperado dela é negativo.",
  "pecas com menos de 50 de chance de vender": "Peças compradas cuja probabilidade de venda no horizonte é menor que 50%. Não são erro: uma peça de margem alta se paga mesmo com chance baixa. Mas em volume alto merecem olhar.",
  "itens na selecao": "Quantos produtos passam pelos filtros aplicados.",
  "linhas na fila": "Quantas peças candidatas a fila avaliou.",
  "mostrando": "Quantas linhas o recorte atual está exibindo.",
  "acompanhando": "Qual produto a página está usando como exemplo.",

  /* ------------------------------------------------- venda / canal */
  "canal vendedor": "Por onde a venda saiu: o canal e o vendedor registrados na nota.",
  "canal": "Por onde a venda saiu — loja, e-commerce, marketplace.",
  "uf": "Estado de destino da venda, como consta na nota.",
  "tipo cliente": "Natureza da operação registrada na nota — venda normal, bonificação, devolução.",
  "cliente id": "Código do cliente no ERP.",
  "vendas": "Toda venda registrada deste item, linha por linha, direto da base.",
  "vendeu no total": "Peças vendidas e linhas de venda em todo o histórico do item, com o período coberto ao lado.",
  "compras o pedido como o erp registra": "Todo pedido de compra deste item como o ERP registra, com prazo combinado, prazo realizado e forma de pagamento.",
  "entradas em estoque o que de fato chegou": "Reconstruído do estoque diário: a subida do saldo de um dia para o outro. Fecha com o estoque por construção, ao contrário da tabela de compras do ERP.",
  "lancamentos de custo medio": "Só os dias em que o custo médio mudou, com o estoque daquele dia ao lado. É a coluna que denuncia o reset do ERP em dia de saldo zero.",
  "conferencia do dado cru": "Toda venda, toda compra e todo lançamento de custo deste item, direto da base, sem passar pelo modelo. É a trilha para conferir à mão de onde saiu cada número da decisão.",
  "situacao agora": "Onde o item está hoje: posição de estoque, ponto de pedido, risco de faltar e o que comprar.",
  "historico diario": "Um retângulo por dia do histórico, colorido pelo estado do estoque naquele dia.",
  "como a politica foi definida": "O caminho da decisão neste item: o regime escolhido, o lote e o estoque de segurança que saíram dele.",
  "economia anual projetada": "O resultado que esta política deve dar em 12 meses, já descontados os custos de estoque.",

  /* ------------------------------------------------- paineis */
  "onde o estoque esta furando agora": "Os produtos em que a posição atual já está abaixo do ponto de pedido, ordenados pela margem que a ruptura ameaça.",
  "concentracao do lucro": "Quanto do lucro está em quantos produtos. É a curva que justifica a classificação ABC.",
  "abc xyz": "O catálogo cruzado por importância (ABC, pelo lucro) e regularidade (XYZ, pelo coeficiente de variação da demanda).",
  "a operacao ao longo do tempo": "Como estoque, venda e ruptura se moveram no período coberto pela base.",
  "onde o dinheiro esta e onde ele esta vazando": "Onde o capital está imobilizado e onde ele está sendo consumido por custo de estoque ou por ruptura.",
  "tres politicas mesmo catalogo": "O mesmo catálogo sob três políticas de compra diferentes, para ver o que a escolha de política custa.",
  "o que a ruptura escondeu": "Quanto de demanda a prateleira vazia impediu de virar venda — o que a correção de censura recupera.",
  "a corrida das pecas": "A ordem em que as peças entram na compra, da melhor nota para a pior, até o caixa acabar.",
  "quando cada produto entra na compra": "A partir de qual peça da fila global cada produto começa a levar unidades.",
  "a fila inteira": "Todas as peças candidatas, na ordem da nota, com a conta de cada uma à vista.",
  "o que entrou no pedido": "As peças que couberam no caixa deste ciclo.",
  "os proximos da fila": "As peças que se pagariam e ficaram esperando o próximo ciclo.",
  "duas formas de gastar o mesmo caixa": "O mesmo dinheiro distribuído por duas políticas diferentes, e o que cada uma devolve.",
  "a fronteira": "A curva de troca entre risco assumido e caixa gasto. Cada ponto é um critério de parada possível.",
  "as regras": "Os critérios de parada disponíveis e o que cada um faz com a compra.",
  "cada regra sozinha e o conjunto": "O efeito de cada critério isolado e o efeito da combinação — com mais de um ativo, corta o que chegar primeiro.",
  "por onde o lucro entra e por onde escapa": "A cascata do lucro bruto até o líquido, mostrando quanto cada custo de estoque consome.",
  "quanto o proximo real ainda paga": "O retorno marginal: quanto o próximo real investido devolve, à medida que o caixa cresce.",
  "onde o dinheiro esta e o que ele devolve": "Capital imobilizado por produto contra o retorno que cada um dá.",
  "capital preso retorno que ele devolve": "Cada produto posicionado pelo capital que prende e pelo retorno que devolve. O quadrante de baixo à direita é onde o dinheiro está mal aplicado.",
  "produto por produto": "A mesma conta aberta item por item.",
  "produto por produto no horizonte de cada um": "O resultado do backtest item por item, cada um cobrado no horizonte dele.",
  "a compra que foi feita a compra que o modelo faria": "As duas decisões lado a lado. As duas colunas simuladas se comparam entre si — mesma posição inicial, mesma demanda, mesmo dinheiro. A coluna do observado é o mundo real, com todas as compras da empresa dentro.",
  "produtos que ficaram sem estoque de verdade": "Lido do banco, sem simulação: os produtos em que o mercado pediu peça e não levou.",
  "produtos que ficariam sem estoque com a compra do modelo": "Simulado: os produtos em que faltaria peça se a empresa contasse apenas com a compra que o modelo faria naquele dia.",
  "cada ciclo de revisao do intervalo": "Uma linha por decisão de compra. Os horizontes de ciclos consecutivos se sobrepõem, então demanda e falta contam parte do período mais de uma vez.",
  "os dias deste produto": "O histórico diário do produto: quanto vendeu e em que estado o estoque estava em cada dia.",
  "quanto esse produto vende por dia de verdade": "A demanda diária corrigida — sem contar o dia de prateleira vazia como dia de demanda zero.",
  "o que pode acontecer nessa janela": "A distribuição da demanda dentro do período de proteção, e onde o ponto de pedido cai nela.",
  "a escada de pecas deste produto": "Peça por peça: o que cada unidade adicional devolve e onde deixa de se pagar.",
  "cinco produtos bem diferentes na mesma regua": "Cinco itens de perfis opostos avaliados pela mesma conta, para mostrar que a régua é uma só.",
  "como o caixa se espalhou neste ciclo": "Para onde o dinheiro do ciclo foi, produto por produto.",
  "simbolos": "O que cada símbolo das fórmulas significa.",
  "duas palavras que confundem": "Dois termos que parecem sinônimos e não são.",
  "e o estoque ideal": "Por que não existe um número de estoque ideal independente de quanto dinheiro se tem e de quanto risco se aceita.",
  "os tres numeros que mais mexem": "Os parâmetros cuja mudança mais desloca o resultado.",
  "interruptores metodologicos": "As escolhas de método que podem ser ligadas e desligadas, para ver o efeito de cada uma.",
  "ultima execucao": "Quando o pipeline rodou por último e sobre quantos dias de histórico.",
  "cada linha e uma peca candidata": "Cada linha é uma unidade que o modelo avaliou, com a conta inteira dela aberta.",

  /* ------------------------------------------------- respostas diretas */
  "quantas pecas a plataforma mandaria comprar naquele instante": "A soma das peças que o modelo mandaria comprar no dia da decisão, no recorte visível.",
  "quantas pecas o mercado pediu de verdade no horizonte": "A demanda corrigida no horizonte de cada produto, somada no recorte visível.",
  "sobrou componente em estoque no fim do horizonte": "Peças compradas que a demanda do horizonte não pediu.",
  "houve ruptura": "Produtos que ficaram sem estoque em algum dia do horizonte.",

  /* ------------------------------- os que sobraram do primeiro varrimento */
  "pecas com menos de 50 de chance": "Peças compradas cuja probabilidade de venda no horizonte é menor que 50%. Não são erro: uma peça de margem alta se paga mesmo com chance baixa, porque o que decide é margem × chance contra o custo de carregar. Mas em volume alto merecem olhar.",
  "custo das linhas de pedido": "Soma do custo das peças que entraram no pedido — o mesmo que o investimento do ciclo, aberto por linha.",
  "margem esperada liquida": "Margem esperada da compra depois de descontar o custo de carregar as peças no horizonte. É o que sobra, não o que entra.",
  "este produto entrou": "Se este produto recebeu alguma peça no ciclo. Não entrar não significa estar bem de estoque: significa que a próxima peça dele não se pagava, ou que o caixa acabou antes.",
  "chance de faltar antes de repor": "Probabilidade de o estoque não atravessar o período de proteção, na posição de hoje.",
  "depois desta compra": "O mesmo risco depois de a compra entrar. A diferença é a redução de risco que o dinheiro deste item comprou.",
  "retorno por real investido": "Margem esperada da compra dividida pelo que ela custa. É a mesma régua da nota, sem dividir pelo horizonte.",
  "lote economico eoq": "Lote que minimiza a soma do custo de pedir e do custo de manter: a raiz de (2 × demanda anual × custo por pedido ÷ custo de manter uma peça por ano). Vale para item de giro relevante; para item caro de giro baixo o modelo usa o teste da unidade marginal, porque ali o EOQ manda comprar mais peças do que o item vende no horizonte.",
  "unidades marginais": "Quantas peças passaram no teste da unidade marginal: o modelo carrega a k-ésima peça enquanto a chance de precisar dela for maior que o limite em que o custo de mantê-la parada empata com a margem que se perde se ela faltar.",

  /* ------------------------------------------------------ diagnostico */
  "faixa": "Estado do item hoje, avaliado nesta ordem: <b>Zerado com demanda</b> (sem peça e com demanda estimada), <b>Risco</b> (posição igual ou abaixo do ponto de pedido), <b>Sem giro</b> (há peça, mas sem venda há mais dias que o limiar da página), <b>Excesso</b> (acima do estoque máximo do modelo), <b>Saudável</b> (o resto). Um item cai numa faixa só.",
  "acao": "O que o diagnóstico sugere para o item: comprar (está na fila do plano), transferir (falta no CD e a rede tem o suficiente para o período de proteção), revisar (falta e nem o plano nem a rede resolvem), liquidar (sem giro há mais de um ano), segurar (excesso ou sem giro recente), manter.",
  "idade fifo": "Há quantos dias, em média, as peças do saldo atual estão no CD. Aproximação: percorre as entradas de mercadoria da mais recente para trás até cobrir o saldo e pondera a idade de cada entrada pela quantidade usada. O ERP não guarda lote; quando as entradas registradas não cobrem o saldo, a parte descoberta recebe a idade da entrada mais antiga disponível e o valor sai marcado com *.",
  "capital contabil": "Estoque físico vezes o custo médio contábil do ERP no último dia com estoque. É o valor que a contabilidade vê; o capital do modelo usa o custo corrigido (ver Custo unitário). A diferença entre os dois é um achado em si.",
  "otimo": "Capital que a política do modelo manteria em estoque médio para o item (estoque médio vezes custo). Real menos ótimo é quanto a operação carrega a mais ou a menos do que a política recomenda.",
  "real menos otimo": "Capital em estoque hoje menos o capital que a política do modelo manteria em estoque médio. Positivo: a empresa carrega mais do que a política recomenda; negativo: menos.",
  "excesso": "Peças acima do estoque máximo do modelo vezes o custo unitário. Só existe na faixa Excesso.",
  "sem giro": "Capital em itens com peça no CD e sem venda há mais dias que o limiar escolhido na página (padrão 180).",
  "dias sem venda": "Dias desde a última venda registrada em qualquer loja até a data da posição. 'nunca' quando não há venda no histórico.",
  "rede": "Saldo somado nas lojas e demais locais fora do CD, na foto mais recente extraída do DW. Não entra na política; serve para apontar transferência em vez de compra.",
  "lojas": "Quantos locais fora do CD têm saldo positivo do item.",
  "precisa no periodo": "Demanda esperada no período de proteção (prazo de entrega + revisão), μ do período. Se a rede tem pelo menos isso, a transferência cobre a falta do CD.",
  "valor em loja": "Saldo em loja vezes o custo médio contábil de cada local, somado.",
  "comprador": "Comprador oficial do item no cadastro do ERP.",
  "parado": "Capital em itens sem giro ou em excesso: peça que está no CD e que, pela demanda estimada, não deveria estar toda lá.",
  "em risco ou zerado": "Capital em itens abaixo do ponto de pedido ou zerados com demanda: onde a falta está acontecendo ou é iminente.",
};

  /* Coluna de um simbolo so - "#", "μ" - nao tem chave possivel: a
     normalizacao remove pontuacao e caractere nao latino, e sobra vazio. Para
     esses o proprio elemento carrega a descricao em `data-ajuda`, que
     `pinarUm` le antes de olhar o glossario. E a valvula de escape para
     qualquer rotulo que nao de para indexar por texto. */

  /* o objeto exportado por nucleo.js e mutado no lugar: assim o glossario
     pode carregar em qualquer ordem depois dele */
  if (raiz.N && raiz.N.GLOSSARIO) {
    for (var k in G) raiz.N.GLOSSARIO[k] = G[k];
  } else {
    raiz.N = raiz.N || {};
    raiz.N.GLOSSARIO = G;
  }
})(window);
