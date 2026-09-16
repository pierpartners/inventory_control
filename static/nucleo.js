/* =====================================================================
   NUCLEO - camada compartilhada do painel de estoque
   Formatadores pt-BR, tema proprio de graficos, motor de tabela e gaveta.
   ===================================================================== */
(function (raiz) {
  "use strict";

  /* -------------------------------------------------------- 1. formatos */
  /* As cores vem do CSS, nao daqui.

     O ECharts recebe cor como VALOR: um grafico montado com "#37D6A0" fica
     com "#37D6A0" para sempre, indiferente a qualquer troca de variavel. Se
     esta tabela tivesse hex cravado, trocar para o tema claro deixaria a
     pagina clara e todos os graficos na paleta escura.

     Os valores abaixo sao o fallback para o caso de o CSS nao ter carregado
     (ou de `getComputedStyle` falhar): sao exatamente o tema escuro, que e o
     `:root` nu do nucleo.css. */
  var COR = {
    ambar: "#F2A93B", ambarEsc: "#C4801F", menta: "#37D6A0", coral: "#FF6A5E",
    ceu: "#55B4F2", violeta: "#A78BFA", tinta: "#E9F0F7", tinta2: "#94A5B6",
    tinta3: "#5D6C7C", tinta4: "#3E4A57", linha: "#212B36", linha2: "#19212A",
    painel: "#11171E", painel2: "#161D26", painel3: "#1C242E",
    fundo: "#0C1015", vazio: "#090C10",
    serie6: "#E2E8F0", serie7: "#F472B6", serie8: "#94A5B6"
  };
  var DE_CSS = {
    ambar: "--ambar", ambarEsc: "--ambar-2", menta: "--menta", coral: "--coral",
    ceu: "--ceu", violeta: "--violeta", tinta: "--tinta", tinta2: "--tinta-2",
    tinta3: "--tinta-3", tinta4: "--tinta-4", linha: "--linha", linha2: "--linha-2",
    painel: "--painel", painel2: "--painel-2", painel3: "--painel-3",
    fundo: "--fundo", vazio: "--void",
    serie6: "--serie-6", serie7: "--serie-7", serie8: "--serie-8"
  };
  function lerCoresDoCSS() {
    try {
      var cs = getComputedStyle(document.documentElement);
      for (var k in DE_CSS) {
        var v = cs.getPropertyValue(DE_CSS[k]).trim();
        if (v) COR[k] = v;   /* muta no lugar: N.COR ja esta referenciado */
      }
    } catch (e) {}
  }
  lerCoresDoCSS();

  var SERIE = [COR.ambar, COR.ceu, COR.menta, COR.violeta, COR.coral,
               COR.serie6, COR.serie7, COR.serie8];

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
  /* Object.assign e raso: quem passa `axisLabel: {color}` apagaria margem,
     fonte e hideOverlap junto. Estes dois merges evitam isso - e e por
     hideOverlap que os rotulos de eixo param de se empilhar. */
  function fundir(base, extra, chaves) {
    var r = Object.assign({}, base, extra || {});
    chaves.forEach(function (k) {
      if (base[k] && extra && extra[k]) r[k] = Object.assign({}, base[k], extra[k]);
    });
    return r;
  }
  function eixoX(extra) {
    return fundir({
      type: "category",
      axisLine: { lineStyle: { color: COR.linha } },
      axisTick: { show: false },
      axisLabel: { color: COR.tinta3, fontSize: 10.5, margin: 11, hideOverlap: true },
      splitLine: { show: false },
      boundaryGap: true,
      nameTextStyle: { color: COR.tinta4, fontSize: 10 }
    }, extra, ["axisLabel", "axisLine", "splitLine", "nameTextStyle"]);
  }
  function eixoY(extra) {
    return fundir({
      type: "value",
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: COR.tinta3, fontSize: 10.5, margin: 12, hideOverlap: true,
        fontFamily: '"JetBrains Mono", monospace' },
      splitLine: { lineStyle: { color: COR.linha2, type: [3, 4] } },
      nameTextStyle: { color: COR.tinta4, fontSize: 10 }
    }, extra, ["axisLabel", "axisLine", "splitLine", "nameTextStyle"]);
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

  /* ---------------------------------------------- series com cor coerente */
  /* O quadradinho da legenda do ECharts vem de `itemStyle`, nao de
     `lineStyle`. Definir so a cor da linha faz a legenda mostrar a cor da
     paleta padrao - foi assim que quase todo grafico daqui ficou com legenda
     de uma cor e linha de outra. Estes dois construtores fecham a porta:
     a cor entra uma vez e vale para linha, area, ponto e legenda. */
  function serieLinha(nome, dados, cor, extra) {
    extra = extra || {};
    var s = {
      name: nome, type: "line", data: dados, symbol: "none",
      itemStyle: { color: cor },                       // <- a legenda le daqui
      lineStyle: Object.assign({ color: cor, width: 2 }, extra.lineStyle || {}),
    };
    if (extra.area) s.areaStyle = { color: area(cor, extra.area === true ? 0.2 : extra.area) };
    Object.keys(extra).forEach(function (k) {
      if (k !== "lineStyle" && k !== "area") s[k] = extra[k];
    });
    return s;
  }

  function serieBarra(nome, dados, cor, extra) {
    extra = extra || {};
    var s = {
      name: nome, type: "bar", data: dados,
      itemStyle: Object.assign({ color: cor }, extra.itemStyle || {}),
    };
    Object.keys(extra).forEach(function (k) {
      if (k !== "itemStyle") s[k] = extra[k];
    });
    return s;
  }

  /* ------------------------------------------- marcadores verticais */
  /* Varios marcadores no mesmo lugar viram um borrao de texto. Aqui os que
     caem praticamente no mesmo x sao fundidos num unico rotulo, e os que
     sobram sao escalonados na vertical para nao encavalar. */
  function marcasX(itens, opcoes) {
    opcoes = opcoes || {};
    var faixa = opcoes.faixa || 1;                  // largura total do eixo
    var minimo = opcoes.minimo || 0;                // origem do eixo
    var junta = (opcoes.tolerancia || 0.035) * faixa;
    var alturas = opcoes.alturas || [8, 28, 48, 68];
    var ordenados = itens.slice().filter(function (m) {
      return m && isFinite(m.x);
    }).sort(function (a, b) { return a.x - b.x; });

    // marcadores vizinhos viram um rotulo unico: dois textos empilhados no
    // mesmo pixel nao se leem, e a distincao de 1% nao interessa a ninguem
    var grupos = [];
    ordenados.forEach(function (m) {
      var g = grupos[grupos.length - 1];
      if (g && Math.abs(m.x - g.x) <= junta) {
        if (m.ativo) { g.cor = m.cor; g.ativo = true; g.textos.unshift(m.texto); }
        else { g.textos.push(m.texto); }
      } else {
        grupos.push({ x: m.x, cor: m.cor, ativo: !!m.ativo, textos: [m.texto] });
      }
    });

    return grupos.map(function (g, i) {
      // rotulo sempre para dentro do grafico: encostado na direita ele sai
      // pela borda, encostado na esquerda ele e cortado pelo eixo
      var esquerda = (g.x - minimo) / faixa < 0.62;
      return {
        xAxis: g.x,
        lineStyle: { color: g.cor, width: g.ativo ? 2 : 1,
                     type: g.ativo ? "solid" : [4, 4] },
        label: {
          color: g.cor, fontSize: 10.5, rotate: 0,
          position: "insideEndTop", align: esquerda ? "left" : "right",
          verticalAlign: "top",
          distance: [esquerda ? 6 : -6, alturas[i % alturas.length]],
          formatter: (g.ativo ? "▸ " : "") + g.textos.join("  ·  "),
          backgroundColor: "rgba(9,12,16,.86)", padding: [3, 6], borderRadius: 3,
          borderColor: g.cor, borderWidth: g.ativo ? 1 : 0,
        },
      };
    });
  }

  /* marcador horizontal unico (teto, limite).
     O merge de `label` e profundo de proposito: quem so quer mudar a posicao
     nao pode perder o formatter no caminho - foi assim que o rotulo "caixa
     do ciclo" desapareceu do grafico da fila. */
  function marcaY(valor, cor, texto, extra) {
    return fundir({
      yAxis: valor,
      lineStyle: { color: cor, width: 1.5, type: [5, 4] },
      label: { color: cor, fontSize: 10.5, position: "insideEndTop", rotate: 0,
               formatter: texto, backgroundColor: "rgba(9,12,16,.86)",
               padding: [3, 6], borderRadius: 3 },
    }, extra, ["label", "lineStyle"]);
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
  /* =================================================================
     Preferencias: tema e daltonismo.

     Gravar e recarregar. O script do <head> aplica antes da pintura, entao
     nao ha piscada; e o recarregamento e o unico caminho que garante grafico
     repintado, porque canvas ja desenhado nao acompanha variavel de CSS.
  ================================================================= */
  function lerPref(k, padrao) {
    try { return localStorage.getItem(k) || padrao; } catch (e) { return padrao; }
  }
  function gravarPref(k, v) {
    try { localStorage.setItem(k, v); } catch (e) {}
  }
  function montarPrefs() {
    var r = document.documentElement;
    var tema = r.getAttribute("data-tema") || "escuro";
    var dalt = r.getAttribute("data-daltonismo") === "on";

    document.querySelectorAll("[data-tema-btn]").forEach(function (b) {
      if (b.dataset.temaBtn === tema) b.classList.add("on");
      b.addEventListener("click", function () {
        gravarPref("nucleo-tema", b.dataset.temaBtn);
        raiz.location.reload();
      });
    });

    var bd = document.getElementById("btn-daltonismo");
    if (bd) {
      if (dalt) bd.classList.add("on");
      var rot = document.getElementById("rot-daltonismo");
      if (rot) rot.textContent = dalt ? "Daltonismo: ligado" : "Modo daltonismo";
      bd.setAttribute("aria-pressed", dalt ? "true" : "false");
      bd.addEventListener("click", function () {
        gravarPref("nucleo-daltonismo", dalt ? "off" : "on");
        raiz.location.reload();
      });
    }
  }

  /* =================================================================
     O "!" de ajuda, pendurado automaticamente.

     Por que automatico e nao escrito a mao em cada <th>: sao 163 cabecalhos
     de tabela em nove telas, mais titulo de painel, rotulo de metrica e
     formula - e boa parte deles e montada por JS em tempo de execucao (a
     lista do backtest, a gaveta do item, a conferencia). Escrever a mao
     significaria manter a mesma definicao repetida em varios arquivos, e
     divergir. Aqui a definicao mora num lugar so, o glossario.

     O casamento e por texto normalizado - minusculo, sem acento, sem
     pontuacao -, entao "Custo/peça" e "custo peca" chegam na mesma chave e
     um mesmo verbete serve a todas as telas que usam aquele rotulo.

     Quem ja tem "!" escrito a mao fica como esta: a tela de backtest tem
     descricoes especificas do contexto dela, mais precisas que um verbete
     geral, e sobrescrever seria perder informacao.
  ================================================================= */
  var SEM_ACENTO = /[\u0300-\u036f]/g;
  function chaveGlossario(t) {
    return String(t).normalize("NFKD").replace(SEM_ACENTO, "")
      .toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  }
  /* pega so o texto proprio do elemento, sem o de filhos que sao legenda,
     contador ou o proprio pin - senao a chave nunca casa */
  function textoDoRotulo(el) {
    var t = "";
    for (var i = 0; i < el.childNodes.length; i++) {
      var n = el.childNodes[i];
      if (n.nodeType === 3) { t += n.nodeValue; continue; }
      if (n.nodeType !== 1) continue;
      if (n.classList && (n.classList.contains("pin") ||
                          n.classList.contains("dica") ||
                          n.classList.contains("q") ||
                          n.classList.contains("suf") ||
                          n.classList.contains("un") ||
                          n.classList.contains("t4"))) continue;
      if (n.tagName === "SVG" || n.tagName === "svg") continue;
      t += n.textContent;
    }
    return t.replace(/\s+/g, " ").trim();
  }

  var ALVOS = "table.tb th, .painel-cab h2, .secao-cab h2, .metrica .rotulo, " +
              ".rp .perg, .formula .titulo, .kv > .k";

  function pinarUm(el) {
    if (!el || el.querySelector(":scope > .pin")) return 0;
    var txt = textoDoRotulo(el);
    /* `data-ajuda` no proprio elemento vence o glossario. E a valvula de
       escape para o rotulo que nao da para indexar por texto - coluna de um
       simbolo so, como "#" ou "μ", cuja chave normalizada fica vazia - e para
       o caso em que uma tela precisa de uma descricao mais especifica que o
       verbete geral. */
    var d = el.getAttribute("data-ajuda");
    if (!d) {
      var g = raiz.N && raiz.N.GLOSSARIO;
      if (!g) return 0;
      if (!txt || txt.length > 90) return 0;
      d = g[chaveGlossario(txt)];
    }
    if (!d) return 0;
    var sp = document.createElement("span");
    sp.className = "pin";
    sp.setAttribute("title", d);
    sp.setAttribute("tabindex", "0");
    sp.setAttribute("role", "note");
    sp.setAttribute("aria-label", txt + ": " + d);
    sp.textContent = "!";
    el.appendChild(sp);
    return 1;
  }

  function pinar(raizEl) {
    var base = raizEl || document;
    var n = 0;
    if (base.matches && base.matches(ALVOS)) n += pinarUm(base);
    var lista = base.querySelectorAll ? base.querySelectorAll(ALVOS) : [];
    for (var i = 0; i < lista.length; i++) n += pinarUm(lista[i]);
    return n;
  }

  /* o que e montado por JS depois - lista do backtest, gaveta, conferencia -
     tambem recebe o "!", sem precisar que cada um desses lugares se lembre de
     chamar pinar(). Em lote, num rAF, para nao pinar linha por linha
     enquanto uma tabela de mil linhas esta sendo escrita. */
  var _pend = [], _agendado = false;
  /* MICROTAREFA, nao requestAnimationFrame.
     rAF nao dispara em aba de fundo nem em painel oculto, e a tabela montada
     ali ficaria sem "!" ate a aba receber foco - foi exatamente o que
     aconteceu no primeiro teste, com tres cabecalhos do painel sem pin porque
     a tabela deles e montada depois do fetch. A microtarefa roda ao fim da
     tarefa atual, sempre, e junta num passo so todas as escritas de innerHTML
     da mesma funcao. */
  function agendarPin() {
    if (_agendado) return;
    _agendado = true;
    var correr = function () {
      _agendado = false;
      var f = _pend; _pend = [];
      for (var k = 0; k < f.length; k++) { try { pinar(f[k]); } catch (e) {} }
    };
    if (raiz.queueMicrotask) raiz.queueMicrotask(correr);
    else setTimeout(correr, 0);
  }
  function observarParaPinar() {
    if (!raiz.MutationObserver) return;
    new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var add = muts[i].addedNodes;
        for (var j = 0; j < add.length; j++)
          if (add[j].nodeType === 1) _pend.push(add[j]);
      }
      if (_pend.length) agendarPin();
    }).observe(document.body, { childList: true, subtree: true });
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
    serieLinha: serieLinha, serieBarra: serieBarra,
    marcasX: marcasX, marcaY: marcaY,
    grafico: grafico, espera: espera, buscar: buscar,
    tabela: tabela, abrir: abrir, fechar: fechar,
    barraPosicao: barraPosicao, categoriaMaisProxima: categoriaMaisProxima,
    corProduto: corProduto, corProdutoFundo: corProdutoFundo, pontoProduto: pontoProduto,
    seloClasse: seloClasse, seloRegime: seloRegime,
    seloDecisao: seloDecisao, seloRisco: seloRisco,
    pinar: pinar, chaveGlossario: chaveGlossario, GLOSSARIO: {}
  };

  /* O arranque fica aqui, DEPOIS da exportacao. `pinarUm` le
     `raiz.N.GLOSSARIO`, e `raiz.N` so passa a existir na linha acima - iniciar
     antes daria zero pin, em silencio. E o glossario.js, carregado a seguir,
     preenche o objeto antes do DOMContentLoaded. */
  function iniciar() {
    montarPrefs();
    pinar(document);
    observarParaPinar();
  }
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", iniciar);
  else iniciar();
})(window);
