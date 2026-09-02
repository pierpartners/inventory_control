/* =====================================================================
   NUCLEO - camada compartilhada do painel de estoque
   Formatadores pt-BR, tema proprio de graficos, motor de tabela e gaveta.
   ===================================================================== */
(function (raiz) {
  "use strict";

  /* -------------------------------------------------------- 1. formatos */
  var COR = {
    ambar: "#F2A93B", ambarEsc: "#C4801F", menta: "#37D6A0", coral: "#FF6A5E",
    ceu: "#55B4F2", violeta: "#A78BFA", tinta: "#E9F0F7", tinta2: "#94A5B6",
    tinta3: "#5D6C7C", tinta4: "#3E4A57", linha: "#212B36", linha2: "#19212A",
    painel: "#11171E", painel2: "#161D26", painel3: "#1C242E",
    fundo: "#0C1015", vazio: "#090C10"
  };
  var SERIE = [COR.ambar, COR.ceu, COR.menta, COR.violeta, COR.coral, "#E2E8F0", "#F472B6", "#94A5B6"];

  function num(v, casas) {
    if (v === null || v === undefined || isNaN(v)) return "–";
    return Number(v).toLocaleString("pt-BR", {
      minimumFractionDigits: casas || 0, maximumFractionDigits: casas || 0
    });
  }
  function moeda(v, casas) {
    if (v === null || v === undefined || isNaN(v)) return "–";
    return num(v, casas === undefined ? 0 : casas);
  }
  /* 1.234.567 -> 1,23 mi  (para eixos e rotulos apertados) */
  function curto(v) {
    if (v === null || v === undefined || isNaN(v)) return "–";
    var a = Math.abs(v), s = v < 0 ? "-" : "";
    if (a >= 1e9) return s + num(a / 1e9, 2) + " bi";
    if (a >= 1e6) return s + num(a / 1e6, 2) + " mi";
    if (a >= 1e3) return s + num(a / 1e3, a >= 1e5 ? 0 : 1) + " mil";
    return s + num(a, a < 10 && a % 1 !== 0 ? 1 : 0);
  }
  function pct(v, casas) {
    if (v === null || v === undefined || isNaN(v)) return "–";
    return num(v * 100, casas === undefined ? 1 : casas) + "%";
  }
  function data(d) {
    if (!d) return "–";
    var s = String(d).slice(0, 10).split("-");
    return s.length === 3 ? s[2] + "/" + s[1] : String(d);
  }
  function dataLonga(d) {
    if (!d) return "–";
    var s = String(d).slice(0, 10).split("-");
    if (s.length !== 3) return String(d);
    var m = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
    return s[2] + " " + m[parseInt(s[1], 10) - 1] + " " + s[0];
  }
  /* R$ com prefixo estilizado */
  function rs(v, casas) { return '<span class="pre">R$</span>' + moeda(v, casas); }

  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  /* ---------------------------------------------------- 2. tema grafico */
  var BASE_TEXTO = {
    color: COR.tinta2,
    fontFamily: '"Instrument Sans", Inter, system-ui, sans-serif',
    fontSize: 11
  };

  function dica(formatador) {
    return {
      trigger: "axis",
      backgroundColor: "rgba(9,12,16,.96)",
      borderColor: COR.linha,
      borderWidth: 1,
      padding: [9, 12],
      extraCssText: "border-radius:7px;box-shadow:0 12px 34px -10px rgba(0,0,0,.85);backdrop-filter:blur(6px);",
      textStyle: { color: COR.tinta, fontSize: 11.5, fontFamily: '"Instrument Sans", Inter, sans-serif' },
      axisPointer: {
        type: "line",
        lineStyle: { color: COR.tinta4, width: 1, type: [4, 4] },
        crossStyle: { color: COR.tinta4 },
        z: 1
      },
      formatter: formatador
    };
  }

  /* linha de titulo padrao dentro do tooltip */
  function dicaTit(txt) {
    return '<div style="font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:' +
      COR.tinta3 + ';margin-bottom:6px;font-weight:600">' + esc(txt) + "</div>";
  }
  function dicaLin(cor, rotulo, valor) {
    return '<div style="display:flex;align-items:center;gap:8px;margin:3px 0;">' +
      '<i style="width:8px;height:8px;border-radius:2px;background:' + cor + ';flex:0 0 8px"></i>' +
      '<span style="color:' + COR.tinta2 + '">' + esc(rotulo) + '</span>' +
      '<b style="margin-left:auto;font-family:\'JetBrains Mono\',monospace;color:' + COR.tinta +
      ';font-weight:500;font-variant-numeric:tabular-nums">' + valor + "</b></div>";
  }

  /* Eixo de categoria. boundaryGap fica em `true` (padrao do ECharts) porque
     e o unico valor correto quando ha barras: com `false` a barra fica
     centrada no tique e a primeira/ultima sai cortada pelo eixo. Graficos
     so de linha passam `boundaryGap: false` explicitamente. */
  function eixoX(extra) {
    return Object.assign({
      type: "category",
      axisLine: { lineStyle: { color: COR.linha } },
      axisTick: { show: false },
      axisLabel: { color: COR.tinta3, fontSize: 10.5, margin: 11 },
      splitLine: { show: false },
      boundaryGap: true
    }, extra || {});
  }
  function eixoY(extra) {
    return Object.assign({
      type: "value",
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: COR.tinta3, fontSize: 10.5, margin: 12,
        fontFamily: '"JetBrains Mono", monospace' },
      splitLine: { lineStyle: { color: COR.linha2, type: [3, 4] } },
      nameTextStyle: { color: COR.tinta4, fontSize: 10 }
    }, extra || {});
  }
  function grade(extra) {
    return Object.assign({ left: 8, right: 14, top: 20, bottom: 6, containLabel: true }, extra || {});
  }

  /* gradiente vertical para area */
  function area(cor, forca) {
    return {
      type: "linear", x: 0, y: 0, x2: 0, y2: 1,
      colorStops: [
        { offset: 0, color: sombra(cor, forca === undefined ? 0.28 : forca) },
        { offset: 1, color: sombra(cor, 0) }
      ]
    };
  }
  function sombra(hex, alfa) {
    var h = hex.replace("#", "");
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var n = parseInt(h, 16);
    return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + alfa + ")";
  }

  /* inicializa um grafico com o tema aplicado e responsividade */
  var _graficos = [];
  function grafico(el, opcoes) {
    if (typeof el === "string") el = document.getElementById(el);
    if (!el || !raiz.echarts) return null;
    var g = raiz.echarts.getInstanceByDom(el) || raiz.echarts.init(el, null, { renderer: "canvas" });
    g.setOption(Object.assign({
      color: SERIE,
      textStyle: BASE_TEXTO,
      animationDuration: 520,
      animationEasing: "cubicOut"
    }, opcoes), true);
    if (_graficos.indexOf(g) < 0) _graficos.push(g);
    return g;
  }
  var _tmr;
  raiz.addEventListener("resize", function () {
    clearTimeout(_tmr);
    _tmr = setTimeout(function () {
      _graficos.forEach(function (g) { try { g.resize(); } catch (e) {} });
    }, 90);
  });

  function espera(el) {
    if (typeof el === "string") el = document.getElementById(el);
    if (el) el.innerHTML = '<div class="carregando"><div class="girar"></div></div>';
  }

  /* ------------------------------------------------------- 3. requisicao */
  var _cache = {};
  function buscar(url, semCache) {
    if (!semCache && _cache[url]) return Promise.resolve(_cache[url]);
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (j) { _cache[url] = j; return j; });
  }

  /* ------------------------------------------------- 4. motor de tabela */
  /* Ordenacao por clique + filtro por texto, sem dependencia externa.    */
  function tabela(tbl, opcoes) {
    if (typeof tbl === "string") tbl = document.querySelector(tbl);
    if (!tbl) return null;
    opcoes = opcoes || {};
    var corpo = tbl.tBodies[0];
    /* linhas marcadas com data-fixo (separadores, marcadores de corte) nao
       entram na ordenacao nem no filtro - quem as coloca cuida delas */
    var linhas = Array.prototype.slice.call(corpo.rows).filter(function (tr) {
      return !tr.hasAttribute("data-fixo");
    });
    var estado = { col: opcoes.col === undefined ? null : opcoes.col, dir: opcoes.dir || "desc", txt: "" };

    function valor(tr, i) {
      var td = tr.cells[i];
      if (!td) return "";
      var v = td.getAttribute("data-v");
      if (v !== null) { var n = parseFloat(v); return isNaN(n) ? v.toLowerCase() : n; }
      var t = td.textContent.trim();
      var n2 = parseFloat(t.replace(/\./g, "").replace(",", ".").replace(/[^\d.\-]/g, ""));
      return isNaN(n2) ? t.toLowerCase() : n2;
    }

    function aplicar() {
      var vis = linhas;
      if (opcoes.filtro) vis = vis.filter(opcoes.filtro);
      if (estado.txt) {
        var q = estado.txt.toLowerCase();
        vis = vis.filter(function (tr) { return tr.textContent.toLowerCase().indexOf(q) >= 0; });
      }
      if (estado.col !== null) {
        var s = estado.dir === "asc" ? 1 : -1;
        vis = vis.slice().sort(function (a, b) {
          var x = valor(a, estado.col), y = valor(b, estado.col);
          if (x === y) return 0;
          if (typeof x === "number" && typeof y === "number") return (x - y) * s;
          return String(x) > String(y) ? s : -s;
        });
      }
      linhas.forEach(function (tr) { tr.style.display = "none"; });
      var frag = document.createDocumentFragment();
      vis.forEach(function (tr) { tr.style.display = ""; frag.appendChild(tr); });
      corpo.appendChild(frag);
      if (opcoes.aoFiltrar) opcoes.aoFiltrar(vis);
      if (opcoes.conta) {
        var c = document.querySelector(opcoes.conta);
        if (c) c.textContent = vis.length;
      }
    }

    Array.prototype.slice.call(tbl.tHead.rows[0].cells).forEach(function (th, i) {
      if (th.classList.contains("nao-ord")) return;
      th.classList.add("ord");
      if (!th.querySelector(".seta")) th.insertAdjacentHTML("beforeend", '<span class="seta"></span>');
      th.addEventListener("click", function () {
        if (estado.col === i) estado.dir = estado.dir === "asc" ? "desc" : "asc";
        else { estado.col = i; estado.dir = th.classList.contains("n") ? "desc" : "asc"; }
        Array.prototype.slice.call(tbl.tHead.rows[0].cells).forEach(function (o) {
          o.classList.remove("asc", "desc");
        });
        th.classList.add(estado.dir);
        aplicar();
      });
    });

    if (opcoes.busca) {
      var inp = document.querySelector(opcoes.busca);
      if (inp) inp.addEventListener("input", function () { estado.txt = inp.value; aplicar(); });
    }
    if (estado.col !== null) {
      var th0 = tbl.tHead.rows[0].cells[estado.col];
      if (th0) th0.classList.add(estado.dir);
    }
    aplicar();
    return { aplicar: aplicar, estado: estado };
  }

  /* ---------------------------------------------------------- 5. gaveta */
  var _gaveta, _veu;
  function gaveta() {
    if (_gaveta) return _gaveta;
    _veu = document.createElement("div"); _veu.className = "veu";
    _gaveta = document.createElement("aside"); _gaveta.className = "gaveta";
    _gaveta.innerHTML =
      '<div class="gaveta-cab"><div class="tit"><h2 id="gv-tit"></h2>' +
      '<div class="sub" id="gv-sub"></div></div>' +
      '<button class="fechar" id="gv-x" aria-label="Fechar">' +
      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>' +
      '</button></div><div class="gaveta-int" id="gv-int"></div>';
    document.body.appendChild(_veu); document.body.appendChild(_gaveta);
    _veu.addEventListener("click", fechar);
    _gaveta.querySelector("#gv-x").addEventListener("click", fechar);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") fechar(); });
    return _gaveta;
  }
  function abrir(titulo, sub, html) {
    gaveta();
    _gaveta.querySelector("#gv-tit").innerHTML = titulo;
    _gaveta.querySelector("#gv-sub").innerHTML = sub || "";
    _gaveta.querySelector("#gv-int").innerHTML = html || '<div class="carregando"><div class="girar"></div></div>';
    _veu.classList.add("on"); _gaveta.classList.add("on");
    return _gaveta.querySelector("#gv-int");
  }
  function fechar() {
    if (!_gaveta) return;
    _veu.classList.remove("on"); _gaveta.classList.remove("on");
  }

  /* ------------------------------------------------------ 6. utilitarios */
  /* barra posicao x ponto de pedido, desenhada em HTML */
  function barraPosicao(posicao, rop, maximo) {
    var teto = Math.max(posicao, rop, maximo || 0, 1);
    var p = Math.min(100, (posicao / teto) * 100);
    var r = Math.min(100, (rop / teto) * 100);
    var cor = posicao <= rop ? COR.coral : (posicao <= rop * 1.25 ? COR.ambar : COR.ceu);
    return '<div class="mini-barra"><div class="tr">' +
      '<i style="width:' + p.toFixed(1) + '%;background:' + cor + '"></i>' +
      '<b style="left:' + r.toFixed(1) + '%"></b></div>' +
      '<span class="vl">' + num(posicao) + "</span></div>";
  }

  /* ------------------------------------------------- cor por produto */
  /* A cor sai de um hash do SKU, entao e a mesma em qualquer tela e nao muda
     quando a lista e filtrada ou reordenada - o olho pode usar a cor para
     seguir um produto entre o plano, a corrida e a conferencia.
     Matiz espalhada pelo circulo; luminosidade alta o bastante para o texto
     escuro em cima do bloco continuar legivel no tema escuro. */
  var _cores = {};
  function corProduto(sku) {
    sku = String(sku || "");
    if (_cores[sku]) return _cores[sku];
    var h = 2166136261;
    for (var i = 0; i < sku.length; i++) {
      h ^= sku.charCodeAt(i);
      h = (h * 16777619) >>> 0;
    }
    var matiz = h % 360;
    // saturacao contida: no tema escuro, cor saturada em bloco pequeno vira
    // ruido. Aqui o objetivo e distinguir produtos, nao chamar atencao.
    var sat = 34 + ((h >>> 9) % 22);      // 34-55%
    var luz = 54 + ((h >>> 17) % 15);     // 54-68%
    _cores[sku] = "hsl(" + matiz + "," + sat + "%," + luz + "%)";
    return _cores[sku];
  }
  /* versao lavada, para fundo de linha de tabela */
  function corProdutoFundo(sku, alfa) {
    var c = corProduto(sku);
    return c.replace("hsl(", "hsla(").replace(")", "," + (alfa || 0.14) + ")");
  }
  function pontoProduto(sku, tam) {
    var t = tam || 9;
    return '<span style="display:inline-block;width:' + t + "px;height:" + t +
      "px;border-radius:3px;background:" + corProduto(sku) +
      ';flex:0 0 ' + t + 'px"></span>';
  }

  /* markLine sobre eixo de categoria so ancora em um valor que exista na
     lista; quando o eixo esta agrupado em faixas, usa-se a faixa mais proxima */
  function categoriaMaisProxima(lista, alvo) {
    if (!lista || !lista.length) return alvo;
    return lista.reduce(function (a, b) {
      return Math.abs(b - alvo) < Math.abs(a - alvo) ? b : a;
    });
  }

  function seloClasse(c) {
    if (!c) return "";
    return '<span class="classe classe-' + esc(c[0]) + '">' + esc(c) + "</span>";
  }
  function seloRegime(r) {
    var eh = /marginal/i.test(r || "");
    return '<span class="selo ' + (eh ? "selo-vi" : "selo-ce") + '">' +
      (eh ? "Unidade marginal" : "EOQ + normal") + "</span>";
  }
  function seloDecisao(d) {
    if (/AGORA/i.test(d)) return '<span class="selo selo-mt">Comprar agora</span>';
    if (/FORA DO TETO|SEGURAR/i.test(d)) return '<span class="selo selo-am">Fora do caixa</span>';
    if (/COMPENSA/i.test(d)) return '<span class="selo selo-nu">Não compensa</span>';
    return '<span class="selo selo-nu">' + esc(d || "–") + "</span>";
  }
  function seloRisco(v) {
    if (v >= 0.5) return '<span class="selo selo-cr">' + pct(v, 0) + "</span>";
    if (v >= 0.2) return '<span class="selo selo-am">' + pct(v, 0) + "</span>";
    return '<span class="selo selo-mt">' + pct(v, 0) + "</span>";
  }

  raiz.N = {
    COR: COR, SERIE: SERIE,
    num: num, moeda: moeda, curto: curto, pct: pct, data: data, dataLonga: dataLonga,
    rs: rs, esc: esc, sombra: sombra, area: area,
    dica: dica, dicaTit: dicaTit, dicaLin: dicaLin,
    eixoX: eixoX, eixoY: eixoY, grade: grade,
    grafico: grafico, espera: espera, buscar: buscar,
    tabela: tabela, abrir: abrir, fechar: fechar,
    barraPosicao: barraPosicao, categoriaMaisProxima: categoriaMaisProxima,
    corProduto: corProduto, corProdutoFundo: corProdutoFundo, pontoProduto: pontoProduto,
    seloClasse: seloClasse, seloRegime: seloRegime,
    seloDecisao: seloDecisao, seloRisco: seloRisco
  };
})(window);
