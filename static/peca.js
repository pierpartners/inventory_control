/* =====================================================================
   Dossie de UMA peca da fila - a trilha de calculo completa daquela linha.
   Usado pela tela de Conferencia e pelos bloquinhos da corrida no Plano.
   Depende de nucleo.js (N.*), /api/etapas e /api/conferencia/{posicao}.
   ===================================================================== */
(function (raiz) {
  "use strict";

  function fmt(v, tipo) {
    if (v === null || v === undefined || v === "") return '<span class="t4">–</span>';
    switch (tipo) {
      case "int":  return N.num(v);
      case "num2": return N.num(v, 2);
      case "num3": return N.num(v, 3);
      case "num4": return N.num(v, 4);
      case "num6": return N.num(v, 6);
      case "pct":  return N.pct(v, 2);
      case "brl":  return '<span class="pre">R$</span>' + N.moeda(v);
      case "brl2": return '<span class="pre">R$</span>' + N.moeda(v, 2);
      case "bool": return v ? '<span class="c-mt">sim</span>' : '<span class="c-cr">não</span>';
      default:     return N.esc(v);
    }
  }

  /* as contas refeitas com os numeros da linha, para conferir na mao */
  function contas(r) {
    var q = r.quantidade || 1;
    return [
      ["Horizonte",
       "H = prazo do fornecedor + revisão\n" +
       "H = " + N.num(r.lead_time_dias) + " + " + N.num(r.periodo_revisao_dias) +
       " = <b>" + N.num(r.horizonte) + " dias</b>"],
      ["Demanda esperada no horizonte",
       "μ = demanda diária × H\n" +
       "μ = " + N.num(r.demanda_dia_corrigida, 4) + " × " + N.num(r.horizonte) +
       " = <b>" + N.num(r.mu_periodo, 3) + "</b>\n" +
       "σ = desvio diário × √H\n" +
       "σ = " + N.num(r.desvio_dia, 4) + " × √" + N.num(r.horizonte) +
       " = <b>" + N.num(r.sd_periodo, 3) + "</b>\n" +
       "<i>σ²/μ = " + N.num(r.razao_var_media, 3) + " ⇒ " + N.esc(r.distribuicao) + "</i>"],
      ["Chance de esta peça vender",
       "P = 1 − F(k−1),  com k = " + N.num(r.unidade_de) + "\n" +
       "P = 1 − " + N.num(r.cdf_ate_k_menos_1, 6) +
       " = <b>" + N.num(r.p_vender, 6) + "</b>  (" + N.pct(r.p_vender, 2) + ")"],
      ["Margem e perda por peça",
       "M = lucro por peça × fator de perda na ruptura\n" +
       "M = " + N.moeda(r.lucro_por_peca, 2) + " × " + N.num(r.fator_perda_ruptura, 2) +
       " = <b>R$ " + N.moeda(r.margem_unit, 2) + "</b>\n\n" +
       "L = carregar no horizonte + obsolescência\n" +
       "L = " + N.moeda(r.custo_manter_no_periodo, 2) + " + " +
       N.moeda(r.custo_obsolescencia, 2) + " = <b>R$ " + N.moeda(r.perda_unit, 2) + "</b>\n" +
       "<i>limite: só vale se P > L/(M+L) = " + N.num(r.limite_marginal_compra, 4) + "</i>"],
      ["Valor desta linha",
       "ganho = P × M × peças\n" +
       "ganho = " + N.num(r.p_vender, 6) + " × " + N.moeda(r.margem_unit, 2) + " × " + q +
       " = <b>R$ " + N.moeda(r.ganho_esperado, 2) + "</b>\n\n" +
       "custo = (1 − P) × L × peças\n" +
       "custo = " + N.num(1 - r.p_vender, 6) + " × " + N.moeda(r.perda_unit, 2) + " × " + q +
       " = <b>R$ " + N.moeda(r.custo_esperado, 2) + "</b>\n\n" +
       "V = ganho − custo = <b>R$ " + N.moeda(r.valor_esperado, 2) + "</b>"],
      ["Nota — o que ordena a fila",
       "nota = V / (c × peças) / H\n" +
       "nota = " + N.moeda(r.valor_esperado, 2) + " / (" + N.moeda(r.custo_unitario, 2) +
       " × " + q + ") / " + N.num(r.horizonte) + "\n" +
       "nota = <b>" + N.num(r.nota, 8) + "</b>  " +
       "<i>(× 1.000 nas tabelas = " + N.num(r.nota * 1000, 3) + ")</i>"],
      ["Decisão do caixa",
       "caixa já gasto ao chegar aqui: " + N.moeda(r.caixa_antes) + "\n" +
       "esta linha custa: " + N.moeda(r.custo) + "\n" +
       "caixa do ciclo: " + N.moeda(r.teto_ciclo) + "\n" +
       "→ <b>" + N.esc(r.motivo) + "</b>\n" +
       "caixa restante depois: " + N.moeda(r.caixa_restante)]
    ];
  }

  /* `ordem` é opcional: quando o clique veio de um bloquinho da corrida,
     diz qual peça do ciclo aquele bloco representa */
  raiz.abrirPeca = function (posicao, ordem) {
    var alvo = N.abrir("Peça " + posicao + " da fila", "", "");
    Promise.all([
      N.buscar("/api/conferencia/" + posicao),
      N.buscar("/api/etapas")
    ]).then(function (res) {
      var r = res[0].linha, etapas = res[1].etapas;
      var cor = N.corProduto(r.sku);

      document.getElementById("gv-tit").innerHTML =
        '<span style="display:inline-block;width:11px;height:11px;border-radius:3px;' +
        "background:" + cor + ';margin-right:9px;vertical-align:1px"></span>' + N.esc(r.item);
      document.getElementById("gv-sub").innerHTML =
        '<span class="t4 mono">' + N.esc(r.sku) + "</span><span class='t4'>·</span>" +
        "<span>" + N.esc(r.familia) + "</span>" +
        N.seloClasse(r.classificacao) +
        '<span class="selo ' + (r.comprar ? "selo-mt" : "selo-nu") + '">' +
        (r.comprar ? "comprada" : "fora do caixa") + "</span>" +
        '<span class="t4">' +
        (ordem ? ordem + "ª peça do ciclo · " : "") +
        "unidade nº " + N.num(r.unidade_de) + " deste produto · " +
        N.num(r.posicao_fila) + "º da fila</span>";

      var h = '<div class="painel mb14"><div class="painel-cab">' +
        "<h2>A conta refeita</h2>" +
        "<span class='dica'>cada linha usa só os números acima dela</span>" +
        '</div><div class="painel-int">';
      contas(r).forEach(function (c) {
        h += '<div class="etapa"><div class="tt">' + c[0] + "</div>" +
          '<div class="conta">' + c[1] + "</div></div>";
      });
      h += "</div></div>";

      h += '<div class="painel"><div class="painel-cab"><h2>Todos os campos</h2>' +
        "<span class='dica'>na ordem em que o cálculo os produz</span></div>" +
        '<div class="painel-int">';
      etapas.forEach(function (et) {
        h += '<div class="etapa"><div class="tt">' + N.esc(et[0]) + "</div>";
        et[1].forEach(function (campo) {
          h += '<div class="lin"><span class="k">' + N.esc(campo[1]) + "</span>" +
            '<span class="v">' + fmt(r[campo[0]], campo[2]) + "</span></div>";
        });
        h += "</div>";
      });
      h += "</div></div>";

      h += '<div class="mt14 flex g8 envolve">' +
        '<a class="btn btn-p" href="/conferencia?sku=' + encodeURIComponent(r.sku) +
        '">Ver todas as peças deste produto</a>' +
        '<a class="btn btn-p" href="/metodologia?sku=' + encodeURIComponent(r.sku) +
        '">Entender o cálculo deste produto</a></div>';

      alvo.innerHTML = h;
    }).catch(function () {
      alvo.innerHTML = '<div class="msg msg-er">Não foi possível carregar esta peça.</div>';
    });
  };
})(window);
