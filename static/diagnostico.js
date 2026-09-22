/* Diagnostico do estoque atual: le /api/diagnostico/* e monta KPIs, os dois
   graficos, o agregado por dimensao, a rede e a tabela de itens. Todo numero
   vem do backend; aqui so se formata. */
(function () {
  "use strict";
  var C = N.COR;
  var COR_FAIXA = {
    "Zerado com demanda": C.coral, "Risco": C.ambar, "Sem giro": C.violeta,
    "Excesso": C.ceu, "Saudável": C.menta
  };
  var CLS_FAIXA = {
    "Zerado com demanda": "fx-zerado", "Risco": "fx-risco", "Sem giro": "fx-semgiro",
    "Excesso": "fx-excesso", "Saudável": "fx-saudavel"
  };
  /* a regra de cada faixa, na ordem em que classificar_faixas() as avalia.
     Texto e codigo tem de andar juntos: se a cascata de backend/diagnostico.py
     mudar, esta tabela muda no mesmo commit. */
  var REGRA = {
    "Zerado com demanda": "sem peça no CD e demanda corrigida acima de zero — a ruptura está acontecendo agora",
    "Risco": "posição (físico + trânsito) igual ou abaixo do ponto de pedido, com ponto de pedido positivo — item sem política fica de fora",
    "Sem giro": "há peça no CD e nenhuma venda há mais de {d} dias",
    "Excesso": "físico acima do estoque máximo do modelo",
    "Saudável": "não caiu em nenhuma das anteriores"
  };
  var estado = { dias: parseInt(document.getElementById("dias-sem-giro").value, 10) || 180,
                 ate: document.getElementById("ate").value || "",
                 por: "fornecedor", chave: "", faixa: "", q: "",
                 mLinha: "origem", mChaveL: "", mChaveC: "", metrica: "capital_modelo" };

  /* Agregado e matriz disputam os dois pares de filtro da lista de itens, e
     uma celula ja consome os dois. Em vez de somar recortes que o usuario nao
     consegue ver, quem foi clicado por ultimo manda e o outro se apaga. */
  function filtros() {
    if (estado.mChaveL || estado.mChaveC) {
      return { por: estado.mLinha, chave: estado.mChaveL, por2: "comprador", chave2: estado.mChaveC };
    }
    return { por: estado.chave ? estado.por : "", chave: estado.chave, por2: "", chave2: "" };
  }

  function qs(extra) {
    var p = ["dias_sem_giro=" + estado.dias];
    if (estado.ate) p.push("ate=" + encodeURIComponent(estado.ate));
    for (var k in (extra || {})) if (extra[k]) p.push(k + "=" + encodeURIComponent(extra[k]));
    return "?" + p.join("&");
  }
  function selo(f) { return '<span class="faixa-selo ' + (CLS_FAIXA[f] || "") + '">' + N.esc(f) + "</span>"; }
  function rs(v) { return "R$ " + N.curto(v); }
  function t(id, v) { document.getElementById(id).innerHTML = v; }

  /* ------------------------------------------------------------ geral */
  function geral() {
    N.buscar("/api/diagnostico/geral" + qs(), true).then(function (g) {
      var byF = {}; g.faixas.forEach(function (f) { byF[f.faixa] = f; });
      var risco = byF["Risco"].capital_modelo + byF["Zerado com demanda"].capital_modelo;
      var parado = byF["Sem giro"].capital_modelo + byF["Excesso"].capital_modelo;
      t("k-cap-modelo", rs(g.capital_modelo));
      t("k-cap-erp", rs(g.capital_erp));
      t("k-sem-erp", g.itens_sem_custo_erp ? "(" + N.num(g.itens_sem_custo_erp) + " itens sem custo contábil)" : "");
      t("k-dif", (g.diferenca_real_otimo >= 0 ? "+" : "−") + rs(Math.abs(g.diferenca_real_otimo)));
      t("k-cap-otimo", rs(g.capital_otimo));
      t("k-cob-real", N.num(g.cobertura_real_dias, 0));
      t("k-cob-otima", N.num(g.cobertura_otima_dias, 0));
      t("k-giro", N.num(g.giro_real, 1) + "× vs " + N.num(g.giro_otimo, 1) + "×");
      t("k-risco", rs(risco));
      t("k-risco-n", N.num(byF["Risco"].itens + byF["Zerado com demanda"].itens));
      t("k-perdido", rs(g.lucro_perdido_ruptura));
      t("k-parado", rs(parado));
      t("k-parado-n", N.num(byF["Sem giro"].itens + byF["Excesso"].itens));
      t("k-excesso", rs(byF["Excesso"].excesso_valor));
      document.getElementById("aviso-ate").style.display = g.congelado ? "" : "none";
      document.getElementById("aviso-ate-data").textContent = N.dataLonga(g.data_posicao);
      legenda(g.faixas);

      N.grafico("g-faixas", {
        grid: N.grade({ left: 8, right: 70, top: 8, bottom: 8 }),
        tooltip: N.dica(function (ps) {
          var f = g.faixas[ps[0].dataIndex];
          return N.dicaTit(f.faixa) +
            N.dicaLin(COR_FAIXA[f.faixa], "custo do modelo", rs(f.capital_modelo)) +
            N.dicaLin(C.tinta4, "custo contábil", rs(f.capital_erp)) +
            N.dicaLin(C.tinta4, "itens", N.num(f.itens));
        }),
        xAxis: N.eixoY({ axisLabel: { formatter: function (v) { return N.curto(v); } } }),
        yAxis: N.eixoX({ data: g.faixas.map(function (f) { return f.faixa; }), inverse: true,
          axisLabel: { color: C.tinta2, fontSize: 10.5 }, axisLine: { show: false } }),
        series: [{ type: "bar", barWidth: "58%",
          data: g.faixas.map(function (f) { return { value: f.capital_modelo,
            itemStyle: { color: N.sombra(COR_FAIXA[f.faixa], 0.85), borderRadius: [0, 3, 3, 0] } }; }),
          label: { show: true, position: "right", color: C.tinta2, fontSize: 10.5,
            fontFamily: '"JetBrains Mono", monospace',
            formatter: function (p) { return N.num(g.faixas[p.dataIndex].itens) + " itens"; } } }]
      });
    });
  }

  /* ---------------------------------------------------------- legenda */
  function marcarLegenda() {
    document.querySelectorAll("#legenda tr").forEach(function (tr) {
      tr.classList.toggle("sel", !!estado.faixa && tr.dataset.faixa === estado.faixa);
    });
  }
  function legenda(faixas) {
    var el = document.getElementById("legenda");
    el.innerHTML = faixas.map(function (f, i) {
      var regra = (REGRA[f.faixa] || "").replace("{d}", N.num(estado.dias));
      return '<tr class="clicavel" data-faixa="' + N.esc(f.faixa) + '">' +
        '<td class="ord">' + (i + 1) + "</td>" +
        "<td>" + selo(f.faixa) + "</td>" +
        '<td class="regra">' + regra + "</td>" +
        '<td class="n">' + N.num(f.itens) + "</td>" +
        '<td class="n">' + rs(f.capital_modelo) + "</td></tr>";
    }).join("");
    el.querySelectorAll("tr").forEach(function (tr) {
      tr.addEventListener("click", function () {
        estado.faixa = (estado.faixa === tr.dataset.faixa) ? "" : tr.dataset.faixa;
        document.getElementById("sel-faixa").value = estado.faixa;
        itens();
      });
    });
    marcarLegenda();
  }

  /* ------------------------------------------------------------ idade */
  function idade() {
    N.buscar("/api/diagnostico/idade" + qs(), true).then(function (d) {
      var faixas = Object.keys(COR_FAIXA);
      N.grafico("g-idade", {
        grid: N.grade({ left: 8, right: 14, top: 32, bottom: 6 }),
        legend: { top: 0, left: 0, itemWidth: 9, itemHeight: 9, itemGap: 12, icon: "roundRect",
          textStyle: { color: C.tinta3, fontSize: 10.5 } },
        tooltip: N.dica(function (ps) {
          var f = d.faixas[ps[0].dataIndex], s = N.dicaTit(f.faixa_idade);
          ps.forEach(function (x) { if (x.value) s += N.dicaLin(x.color, x.seriesName, rs(x.value)); });
          s += N.dicaLin(C.tinta, "total", rs(f.capital_modelo) + " · " + N.num(f.itens) + " itens");
          return s;
        }),
        xAxis: N.eixoX({ data: d.faixas.map(function (f) { return f.faixa_idade; }),
          axisLabel: { color: C.tinta2, interval: 0 } }),
        yAxis: N.eixoY({ min: 0, axisLabel: { formatter: function (v) { return N.curto(v); } } }),
        series: faixas.map(function (f, i) {
          return { name: f, type: "bar", stack: "c", barWidth: "52%",
            data: d.faixas.map(function (x) { return x.por_faixa[f] || 0; }),
            itemStyle: { color: N.sombra(COR_FAIXA[f], 0.85),
              borderRadius: i === faixas.length - 1 ? [3, 3, 0, 0] : 0 } };
        })
      });
    });
  }

  /* --------------------------------------------------------- agregado */
  var ROTULO = { fornecedor: "Fornecedor", comprador: "Comprador", familia: "Família", origem: "Marca" };
  function rotuloPor(por) { return ROTULO[por] || por; }
  function agregado() {
    var el = document.getElementById("agregado"); N.espera(el);
    N.buscar("/api/diagnostico/agregado" + qs({ por: estado.por }), true).then(function (d) {
      var h = '<table class="tb" id="tb-agregado"><thead><tr>' +
        "<th>" + N.esc(rotuloPor(estado.por)) + "</th>" +
        '<th class="n">Itens</th><th class="n">Capital</th><th class="n">Capital contábil</th>' +
        '<th class="n">Ótimo</th><th class="n">Zerado + risco</th><th class="n">Sem giro</th>' +
        '<th class="n">Excesso</th><th class="n">Itens em risco</th><th class="n">Lucro perdido</th>' +
        "</tr></thead><tbody>";
      d.grupos.forEach(function (g) {
        var risco = g.por_faixa["Zerado com demanda"] + g.por_faixa["Risco"];
        h += '<tr class="clicavel' + (g.chave === estado.chave ? " sel" : "") + '" data-chave="' + N.esc(g.chave) + '">' +
          "<td>" + N.esc(g.chave) + "</td>" +
          '<td class="n">' + N.num(g.itens) + "</td>" +
          '<td class="n" data-v="' + g.capital_modelo + '">' + rs(g.capital_modelo) + "</td>" +
          '<td class="n" data-v="' + g.capital_erp + '">' + rs(g.capital_erp) + "</td>" +
          '<td class="n" data-v="' + g.capital_otimo + '">' + rs(g.capital_otimo) + "</td>" +
          '<td class="n" data-v="' + risco + '">' + rs(risco) + "</td>" +
          '<td class="n" data-v="' + g.sem_giro_valor + '">' + rs(g.sem_giro_valor) + "</td>" +
          '<td class="n" data-v="' + g.excesso_valor + '">' + rs(g.excesso_valor) + "</td>" +
          '<td class="n">' + N.num(g.itens_risco + g.itens_zerados) + "</td>" +
          '<td class="n" data-v="' + g.lucro_perdido_ruptura + '">' + rs(g.lucro_perdido_ruptura) + "</td>" +
          "</tr>";
      });
      el.innerHTML = h + "</tbody></table>";
      N.tabela("#tb-agregado", { col: 2, dir: "desc" });
      el.querySelectorAll("tr.clicavel").forEach(function (tr) {
        tr.addEventListener("click", function () {
          estado.chave = (estado.chave === tr.dataset.chave) ? "" : tr.dataset.chave;
          estado.mChaveL = estado.mChaveC = "";
          el.querySelectorAll("tr.sel").forEach(function (x) { x.classList.remove("sel"); });
          if (estado.chave) tr.classList.add("sel");
          marcarMatriz(); itens();
        });
      });
    });
  }

  /* ------------------------------------------------------------ matriz */
  var METRICA = {
    capital_modelo: { rot: "capital em estoque", cor: "ceu", dinheiro: true },
    parado: { rot: "capital parado", cor: "violeta", dinheiro: true },
    risco: { rot: "capital em risco ou zerado", cor: "coral", dinheiro: true },
    lucro_perdido_ruptura: { rot: "lucro perdido por ruptura", cor: "ambar", dinheiro: true },
    itens: { rot: "itens", cor: "menta", dinheiro: false }
  };
  var mDados = null;

  function valor(m, c) { return METRICA[estado.metrica].dinheiro ? rs(c[estado.metrica]) : N.num(c[estado.metrica]); }

  function marcarMatriz() {
    var el = document.getElementById("mtz");
    el.querySelectorAll(".sel").forEach(function (x) { x.classList.remove("sel"); });
    if (!estado.mChaveL && !estado.mChaveC) return;
    el.querySelectorAll("td.cel").forEach(function (td) {
      var l = td.dataset.linha, c = td.dataset.col;
      var casa = (!estado.mChaveL || estado.mChaveL === l) && (!estado.mChaveC || estado.mChaveC === c);
      if (casa) td.classList.add("sel");
    });
    el.querySelectorAll("th.cab-col").forEach(function (th) {
      if (th.dataset.col === estado.mChaveC) th.classList.add("sel");
    });
    el.querySelectorAll("td.lin").forEach(function (td) {
      if (td.dataset.linha === estado.mChaveL) td.classList.add("sel");
    });
  }

  function pintarMatriz() {
    if (!mDados) return;
    var m = estado.metrica, cor = C[METRICA[m].cor];
    /* a escala e a maior celula, nao o maior total: senao uma marca inteira
       apaga o contraste entre os cruzamentos, que e o que se veio ver */
    var max = 0;
    mDados.linhas.forEach(function (l) { l.celulas.forEach(function (c) { max = Math.max(max, c[m]); }); });
    document.getElementById("mtz").querySelectorAll("td.cel").forEach(function (td) {
      var v = parseFloat(td.dataset.v) || 0;
      td.textContent = v ? (METRICA[m].dinheiro ? rs(v) : N.num(v)) : "–";
      td.classList.toggle("zero", !v);
      td.style.background = v > 0 && max > 0 ? N.sombra(cor, 0.06 + 0.5 * Math.sqrt(v / max)) : "";
    });
    /* [data-v] deixa de fora a celula-rotulo "Total" do canto */
    document.getElementById("mtz").querySelectorAll("td.tot[data-v],th.tot-col[data-v]").forEach(function (td) {
      var v = parseFloat(td.dataset.v) || 0;
      td.textContent = METRICA[m].dinheiro ? rs(v) : N.num(v);
    });
    document.getElementById("mtz-legenda").textContent =
      mDados.linhas.length + " " + (estado.mLinha === "origem" ? "marcas" : "famílias") +
      " × " + mDados.colunas.length + " compradores · célula = " + METRICA[m].rot +
      " · cor pela intensidade dentro da matriz";
  }

  function matriz() {
    var el = document.getElementById("mtz"); N.espera(el);
    N.buscar("/api/diagnostico/matriz" + qs({ por: estado.mLinha }), true).then(function (d) {
      mDados = d; desenharMatriz();
    });
  }

  function desenharMatriz() {
    var el = document.getElementById("mtz"), d = mDados;
      var h = '<table><thead><tr><th class="lin">' + N.esc(d.rotulo_linha) + "</th>";
      d.colunas.forEach(function (c) {
        h += '<th class="n cab-col" data-col="' + N.esc(c) + '" title="' + N.esc(c) + '">' + N.esc(c) + "</th>";
      });
      h += '<th class="n tot">Total</th></tr></thead><tbody>';
      d.linhas.forEach(function (l) {
        h += '<tr><td class="lin clicavel" data-linha="' + N.esc(l.chave) + '" title="' + N.esc(l.chave) + '">' +
          N.esc(l.chave) + "</td>";
        l.celulas.forEach(function (c, i) {
          h += '<td class="cel" data-linha="' + N.esc(l.chave) + '" data-col="' + N.esc(d.colunas[i]) +
            '" data-v="' + c[estado.metrica] + '"></td>';
        });
        h += '<td class="n tot" data-v="' + l.total[estado.metrica] + '"></td></tr>';
      });
      h += '<tr><td class="lin tot">Total</td>';
      d.total_coluna.forEach(function (c) { h += '<th class="n tot tot-col" data-v="' + c[estado.metrica] + '"></th>'; });
      h += '<td class="n tot" data-v="' + d.total[estado.metrica] + '"></td></tr>';
      el.innerHTML = h + "</tbody></table>" +
        '<div class="t4 pequeno" id="mtz-legenda" style="padding:8px 12px"></div>';

      el.querySelectorAll("td.cel").forEach(function (td) {
        td.addEventListener("click", function () {
          var mesmo = estado.mChaveL === td.dataset.linha && estado.mChaveC === td.dataset.col;
          estado.mChaveL = mesmo ? "" : td.dataset.linha;
          estado.mChaveC = mesmo ? "" : td.dataset.col;
          estado.chave = "";
          document.querySelectorAll("#agregado tr.sel").forEach(function (x) { x.classList.remove("sel"); });
          marcarMatriz(); itens();
        });
      });
      el.querySelectorAll("td.lin.clicavel").forEach(function (td) {
        td.addEventListener("click", function () {
          var mesmo = estado.mChaveL === td.dataset.linha && !estado.mChaveC;
          estado.mChaveL = mesmo ? "" : td.dataset.linha;
          estado.mChaveC = ""; estado.chave = "";
          document.querySelectorAll("#agregado tr.sel").forEach(function (x) { x.classList.remove("sel"); });
          marcarMatriz(); itens();
        });
      });
      el.querySelectorAll("th.cab-col").forEach(function (th) {
        th.addEventListener("click", function () {
          var mesmo = estado.mChaveC === th.dataset.col && !estado.mChaveL;
          estado.mChaveC = mesmo ? "" : th.dataset.col;
          estado.mChaveL = ""; estado.chave = "";
          document.querySelectorAll("#agregado tr.sel").forEach(function (x) { x.classList.remove("sel"); });
          marcarMatriz(); itens();
        });
      });
      pintarMatriz(); marcarMatriz();
  }

  /* -------------------------------------------------------------- rede */
  function tabelaRede(el, lista, colExtra) {
    if (!lista.length) { el.innerHTML = '<div class="vazio">nada a apontar</div>'; return; }
    var h = '<table class="tb"><thead><tr><th>Item</th><th>Faixa</th><th class="n">CD</th>' +
      '<th class="n">Rede</th><th class="n">Lojas</th><th class="n">' + colExtra.rotulo + "</th></tr></thead><tbody>";
    lista.forEach(function (r) {
      h += '<tr class="clicavel" data-sku="' + N.esc(r.sku) + '"><td>' + N.esc(r.item) +
        '<div class="t4 pequeno">' + N.esc(r.sku) + " · " + N.esc(r.fornecedor) + "</div></td>" +
        "<td>" + selo(r.faixa) + "</td>" +
        '<td class="n">' + N.num(r.estoque_fisico) + "</td>" +
        '<td class="n">' + N.num(r.saldo_lojas) + "</td>" +
        '<td class="n">' + N.num(r.lojas_com_saldo) + "</td>" +
        '<td class="n">' + colExtra.valor(r) + "</td></tr>";
    });
    el.innerHTML = h + "</tbody></table>";
    el.querySelectorAll("tr.clicavel").forEach(function (tr) {
      tr.addEventListener("click", function () { if (window.abrirItem) abrirItem(tr.dataset.sku); });
    });
  }
  function rede() {
    N.buscar("/api/diagnostico/rede" + qs(), true).then(function (d) {
      tabelaRede(document.getElementById("rede-transferir"), d.transferir,
        { rotulo: "Precisa no período", valor: function (r) { return N.num(r.mu_periodo, 1); } });
      tabelaRede(document.getElementById("rede-parado"), d.parado_em_loja,
        { rotulo: "Valor em loja", valor: function (r) { return rs(r.valor_lojas_erp); } });
    });
  }

  /* ------------------------------------------------------------- itens */
  function itens() {
    var el = document.getElementById("itens"); N.espera(el);
    var f = filtros();
    var busca = { faixa: estado.faixa, por: f.por, chave: f.chave, por2: f.por2, chave2: f.chave2, q: estado.q };
    var rotulo = (estado.faixa || "todas as faixas");
    if (f.chave) rotulo += " · " + rotuloPor(f.por).toLowerCase() + " = " + f.chave;
    if (f.chave2) rotulo += " · " + rotuloPor(f.por2).toLowerCase() + " = " + f.chave2;
    document.getElementById("itens-filtro").textContent = rotulo + (estado.q ? ' · "' + estado.q + '"' : "");
    document.getElementById("bt-csv").href = "/diagnostico.csv" + qs(busca);
    marcarLegenda();
    N.buscar("/api/diagnostico/itens" + qs(busca), true).then(function (d) {
      var h = '<table class="tb" id="tb-itens"><thead><tr>' +
        "<th>Item</th><th>Fornecedor</th><th>Comprador</th><th>Classe</th><th>Faixa</th><th>Ação</th>" +
        '<th class="n">Físico</th><th class="n">Trânsito</th><th class="n">Rede</th>' +
        '<th class="n">Ponto de pedido</th><th class="n">Máximo</th><th class="n">Cobertura</th>' +
        '<th class="n">Dias sem venda</th><th class="n">Idade FIFO</th>' +
        '<th class="n">Custo unitário</th><th class="n">Custo contábil</th>' +
        '<th class="n">Capital</th><th class="n">Excesso</th><th class="n">Lucro perdido</th>' +
        "</tr></thead><tbody>";
      d.itens.forEach(function (r) {
        h += '<tr class="clicavel" data-sku="' + N.esc(r.sku) + '">' +
          "<td>" + N.esc(r.item) + '<div class="t4 pequeno">' + N.esc(r.sku) + " · " + N.esc(r.familia) +
            (r.origem && r.origem !== "Nao informado" ? " · " + N.esc(r.origem) : "") + "</div></td>" +
          "<td>" + N.esc(r.fornecedor) + "</td><td>" + N.esc(r.comprador) + "</td>" +
          "<td>" + N.seloClasse((r.curva_abc || "") + (r.classe_xyz || "")) + "</td>" +
          "<td>" + selo(r.faixa) + "</td><td>" + N.esc(r.acao) + "</td>" +
          '<td class="n">' + N.num(r.estoque_fisico) + "</td>" +
          '<td class="n">' + N.num(r.em_transito) + "</td>" +
          '<td class="n">' + N.num(r.saldo_lojas) + "</td>" +
          '<td class="n">' + N.num(r.ponto_de_pedido) + "</td>" +
          '<td class="n">' + N.num(r.estoque_maximo) + "</td>" +
          '<td class="n">' + N.num(r.cobertura_dias, 0) + "</td>" +
          '<td class="n" data-v="' + (r.dias_sem_venda === null ? 99999 : r.dias_sem_venda) + '">' +
            (r.dias_sem_venda === null ? "nunca" : N.num(r.dias_sem_venda)) + "</td>" +
          '<td class="n" data-v="' + (r.idade_fifo_dias === null ? -1 : r.idade_fifo_dias) + '">' +
            (r.idade_fifo_dias === null ? "–" : N.num(r.idade_fifo_dias, 0) + (r.entradas_cobrem_saldo ? "" : "*")) + "</td>" +
          '<td class="n">' + N.num(r.custo_unitario, 2) + "</td>" +
          '<td class="n">' + (r.custo_medio_erp === null ? "–" : N.num(r.custo_medio_erp, 2)) + "</td>" +
          '<td class="n" data-v="' + r.capital_modelo + '">' + rs(r.capital_modelo) + "</td>" +
          '<td class="n" data-v="' + r.excesso_valor + '">' + (r.excesso_valor ? rs(r.excesso_valor) : "–") + "</td>" +
          '<td class="n" data-v="' + r.lucro_perdido_ruptura + '">' + rs(r.lucro_perdido_ruptura) + "</td>" +
          "</tr>";
      });
      el.innerHTML = h + "</tbody></table>" +
        '<div class="t4 pequeno" style="padding:8px 12px">' + N.num(d.total) + " itens · * idade estimada: as entradas registradas não cobrem o saldo</div>";
      N.tabela("#tb-itens", { col: 16, dir: "desc" });
      el.querySelectorAll("tr.clicavel").forEach(function (tr) {
        tr.addEventListener("click", function () { if (window.abrirItem) abrirItem(tr.dataset.sku); });
      });
    });
  }

  /* --------------------------------------------------------- confianca */
  function confianca() {
    N.buscar("/api/diagnostico/confianca" + qs(), true).then(function (d) {
      document.querySelectorAll(".conf").forEach(function (el) {
        var b = d.blocos[el.dataset.bloco]; if (!b) return;
        el.classList.remove("ok", "aviso", "erro");
        if (b.nivel) el.classList.add(b.nivel);
        el.querySelector("span").textContent =
          (b.nivel ? "qualidade: " + b.nivel : "qualidade: abra /qualidade para medir") +
          (b.outliers ? " · " + N.num(b.outliers) + " outliers" : "");
      });
    });
  }

  /* ----------------------------------------------------------- eventos */
  document.getElementById("abas-por").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-por]"); if (!b) return;
    document.querySelectorAll("#abas-por button").forEach(function (x) { x.classList.remove("on"); });
    b.classList.add("on"); estado.por = b.dataset.por; estado.chave = "";
    agregado(); itens();
  });
  document.getElementById("abas-mtz").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-linha]"); if (!b) return;
    document.querySelectorAll("#abas-mtz button").forEach(function (x) { x.classList.remove("on"); });
    b.classList.add("on"); estado.mLinha = b.dataset.linha; estado.mChaveL = "";
    document.querySelector("#secao-mtz h2").textContent = "Comprador × " + rotuloPor(estado.mLinha).toLowerCase();
    matriz(); itens();
  });
  document.getElementById("sel-metrica").addEventListener("change", function (e) {
    /* trocar a leitura nao volta ao servidor: a celula ja veio com as cinco */
    estado.metrica = e.target.value;
    if (mDados) desenharMatriz();
  });
  document.getElementById("sel-faixa").addEventListener("change", function (e) { estado.faixa = e.target.value; itens(); });
  var tmr;
  document.getElementById("busca-itens").addEventListener("input", function (e) {
    clearTimeout(tmr); tmr = setTimeout(function () { estado.q = e.target.value.trim(); itens(); }, 250);
  });
  document.getElementById("bt-limpar").addEventListener("click", function () {
    estado.faixa = ""; estado.chave = ""; estado.q = "";
    estado.mChaveL = ""; estado.mChaveC = ""; marcarMatriz();
    document.getElementById("sel-faixa").value = ""; document.getElementById("busca-itens").value = "";
    agregado(); itens();
  });
  document.getElementById("dias-sem-giro").addEventListener("change", function (e) {
    var v = parseInt(e.target.value, 10); if (!v || v < 30) return;
    estado.dias = v; tudo();
  });
  document.getElementById("ate").addEventListener("change", function (e) {
    estado.ate = e.target.value || ""; tudo();
  });

  function tudo() { geral(); idade(); agregado(); matriz(); rede(); itens(); confianca(); }
  tudo();
})();
