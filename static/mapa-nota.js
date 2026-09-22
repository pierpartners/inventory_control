/* =====================================================================
   O mapa da nota - a estacao 0 da metodologia.

   Mostra, com os numeros do SKU aberto, como a nota se monta: o lado que
   RENDE (P, M, L -> V) e o lado que PRENDE (c, D -> reais x dia),
   encontrando-se em V / (c x D).

   A topologia e fixa e mora no HTML (templates/metodologia.html); aqui so
   entram os numeros, os fios entre as caixas e o realce. Os numeros vem do
   mesmo /api/item/{sku} que pinta as estacoes 3 e 5 - nenhuma conta e
   refeita aqui, entao o mapa nao pode discordar das equacoes de la.
   ===================================================================== */
(function (raiz) {
  "use strict";

  /* A unica descricao do grafo. Os fios, o realce do caminho e o grupo do
     capital saem todos daqui - mexer no desenho e mexer nesta lista. */
  var FIOS = [
    ["demanda", "p"],
    ["lucro", "m"],
    ["aluguel", "l"], ["encalhe", "l"],
    ["h", "d"], ["receb", "d"], ["pag", "d"],
    ["p", "v"], ["m", "v"], ["l", "v"],
    ["c", "cd"], ["d", "cd"],
    ["v", "nota"], ["cd", "nota"]
  ];

  /* As caixas em que o custo do capital aparece. E a resposta da pergunta
     que este mapa existe para responder, entao fica declarada, nao inferida. */
  var CAPITAL = ["aluguel", "c", "receb", "pag", "d"];

  var SVG = "http://www.w3.org/2000/svg";

  function no(k) { return document.getElementById("mn-" + k); }

  /* dias com casa decimal so quando ela existe: sem isso a soma
     H + recebimento - pagamento nao fecha na tela, e o mapa mente */
  function dias(v) { return N.num(v, Math.abs(v % 1) > 0.049 ? 1 : 0); }
  function und(v) { return Math.abs(Math.abs(v) - 1) < 1e-9 ? "dia" : "dias"; }
  function tela() { return document.getElementById("mn-tela"); }

  /* Escreve valor e legenda de uma caixa. `s` ausente mantem a do HTML. */
  function por(k, v, s) {
    var el = no(k);
    if (!el) return;
    var ev = el.querySelector(".v");
    if (ev) ev.innerHTML = v;
    if (s !== undefined) {
      var es = el.querySelector(".s");
      if (es) es.innerHTML = s;
    }
  }

  /* Tudo o que descende de `k`, ele incluso: o caminho dele ate a nota. */
  function adiante(k) {
    var vistos = {}, pilha = [k];
    while (pilha.length) {
      var a = pilha.pop();
      if (vistos[a]) continue;
      vistos[a] = true;
      FIOS.forEach(function (f) { if (f[0] === a) pilha.push(f[1]); });
    }
    return vistos;
  }

  /* ---------------------------------------------------------------- fios */

  /* Cotovelo de `origem` (fundo da caixa) ate `destino` (topo da caixa):
     desce, vira no meio do vao, desce de novo. O raio se encolhe sozinho
     quando o vao e curto, senao a curva estoura para fora do caminho. */
  function cotovelo(sx, sy, tx, ty) {
    if (Math.abs(tx - sx) < 2) return "M" + sx + " " + sy + "V" + ty;
    var my = (sy + ty) / 2;
    var r = Math.min(9, Math.abs(ty - sy) / 3, Math.abs(tx - sx) / 2);
    var dx = tx > sx ? 1 : -1;
    return "M" + sx + " " + sy +
      "V" + (my - r) +
      "Q" + sx + " " + my + " " + (sx + dx * r) + " " + my +
      "H" + (tx - dx * r) +
      "Q" + tx + " " + my + " " + tx + " " + (my + r) +
      "V" + ty;
  }

  function desenhar() {
    var t = tela(), svg = document.getElementById("mn-svg");
    if (!t || !svg) return;
    var cx = t.getBoundingClientRect();
    if (!cx.width) return;
    svg.setAttribute("viewBox", "0 0 " + cx.width + " " + cx.height);
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    FIOS.forEach(function (f) {
      var a = no(f[0]), b = no(f[1]);
      if (!a || !b) return;
      var ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      var sx = ra.left - cx.left + ra.width / 2, sy = ra.bottom - cx.top;
      var tx = rb.left - cx.left + rb.width / 2, ty = rb.top - cx.top - 2;
      /* caixa que ficou ACIMA da origem (so acontece quando a tela quebra
         em uma coluna so): o fio ainda liga certo, mas sem ponta para baixo */
      if (ty < sy) { ty = rb.bottom - cx.top + 2; }

      var g = document.createElementNS(SVG, "g");
      g.setAttribute("class", "mn-fio");
      g.dataset.de = f[0];
      g.dataset.para = f[1];

      var l = document.createElementNS(SVG, "path");
      l.setAttribute("class", "l");
      l.setAttribute("d", cotovelo(sx, sy, tx, ty - 5));
      g.appendChild(l);

      var p = document.createElementNS(SVG, "path");
      p.setAttribute("class", "p");
      p.setAttribute("d", "M" + tx + " " + ty + "l-4 -6 h8 z");
      g.appendChild(p);

      svg.appendChild(g);
    });
  }

  /* -------------------------------------------------------------- realce */

  function limpar() {
    var t = tela();
    if (!t) return;
    t.classList.remove("acesa");
    t.querySelectorAll(".mn-no, .mn-fio").forEach(function (e) {
      e.classList.remove("aceso", "capital");
    });
  }

  /* `classe` e "aceso" (caminho ate a nota) ou "capital" (as caixas do
     custo do capital). No capital nao ha caminho: as caixas acendem soltas,
     porque e exatamente isso que se quer mostrar - ele entra em tres lugares
     que nao se falam. */
  function acender(chaves, classe) {
    var t = tela();
    if (!t) return;
    limpar();
    t.classList.add("acesa");
    var alvo = {};
    chaves.forEach(function (k) { alvo[k] = true; });
    Object.keys(alvo).forEach(function (k) {
      var e = no(k);
      if (e) e.classList.add(classe);
    });
    t.querySelectorAll(".mn-fio").forEach(function (g) {
      if (alvo[g.dataset.de] && alvo[g.dataset.para]) g.classList.add(classe);
    });
  }

  /* ------------------------------------------------------------ pintura */

  raiz.mapaNota = function (d) {
    var t = tela();
    if (!t) return;
    var m = d.item, ec = d.economia, pr = d.parametros, ca = d.canais || {};
    var primeira = (d.escada || [])[0];
    /* sem escada nao ha proxima peca para medir - e o mapa nao tem numeros
       para mostrar. Dizer isso, em vez de desenhar dezesseis travessoes. */
    var vazio = document.getElementById("mn-vazio");
    if (vazio) vazio.hidden = !!primeira;
    t.hidden = !primeira;
    if (!primeira) return;

    var H = Number(m.periodo_protecao_dias) || 0;
    var rec = Number(m.prazo_recebimento_dias) || 0;
    var pag = Number(m.prazo_pagamento_dias) || 0;
    var D = Number(m.dias_capital);
    if (!D && D !== 0) D = H;
    var lam = Number(m.premio_escassez) || 0;
    var share = Number((ca.ecommerce || {}).share) || 0;
    var dias_ano = pr.dias_por_ano || 365;

    /* ---- lado que rende */
    /* itens de giro baixo tem mu < 1: com uma casa so, 0,15 vira 0,2 e nao
       da mais para conferir o P que sai dele */
    var casas = Math.abs(m.mu_periodo) >= 100 ? 0 : (Math.abs(m.mu_periodo) >= 1 ? 1 : 2);
    por("demanda", N.num(m.mu_periodo, casas) + " <span class='suf'>±</span> " +
        N.num(m.sd_periodo, casas),
        "μ e σ em " + dias(H) + " dias");
    /* o fator de perda na ruptura nao ganha caixa propria: e um multiplicador
       do lucro, e cabe na legenda sem virar mais um no no grafo */
    por("lucro", "R$ " + N.moeda(ec.lucro_por_peca, 2),
        "e " + N.pct(pr.fator_perda_ruptura_ecommerce, 0) + " / " +
        N.pct(pr.fator_perda_ruptura_lojas, 0) + " some na falta" +
        (share > 0.005 && share < 0.995
          ? " · " + N.pct(share, 0) + " e-comm" : ""));
    por("aluguel", "R$ " + N.moeda(ec.custo_carregar, 2),
        "c × " + (lam > 0.0005
          ? "(" + N.pct(pr.taxa_manutencao_ano, 0) + " + λ " + N.num(lam, 3) + ")"
          : N.pct(pr.taxa_manutencao_ano, 0)) +
        " × " + dias(H) + "/" + N.num(dias_ano, 0));
    por("encalhe", "R$ " + N.moeda(ec.custo_obsolescencia, 2),
        N.pct(P_encalhe(pr, ec), 0) + " do custo");

    por("p", N.pct(primeira.p_vender, 0),
        "P(demanda ≥ " + N.num(primeira.unidade) + ") · a próxima peça");
    por("m", "R$ " + N.moeda(ec.margem_se_vender, 2), "M = lucro × fator do canal");
    por("l", "R$ " + N.moeda(ec.perda_se_encalhar, 2), "L = aluguel + encalhe");
    por("v", "R$ " + N.moeda(primeira.valor, 2), "V = P×M − (1−P)×L");

    /* ---- lado que prende */
    por("c", "R$ " + N.moeda(ec.custo_unitario, 2), "c · custo de uma peça");
    por("h", dias(H) + " <span class='suf'>" + und(H) + "</span>", "H = prazo + revisão");
    por("receb", "+" + dias(rec) + " <span class='suf'>" + und(rec) + "</span>",
        "até a venda virar caixa");
    por("pag", "−" + dias(pag) + " <span class='suf'>" + und(pag) + "</span>",
        "o que o fornecedor financia");
    /* o piso de 1 dia de `ciclo_financeiro` morde quando o fornecedor
       financia mais do que o ciclo inteiro - dizer isso, em vez de mostrar
       uma soma que nao fecha */
    var bruto = H + rec - pag;
    por("d", dias(D) + " <span class='suf'>" + und(D) + "</span>",
        Math.abs(bruto - D) > 0.05
          ? "D = " + dias(bruto) + " → piso de 1 dia"
          : "D = H + receb. − pagto.");
    por("cd", "R$ " + N.moeda(ec.custo_unitario * D, 0), "c × D · reais × dia presos");

    /* ---- a nota */
    var nota = primeira.nota * 1000;
    por("nota", N.num(nota, 2), "V ÷ (c × D) · por R$ 1.000 ao dia");
    var cn = no("nota");
    if (cn) cn.classList.toggle("mau", !(primeira.valor > 0));

    /* ---- o texto do botao do capital, com os numeros deste produto */
    var sem_lambda = (Number(m.custo_manter_unit_real) || 0) * H / dias_ano;
    var alvo = document.getElementById("mn-capital-txt");
    if (alvo) {
      alvo.innerHTML =
        "O custo do capital <b>não entra numa caixa só</b> — entra em três naturezas " +
        "diferentes, e é por isso que ele parece sumir do meio da conta:<br><br>" +
        "<b>1 · como desconto</b>, dentro de L: R$ " + N.moeda(ec.custo_carregar, 2) +
        " de aluguel do dinheiro enquanto a peça espera. E <b>só no ramo do encalhe</b> — " +
        "a peça que vende não paga aluguel nenhum, porque o dinheiro já voltou.<br>" +
        "<b>2 · como régua de quanto</b>, o <b>c</b> do denominador: R$ " +
        N.moeda(ec.custo_unitario, 2) + " imobilizados por peça.<br>" +
        "<b>3 · como régua de por quanto tempo</b>, o <b>D</b>: " + dias(D) +
        " " + und(D) + ". Eles não acabam na venda — acabam quando a venda vira caixa (+" +
        dias(rec) + ") — e não começam na compra: começam quando o fornecedor é " +
        "pago (−" + dias(pag) + ").<br><br>" +
        "O <b>λ = " + N.num(lam, 3) + "</b> é o quarto lugar, e o único que não é fixo: " +
        (lam > 0.0005
          ? "quando a soma do estoque ideal não cabe no teto de capital, o modelo sobe λ " +
            "até caber, e ele encarece o aluguel de todo mundo ao mesmo tempo. Com λ = 0 o " +
            "aluguel desta peça seria R$ " + N.moeda(sem_lambda, 2) + " em vez de R$ " +
            N.moeda(ec.custo_carregar, 2) + "."
          : "hoje ele está em zero — o estoque ideal cabe no teto de capital, então o " +
            "aluguel é só a taxa de manter.");
    }

    limpar();
    desenhar();
  };

  /* a perda no encalhe nao vem em `parametros`; sai da propria conta que o
     backend ja fez, para nao repetir o numero em dois lugares */
  function P_encalhe(pr, ec) {
    return ec.custo_unitario > 0 ? ec.custo_obsolescencia / ec.custo_unitario : 0;
  }

  /* ------------------------------------------------------------ arranque */

  function ligar() {
    var t = tela();
    if (!t) return;

    /* Com o capital aceso o hover fica desligado: sao duas leituras do mesmo
       desenho, e piscar entre elas atrapalha mais do que ajuda. */
    function travada() { return t.classList.contains("travada"); }
    t.querySelectorAll(".mn-no").forEach(function (e) {
      var k = e.dataset.mn;
      function entra() { if (!travada()) acender(Object.keys(adiante(k)), "aceso"); }
      function sai() { if (!travada()) limpar(); }
      e.addEventListener("mouseenter", entra);
      e.addEventListener("mouseleave", sai);
      e.addEventListener("focus", entra);
      e.addEventListener("blur", sai);
    });

    var b = document.getElementById("mn-b-capital");
    var cx = document.getElementById("mn-capital");
    if (b && cx) {
      b.addEventListener("click", function () {
        var on = b.classList.toggle("on");
        cx.hidden = !on;
        b.textContent = on ? "apagar o custo do capital" : "onde entra o custo do capital?";
        t.classList.toggle("travada", on);
        if (on) acender(CAPITAL, "capital");
        else limpar();
      });
    }

    if (raiz.ResizeObserver) new ResizeObserver(desenhar).observe(t);
    raiz.addEventListener("resize", desenhar);
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", ligar);
  else ligar();
})(window);
