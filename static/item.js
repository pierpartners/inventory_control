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
        (pl.decisao ? N.seloDecisao(pl.decisao) : "");

      var h = "";

      /* ------- 1. situação */
      var faixaPos = N.barraPosicao(pos, m.ponto_de_pedido, m.estoque_maximo);
      h += bloco("Situação agora", '<div class="gr gr-2" style="gap:0 22px">' +
        "<div>" +
        kv("Posição de estoque", N.num(pos) + " un") +
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

      /* ------- 2. histórico */
      h += bloco("Histórico diário", '<div class="fita" id="gv-fita"></div>' +
        '<div class="legenda mt10" style="font-size:11px">' +
        '<span><i style="background:#2C5A4A"></i>' + rd.disponivel + " dias disponível</span>" +
        '<span><i style="background:' + C.ambar + '"></i>' + rd.ruptura_parcial + " acabou no meio do dia</span>" +
        '<span><i style="background:' + C.coral + '"></i>' + rd.sem_estoque + " sem estoque</span>" +
        "</div>" +
        '<div id="gv-hist" class="gfx m mt14"></div>');

      /* ------- 3. demanda */
      var linhasDem =
        kv("Demanda média corrigida", N.num(m.demanda_media_dia, 2) + " un/dia", "am") +
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
        '</div><div><div id="gv-dist" class="gfx" style="height:238px"></div></div></div>');

      /* ------- 4. política */
      var pol = "";
      if (discreto) {
        pol = '<div class="prosa mb14" style="font-size:12.5px">Item de giro baixo: em vez da curva ' +
          "normal, o modelo testa <strong>peça por peça</strong> se vale carregar a próxima unidade. " +
          "Guarda a k-ésima peça enquanto a chance de precisar dela for maior que " +
          "<em>" + N.pct(m.limite_marginal, 1) + "</em> — o ponto em que o custo de mantê-la " +
          "parada empata com a margem que se perde se ela faltar.</div>" +
          '<div id="gv-marg" class="gfx" style="height:210px"></div>';
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

      alvo.innerHTML = h;

      /* ---------------------------------------------------- fita */
      document.getElementById("gv-fita").innerHTML = d.dias.map(function (x) {
        var c = x.estado === "Disponivel" ? "dsp" : (x.estado === "Sem estoque" ? "sem" : "par");
        return '<i class="' + c + '" title="' + N.data(x.data) + ": " + x.estado +
          " · vendeu " + N.num(x.vendido) + '"></i>';
      }).join("");

      /* ---------------------------------------------------- histórico */
      var dias = d.dias;
      N.grafico("gv-hist", {
        grid: N.grade({ top: 26, right: 12, bottom: 4, left: 4 }),
        legend: { top: 0, left: 0, itemWidth: 9, itemHeight: 9, itemGap: 14, icon: "roundRect",
          textStyle: { color: C.tinta3, fontSize: 10.5 } },
        tooltip: N.dica(function (ps) {
          var x = dias[ps[0].dataIndex];
          return N.dicaTit(N.dataLonga(x.data)) +
            N.dicaLin(C.ceu, "saldo no fim do dia", N.num(x.saldo_final)) +
            N.dicaLin(C.ambar, "peças vendidas", N.num(x.vendido)) +
            (x.imputado != null ? N.dicaLin(C.menta, "demanda estimada (imputada)",
              N.num(x.imputado, 1)) : "") +
            N.dicaLin(C.tinta4, "estado", x.estado);
        }),
        xAxis: N.eixoX({ data: dias.map(function (x) { return x.data; }),
          axisLabel: { color: C.tinta4, fontSize: 9.5,
            formatter: function (v) { return N.data(v); },
            interval: Math.floor(dias.length / 7) } }),
        yAxis: [N.eixoY({ axisLabel: { color: C.tinta4, fontSize: 9.5,
          fontFamily: '"JetBrains Mono", monospace',
          formatter: function (v) { return N.curto(v); } } }),
          N.eixoY({ show: false })],
        series: [
          { name: "Saldo", type: "line", data: dias.map(function (x) { return x.saldo_final; }),
            symbol: "none", smooth: 0.15, lineStyle: { color: C.ceu, width: 1.5 },
            areaStyle: { color: N.area(C.ceu, 0.18) },
            markLine: { silent: true, symbol: "none",
              lineStyle: { color: C.ambarEsc, type: [4, 4], width: 1 },
              label: { color: C.ambarEsc, fontSize: 9.5, formatter: "ponto de pedido",
                position: "insideStartTop" },
              data: [{ yAxis: m.ponto_de_pedido }] } },
          { name: "Vendas", type: "bar", yAxisIndex: 1,
            data: dias.map(function (x) {
              return { value: x.vendido,
                itemStyle: { color: x.estado === "Sem estoque" ? N.sombra(C.coral, .5)
                  : (x.estado === "Ruptura parcial" ? C.ambar : N.sombra(C.ambar, .45)) } };
            }), barWidth: "62%" }
        ]
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
              symbol: "none", lineStyle: { color: C.ambar, width: 1.3, type: [4, 3] } },
            { name: "Faltar", type: "line", data: sg.map(function (r) { return r.custo_ruptura; }),
              symbol: "none", lineStyle: { color: C.coral, width: 1.3, type: [4, 3] } },
            { name: "Total", type: "line", data: sg.map(function (r) { return r.custo_total; }),
              symbol: "none", smooth: 0.2, lineStyle: { color: C.tinta, width: 2 },
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
