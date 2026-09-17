/* =====================================================================
   Gaveta de item - o dossie completo de um SKU, aberto de qualquer tela.
   Depende de nucleo.js (N.*) e do endpoint /api/item/{sku}.
   ===================================================================== */
(function (raiz) {
  "use strict";
  var C = N.COR;

  function bloco(titulo, corpo, dica) {
    return '<div class="painel mb14"><div class="painel-cab"><h2>' + titulo + "</h2>" +
      (dica ? '<span class="dica">' + dica + "</span>" : "") +
      '</div><div class="painel-int">' + corpo + "</div></div>";
  }
  function kv(k, v, cls) {
    return '<div class="kv"><span class="k">' + k + '</span><span class="v ' +
      (cls || "") + '">' + v + "</span></div>";
  }

  /* uma tabela de conferencia: cabecalho, linhas e um rodape de soma */
  function tab(titulo, cols, linhas, rodape, aviso, par) {
    if (!linhas.length) {
      return '<div class="mt18"><div class="t4 pequeno" style="letter-spacing:.09em;' +
        'text-transform:uppercase;font-weight:700">' + titulo + "</div>" +
        '<div class="nota-lat mt6" style="margin-left:0">nenhum registro</div></div>';
    }
    return '<div class="mt18">' +
      '<div class="t4 pequeno mb8" style="letter-spacing:.09em;text-transform:uppercase;' +
      'font-weight:700">' + titulo + ' <span class="t4">· ' +
      (par && par[1] > par[0]
        ? "mostrando as " + N.num(par[0], 0) + " mais recentes de " +
          N.num(par[1], 0) + " (o rodapé soma todas)"
        : N.num(linhas.length, 0) + " registros") + "</span></div>" +
      (aviso ? '<div class="nota-lat mb8" style="margin-left:0">' + aviso + "</div>" : "") +
      '<div class="rolo rolo-medio"><table class="tb" style="font-size:11.5px">' +
      "<thead><tr>" + cols.map(function (c) {
        return '<th' + (c[2] ? ' class="n nao-ord"' : ' class="nao-ord"') + ">" +
          c[0] + "</th>"; }).join("") + "</tr></thead><tbody>" +
      linhas.map(function (r) {
        return "<tr>" + cols.map(function (c) {
          return "<td" + (c[2] ? ' class="n"' : "") + ">" + c[1](r) + "</td>";
        }).join("") + "</tr>";
      }).join("") +
      (rodape ? '<tr data-fixo style="background:var(--painel-2)">' + rodape + "</tr>" : "") +
      "</tbody></table></div></div>";
  }
  function dt(x) { return x ? String(x).slice(0, 10) : '<span class="t4">–</span>'; }
  function rs(x, c) {
    return x === null || x === undefined
      ? '<span class="t4">–</span>'
      : '<span class="pre">R$</span>' + N.moeda(x, c === undefined ? 2 : c);
  }

  function conferencia(c, m) {
    var t = c.totais, cab = c.cabecalho;
    var resetou = (c.custos || []).filter(function (x) {
      return x.estoque <= 0 && x.custo < cab.custo_mediano * 0.5; });

    var h = '<div class="gr gr-2" style="gap:0 22px"><div>' +
      kv("Vendeu no total", N.num(t.vendas_pecas, 0) + " un em " +
         N.num(t.vendas_linhas) + " linhas" +
         (t.primeira_venda ? ' <span class="t4">(' + N.dataLonga(t.primeira_venda) +
          " → " + N.dataLonga(t.ultima_venda) + ")</span>" : "")) +
      kv("Receita líquida", "R$ " + N.moeda(t.vendas_receita)) +
      kv("Custo do vendido (CMV)", "R$ " + N.moeda(t.vendas_cmv)) +
      kv("Lucro observado", "R$ " + N.moeda(t.vendas_lucro),
         t.vendas_lucro >= 0 ? "mt" : "cr") +
      "</div><div>" +
      kv("Preço médio praticado", "R$ " + N.moeda(t.preco_medio, 2)) +
      kv("Custo médio do vendido", "R$ " + N.moeda(t.custo_medio_vendido, 2)) +
      kv("Lucro por peça", "R$ " + N.moeda(t.lucro_medio, 2),
         t.lucro_medio >= 0 ? "mt" : "cr") +
      kv("Custo que o modelo usa", "R$ " + N.moeda(cab.custo_unitario, 2), "am") +
      "</div></div>";

    /* O cruzamento de procedencia do custo. Duas causas possiveis para a
       divergencia, e elas pedem acoes opostas:
         - custo fora da faixa do proprio item  -> lancamento suspeito
         - custo dentro da faixa, longe do CMV  -> o preco se moveu no tempo
       Diagnosticar sempre a primeira seria errar em 64 dos 65 casos desta
       base. */
    var med = cab.custo_mediano || 0;
    var razao = med ? cab.custo_unitario / med : 1;
    var naFaixa = !med || (razao > 0.6 && razao < 1.6);
    var dif = cab.custo_unitario / (t.custo_medio_vendido || 1) - 1;
    var subiu = dif > 0;

    h += '<div class="nota-lat mt14 ' + (naFaixa ? "" : "aviso") +
      '" style="margin-left:0">' +
      "O modelo usa <b>R$ " + N.moeda(cab.custo_unitario, 2) + "</b> por peça " +
      "— o último custo médio lançado num dia com estoque. A média do que foi " +
      "de fato vendido nos últimos anos é <b>R$ " +
      N.moeda(t.custo_medio_vendido, 2) + "</b>, e a mediana histórica do item " +
      "é <b>R$ " + N.moeda(med, 2) + "</b>. " +
      (Math.abs(dif) < 0.1
        ? "As três leituras estão juntas: custo estável."
        : (naFaixa
            ? "Hoje o item custa <b>" + N.pct(Math.abs(dif), 0) + " " +
              (subiu ? "mais" : "menos") + "</b> do que a média do que já foi " +
              "vendido, e o valor de hoje está na faixa da própria história do " +
              "item — <b>é o preço que se moveu</b>, não um lançamento errado. " +
              "A decisão de compra usa o custo de hoje, que é o que se vai pagar."
            : "<b>O custo de hoje está fora da faixa da própria história do " +
              "item</b> (" + N.num(razao, 2) + "× a mediana). Vale conferir os " +
              "lançamentos abaixo antes de confiar na nota deste item.")) +
      (resetou.length
        ? " <span class=\"fraco\">O ERP lançou custo abaixo de metade da mediana " +
          "em " + resetou.length + " dos dias listados abaixo, com estoque zero; o " +
          "modelo não usa esses dias.</span>"
        : "") + "</div>";

    /* as duas leituras da margem, e a divergencia que explica a fila */
    if (m && m.preco_liquido_peca) {
      var mh = m.lucro_por_peca_historico, mn = m.lucro_por_peca;
      var dm = mh ? mn / mh - 1 : 0;
      h += '<div class="nota-lat mt10 ' + (Math.abs(dm) > 0.25 ? "aviso" : "") +
        '" style="margin-left:0"><b>A margem que decide a compra</b> é o preço ' +
        "praticado (R$ " + N.moeda(m.preco_liquido_peca, 2) + ") menos o custo de " +
        "hoje (R$ " + N.moeda(m.custo_unitario, 2) + ") = <b>R$ " + N.moeda(mn, 2) +
        "</b> por peça. A margem que as vendas passadas <i>observaram</i> é R$ " +
        N.moeda(mh, 2) + ", porque cada venda subtraiu o custo do dia dela — " +
        (Math.abs(dm) > 0.25
          ? "<b>" + N.pct(dm, 0) + " de diferença</b>. O custo deste item se " +
            "moveu, e por isso as duas leituras discordam. A decisão usa a " +
            "primeira: é o custo que se vai pagar, não o que já se pagou."
          : "uma diferença de " + N.pct(dm, 0) + ", ou seja, custo estável.") +
        "</div>";
    }

    h += tab("Vendas", [
      ["Data", function (r) { return dt(r.data); }],
      ["Pedido", function (r) { return '<span class="mono t3">' + N.esc(r.pedido || "") + "</span>"; }],
      ["Canal / vendedor", function (r) {
        return N.esc(r.vendedor || r.canal || ""); }],
      ["UF", function (r) { return N.esc(r.uf || ""); }],
      ["Peças", function (r) { return N.num(r.pecas_vendidas, 0); }, 1],
      ["Preço/un", function (r) { return rs(r.valor_da_peca); }, 1],
      ["Receita líq.", function (r) { return rs(r.receita_liquida); }, 1],
      ["Custo/un", function (r) { return rs(r.custo_unitario); }, 1],
      ["CMV", function (r) { return rs(r.cmv); }, 1],
      ["Lucro", function (r) {
        return '<span class="' + (r.lucro >= 0 ? "c-mt" : "c-cr") + '">' +
          rs(r.lucro) + "</span>"; }, 1]
    ], c.vendas,
      '<td colspan="4" class="forte">total do histórico</td>' +
      '<td class="n forte">' + N.num(t.vendas_pecas, 0) + "</td><td></td>" +
      '<td class="n forte">' + rs(t.vendas_receita, 0) + "</td><td></td>" +
      '<td class="n forte">' + rs(t.vendas_cmv, 0) + "</td>" +
      '<td class="n forte ' + (t.vendas_lucro >= 0 ? "c-mt" : "c-cr") + '">' +
      rs(t.vendas_lucro, 0) + "</td>",
      (t.primeira_venda
        ? "Vendeu de " + N.dataLonga(t.primeira_venda) + " a " + N.dataLonga(t.ultima_venda) +
          ". <b>Custo/un</b> é o custo médio do estoque no dia da venda, e " +
          "<b>lucro</b> é receita líquida menos esse custo — não a margem do ERP, " +
          "que carrega rateio de despesa e por isso não serve para decidir compra."
        : ""),
      c.mostrados.vendas);

    h += tab("Compras — o pedido como o ERP registra", [
      ["Data", function (r) { return dt(r.data); }],
      ["Pedido", function (r) { return '<span class="mono t3">' + N.esc(r.idpedido || "") + "</span>"; }],
      ["Fornecedor", function (r) { return N.esc((r.fornecedor || "").slice(0, 30)); }],
      ["Pedido/atendido", function (r) {
        return N.num(r.solicitado, 0) + " / " + N.num(r.atendido, 0); }, 1],
      ["Valor/un", function (r) { return rs(r.valor_unitario); }, 1],
      ["Total", function (r) { return rs(r.valor_total); }, 1],
      ["Prazo combinado", function (r) {
        return r.prazo_previsto ? N.num(r.prazo_previsto, 0) + "d" : '<span class="t4">–</span>'; }, 1],
      ["Prazo realizado", function (r) {
        return r.prazo_realizado === null || r.prazo_realizado === undefined
          ? '<span class="t4">–</span>'
          : '<span class="' + (r.prazo_realizado > (r.prazo_previsto || 0)
              ? "c-cr" : "c-mt") + '">' + N.num(r.prazo_realizado, 0) + "d</span>"; }, 1],
      ["Entrou em", function (r) { return dt(r.entrou_em); }],
      ["Pagamento", function (r) {
        return (r.prazo_pagamento ? N.num(r.prazo_pagamento, 0) + "d · " : "") +
          N.esc((r.pagamento || "").slice(0, 18)); }]
    ], c.compras,
      '<td colspan="3" class="forte">total do histórico</td>' +
      '<td class="n forte">' + N.num(t.compras_solicitado, 0) + " / " +
      N.num(t.compras_atendido, 0) + "</td><td></td>" +
      '<td class="n forte">' + rs(t.compras_valor, 0) + "</td>" +
      '<td colspan="4"></td>',
      "O prazo <b>combinado</b> é o que o cadastro do pedido diz; o " +
      "<b>realizado</b> é a diferença entre a data do pedido e a entrada em " +
      "estoque. O modelo dimensiona pelo realizado, e é a mediana dele que " +
      "aparece como prazo do item (" + N.num(cab.lead_time_dias, 0) + " dias, de " +
      N.num(cab.lead_time_pedidos, 0) + " pedidos).", c.mostrados.compras);

    h += tab("Entradas em estoque — o que de fato chegou", [
      ["Data", function (r) { return dt(r.data); }],
      ["Peças", function (r) { return N.num(r.pecas, 0); }, 1],
      ["Valor ao custo", function (r) { return rs(r.valor); }, 1]
    ], c.entradas,
      '<td class="forte">total do histórico</td>' +
      '<td class="n forte">' + N.num(t.entradas_pecas, 0) + "</td>" +
      '<td class="n forte">' + rs(t.entradas_valor, 0) + "</td>",
      "Reconstruído do estoque diário — a subida do saldo de um dia para o " +
      "outro. Fecha com o estoque por construção, ao contrário da tabela de " +
      "compras do ERP, que salta de 18 mil para 322 mil peças por mês em março " +
      "de 2026 sem o estoque acusar.", c.mostrados.entradas);

    h += tab("Lançamentos de custo médio", [
      ["Data", function (r) { return dt(r.data); }],
      ["Custo lançado", function (r) { return rs(r.custo); }, 1],
      ["Estoque no dia", function (r) {
        return '<span class="' + (r.estoque <= 0 ? "c-cr" : "fraco") + '">' +
          N.num(r.estoque, 1) + "</span>"; }, 1],
      ["", function (r) {
        return r.estoque <= 0
          ? '<span class="c-cr pequeno">estoque zerado — o ERP reseta o custo aqui</span>'
          : ""; }]
    ], c.custos, "",
      "Só os dias em que o custo mudou. O modelo usa o último lançamento de um " +
      "dia <b>com estoque</b> (R$ " + N.moeda(cab.custo_ultimo_lancado, 2) +
      "), e a mediana histórica é R$ " + N.moeda(cab.custo_mediano, 2) + ".");

    return h;
  }

  raiz.abrirItem = function (sku) {
    var alvo = N.abrir(
      '<span class="carregando" style="min-height:0"></span>',
      '<span class="t4">' + N.esc(sku) + "</span>", "");

    N.buscar("/api/item/" + encodeURIComponent(sku)).then(function (d) {
      var m = d.item, pl = d.plano || {}, rd = d.resumo_dias;
      var discreto = /marginal/i.test(m.regime);
      var pos = pl.posicao_estoque !== undefined ? pl.posicao_estoque : 0;

      /* ------- cabeçalho */
      document.getElementById("gv-tit").innerHTML = N.esc(m.item);
      document.getElementById("gv-sub").innerHTML =
        '<span class="t4 mono">' + N.esc(m.sku) + "</span>" +
        '<span class="t4">·</span><span>' + N.esc(m.familia) + "</span>" +
        N.seloClasse(m.classificacao) + N.seloRegime(m.regime) +
        (pl.decisao ? N.seloDecisao(pl.decisao) : "") +
        (m.historico_insuficiente ? '<span class="selo selo-am">histórico insuficiente</span>' : "");

      var h = "";

      /* ------- 1. situação */
      var faixaPos = N.barraPosicao(pos, m.ponto_de_pedido, m.estoque_maximo);
      h += bloco("Situação agora", '<div class="gr gr-2" style="gap:0 22px">' +
        "<div>" +
        kv("Posição de estoque", N.num(pos) + " un" + (pl.em_transito > 0
          ? ' <span class="t4 pequeno">(' + N.num(pl.estoque_fisico) + " disponível + " +
            N.num(pl.em_transito) + " a caminho)</span>" : "")) +
        kv("Ponto de pedido", N.num(m.ponto_de_pedido) + " un", "am") +
        kv("Estoque máximo", N.num(m.estoque_maximo) + " un") +
        kv("Cobertura atual", N.num(m.cobertura_dias, 0) + " dias") +
        "</div><div>" +
        kv("Risco de faltar até repor", N.seloRisco(pl.risco_de_faltar || 0)) +
        kv("Comprar agora", pl.quantidade_a_comprar
          ? N.num(pl.quantidade_a_comprar) + " un · R$ " + N.curto(pl.valor_da_compra)
          : '<span class="t4">não precisa</span>',
          pl.quantidade_a_comprar ? "mt" : "") +
        kv("Retorno por real investido", pl.retorno_por_real > 0
          ? N.num(pl.retorno_por_real, 1) + "×" : "–") +
        kv("Cobertura após a compra", N.num(pl.cobertura_apos_dias || m.cobertura_dias, 0) + " dias") +
        "</div></div>" +
        '<div class="mt14">' + faixaPos + "</div>");

      /* ------- 1b. a escada: chance de vender e lucro esperado de cada peça a mais */
      var esc = d.escada || [], ec = d.economia || {};
      if (esc.length) {
        var compradas = esc.filter(function (r) { return r.peca <= (ec.comprar || 0); });
        var primeiraE = esc[0], ultimaC = compradas.length ? compradas[compradas.length - 1] : null;
        var vale = esc.filter(function (r) { return r.vale; });
        var limiteP = (ec.margem_se_vender + ec.perda_se_encalhar) > 0
          ? ec.perda_se_encalhar / (ec.margem_se_vender + ec.perda_se_encalhar) : 0;
        var frase = "A próxima peça (a nº " + N.num(primeiraE.unidade) + " em estoque) tem <b>" +
          N.pct(primeiraE.p_vender, 0) + "</b> de chance de vender no horizonte e devolve <b>R$ " +
          N.moeda(primeiraE.valor, 2) + "</b> esperados. " +
          (ultimaC
            ? "A última que o plano compra (a nº " + N.num(ultimaC.unidade) + ") ainda tem <b>" +
              N.pct(ultimaC.p_vender, 0) + "</b> e vale R$ " + N.moeda(ultimaC.valor, 2) + ". "
            : "O plano não compra nenhuma neste ciclo. ") +
          (vale.length
            ? "A conta fica positiva até a peça nº <b>" + N.num(vale[vale.length - 1].unidade) +
              "</b>; dali em diante a chance cai abaixo de " + N.pct(limiteP, 0) +
              " e a peça encalha mais do que rende."
            : "Nem a próxima peça se paga: a posição atual já cobre o horizonte.") +
          (primeiraE.passo > 1
            ? " <span class='t4'>(uma peça a cada " + primeiraE.passo + " no gráfico)</span>" : "");
        var lojasEsc = (d.escada_lojas && d.escada_lojas.lojas) || [];
        var seletorLoja = lojasEsc.length
          ? '<div class="filtros mb10" style="width:100%">' +
            '<span class="rotulo" style="flex:0 0 auto">Empresa</span>' +
            '<select id="gv-esc-loja" class="cresce" style="max-width:340px">' +
            '<option value="">Todas (estoque do CD)</option>' +
            lojasEsc.map(function (l) {
              return '<option value="' + N.esc(l.loja_id) + '">' + N.esc(l.loja) +
                " · " + N.pct(l.participacao, 0) + "</option>"; }).join("") +
            '</select><span class="pequeno t4" id="gv-esc-loja-nota"></span></div>'
          : "";
        h += bloco("Cada peça a mais: chance de vender e lucro esperado",
          seletorLoja +
          '<div class="gr gr-2" style="gap:0 22px">' +
          '<div><div class="t4 pequeno mb6" style="letter-spacing:.09em;text-transform:uppercase;font-weight:700">' +
          'Chance de vender a k-ésima peça</div><div id="gv-esc-p" class="gfx" style="height:200px"></div></div>' +
          '<div><div class="t4 pequeno mb6" style="letter-spacing:.09em;text-transform:uppercase;font-weight:700">' +
          'Lucro esperado da k-ésima peça</div><div id="gv-esc-v" class="gfx" style="height:200px"></div></div>' +
          '</div>' +
          '<div class="legenda mt8" style="font-size:10.5px">' +
          '<span><i style="background:' + N.sombra(C.menta, .8) + '"></i>o plano compra</span>' +
          '<span><i style="background:' + N.sombra(C.tinta4, .6) + '"></i>não compra</span>' +
          '<span><i style="background:' + C.ambar + '"></i>limite para valer a pena</span></div>' +
          '<div class="nota-lat mt10" style="margin-left:0">' + frase + "</div>",
          "P(demanda ≥ k): só decai");
      }

      /* ------- 2. histórico */
      var pr = d.projecao || {}, fut = pr.dias || [];
      var notaProj = "";
      if (fut.length) {
        var ab = pr.pedidos_abertos || [];
        var prox = ab.filter(function (a) { return !a.atrasado; })[0];
        var atras = ab.filter(function (a) { return a.atrasado; });
        notaProj = "Daqui para a frente o gráfico é <b>projeção</b>: a partir da posição disponível de <b>" +
          N.num(pr.posicao_inicial) + " un</b>, consome <b>" + N.num(pr.demanda_dia, 2) +
          " un/dia</b> (" + (m.historico_insuficiente ? "a média simples, histórico insuficiente" : "a demanda corrigida") + "). " +
          (ab.length
            ? "Há <b>" + N.num(pr.pecas_em_aberto) + " un</b> em " + ab.length +
              (ab.length > 1 ? " pedidos" : " pedido") + " em aberto" +
              (prox ? ", o próximo chega em " + N.data(prox.chega_em) : "") +
              (atras.length ? " (" + atras.length + " já " + (atras.length > 1 ? "atrasados" : "atrasado") +
                ", desenhado" + (atras.length > 1 ? "s" : "") + " em hoje)" : "") + ". "
            : "Não há pedido de compra em aberto. ") +
          (pr.zera_em ? "Sem nova compra, o estoque zera em <b>" + N.data(pr.zera_em) + "</b>. "
                      : "O estoque não zera dentro do horizonte desenhado. ") +
          (pr.compra_plano
            ? "A compra deste ciclo (" + N.num(pr.compra_plano) + " un), feita hoje, chegaria em <b>" +
              N.data(pr.marcos.recebimento) + "</b>."
            : "Uma compra feita hoje chegaria em " + N.data(pr.marcos.recebimento) + ".") +
          " <span class='t4'>" + (pr.em_transito_na_posicao > 0
            ? "Das peças a caminho, " + N.num(pr.em_transito_na_posicao) +
              " chegam dentro do período de proteção e já contam na posição que decidiu a compra."
            : "Nenhuma peça a caminho chega dentro do período de proteção, então a posição que decidiu a compra é só o disponível.") +
          "</span>";
      }
      h += bloco("Histórico diário" + (fut.length ? " e projeção" : ""),
        '<div class="fita" id="gv-fita"></div>' +
        '<div class="legenda mt10" style="font-size:11px">' +
        '<span><i style="background:#2C5A4A"></i>' + rd.disponivel + " dias disponível</span>" +
        '<span><i style="background:' + C.ambar + '"></i>' + rd.ruptura_parcial + " acabou no meio do dia</span>" +
        '<span><i style="background:' + C.coral + '"></i>' + rd.sem_estoque + " sem estoque</span>" +
        "</div>" +
        '<div id="gv-hist" class="gfx mt14" style="height:' + (fut.length ? 330 : 250) + 'px"></div>' +
        (notaProj ? '<div class="nota-lat mt10" style="margin-left:0">' + notaProj + "</div>" : ""),
        fut.length ? "arraste a barra embaixo para ampliar o período" : "");

      /* ------- 3. demanda */
      var linhasDem = (m.historico_insuficiente
        ? '<div class="msg msg-am mb10" style="font-size:12px">Só <b>' + N.num(m.dias_utilizaveis) +
          " dias com estoque</b> em " + N.num(m.dias_historico) + " (piso: " +
          N.num(d.parametros.dias_utilizaveis_minimo) + "). A correção de ruptura não tem amostra: " +
          "a demanda usada é a <b>média simples</b>, com os dias sem estoque valendo zero. " +
          "A corrigida daria " + N.num(m.demanda_media_dia_em, 2) + " un/dia.</div>"
        : "") +
        kv(m.historico_insuficiente ? "Demanda média usada (simples)" : "Demanda média corrigida",
           N.num(m.demanda_media_dia, 2) + " un/dia", "am") +
        kv("Se contasse falta como zero", N.num(m.demanda_media_dia_ingenua, 2) + " un/dia") +
        kv("Subestimação evitada", "+" + N.pct(m.subestimacao_ingenua_pct, 0),
          m.subestimacao_ingenua_pct > 0.05 ? "mt" : "") +
        kv("Variabilidade (CV diário)", N.num(m.cv_diario, 2)) +
        kv("Período de proteção", N.num(m.periodo_protecao_dias, 0) + " dias " +
          '<span class="t4">(' + m.lead_time_dias + "d fornecedor + " +
          d.parametros.periodo_revisao_dias + "d revisão)</span>") +
        kv("Demanda esperada nesse prazo", N.num(m.mu_periodo, 1) + " ± " +
          N.num(m.sd_periodo, 1) + " un", "ce") +
        kv("Distribuição ajustada", m.distribuicao, "vi");
      h += bloco("Demanda", '<div class="gr gr-2" style="gap:0 22px"><div>' + linhasDem +
        '</div><div><div id="gv-dist" class="gfx" style="height:206px"></div>' +
        '<div class="legenda" style="font-size:10.5px;margin-top:8px">' +
        '<span><i style="background:' + N.sombra(C.violeta, .8) + '"></i>' +
        'demanda que o estoque cobre</span>' +
        '<span><i style="background:' + N.sombra(C.coral, .75) + '"></i>' +
        'demanda que passaria do ponto de pedido</span>' +
        '<span><i style="background:' + C.ambar + '"></i>ponto de pedido</span>' +
        '</div></div></div>');

      /* ------- 4. política */
      var pol = "";
      if (discreto) {
        pol = '<div class="prosa mb14" style="font-size:12.5px">Item de giro baixo: em vez da curva ' +
          "normal, o modelo testa <strong>peça por peça</strong> se vale carregar a próxima unidade. " +
          "Guarda a k-ésima peça enquanto a chance de precisar dela for maior que " +
          "<em>" + N.pct(m.limite_marginal, 1) + "</em> — o ponto em que o custo de mantê-la " +
          "parada empata com a margem que se perde se ela faltar.</div>" +
          '<div id="gv-marg" class="gfx" style="height:184px"></div>' +
          '<div class="legenda" style="font-size:10.5px;margin-top:8px">' +
          '<span><i style="background:' + N.sombra(C.menta, .8) + '"></i>vale carregar</span>' +
          '<span><i style="background:' + N.sombra(C.tinta4, .6) + '"></i>não vale</span>' +
          '<span><i style="background:' + C.ambar + '"></i>limite de ' +
          N.pct(m.limite_marginal, 1) + '</span></div>';
      } else {
        pol = '<div id="gv-seg" class="gfx" style="height:210px"></div>' +
          '<div class="nota-lat mt10">O mínimo da curva é o estoque de segurança escolhido: ' +
          "<b>" + N.num(m.estoque_seguranca) + " peças</b>, que equivale a um nível de serviço de " +
          "<b>" + N.pct(m.nivel_servico, 1) + "</b>.</div>";
      }
      h += bloco("Como a política foi definida",
        '<div class="gr gr-2" style="gap:0 22px"><div>' +
        kv("Regime", N.seloRegime(m.regime)) +
        kv(discreto ? "Unidades marginais" : "Lote econômico (EOQ)",
           N.num(discreto ? m.unidades_marginais : m.eoq, 0) + " un") +
        kv("Lote de compra", N.num(m.lote_compra) + " un") +
        kv("Estoque de segurança", N.num(m.estoque_seguranca) + " un") +
        kv("Nível de serviço", N.pct(m.nivel_servico, 1), "mt") +
        kv("Pedidos por ano", N.num(m.pedidos_por_ano, 1)) +
        kv("Capital parado", "R$ " + N.moeda(m.capital_imobilizado), "am") +
        kv("Giro anual", N.num(m.giro_ano, 1) + "×") +
        "</div><div>" + pol + "</div></div>");

      /* ------- 4b. os dois canais */
      var cn = d.canais || {}, ce = cn.ecommerce || {}, cl = cn.lojas || {};
      h += bloco("Demanda por canal",
        '<div class="gr gr-2" style="gap:0 22px"><div>' +
        kv("E-commerce · demanda/dia", N.num(ce.demanda_dia, 3) + " ± " + N.num(ce.desvio_dia, 3)) +
        kv("Participação", N.pct(ce.share, 1)) +
        kv("Lucro por peça", "R$ " + N.moeda(ce.lucro_por_peca, 2) + " × " + N.num(ce.fator, 2)) +
        "</div><div>" +
        kv("Lojas · demanda/dia", N.num(cl.demanda_dia, 3) + " ± " + N.num(cl.desvio_dia, 3)) +
        kv("Participação", N.pct(cl.share, 1)) +
        kv("Lucro por peça", "R$ " + N.moeda(cl.lucro_por_peca, 2) + " × " + N.num(cl.fator, 2)) +
        "</div></div>" +
        '<div class="pequeno t4 mt10">Os dois canais são estimados com as mesmas máscaras de ' +
        'ruptura do CD. A política de estoque usa a soma; a participação pondera o custo de ' +
        'ruptura e reparte o custo de cada peça entre os dois caixas. Covariância diária entre ' +
        'canais: ' + N.num(cn.covariancia, 3) + '.</div>',
        "a mesma demanda, separada em quem a puxa");

      /* ------- 5. economia */
      h += bloco("Economia anual projetada",
        '<div class="gr gr-2" style="gap:0 22px"><div>' +
        kv("Lucro bruto", "R$ " + N.moeda(m.lucro_bruto_ano), "mt") +
        kv("Custo de manter", "R$ " + N.moeda(m.custo_manter_ano)) +
        kv("Custo de pedir", "R$ " + N.moeda(m.custo_pedir_ano)) +
        "</div><div>" +
        kv("Custo de ruptura", "R$ " + N.moeda(m.custo_ruptura_ano), "cr") +
        kv("Lucro líquido", "R$ " + N.moeda(m.lucro_liquido_ano), "mt") +
        kv("Faltas esperadas", N.num(m.faltas_esperadas_ano, 1) + " un/ano") +
        "</div></div>" +
        '<div class="mt14"><a class="btn btn-p" href="/metodologia?sku=' +
        encodeURIComponent(m.sku) + '">Ver este item passo a passo na metodologia →</a></div>');

      /* ------- 6. conferencia: o dado cru, sob demanda */
      h += bloco("Conferência do dado cru",
        '<div class="prosa mb14" style="font-size:12.5px">Toda venda, toda compra e ' +
        'todo lançamento de custo deste item, direto da base — <b>sem passar pelo ' +
        'modelo</b>. É a trilha para conferir a mão de onde saiu cada número da ' +
        'decisão.</div>' +
        '<button class="btn btn-am" id="gv-conf-btn" data-sku="' + N.esc(m.sku) +
        '">Carregar conferência</button>' +
        '<div id="gv-conf" class="mt14"></div>',
        "venda, compra e custo, linha por linha");

      alvo.innerHTML = h;

      /* ---------------------------------------------------- conferencia */
      var btn = document.getElementById("gv-conf-btn");
      if (btn) btn.addEventListener("click", function () {
        btn.disabled = true;
        btn.textContent = "carregando…";
        var alvoC = document.getElementById("gv-conf");
        alvoC.innerHTML = '<div class="carregando"><div class="girar"></div></div>';
        N.buscar("/api/item/" + encodeURIComponent(m.sku) + "/conferencia")
          .then(function (c) { alvoC.innerHTML = conferencia(c, m); btn.remove(); })
          .catch(function (e) {
            btn.disabled = false; btn.textContent = "Carregar conferência";
            alvoC.innerHTML = '<div class="msg msg-er">não deu para carregar: ' +
              N.esc(String(e)) + "</div>";
          });
      });

      /* ---------------------------------------------------- fita */
      document.getElementById("gv-fita").innerHTML = d.dias.map(function (x) {
        var c = x.estado === "Disponivel" ? "dsp" : (x.estado === "Sem estoque" ? "sem" : "par");
        return '<i class="' + c + '" title="' + N.data(x.data) + ": " + x.estado +
          " · vendeu " + N.num(x.vendido) + '"></i>';
      }).join("");

      /* ---------------------------------------------------- escada */
      if (esc.length) {
        /* desenha a escada total ou a de uma empresa: mesma conta, mesma
           regua no eixo (a n-esima peca a mais), so muda a demanda e a fatia
           do estoque/compra que cabe a ela */
        var desenharEscada = function (serie, comprar) {
          var ultima = null;
          for (var i = 0; i < serie.length; i++) if (serie[i].peca <= comprar) ultima = serie[i];
          var dicaEsc = N.dica(function (ps) {
            var r = serie[ps[0].dataIndex];
            return N.dicaTit("A peça nº " + N.num(r.unidade) + " em estoque" +
                (r.peca > 1 ? " (a " + N.num(r.peca) + "ª a mais)" : " (a próxima)")) +
              N.dicaLin(C.violeta, "chance de vender", N.pct(r.p_vender, 1)) +
              N.dicaLin(C.menta, "ganho se vender", "R$ " + N.moeda(r.ganho, 2)) +
              N.dicaLin(C.coral, "perda se encalhar", "R$ " + N.moeda(r.custo_encalhe, 2)) +
              N.dicaLin(r.vale ? C.menta : C.coral, "lucro esperado", "R$ " + N.moeda(r.valor, 2)) +
              N.dicaLin(C.tinta4, "plano", r.peca <= comprar ? "compra" : "não compra");
          });
          var eixoEsc = N.eixoX({ data: serie.map(function (r) { return r.unidade; }),
            name: "peça nº (contando o estoque atual)", nameLocation: "middle", nameGap: 25,
            nameTextStyle: { color: C.tinta4, fontSize: 9.5 },
            axisLabel: { color: C.tinta4, fontSize: 9.5,
              interval: Math.max(0, Math.floor(serie.length / 8)) } });
          var corEsc = function (r) {
            return N.sombra(r.peca <= comprar ? C.menta : C.tinta4, .75); };
          /* num eixo de categoria o ECharts le `xAxis` numerico como indice */
          var marcaCorte = ultima ? [{ xAxis: serie.indexOf(ultima) }] : [];

          N.grafico("gv-esc-p", {
            grid: N.grade({ top: 14, right: 12, bottom: 4, left: 4 }),
            tooltip: dicaEsc, xAxis: eixoEsc,
            yAxis: N.eixoY({ min: 0, max: 1, axisLabel: { color: C.tinta4, fontSize: 9.5,
              formatter: function (v) { return Math.round(v * 100) + "%"; } } }),
            series: [{
              type: "bar", barWidth: "78%",
              data: serie.map(function (r) { return { value: r.p_vender, itemStyle: { color: corEsc(r) } }; }),
              markLine: { silent: true, symbol: "none",
                lineStyle: { color: C.ambar, type: [4, 4], width: 1.3 },
                label: { color: C.ambar, fontSize: 9.5, position: "insideEndTop",
                  formatter: "limite " + N.pct(limiteP, 0) },
                data: [{ yAxis: limiteP }].concat(marcaCorte.map(function (x) {
                  return Object.assign({}, x, { lineStyle: { color: C.menta, type: [2, 3], width: 1 },
                    label: { color: C.menta, fontSize: 9.5, formatter: "última comprada",
                      rotate: 0, position: "insideEndTop", distance: 4 } }); })) }
            }]
          });

          N.grafico("gv-esc-v", {
            grid: N.grade({ top: 14, right: 12, bottom: 4, left: 4 }),
            tooltip: dicaEsc, xAxis: eixoEsc,
            yAxis: N.eixoY({ axisLabel: { color: C.tinta4, fontSize: 9.5,
              fontFamily: '"JetBrains Mono", monospace',
              formatter: function (v) { return "R$ " + N.curto(v); } } }),
            series: [{
              type: "bar", barWidth: "78%",
              data: serie.map(function (r) {
                return { value: r.valor, itemStyle: { color: r.vale ? corEsc(r) : N.sombra(C.coral, .75) } }; }),
              markLine: { silent: true, symbol: "none",
                lineStyle: { color: C.tinta4, width: 1, type: [3, 3] },
                label: { show: false }, data: [{ yAxis: 0 }].concat(marcaCorte.map(function (x) {
                  return Object.assign({}, x, { lineStyle: { color: C.menta, type: [2, 3], width: 1 } }); })) }
            }]
          });
        };

        desenharEscada(esc, ec.comprar || 0);

        var selLoja = document.getElementById("gv-esc-loja");
        if (selLoja) selLoja.addEventListener("change", function () {
          var nota = document.getElementById("gv-esc-loja-nota");
          var l = lojasEsc.filter(function (x) { return x.loja_id === selLoja.value; })[0];
          if (!l) { desenharEscada(esc, ec.comprar || 0); nota.innerHTML = ""; return; }
          desenharEscada(l.escada, l.comprar || 0);
          nota.innerHTML = N.pct(l.participacao, 1) + " da venda deste item em " +
            d.escada_lojas.janela_dias + " dias (" + N.num(l.pecas_janela, 0) + " peças) · " +
            "demanda no horizonte " + N.num(l.mu_periodo, 1) + " ± " + N.num(l.sd_periodo, 1) +
            " un · fatia do estoque " + N.num(l.posicao) + " un" +
            (l.comprar ? " · da compra " + N.num(l.comprar) + " un" : "");
        });
      }

      /* ---------------------------------------------------- histórico + projeção */
      var dias = d.dias, nH = dias.length, nF = fut.length;
      var eixoDatas = dias.map(function (x) { return x.data; }).concat(fut.map(function (f) { return f.data; }));
      var nulos = function (n) { var a = []; for (var i = 0; i < n; i++) a.push(null); return a; };
      var ultimoSaldo = nH ? dias[nH - 1].saldo_final : null;
      /* a projecao emenda no ultimo dia real, partindo da posicao DISPONIVEL */
      var emenda = function (campo) {
        return nulos(nH - 1).concat([pr.posicao_inicial]).concat(fut.map(function (f) { return f[campo]; })); };
      var idx = function (data) { var i = eixoDatas.indexOf(data); return i < 0 ? null : i; };

      var chegadas = [];
      fut.forEach(function (f, i) {
        if (f.entrada > 0) chegadas.push({ coord: [nH + i, f.saldo], value: "+" + N.num(f.entrada),
          itemStyle: { color: C.ceu }, symbol: "pin", symbolSize: 34,
          label: { color: "#fff", fontSize: 9, fontWeight: 700 } });
        if (f.compra_plano > 0) chegadas.push({ coord: [nH + i, f.saldo_com_compra], value: "+" + N.num(f.compra_plano),
          itemStyle: { color: C.menta }, symbol: "pin", symbolSize: 34,
          label: { color: "#fff", fontSize: 9, fontWeight: 700 } });
      });
      var marcos = [{ yAxis: m.ponto_de_pedido, lineStyle: { color: C.ambarEsc, type: [4, 4], width: 1 },
        label: { color: C.ambarEsc, fontSize: 9.5, formatter: "ponto de pedido", position: "insideStartTop" } }];
      if (nF) {
        /* cada marco com o rotulo numa altura diferente, para nao se sobreporem */
        var mk = function (data, rotulo, cor, posicao) {
          var i = idx(data); if (i === null) return null;
          return { xAxis: i, lineStyle: { color: cor, type: [3, 3], width: 1 },
            label: { color: cor, fontSize: 9.5, formatter: rotulo, position: posicao, rotate: 0, distance: 4,
              backgroundColor: N.sombra(C.painel, .85), padding: [1, 3], borderRadius: 2 } };
        };
        marcos = marcos.concat([
          { xAxis: nH - 1, lineStyle: { color: C.tinta4, type: "solid", width: 1 },
            label: { color: C.tinta4, fontSize: 9.5, formatter: "hoje", position: "insideEndBottom", rotate: 0, distance: 4,
              backgroundColor: N.sombra(C.painel, .85), padding: [1, 3], borderRadius: 2 } },
          mk(pr.marcos.recompra, "recompra (" + pr.revisao_dias + "d)", C.violeta, "insideEndTop"),
          mk(pr.marcos.recebimento, "recebimento (" + pr.lead_time_dias + "d)", C.menta, "insideMiddleTop"),
          mk(pr.marcos.fim_protecao, "fim da proteção", C.tinta4, "insideStartTop")
        ].filter(Boolean));
      }

      var series = [
        { name: "Saldo", type: "line", data: dias.map(function (x) { return x.saldo_final; }).concat(nulos(nF)),
          symbol: "none", smooth: 0.15, itemStyle: { color: C.ceu },
          lineStyle: { color: C.ceu, width: 1.5 },
          areaStyle: { color: N.area(C.ceu, 0.18) },
          markLine: { silent: true, symbol: "none", data: marcos } },
        { name: "Vendas", type: "bar", yAxisIndex: 1,
          data: dias.map(function (x) {
            return { value: x.vendido,
              itemStyle: { color: x.estado === "Sem estoque" ? N.sombra(C.coral, .5)
                : (x.estado === "Ruptura parcial" ? C.ambar : N.sombra(C.ambar, .45)) } };
          }).concat(nulos(nF)), barWidth: "62%" }
      ];
      if (nF) {
        series.push(
          /* faixa +-1 desvio: base transparente + altura empilhada */
          { name: "faixa-base", type: "line", stack: "faixa", silent: true, symbol: "none",
            data: emenda("saldo_baixo"), lineStyle: { opacity: 0 }, tooltip: { show: false } },
          { name: "Faixa ±1σ", type: "line", stack: "faixa", silent: true, symbol: "none",
            data: nulos(nH - 1).concat([0]).concat(fut.map(function (f) { return f.saldo_alto - f.saldo_baixo; })),
            lineStyle: { opacity: 0 }, areaStyle: { color: N.sombra(C.ceu, .13) }, itemStyle: { color: N.sombra(C.ceu, .3) } },
          { name: "Saldo projetado", type: "line", data: emenda("saldo"), symbol: "none",
            itemStyle: { color: C.ceu }, lineStyle: { color: C.ceu, width: 1.6, type: [5, 4] },
            markPoint: { data: chegadas, silent: true } },
          { name: "Venda esperada", type: "bar", yAxisIndex: 1, barWidth: "62%",
            data: nulos(nH).concat(fut.map(function (f) { return f.demanda_esperada; })),
            itemStyle: { color: N.sombra(C.violeta, .22) } });
        if (pr.compra_plano > 0) series.push(
          { name: "Com a compra do plano", type: "line", data: emenda("saldo_com_compra"), symbol: "none",
            itemStyle: { color: C.menta }, lineStyle: { color: C.menta, width: 1.4, type: [2, 3] } });
      }

      /* abre mostrando os ultimos ~120 dias mais a projecao; o resto fica no zoom */
      var ini0 = Math.max(0, nH - 120), pct0 = 100 * ini0 / eixoDatas.length;
      N.grafico("gv-hist", {
        grid: N.grade({ top: nF ? 36 : 26, right: 12, bottom: nF ? 46 : 4, left: 4 }),
        legend: { top: 0, left: 0, itemWidth: 9, itemHeight: 9, itemGap: 14, icon: "roundRect",
          textStyle: { color: C.tinta3, fontSize: 10.5 },
          data: series.map(function (s) { return s.name; }).filter(function (n) { return n !== "faixa-base"; }) },
        tooltip: N.dica(function (ps) {
          var i = ps[0].dataIndex;
          if (i < nH) {
            var x = dias[i];
            return N.dicaTit(N.dataLonga(x.data)) +
              N.dicaLin(C.ceu, "saldo no fim do dia", N.num(x.saldo_final)) +
              N.dicaLin(C.ambar, "peças vendidas", N.num(x.vendido)) +
              (x.imputado != null ? N.dicaLin(C.menta, "demanda estimada (imputada)",
                N.num(x.imputado, 1)) : "") +
              N.dicaLin(C.tinta4, "estado", x.estado);
          }
          var f = fut[i - nH];
          var abertosDia = (pr.pedidos_abertos || []).filter(function (a) { return a.chega_em === f.data; });
          return N.dicaTit(N.dataLonga(f.data) + " · projeção") +
            N.dicaLin(C.ceu, "saldo esperado", N.num(f.saldo, 0) +
              (f.saldo_bruto < 0 ? " (faltariam " + N.num(-f.saldo_bruto, 0) + ")" : "")) +
            N.dicaLin(N.sombra(C.ceu, .5), "faixa ±1σ", N.num(f.saldo_baixo, 0) + " – " + N.num(f.saldo_alto, 0)) +
            N.dicaLin(C.violeta, "venda esperada", N.num(f.demanda_esperada, 2)) +
            (f.entrada > 0 ? N.dicaLin(C.ceu, "chega de pedido em aberto", "+" + N.num(f.entrada) +
              (abertosDia.length ? " (" + abertosDia.map(function (a) {
                return "nº " + a.pedido + (a.atrasado ? ", atrasado" : ""); }).join("; ") + ")" : "")) : "") +
            (f.compra_plano > 0 ? N.dicaLin(C.menta, "chega a compra do plano", "+" + N.num(f.compra_plano)) : "") +
            (pr.compra_plano > 0 ? N.dicaLin(C.menta, "saldo com a compra", N.num(f.saldo_com_compra, 0)) : "");
        }),
        xAxis: N.eixoX({ data: eixoDatas,
          axisLabel: { color: C.tinta4, fontSize: 9.5,
            formatter: function (v) { return N.data(v); } } }),
        yAxis: [N.eixoY({ min: 0, axisLabel: { color: C.tinta4, fontSize: 9.5,
          fontFamily: '"JetBrains Mono", monospace',
          formatter: function (v) { return N.curto(v); } } }),
          N.eixoY({ show: false })],
        dataZoom: nF ? [
          { type: "inside", start: pct0, end: 100, zoomOnMouseWheel: true, moveOnMouseMove: true },
          { type: "slider", start: pct0, end: 100, height: 22, bottom: 6,
            borderColor: "transparent", backgroundColor: N.sombra(C.tinta4, .06),
            fillerColor: N.sombra(C.ceu, .14), handleSize: 14,
            dataBackground: { lineStyle: { color: N.sombra(C.ceu, .4) }, areaStyle: { color: N.sombra(C.ceu, .12) } },
            selectedDataBackground: { lineStyle: { color: C.ceu }, areaStyle: { color: N.sombra(C.ceu, .2) } },
            textStyle: { color: C.tinta4, fontSize: 9.5 },
            labelFormatter: function (i, v) { return N.data(v); } }
        ] : undefined,
        series: series
      });

      /* ---------------------------------------------------- distribuição */
      var dd = d.distribuicao;
      if (dd.x.length) {
        N.grafico("gv-dist", {
          grid: N.grade({ top: 26, right: 12, bottom: 4, left: 4 }),
          tooltip: N.dica(function (ps) {
            var i = ps[0].dataIndex;
            return N.dicaTit(dd.passo > 1
              ? "Vender entre " + N.num(dd.x[i]) + " e " + N.num(dd.x[i] + dd.passo - 1) + " peças"
              : "Vender exatamente " + N.num(dd.x[i]) + " peças") +
              N.dicaLin(C.violeta, "probabilidade", N.pct(dd.pmf[i], 2)) +
              N.dicaLin(C.tinta3, "chance de vender mais que isso", N.pct(dd.cauda[i], 1));
          }),
          xAxis: N.eixoX({ data: dd.x, name: "peças no período de proteção",
            nameLocation: "middle", nameGap: 26, nameTextStyle: { color: C.tinta4, fontSize: 9.5 },
            axisLabel: { color: C.tinta4, fontSize: 9.5,
              interval: Math.max(0, Math.floor(dd.x.length / 8)) } }),
          yAxis: N.eixoY({ show: false }),
          series: [{
            type: "bar", data: dd.pmf.map(function (v, i) {
              return { value: v, itemStyle: { color: dd.x[i] <= m.ponto_de_pedido
                ? N.sombra(C.violeta, .8) : N.sombra(C.coral, .75) } };
            }),
            barWidth: "92%",
            markLine: { silent: true, symbol: "none",
              lineStyle: { color: C.ambar, width: 1.4 },
              label: { color: C.ambar, fontSize: 10, rotate: 0, distance: 5,
                formatter: "ROP " + N.num(m.ponto_de_pedido), position: "insideEndTop" },
              data: [{ xAxis: N.categoriaMaisProxima(dd.x, m.ponto_de_pedido) }] }
          }]
        });
      }

      /* ---------------------------------------------------- regime */
      if (discreto && d.marginal.length) {
        var mg = d.marginal;
        N.grafico("gv-marg", {
          grid: N.grade({ top: 24, right: 12, bottom: 4, left: 4 }),
          tooltip: N.dica(function (ps) {
            var r = mg[ps[0].dataIndex];
            return N.dicaTit("A " + r.k + "ª peça") +
              N.dicaLin(C.ceu, "chance de precisar dela", N.pct(r.p_precisar, 1)) +
              N.dicaLin(C.ambar, "limite para valer a pena", N.pct(m.limite_marginal, 1)) +
              N.dicaLin(r.vale ? C.menta : C.coral, "veredito", r.vale ? "carregar" : "não carregar");
          }),
          xAxis: N.eixoX({ data: mg.map(function (r) { return r.k; }),
            name: "k-ésima peça", nameLocation: "middle", nameGap: 25,
            nameTextStyle: { color: C.tinta4, fontSize: 9.5 },
            axisLabel: { color: C.tinta4, fontSize: 9.5 } }),
          yAxis: N.eixoY({ axisLabel: { color: C.tinta4, fontSize: 9.5,
            formatter: function (v) { return Math.round(v * 100) + "%"; } } }),
          series: [{
            type: "bar", data: mg.map(function (r) {
              return { value: r.p_precisar, itemStyle: { color: r.vale
                ? N.sombra(C.menta, .8) : N.sombra(C.tinta4, .6) } };
            }), barWidth: "70%",
            markLine: { silent: true, symbol: "none",
              lineStyle: { color: C.ambar, type: [4, 4], width: 1.4 },
              label: { color: C.ambar, fontSize: 10, position: "insideEndTop",
                formatter: "limite " + N.pct(m.limite_marginal, 1) },
              data: [{ yAxis: m.limite_marginal }] }
          }]
        });
      } else if (d.seguranca.length) {
        var sg = d.seguranca;
        N.grafico("gv-seg", {
          grid: N.grade({ top: 26, right: 12, bottom: 4, left: 4 }),
          legend: { top: 0, left: 0, itemWidth: 9, itemHeight: 9, itemGap: 12, icon: "roundRect",
            textStyle: { color: C.tinta3, fontSize: 10 } },
          tooltip: N.dica(function (ps) {
            var r = sg[ps[0].dataIndex];
            return N.dicaTit(N.num(r.es) + " peças de segurança") +
              N.dicaLin(C.ambar, "custo de manter", "R$ " + N.curto(r.custo_manter)) +
              N.dicaLin(C.coral, "custo de faltar", "R$ " + N.curto(r.custo_ruptura)) +
              N.dicaLin(C.tinta, "total", "R$ " + N.curto(r.custo_total)) +
              N.dicaLin(C.tinta4, "nível de serviço", N.pct(r.nivel_servico, 1));
          }),
          xAxis: N.eixoX({ boundaryGap: false,
            data: sg.map(function (r) { return Math.round(r.es); }),
            name: "estoque de segurança (peças)", nameLocation: "middle", nameGap: 25,
            nameTextStyle: { color: C.tinta4, fontSize: 9.5 },
            axisLabel: { color: C.tinta4, fontSize: 9.5,
              interval: Math.floor(sg.length / 6) } }),
          yAxis: N.eixoY({ axisLabel: { color: C.tinta4, fontSize: 9.5,
            fontFamily: '"JetBrains Mono", monospace',
            formatter: function (v) { return N.curto(v); } } }),
          series: [
            { name: "Manter", type: "line", data: sg.map(function (r) { return r.custo_manter; }),
              symbol: "none", itemStyle: { color: C.ambar },
              lineStyle: { color: C.ambar, width: 1.3, type: [4, 3] } },
            { name: "Faltar", type: "line", data: sg.map(function (r) { return r.custo_ruptura; }),
              symbol: "none", itemStyle: { color: C.coral },
              lineStyle: { color: C.coral, width: 1.3, type: [4, 3] } },
            { name: "Total", type: "line", data: sg.map(function (r) { return r.custo_total; }),
              symbol: "none", smooth: 0.2, itemStyle: { color: C.tinta },
              lineStyle: { color: C.tinta, width: 2 },
              areaStyle: { color: N.area(C.tinta, 0.08) },
              markPoint: { symbol: "circle", symbolSize: 8,
                itemStyle: { color: C.menta, borderColor: C.fundo, borderWidth: 2 },
                label: { show: false },
                data: [{ type: "min", name: "ótimo" }] } }
          ]
        });
      }
    }).catch(function () {
      alvo.innerHTML = '<div class="msg msg-er">Não foi possível carregar este item.</div>';
    });
  };
})(window);
