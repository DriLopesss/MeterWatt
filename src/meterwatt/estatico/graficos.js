/* MeterWatt: os gráficos do painel.
 *
 * - Para onde vai agora: o consumo de agora saindo da entrada para cada parte (circuitos medidos,
 *   imóveis ou aparelhos que o Detetive reconhece), em faixas com espessura pela potência.
 * - Perfil do dia: hoje hora a hora contra o normal da casa, e o mapa dos últimos 14 dias.
 * - A casa em 3D: a casa em linhas em volta do número de "Sua casa agora", com a cor pela carga.
 *
 * - Últimos 14 dias, relatório do mês, Tarifa Branca, tensão de cada dia e a linha de cada medidor.
 *
 * A cara é a do protótipo do MeterWatt (o quadro de disjuntores): âmbar é energia passando, magenta é
 * pico, ciano é o que está calmo; números em monoespaçada, barras que sobem do zero, dica escura de
 * borda ciano. As cores neon ficam acima da faixa de luminosidade que o método de dataviz recomenda
 * (escolha da marca); contraste com o fundo #171428 e separação para daltonismo passam no validador.
 * Por isso nenhum gráfico depende só da cor: rótulos, dica, texto ao lado e tabela dizem o mesmo.
 * Texto nunca usa a cor da série. Nada de innerHTML: o que vem do servidor entra como texto.
 */
"use strict";
(() => {
  const NS = "http://www.w3.org/2000/svg";
  const semMovimento = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const COR = { ambar: "#ffb020", magenta: "#ff4fd8", ciano: "#36e3ff", verde: "#46f0a0", vermelho: "#ff6b84",
    fundo: "#171428", energia: "#ffb020", base: "#ff4fd8", resto: "#8a85b0", normal: "#b7b2d8", grade: "rgba(255,255,255,.08)" };
  const COR_DO_TIPO = { circuito: COR.energia, aparelho: COR.energia, entrada: COR.energia, base: COR.base, resto: COR.resto };
  // Rampa do mapa de 14 dias: do roxo da noite ao magenta e ao âmbar, com a luminosidade sempre subindo
  // (quanto mais claro, mais gasto). A mesma rampa está na legenda (app.css, .legenda-rampa).
  const RAMPA = ["#241a3d", "#3a1c54", "#561f66", "#772371", "#9a2a74", "#bd3a70", "#dc5363", "#f27850", "#ffa23c", "#ffc93a"];
  const SEMANA = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"];

  function s(tag, attrs = {}, ...filhos) {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v);
    for (const f of filhos.flat()) if (f !== null && f !== undefined && f !== false) n.append(f instanceof Node ? f : String(f));
    return n;
  }
  const num = (v, casas = 0) => Number(v).toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });
  const kwh = (v) => (v == null ? "sem medição" : `${num(v, v < 10 ? 2 : 1)} kWh`);
  const reais = (v) => Number(v).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  const hora = (h) => `${h}h às ${(h + 1) % 24}h`;
  const largura = (caixa) => Math.max(260, Math.floor(caixa.clientWidth || caixa.getBoundingClientRect().width || 320));

  function hexParaRgb(h) { return [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)); }
  function rampa(t) {            // t de 0 a 1 -> cor da rampa (interpolada entre os degraus vizinhos)
    const x = Math.max(0, Math.min(1, t)) * (RAMPA.length - 1);
    const i = Math.min(RAMPA.length - 2, Math.floor(x)), f = x - i;
    const a = hexParaRgb(RAMPA[i]), b = hexParaRgb(RAMPA[i + 1]);
    return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * f)).join(",")})`;
  }
  function teto(v) {              // número redondo para o topo do eixo
    if (v <= 0) return 0.1;
    const p = 10 ** Math.floor(Math.log10(v));
    for (const m of [1, 2, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;     // a metade também é redonda
    return 10 * p;
  }
  // Retângulo com os dois cantos de cima arredondados (a ponta do dado) e a base reta.
  function colunaPath(x, y, w, h, r) {
    r = Math.min(r, w / 2, h);
    return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
  }
  function cortar(texto, maxLetras) {
    return texto.length <= maxLetras ? texto : `${texto.slice(0, Math.max(1, maxLetras - 1))}…`;
  }


  /* ------------------------------------------------------------ peças comuns: degradê, brilho, dica */

  let proximoId = 0;
  const novoId = (nome) => `mw-${nome}-${++proximoId}`;

  // Degradê da energia no espaço do gráfico (não de cada barra): barra baixa fica âmbar, só a alta chega ao magenta.
  function degrade(svg, yTopo, yBase, paradas) {
    const id = novoId("degrade");
    svg.append(s("defs", {}, s("linearGradient", { id, gradientUnits: "userSpaceOnUse", x1: 0, y1: yTopo, x2: 0, y2: yBase },
      paradas.map(([o, cor, op]) => s("stop", { offset: o, "stop-color": cor, "stop-opacity": op ?? 1 })))));
    return `url(#${id})`;
  }
  const degradeEnergia = (svg, yTopo, yBase) => degrade(svg, yTopo, yBase, [[0, COR.magenta], [0.55, "#ff8a5c"], [1, COR.ambar]]);
  function brilho(svg, cor, forca = 3) {
    const id = novoId("brilho");
    svg.append(s("defs", {}, s("filter", { id, x: "-20%", y: "-60%", width: "140%", height: "220%" },
      s("feDropShadow", { dx: 0, dy: 0, stdDeviation: forca, "flood-color": cor, "flood-opacity": 0.7 }))));
    return `url(#${id})`;
  }
  // Barras que sobem do zero, uma depois da outra (só no primeiro desenho, e nunca com "reduzir movimento").
  function crescer(no, k, animar) {
    if (animar && !semMovimento) {
      no.classList.add("cresce");
      no.style.setProperty("--k", k);
    }
    return no;
  }
  function grade(svg, m, W, y, max, casasMin = 0) {
    for (let i = 0; i <= 2; i++) {
      const v = (max / 2) * i;
      const casas = Math.max(casasMin, [0, 1, 2].find((c) => Math.abs(v * 10 ** c - Math.round(v * 10 ** c)) < 1e-9) ?? 2);
      svg.append(s("line", { x1: m.e, x2: W - m.d, y1: y(v), y2: y(v), class: "grade" }),
        s("text", { x: m.e - 6, y: y(v) + 4, class: "eixo", "text-anchor": "end" }, num(v, casas)));
    }
  }
  // Posição do ponteiro em coordenadas do desenho (o SVG encolhe no celular).
  function pontoNoDesenho(svg, ev, W, H) {
    const r = svg.getBoundingClientRect();
    return [((ev.clientX - r.left) * W) / (r.width || W), ((ev.clientY - r.top) * H) / (r.height || H)];
  }
  function naTela(svg, x, y, W, H) {
    const r = svg.getBoundingClientRect();
    return [r.left + (x * r.width) / W, r.top + (y * r.height) / H];
  }

  // A dica que acompanha o ponteiro (no toque, fica 2,5 s). Ela só repete o que já está no texto ao lado do
  // gráfico ou na tabela, que é o que o leitor de tela lê: por isso é aria-hidden.
  const dica = (() => {
    let caixa = null, relogio = 0;
    addEventListener("scroll", () => caixa && caixa.classList.remove("on"), { passive: true });
    return {
      mostrar(texto, x, y, cor) {
        if (!caixa) {
          caixa = document.createElement("div");
          caixa.className = "mw-dica";
          caixa.setAttribute("aria-hidden", "true");
          document.body.append(caixa);
        }
        clearTimeout(relogio);
        caixa.textContent = texto;
        caixa.style.setProperty("--cor-dica", cor || COR.ciano);
        caixa.classList.add("on");
        const w = caixa.offsetWidth, h = caixa.offsetHeight;
        caixa.style.left = `${Math.round(Math.min(innerWidth - w - 8, Math.max(8, x - w / 2)))}px`;
        caixa.style.top = `${Math.round(y - h - 16 < 8 ? y + 20 : y - h - 16)}px`;
      },
      esconder(depois = 0) {
        clearTimeout(relogio);
        relogio = setTimeout(() => caixa && caixa.classList.remove("on"), depois);
      },
    };
  })();
  // achar(ev) -> {texto, cor, x, y} (x e y opcionais: senão a dica vai no ponteiro) ou null. sair() ao sair.
  function ligarDica(alvo, achar, sair) {
    const mostrar = (ev) => {
      const r = achar(ev);
      if (r) dica.mostrar(r.texto, r.x ?? ev.clientX, r.y ?? ev.clientY, r.cor);
      else dica.esconder();
      return r;
    };
    alvo.addEventListener("pointermove", (ev) => { if (ev.pointerType === "mouse") mostrar(ev); });
    alvo.addEventListener("pointerdown", (ev) => { if (ev.pointerType !== "mouse" && mostrar(ev)) dica.esconder(2500); });
    alvo.addEventListener("pointerleave", (ev) => { if (ev.pointerType === "mouse") { dica.esconder(); if (sair) sair(); } });
  }
  const diaMes = (iso) => `${iso.slice(8, 10)}/${iso.slice(5, 7)}`;
  const diaDaSemana = (iso) => SEMANA[(new Date(`${iso}T12:00:00`).getDay() + 6) % 7];

  /* ------------------------------------------------------------ colunas por dia */

  // Últimos 14 dias e o relatório do mês. o.itens: {valor, eixo, dica, inteiro (false: só contorno), parcial
  // (hoje, ainda enchendo)}. o.n: quantas posições (o mês tem dias que ainda não chegaram). o.media: linha tracejada.
  function desenharColunas(caixa, o) {
    const W = largura(caixa), H = o.altura || 196;
    const m = { e: 40, d: 8, t: 28, b: 22 };
    const pw = W - m.e - m.d, ph = H - m.t - m.b, base = m.t + ph;
    const n = Math.max(1, o.n || o.itens.length);
    const max = teto(Math.max(o.minimo || 0.5, ...o.itens.map((d) => d.valor || 0), o.media || 0) * 1.06);
    const y = (v) => m.t + ph - (v / max) * ph;
    const slot = pw / n, bw = Math.max(3, Math.min(o.larguraMax || 26, slot * 0.66));
    const animar = !caixa.querySelector("svg");
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "grafico-svg", "aria-hidden": "true" });
    const tinta = degradeEnergia(svg, m.t, base);
    const luz = brilho(svg, COR.ambar, 4);
    grade(svg, m, W, y, max);
    svg.append(s("text", { x: m.e - 6, y: 12, class: "eixo", "text-anchor": "end" }, o.unidade || "kWh"));
    const foco = s("rect", { x: 0, y: m.t - 6, width: slot, height: ph + 6, rx: 8, class: "foco-coluna", visibility: "hidden" });
    svg.append(foco);
    let maior = -1;
    o.itens.forEach((d, i) => { if (d.inteiro !== false && !d.parcial && (maior < 0 || d.valor > o.itens[maior].valor)) maior = i; });
    o.itens.forEach((d, i) => {
      if (!(d.valor > 0)) return;
      const cx = m.e + i * slot + slot / 2;
      const caminho = colunaPath(cx - bw / 2, y(d.valor), bw, base - y(d.valor), Math.min(6, bw / 2));
      const coluna = d.inteiro === false
        ? s("path", { d: caminho, fill: "none", stroke: COR.ambar, "stroke-width": 1.5, "stroke-dasharray": "3 3", class: "coluna" })
        : s("path", { d: caminho, fill: tinta, class: `coluna${d.parcial ? " parcial" : ""}`, filter: i === maior ? luz : null });
      svg.append(crescer(coluna, i, animar));
      // Números só onde ajudam: o maior dia e hoje (ou o dia ainda incompleto).
      if (i === maior || d.parcial) {
        const tx = Math.min(W - m.d - 2, Math.max(m.e + 2, cx));
        svg.append(s("text", { x: tx, y: y(d.valor) - 7, class: `valor-coluna${d.parcial ? " hoje" : ""}`, "text-anchor": "middle" },
          num(d.valor, 1)));
      }
    });
    if (o.media != null && o.media > 0) {
      svg.append(s("line", { x1: m.e, x2: W - m.d, y1: y(o.media), y2: y(o.media), class: "linha-media" }));
      // A legenda da linha fica no canto de cima, onde não bate nos números das barras.
      const rotulo = `média ${num(o.media, 1)} ${o.unidade || "kWh"}`;
      const xr = W - m.d - rotulo.length * 6.4;
      svg.append(s("line", { x1: xr - 22, x2: xr - 6, y1: 9, y2: 9, class: "linha-media" }),
        s("text", { x: W - m.d, y: 13, class: "rotulo-media", "text-anchor": "end" }, rotulo));
    }
    const ultimo = o.itens.length - 1;
    o.itens.forEach((d, i) => {
      if (o.afinar && slot < 26 && (ultimo - i) % 2) return;     // no celular, um dia sim, um não (hoje sempre aparece)
      if (d.eixo) svg.append(s("text", { x: m.e + i * slot + slot / 2, y: H - 6, class: `eixo${d.parcial ? " agora" : ""}`, "text-anchor": "middle" }, d.eixo));
    });
    const indice = (ev) => {
      const [px] = pontoNoDesenho(svg, ev, W, H);
      const i = Math.floor((px - m.e) / slot);
      return i >= 0 && i < o.itens.length && o.itens[i].dica ? i : -1;
    };
    ligarDica(svg, (ev) => {
      const i = indice(ev);
      if (i < 0) { foco.setAttribute("visibility", "hidden"); return null; }
      foco.setAttribute("x", m.e + i * slot);
      foco.setAttribute("visibility", "visible");
      return { texto: o.itens[i].dica, cor: COR.ambar };
    }, () => foco.setAttribute("visibility", "hidden"));
    caixa.replaceChildren(svg);
  }

  function desenharUltimosDias(caixa, r) {
    const hoje = r.dias.length - 1;
    const completos = r.dias.slice(0, -1).filter((d) => d.kwh > 0);
    const media = completos.length ? completos.reduce((t, d) => t + d.kwh, 0) / completos.length : null;
    desenharColunas(caixa, {
      altura: 200, larguraMax: 30, media, afinar: true,
      itens: r.dias.map((d, i) => ({
        valor: d.kwh, parcial: i === hoje,
        eixo: i === hoje ? "hoje" : String(Number(d.dia.slice(8, 10))),
        dica: `${diaDaSemana(d.dia)} ${diaMes(d.dia)}: ${kwh(d.kwh)}${d.custo != null ? ` · ${reais(d.custo)}` : ""}${i === hoje ? " até agora" : ""}`,
      })),
    });
  }

  /* ------------------------------------------------------------ para onde vai agora */

  function desenharFluxo(caixa, r) {
    const W = largura(caixa);
    const partes = r.partes;
    const soma = partes.reduce((t, p) => t + (p.watts > 0 ? p.watts : 0), 0);
    const total = Math.max(1, r.total, soma);
    // A altura do desenho depende só de quantas partes há (não da potência de cada uma): o cartão não
    // pula enquanto a pessoa lê. Partes grandes ganham faixa grossa; os rótulos têm sempre o seu espaço.
    const LINHA = 42, VAO = 10, NO = 8, TOPO = 22;
    const n = partes.length;
    const ALTURA = Math.max(150, n * LINHA + (n - 1) * VAO);
    const xEsq = 2;
    const xDir = Math.round(W * (W < 420 ? 0.34 : 0.4));
    const xTexto = xDir + NO + 10;
    const letras = Math.max(8, Math.floor((W - xTexto - 4) / 7.6));        // nome: 14px, ~7,6px por letra
    const letrasValor = Math.max(8, Math.floor((W - xTexto - 4) / 7.3));   // valores: 12px monoespaçada, ~7,2px
    let escala = ALTURA - (n - 1) * VAO, hs = [];
    for (let volta = 0; volta < 6; volta++) {
      hs = partes.map((p) => (p.watts > 0 ? Math.max(3, (p.watts / total) * escala) : 0));
      const usado = hs.reduce((t, h) => t + Math.max(h, LINHA), 0) + (n - 1) * VAO;
      if (usado <= ALTURA + 0.5) break;
      escala = Math.max(10, escala - (usado - ALTURA));
    }
    const ys = [];
    const ocupado = hs.reduce((t, h) => t + Math.max(h, LINHA), 0) + (n - 1) * VAO;
    let y = TOPO + Math.max(0, (ALTURA - ocupado) / 2);
    partes.forEach((_, i) => { const bloco = Math.max(hs[i], LINHA); ys.push(y + (bloco - hs[i]) / 2); y += bloco + VAO; });
    const H = TOPO + ALTURA + 6;
    const somaH = hs.reduce((a, b) => a + b, 0);
    let yEsq = TOPO + (H - TOPO - somaH) / 2;
    const yEntrada = yEsq;

    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "fluxo-svg", "aria-hidden": "true" });
    const luzes = { [COR.energia]: brilho(svg, COR.energia, 2.5), [COR.base]: brilho(svg, COR.base, 2.5), [COR.resto]: null };
    svg.append(s("text", { x: xEsq, y: 13, class: "fluxo-rotulo-entrada" }, "Entrada"));
    const xm = (xEsq + NO + xDir) / 2;
    partes.forEach((p, i) => {
      const cor = COR_DO_TIPO[p.tipo] || COR.energia;
      const g = s("g", { class: `fluxo-parte tipo-${p.tipo}` });
      const vivo = p.watts != null;
      if (hs[i] > 0) {
        const l0 = yEsq, l1 = yEsq + hs[i], r0 = ys[i], r1 = ys[i] + hs[i];
        yEsq = l1;
        const x0 = xEsq + NO;
        g.append(s("path", { class: "fluxo-faixa", fill: cor, d: `M${x0},${l0}C${xm},${l0} ${xm},${r0} ${xDir},${r0}V${r1}C${xm},${r1} ${xm},${l1} ${x0},${l1}Z` }));
        const lc = (l0 + l1) / 2, rc = (r0 + r1) / 2;
        // As "partículas": tracinhos que andam da entrada para a parte, mais depressa quanto mais potência.
        const duracao = Math.max(0.5, 5.5 - Math.log10(Math.max(1, p.watts)) * 1.3);
        const corre = s("path", { class: "fluxo-corre", stroke: cor, d: `M${x0},${lc}C${xm},${lc} ${xm},${rc} ${xDir},${rc}`,
          "stroke-width": Math.min(3, Math.max(1.5, hs[i] / 5)).toFixed(1), filter: luzes[cor] || null });
        corre.style.animationDuration = `${duracao.toFixed(2)}s`;      // pelo CSSOM: a CSP do site bloqueia o atributo style
        g.append(corre);
        g.append(s("rect", { x: xDir, y: r0, width: NO, height: hs[i], rx: 2, fill: cor }));
      } else {
        g.append(s("rect", { x: xDir, y: ys[i], width: NO, height: 16, rx: 2, class: vivo ? "fluxo-no-zero" : "fluxo-no-sem-sinal" }));
      }
      const meio = ys[i] + Math.max(hs[i], 16) / 2;
      // O nome nunca é cortado para caber a marca de palpite: sem espaço, a marca sai (a nota abaixo explica).
      const t1 = s("text", { x: xTexto, y: meio - 4, class: "fluxo-nome" }, cortar(p.nome, letras));
      if (p.palpite && p.nome.length + 8 <= letras) t1.append(s("tspan", { class: "fluxo-palpite", dx: 6 }, "palpite"));
      // A linha de valores perde peças, da menos para a mais importante, até caber (no celular não cabe tudo).
      const agora = vivo ? `${num(p.watts)} ${r.unidade}` : "sem sinal agora";
      const porHora = vivo && p.custo_por_hora != null ? `${reais(p.custo_por_hora)} por hora` : null;
      const hoje = p.kwh_hoje != null ? `${kwh(p.kwh_hoje)} hoje` : null;
      const opcoes = [[agora, porHora, hoje], [agora, hoje], [agora, porHora], [agora]].map((o) => o.filter(Boolean).join(", "));
      const linha = opcoes.find((o) => o.length <= letrasValor) || opcoes[opcoes.length - 1];
      g.append(t1, s("text", { x: xTexto, y: meio + 13, class: "fluxo-valor" }, linha));
      const fatia = vivo && r.total > 0 ? Math.round((p.watts / r.total) * 100) : null;
      g.append(s("title", {}, [p.nome, p.palpite ? `palpite do Detetive: ${p.palpite}` : null, vivo ? `${num(p.watts)} ${r.unidade}` : "sem sinal agora",
        fatia != null ? `${fatia}% do que a casa gasta agora` : null].filter(Boolean).join(". ")));
      svg.append(g);
    });
    svg.insertBefore(s("rect", { x: xEsq, y: yEntrada, width: NO, height: Math.max(somaH, 3), rx: 3, class: "fluxo-entrada" }), svg.children[1]);
    caixa.replaceChildren(svg);
    const descricao = partes.map((p) => `${p.nome}${p.palpite ? ` (parece ${p.palpite})` : ""}: ${p.watts != null ? `${num(p.watts)} ${r.unidade}` : "sem sinal"}`).join("; ");
    caixa.setAttribute("aria-label", `Para onde vai a energia agora, de ${num(r.total)} ${r.unidade} na entrada. ${descricao}.`);
  }

  /* ------------------------------------------------------------ perfil do dia */

  function criarPerfil(cartao) {
    const grafico = cartao.querySelector(".perfil-grafico");
    const leitura = cartao.querySelector(".perfil-leitura");
    const legenda = cartao.querySelector(".perfil-legenda");
    const tabela = cartao.querySelector(".perfil-tabela");
    const detalhes = cartao.querySelector(".perfil-detalhes");
    const botoes = Array.from(cartao.querySelectorAll("[data-vista]"));
    let dados = null, vista = "hoje", foco = null;      // foco: hora (hoje) ou [linha, hora] (mapa)
    let animar = false, geo = null;                      // geo: medidas do último desenho, para a dica

    botoes.forEach((b) => b.addEventListener("click", () => {
      vista = b.dataset.vista;
      botoes.forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      foco = null;
      animar = true;
      desenhar();
    }));
    detalhes.addEventListener("toggle", () => { if (detalhes.open) montarTabela(); });
    grafico.addEventListener("keydown", (ev) => {
      if (!dados) return;
      const passos = { ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [-1, 0], ArrowDown: [1, 0], Home: [0, -99], End: [0, 99] };
      if (!passos[ev.key]) return;
      ev.preventDefault();
      const [dl, dh] = passos[ev.key];
      if (vista === "hoje") foco = Math.max(0, Math.min(23, (foco ?? dados.hora_atual) + dh));
      else {
        const [l, h] = foco || [dados.mapa.length - 1, dados.hora_atual];
        foco = [Math.max(0, Math.min(dados.mapa.length - 1, l + dl)), Math.max(0, Math.min(23, h + dh))];
      }
      desenhar();
    });
    grafico.addEventListener("pointerleave", () => { foco = null; desenhar(); });
    ligarDica(grafico, (ev) => {
      const svg = grafico.querySelector("svg");
      if (!dados || !geo || !svg) return null;
      const [px, py] = pontoNoDesenho(svg, ev, geo.W, geo.H);
      const h = Math.floor((px - geo.x0) / geo.slot);
      if (h < 0 || h > 23) return null;
      if (vista === "hoje") {
        const v = dados.hoje.horas[h], n = dados.normal ? dados.normal.horas[h] : null;
        const normal = n != null ? ` · normal ${kwh(n)}` : "";
        return { texto: h > dados.hora_atual ? `${hora(h)}: ainda não chegou${normal}` : `${hora(h)}: ${kwh(v)}${h === dados.hora_atual ? " até agora" : ""}${normal}`, cor: COR.ambar };
      }
      const l = Math.floor((py - geo.topo) / geo.passo);
      const linha = dados.mapa[l];
      if (!linha || (l === dados.mapa.length - 1 && h > dados.hora_atual)) return null;
      const [, mes, dia] = linha.dia.split("-");
      return { texto: `${SEMANA[linha.dia_da_semana]} ${dia}/${mes}, ${hora(h)}: ${kwh(linha.horas[h])}`, cor: COR.magenta };
    });

    function custo(v) {
      return v != null && dados.tarifa_kwh != null ? ` (${reais(v * dados.tarifa_kwh)})` : "";
    }
    function textoNormal() {
      return dados.normal && dados.normal.tipo === "fim_de_semana" ? "Normal no fim de semana" : "Normal num dia útil";
    }

    function desenharHoje() {
      const W = largura(grafico), H = 212;
      const m = { e: 40, d: 4, t: 24, b: 24 };
      const pw = W - m.e - m.d, ph = H - m.t - m.b;
      const hoje = dados.hoje.horas, normal = dados.normal ? dados.normal.horas : Array(24).fill(null);
      const max = teto(Math.max(0.05, ...hoje.filter((v) => v != null), ...normal.filter((v) => v != null)));
      const y = (v) => m.t + ph - (v / max) * ph;
      const slot = pw / 24, bw = Math.max(3, Math.min(24, slot - 2));
      const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, "aria-hidden": "true" });
      geo = { W, H, x0: m.e, slot };
      const tinta = degradeEnergia(svg, m.t, m.t + ph);
      grade(svg, m, W, y, max);
      svg.append(s("text", { x: m.e - 6, y: 10, class: "eixo", "text-anchor": "end" }, "kWh"));
      if (foco != null) svg.append(s("rect", { x: m.e + foco * slot, y: m.t, width: slot, height: ph, rx: 4, class: "foco" }));
      // A hora mais pesada até agora ganha brilho e o número em cima.
      let pico = -1;
      hoje.forEach((v, h) => { if (v != null && h <= dados.hora_atual && (pico < 0 || v > hoje[pico])) pico = h; });
      const luz = brilho(svg, COR.ambar, 4);
      hoje.forEach((v, h) => {
        if (v == null || v <= 0) return;
        const x = m.e + h * slot + (slot - bw) / 2;
        svg.append(crescer(s("path", { d: colunaPath(x, y(v), bw, m.t + ph - y(v), Math.min(5, bw / 2)), fill: tinta,
          class: h === dados.hora_atual ? "barra-parcial" : null, filter: h === pico ? luz : null }), h, animar));
      });
      if (pico >= 0 && hoje[pico] > 0) {
        const topo = Math.max(hoje[pico], normal[pico] ?? 0);
        svg.append(s("text", { x: Math.min(W - m.d - 14, Math.max(m.e + 14, m.e + pico * slot + slot / 2)), y: y(topo) - 7,
          class: "valor-coluna", "text-anchor": "middle" }, num(hoje[pico], 2)));
      }
      // O normal: uma linha em degraus, hora a hora.
      let d = "";
      normal.forEach((v, h) => {
        if (v == null) return;
        const x0 = m.e + h * slot, x1 = x0 + slot;
        d += (h > 0 && normal[h - 1] != null ? `L${x0},${y(v)}` : `M${x0},${y(v)}`) + `L${x1},${y(v)}`;
      });
      if (d) svg.append(s("path", { d, class: "linha-normal", stroke: COR.normal }));
      [0, 6, 12, 18].forEach((h) => svg.append(s("text", { x: m.e + h * slot + slot / 2, y: H - 6, class: "eixo", "text-anchor": "middle" }, `${h}h`)));
      svg.append(s("text", { x: m.e + dados.hora_atual * slot + slot / 2, y: H - 6, class: "eixo agora", "text-anchor": "middle" },
        [0, 6, 12, 18].some((h) => Math.abs(h - dados.hora_atual) < 3) ? "" : "agora"));
      // Áreas de toque: a coluna inteira da hora, maior que a barra.
      for (let h = 0; h < 24; h++) {
        svg.append(s("rect", { x: m.e + h * slot, y: m.t, width: slot, height: ph, class: "alvo", "data-h": h }));
      }
      svg.addEventListener("pointermove", (ev) => {
        const alvo = ev.target.closest && ev.target.closest(".alvo");
        if (alvo && Number(alvo.dataset.h) !== foco) { foco = Number(alvo.dataset.h); desenhar(); }
      });
      grafico.replaceChildren(svg);
      const h = foco ?? dados.hora_atual;
      const vh = hoje[h], vn = normal[h];
      const parcial = h === dados.hora_atual ? " até agora" : "";
      leitura.textContent = h > dados.hora_atual
        ? `${hora(h)}: ainda não chegou. ${vn != null ? `${textoNormal()}: ${kwh(vn)}.` : ""}`
        : `${hora(h)}: hoje ${kwh(vh)}${vh != null ? custo(vh) : ""}${parcial}${vn != null ? `; ${textoNormal().toLowerCase()}, ${kwh(vn)}` : ""}.`;
      legenda.replaceChildren(
        itemLegenda("barra", COR.energia, "Hoje"),
        dados.normal ? itemLegenda("linha", COR.normal, `${textoNormal()} (mediana de ${dados.normal.dias} dia${dados.normal.dias === 1 ? "" : "s"})`) : null);
      grafico.setAttribute("aria-label", `Consumo de hoje hora a hora${dados.normal ? `, comparado com o ${textoNormal().toLowerCase()}` : ""}. ` +
        "Use as setas para ler cada hora, ou abra a tabela abaixo.");
    }

    function desenharMapa() {
      const W = largura(grafico);
      const linhas = dados.mapa;
      const rotulo = 50, alto = W < 480 ? 14 : 17, vao = 2, topo = 16;
      const pw = W - rotulo - 2, slot = pw / 24;
      const H = topo + linhas.length * (alto + vao);
      const valores = linhas.flatMap((l) => l.horas.filter((v) => v != null));
      const max = Math.max(0.05, ...valores);
      const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, "aria-hidden": "true" });
      geo = { W, H, x0: rotulo, slot, topo, passo: alto + vao };
      [0, 6, 12, 18].forEach((h) => svg.append(s("text", { x: rotulo + h * slot + 1, y: 11, class: "eixo" }, `${h}h`)));
      linhas.forEach((l, i) => {
        const y = topo + i * (alto + vao);
        const dia = Number(l.dia.slice(8, 10));
        const eHoje = i === linhas.length - 1;
        svg.append(s("text", { x: 0, y: y + alto - 3, class: `eixo${eHoje ? " hoje" : ""}${l.dia_da_semana >= 5 ? " fim-de-semana" : ""}` },
          `${SEMANA[l.dia_da_semana]} ${dia}`));
        l.horas.forEach((v, h) => {
          if (eHoje && h > dados.hora_atual) return;                    // ainda não aconteceu
          const x = rotulo + h * slot + 1;
          const atributos = { x, y, width: Math.max(1, slot - vao), height: alto, rx: 2, "data-l": i, "data-h": h };
          svg.append(v == null
            ? s("rect", { ...atributos, class: "celula sem-medicao" })
            : s("rect", { ...atributos, class: "celula", fill: rampa(Math.sqrt(v / max)) }));
        });
      });
      if (foco) {
        const [fl, fh] = foco;
        svg.append(s("rect", { x: rotulo + fh * slot + 0.5, y: topo + fl * (alto + vao) - 1, width: Math.max(1, slot - vao) + 1, height: alto + 2, rx: 3, class: "celula-foco" }));
      }
      svg.addEventListener("pointermove", (ev) => {
        const c = ev.target.closest && ev.target.closest(".celula");
        if (!c) return;
        const novo = [Number(c.dataset.l), Number(c.dataset.h)];
        if (!foco || foco[0] !== novo[0] || foco[1] !== novo[1]) { foco = novo; desenhar(); }
      });
      grafico.replaceChildren(svg);
      const [fl, fh] = foco || [linhas.length - 1, dados.hora_atual];
      const l = linhas[fl];
      const [, mes, dia] = l.dia.split("-");
      leitura.textContent = `${SEMANA[l.dia_da_semana]} ${dia}/${mes}, ${hora(fh)}: ${kwh(l.horas[fh])}${custo(l.horas[fh])}. No dia: ${kwh(l.kwh)}.`;
      legenda.replaceChildren(
        el("span", { class: "legenda-item" }, el("span", { class: "legenda-rampa", "aria-hidden": "true" }), `de 0 a ${kwh(max)} por hora`),
        el("span", { class: "legenda-item" }, el("span", { class: "legenda-vazio", "aria-hidden": "true" }), "sem medição"));
      grafico.setAttribute("aria-label", `Mapa do consumo por hora nos últimos ${linhas.length} dias: cada linha é um dia, cada coluna uma hora. ` +
        "Use as setas para ler cada hora, ou abra a tabela abaixo.");
    }

    function el(tag, attrs, ...filhos) {
      const n = document.createElement(tag);
      for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
      n.append(...filhos);
      return n;
    }
    function itemLegenda(forma, cor, texto) {
      const marca = el("span", { class: `legenda-${forma}`, "aria-hidden": "true" });
      marca.style.setProperty("--cor", cor);
      return el("span", { class: "legenda-item" }, marca, texto);
    }

    function montarTabela() {
      if (!dados) return;
      const t = el("table", {});
      if (vista === "hoje") {
        t.append(el("thead", {}, el("tr", {}, el("th", { scope: "col" }, "Hora"), el("th", { scope: "col" }, "Hoje"), el("th", { scope: "col" }, textoNormal()))));
        const corpo = el("tbody", {});
        for (let h = 0; h < 24; h++) {
          const v = dados.hoje.horas[h], n = dados.normal ? dados.normal.horas[h] : null;
          corpo.append(el("tr", {}, el("th", { scope: "row" }, hora(h)),
            el("td", {}, h > dados.hora_atual ? "—" : kwh(v)), el("td", {}, n == null ? "—" : kwh(n))));
        }
        t.append(corpo);
      } else {
        const cab = el("tr", {}, el("th", { scope: "col" }, "Dia"));
        for (let h = 0; h < 24; h++) cab.append(el("th", { scope: "col" }, `${h}h`));
        cab.append(el("th", { scope: "col" }, "Total"));
        const corpo = el("tbody", {});
        dados.mapa.forEach((l, i) => {
          const [, mes, dia] = l.dia.split("-");
          const linha = el("tr", {}, el("th", { scope: "row" }, `${SEMANA[l.dia_da_semana]} ${dia}/${mes}`));
          l.horas.forEach((v, h) => linha.append(el("td", {},
            i === dados.mapa.length - 1 && h > dados.hora_atual ? "" : v == null ? "—" : num(v, 2))));
          linha.append(el("td", {}, kwh(l.kwh)));
          corpo.append(linha);
        });
        t.append(el("thead", {}, cab), corpo);
      }
      tabela.replaceChildren(t);
    }

    function desenhar() {
      if (!dados) return;
      if (vista === "hoje") desenharHoje(); else desenharMapa();
      animar = false;
      if (detalhes.open) montarTabela();
    }

    let largUltima = 0;
    new ResizeObserver(() => { const w = grafico.clientWidth; if (w && Math.abs(w - largUltima) > 8) { largUltima = w; desenhar(); } }).observe(grafico);
    return { atualizar(d) { animar = animar || !dados; dados = d; desenhar(); } };
  }

  /* ------------------------------------------------------------ Tarifa Branca: o dia útil típico */

  // Do mais barato ao mais caro: o mesmo caminho de cores da casa em 3D (ciano quando a casa está calma,
  // âmbar no meio, magenta no pico). Contraste e daltonismo conferidos no validador sobre o fundo #171428.
  const COR_POSTO = { fora: COR.ciano, intermediario: COR.ambar, ponta: COR.magenta };
  const NOME_POSTO = { fora: "fora de ponta", intermediario: "intermediário", ponta: "ponta" };
  const meiaHora = (i) => `${Math.floor(i / 2)}h${i % 2 ? "30" : ""}`;
  const horaCurta = (texto) => {             // "18:00" -> "18h", "17:30" -> "17h30", "00:00" (fim) -> "24h"
    const [h, m] = texto.split(":").map(Number);
    return `${h === 0 ? 24 : h}h${m ? String(m).padStart(2, "0") : ""}`;
  };

  function desenharTarifaBranca(caixa, r) {
    const W = largura(caixa), H = 196;
    const m = { e: 40, d: 4, t: 28, b: 22 };
    const pw = W - m.e - m.d, ph = H - m.t - m.b;
    const dados = r.perfil.dia_util;
    const max = teto(Math.max(0.02, ...dados.filter((v) => v != null)));
    const y = (v) => m.t + ph - (v / max) * ph;
    const slot = pw / 48, bw = Math.max(2, Math.min(14, slot - 1.5));
    const animar = !caixa.querySelector("svg");
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, "aria-hidden": "true" });
    const luz = { fora: null, intermediario: null, ponta: brilho(svg, COR.magenta, 3) };
    // As faixas da ponta e do intermediário, atrás das barras.
    const faixas = [];
    for (let i = 0; i < 48;) {
      let j = i;
      while (j < 48 && r.postos[j] === r.postos[i]) j++;
      if (r.postos[i] !== "fora") faixas.push([r.postos[i], i, j]);
      i = j;
    }
    faixas.forEach(([p, a, b]) => svg.append(s("rect", { x: m.e + a * slot, y: m.t, width: (b - a) * slot, height: ph, class: `tb-faixa tb-faixa-${p}` })));
    const ponta = faixas.find(([p]) => p === "ponta");
    if (ponta) {
      svg.append(s("text", { x: m.e + ((ponta[1] + ponta[2]) / 2) * slot, y: m.t - 9, class: "eixo tb-rotulo", "text-anchor": "middle" },
        `ponta ${horaCurta(r.ponta.inicio)}–${horaCurta(r.ponta.fim)}`));
    }
    for (let i = 0; i <= 2; i++) {
      const v = (max / 2) * i;
      const casas = [0, 1, 2].find((c) => Math.abs(v * 10 ** c - Math.round(v * 10 ** c)) < 1e-9) ?? 2;
      svg.append(s("line", { x1: m.e, x2: W - m.d, y1: y(v), y2: y(v), class: "grade" }),
        s("text", { x: m.e - 6, y: y(v) + 4, class: "eixo", "text-anchor": "end" }, num(v, casas)));
    }
    svg.append(s("text", { x: m.e - 6, y: 10, class: "eixo", "text-anchor": "end" }, "kWh"));
    const foco = s("rect", { x: 0, y: m.t, width: slot, height: ph, rx: 4, class: "foco-coluna", visibility: "hidden" });
    svg.append(foco);
    dados.forEach((v, i) => {
      if (v == null || v <= 0) return;
      const x = m.e + i * slot + (slot - bw) / 2;
      svg.append(crescer(s("path", { d: colunaPath(x, y(v), bw, m.t + ph - y(v), Math.min(3, bw / 2)), fill: COR_POSTO[r.postos[i]],
        filter: luz[r.postos[i]] }), i, animar));
    });
    ligarDica(svg, (ev) => {
      const [px] = pontoNoDesenho(svg, ev, W, H);
      const i = Math.floor((px - m.e) / slot);
      if (i < 0 || i >= 48 || dados[i] == null) { foco.setAttribute("visibility", "hidden"); return null; }
      foco.setAttribute("x", m.e + i * slot);
      foco.setAttribute("visibility", "visible");
      return { texto: `${meiaHora(i)} às ${meiaHora(i + 1)}: ${kwh(dados[i])} · ${NOME_POSTO[r.postos[i]]}`, cor: COR_POSTO[r.postos[i]] };
    }, () => foco.setAttribute("visibility", "hidden"));
    [0, 6, 12, 18, 24].forEach((h) => svg.append(s("text", { x: m.e + h * 2 * slot, y: H - 6, class: "eixo",
      "text-anchor": h === 0 ? "start" : h === 24 ? "end" : "middle" }, `${h}h`)));
    caixa.replaceChildren(svg);
    const pct = (k) => `${Math.round(r.mes.percentual[k] * 100)}%`;
    caixa.setAttribute("aria-label", `Consumo de um dia útil típico, de meia em meia hora. Ponta das ${horaCurta(r.ponta.inicio)} às ` +
      `${horaCurta(r.ponta.fim)}. No mês, ${pct("ponta")} do consumo cai na ponta, ${pct("intermediario")} no intermediário e ` +
      `${pct("fora")} fora de ponta. A tabela abaixo tem os totais.`);
  }

  /* ------------------------------------------------------------ relatório: dia a dia */

  function desenharDiasDoMes(caixa, r) {
    desenharColunas(caixa, {
      n: r.dias_no_mes, media: r.media_dia, larguraMax: 20,
      itens: r.dias.map((d) => {
        const dia = Number(d.dia.slice(8, 10));
        return {
          valor: d.kwh, inteiro: d.inteiro,
          eixo: [1, 8, 15, 22, 29].includes(dia) ? String(dia) : "",
          dica: `${diaDaSemana(d.dia)} ${diaMes(d.dia)}: ${kwh(d.kwh)}${d.custo != null ? ` · ${reais(d.custo)}` : ""}${d.inteiro ? "" : " (medição incompleta)"}`,
        };
      }),
    });
    caixa.setAttribute("aria-label", `Consumo por dia em ${r.nome}${r.media_dia != null ? `, média de ${num(r.media_dia, 1)} kWh por dia` : ""}. A tabela abaixo tem cada dia.`);
  }

  /* ------------------------------------------------------------ tensão de cada dia */

  // Uma barra por dia, da menor à maior tensão do dia, e um ponto na média. Ao fundo, a faixa adequada da
  // ANEEL; as linhas tracejadas marcam onde começa a crítica. Quem diz a faixa é o rótulo, não só a cor.
  function desenharTensao(caixa, q) {
    const W = largura(caixa), H = 170;
    const m = { e: 40, d: 6, t: 12, b: 22 };
    const pw = W - m.e - m.d, ph = H - m.t - m.b;
    const [aMin, aMax] = q.faixas.adequada, [pMin, pMax] = q.faixas.precaria;
    const lo = Math.floor(Math.min(pMin - 4, ...q.dias.map((d) => d.min)) / 5) * 5;
    const hi = Math.ceil(Math.max(pMax + 4, ...q.dias.map((d) => d.max)) / 5) * 5;
    const y = (v) => m.t + ph - ((v - lo) / (hi - lo)) * ph;
    const n = Math.max(q.dias.length, 7), slot = pw / n, bw = Math.max(2, Math.min(8, slot - 3));
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, "aria-hidden": "true" });
    svg.append(s("rect", { x: m.e, y: y(aMax), width: pw, height: y(aMin) - y(aMax), class: "qe-adequada" }));
    // Os limites da adequada sempre têm número; os da crítica, só se não encostarem neles (133 e 135 ficam perto).
    const rotulos = [];
    for (const [v, cls] of [[aMin, "eixo"], [aMax, "eixo"], [pMin, "qe-limite"], [pMax, "qe-limite"]]) {
      if (cls === "qe-limite") svg.append(s("line", { x1: m.e, x2: W - m.d, y1: y(v), y2: y(v), class: cls }));
      if (rotulos.some((r) => Math.abs(r - y(v)) < 12)) continue;
      rotulos.push(y(v));
      svg.append(s("text", { x: m.e - 6, y: y(v) + 4, class: "eixo", "text-anchor": "end" }, num(v)));
    }
    // A cor do dia é a pior faixa que ele tocou; quem diz qual é a faixa é a dica e a lista abaixo.
    const faixaDoDia = (d) => (d.min < pMin || d.max > pMax ? "critica" : d.min < aMin || d.max > aMax ? "precaria" : "adequada");
    const COR_FAIXA = { adequada: COR.verde, precaria: COR.ambar, critica: COR.vermelho };
    const NOME_FAIXA = { adequada: "sempre na faixa adequada", precaria: "passou pela faixa precária", critica: "chegou à faixa crítica" };
    const luz = brilho(svg, COR.vermelho, 3);
    const foco = s("rect", { x: 0, y: m.t, width: slot, height: ph, rx: 4, class: "foco-coluna", visibility: "hidden" });
    svg.append(foco);
    const desloc = n - q.dias.length;
    q.dias.forEach((d, i) => {
      const x = m.e + (desloc + i) * slot + slot / 2;
      const f = faixaDoDia(d);
      svg.append(s("g", { class: `qe-dia qe-dia-${f}` },
        s("rect", { x: x - bw / 2, y: y(d.max), width: bw, height: Math.max(2, y(d.min) - y(d.max)), rx: Math.min(3, bw / 2),
          fill: COR_FAIXA[f], "fill-opacity": f === "adequada" ? 0.55 : 0.95, filter: f === "critica" ? luz : null }),
        s("circle", { cx: x, cy: y(d.media), r: 3.2, fill: COR.fundo, stroke: "#f4f1ff", "stroke-width": 1.5 })));
    });
    ligarDica(svg, (ev) => {
      const [px] = pontoNoDesenho(svg, ev, W, H);
      const i = Math.floor((px - m.e) / slot) - desloc;
      const d = q.dias[i];
      if (!d) { foco.setAttribute("visibility", "hidden"); return null; }
      foco.setAttribute("x", m.e + (desloc + i) * slot);
      foco.setAttribute("visibility", "visible");
      const f = faixaDoDia(d);
      return { texto: `${diaMes(d.dia)}: ${num(d.min, 1)} a ${num(d.max, 1)} V, média ${num(d.media, 1)} V · ${NOME_FAIXA[f]}`, cor: COR_FAIXA[f] };
    }, () => foco.setAttribute("visibility", "hidden"));
    const ultimo = q.dias[q.dias.length - 1], primeiro = q.dias[0];
    if (primeiro) svg.append(s("text", { x: m.e + (n - q.dias.length) * slot + slot / 2, y: H - 6, class: "eixo", "text-anchor": "start" },
      `${primeiro.dia.slice(8, 10)}/${primeiro.dia.slice(5, 7)}`));
    if (ultimo && q.dias.length > 1) svg.append(s("text", { x: W - m.d, y: H - 6, class: "eixo", "text-anchor": "end" },
      `${ultimo.dia.slice(8, 10)}/${ultimo.dia.slice(5, 7)}`));
    caixa.replaceChildren(svg);
    caixa.setAttribute("aria-label", `Tensão de cada dia, da menor à maior, com a média. A faixa adequada vai de ${aMin} a ${aMax} V.`);
  }

  /* ------------------------------------------------------------ a linha de cada medidor */

  // Potência ao longo de 2 horas, 24 horas ou 7 dias. Linha ciano com brilho, área em degradê, o pico
  // marcado em magenta e, ao vivo, o ponto de agora pulsando. O cursor segue o ponteiro (ou as setas).
  const DURACAO = { "2h": 7200, "24h": 86400, "7d": 604800 };
  const BURACO = { "2h": 30, "24h": 700, "7d": 4000 };       // mais que isso entre dois pontos: a linha quebra
  const horaMinuto = (t) => new Date(t * 1000).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  const quando = (t, periodo) => {
    const d = new Date(t * 1000);
    return periodo === "7d" ? `${SEMANA[(d.getDay() + 6) % 7]} ${d.getDate()}, ${horaMinuto(t)}` : horaMinuto(t);
  };

  function desenharLinha(caixa, h) {
    const W = largura(caixa), H = W < 480 ? 190 : 220;
    const m = { e: 46, d: 12, t: 24, b: 24 };
    const pw = W - m.e - m.d, ph = H - m.t - m.b, base = m.t + ph;
    const periodo = DURACAO[h.periodo] ? h.periodo : "2h";
    const fim = h.agora, inicio = fim - DURACAO[periodo];
    const pts = h.pontos.filter((p) => p[0] >= inicio - 60);
    const maxV = pts.reduce((a, p) => Math.max(a, p[1]), 0);
    const max = teto(Math.max(10, maxV * 1.1));
    const x = (t) => m.e + ((t - inicio) / (fim - inicio)) * pw;
    const y = (v) => base - (v / max) * ph;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "grafico-svg linha-medidor", "aria-hidden": "true" });
    grade(svg, m, W, y, max);
    svg.append(s("text", { x: m.e - 6, y: 12, class: "eixo", "text-anchor": "end" }, h.unidade));
    for (let i = 0; i <= 4; i++) {
      const t = inicio + ((fim - inicio) / 4) * i;
      const rotulo = periodo === "7d" ? `${SEMANA[(new Date(t * 1000).getDay() + 6) % 7]} ${new Date(t * 1000).getDate()}` : horaMinuto(t);
      svg.append(s("text", { x: x(t), y: H - 6, class: "eixo", "text-anchor": i === 0 ? "start" : i === 4 ? "end" : "middle" }, rotulo));
    }
    caixa.replaceChildren(svg);
    if (!pts.length) {
      svg.append(s("text", { x: m.e + pw / 2, y: m.t + ph / 2, class: "grafico-vazio", "text-anchor": "middle" }, "Sem leituras neste período"));
      caixa.setAttribute("aria-label", "Sem leituras neste período.");
      return;
    }
    const area = degrade(svg, m.t, base, [[0, COR.ciano, 0.42], [0.7, COR.ciano, 0.08], [1, COR.ciano, 0]]);
    const luz = brilho(svg, COR.ciano, 2.5);
    const trechos = [[]];
    pts.forEach((p, i) => {
      if (i && p[0] - pts[i - 1][0] > BURACO[periodo]) trechos.push([]);
      trechos[trechos.length - 1].push(p);
    });
    const desenhoLinha = s("g", { class: semMovimento ? null : "linha-entra" });
    for (const t of trechos) {
      const caminho = t.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
      desenhoLinha.append(
        s("path", { d: `${caminho}L${x(t[t.length - 1][0]).toFixed(1)},${base}L${x(t[0][0]).toFixed(1)},${base}Z`, fill: area }),
        s("path", { d: caminho, class: "linha-valor", stroke: COR.ciano, filter: luz }));
    }
    svg.append(desenhoLinha);
    // O pico do período, em magenta, com o número.
    const pico = pts.reduce((a, p) => (p[1] > a[1] ? p : a), pts[0]);
    const px = x(pico[0]), py = y(pico[1]);
    svg.append(s("circle", { cx: px, cy: py, r: 4, fill: COR.magenta, stroke: COR.fundo, "stroke-width": 1.5, filter: brilho(svg, COR.magenta, 3) }),
      s("text", { x: Math.min(W - m.d, Math.max(m.e, px)), y: Math.max(11, py - 9), class: "valor-coluna",
        "text-anchor": px > W - 90 ? "end" : px < m.e + 60 ? "start" : "middle" }, `pico ${num(pico[1])} ${h.unidade}`));
    // Ao vivo: o ponto de agora pulsa.
    const ultimo = pts[pts.length - 1];
    if (fim - ultimo[0] <= BURACO[periodo] * 2) {
      svg.append(s("circle", { cx: x(ultimo[0]), cy: y(ultimo[1]), r: 4, class: semMovimento ? "agora-ponto" : "agora-ponto pulsa" }));
    }
    // O cursor.
    const cursor = s("g", { class: "cursor", visibility: "hidden" },
      s("line", { y1: m.t, y2: base, class: "cursor-linha" }),
      s("circle", { r: 5, fill: COR.ciano, stroke: COR.fundo, "stroke-width": 2 }));
    svg.append(cursor);
    const perto = (t) => {                // o ponto mais perto do instante t (busca binária: até ~700 pontos)
      let a = 0, b = pts.length - 1;
      while (b - a > 1) { const c = (a + b) >> 1; if (pts[c][0] < t) a = c; else b = c; }
      return Math.abs(pts[a][0] - t) <= Math.abs(pts[b][0] - t) ? a : b;
    };
    let atual = null;
    const mostrar = (i) => {
      atual = i;
      const [t, v] = pts[i];
      cursor.setAttribute("visibility", "visible");
      cursor.firstChild.setAttribute("x1", x(t)); cursor.firstChild.setAttribute("x2", x(t));
      cursor.lastChild.setAttribute("cx", x(t)); cursor.lastChild.setAttribute("cy", y(v));
      return { texto: `${quando(t, periodo)} · ${num(v)} ${h.unidade}`, cor: COR.ciano };
    };
    const esconder = () => { atual = null; cursor.setAttribute("visibility", "hidden"); };
    ligarDica(svg, (ev) => {
      const [pxx] = pontoNoDesenho(svg, ev, W, H);
      if (pxx < m.e - 4 || pxx > W - m.d + 4) { esconder(); return null; }
      const i = perto(inicio + ((pxx - m.e) / pw) * (fim - inicio));
      if (Math.abs(x(pts[i][0]) - pxx) > Math.max(24, pw / 20)) { esconder(); return null; }
      return mostrar(i);
    }, esconder);
    // Teclado: setas andam de ponto em ponto (dez de cada vez com Shift), Home e End vão às pontas.
    caixa.onkeydown = (ev) => {
      const passo = { ArrowLeft: -1, ArrowRight: 1, Home: -Infinity, End: Infinity }[ev.key];
      if (passo === undefined) return;
      ev.preventDefault();
      const n = Math.abs(passo) === Infinity ? passo : passo * (ev.shiftKey ? 10 : 1);
      const i = Math.max(0, Math.min(pts.length - 1, (atual ?? pts.length - 1) + (Math.abs(n) === Infinity ? Math.sign(n) * pts.length : n)));
      const r = mostrar(i);
      const [sx, sy] = naTela(svg, x(pts[i][0]), y(pts[i][1]), W, H);
      dica.mostrar(r.texto, sx, sy, r.cor);
    };
    caixa.onblur = () => { esconder(); dica.esconder(); };
    const minimo = pts.reduce((a, p) => Math.min(a, p[1]), Infinity);
    caixa.setAttribute("aria-label", `Potência de ${quando(pts[0][0], periodo)} a ${quando(ultimo[0], periodo)}: de ${num(minimo)} a ${num(pico[1])} ${h.unidade}, ` +
      `pico às ${quando(pico[0], periodo)}, última leitura ${num(ultimo[1])} ${h.unidade}. Use as setas para ler cada ponto.`);
  }

  /* ------------------------------------------------------------ a casa em 3D */

  // A cor acompanha a carga: ciano perto do que a casa gasta parada, âmbar no meio, magenta perto dos picos.
  const TONS = [[54, 227, 255], [255, 176, 32], [255, 79, 216]];
  function tom(n) {
    const x = Math.max(0, Math.min(1, n)) * 2, i = Math.min(1, Math.floor(x)), f = x - i;
    return TONS[i].map((v, k) => Math.round(v + (TONS[i + 1][k] - v) * f));
  }

  class CasaEm3D {
    constructor(canvas, caixa, visor) {
      this.canvas = canvas; this.caixa = caixa; this.visor = visor;
      this.c = canvas.getContext("2d");
      this.nivel = 0.35; this.alvo = 0.35; this.watts = 0;
      this.ligada = true; this.ativa = false; this.naTela = true;
      this.quadro = 0; this.ultimo = 0; this.fase = 0;
      new ResizeObserver(() => this.medir()).observe(caixa);
      new IntersectionObserver((e) => { this.naTela = e[0].isIntersecting; this.agendar(); }).observe(caixa);
      document.addEventListener("visibilitychange", () => this.agendar());
      this.medir();
    }

    medir() {
      const r = this.caixa.getBoundingClientRect();
      if (!r.width) return;
      this.dpr = Math.min(2, window.devicePixelRatio || 1);
      this.larg = r.width; this.alt = r.height;
      this.canvas.width = Math.round(r.width * this.dpr);
      this.canvas.height = Math.round(r.height * this.dpr);
      this.c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      const v = this.visor.getBoundingClientRect();
      // A parede da frente emoldura o número: um pouco mais larga que os dígitos, nunca maior que a caixa.
      this.parede = Math.max(240, Math.min(r.width - 24, (v.width || 280) + Math.min(180, r.width * 0.18)));
      this.desenhar(this.ativa ? this.fase : 0.35);
    }

    definir(watts, faixa) {
      this.watts = watts || 0;
      let n;
      if (faixa && faixa.alto_w > faixa.base_w + 1) n = (this.watts - faixa.base_w) / (faixa.alto_w - faixa.base_w);
      else n = Math.log10(Math.max(1, this.watts) / 60) / Math.log10(4000 / 60);       // sem histórico: 60 W a 4 kW
      this.alvo = Math.max(0, Math.min(1, n));
      if (!this.ativa) { this.nivel = this.alvo; this.desenhar(0.35); }
    }

    ligar(sim) { this.ligada = sim; this.canvas.hidden = !sim; this.agendar(); }

    agendar() {
      const deve = this.ligada && this.naTela && !document.hidden && !semMovimento && this.caixa.offsetParent !== null;
      if (deve === this.ativa) return;
      this.ativa = deve;
      cancelAnimationFrame(this.quadro);
      if (deve) this.quadro = requestAnimationFrame((t) => this.passo(t));
      else if (this.ligada) this.desenhar(0.35);
    }

    passo(t) {
      if (!this.ativa) return;
      if (this.caixa.offsetParent === null) { this.ativa = false; return; }    // saiu do painel: para até voltar
      if (t - this.ultimo >= 33) {                          // ~30 quadros por segundo bastam
        const dt = Math.min(0.1, (t - (this.ultimo || t)) / 1000);
        this.ultimo = t;
        this.nivel += (this.alvo - this.nivel) * Math.min(1, dt * 1.5);
        this.fase = Math.sin(t / 9000 * Math.PI * 2) * 0.26 + 0.1;
        this.correr = ((this.correr || 0) + dt * (0.25 + Math.log10(Math.max(10, this.watts)) * 0.35)) % 1;
        this.desenhar(this.fase);
      }
      this.quadro = requestAnimationFrame((x) => this.passo(x));
    }

    desenhar(giro) {
      const c = this.c, W = this.larg, Hc = this.alt;
      if (!W || !this.ligada) return;
      c.clearRect(0, 0, W, Hc);
      const meia = this.parede / 2;
      const base = Hc - 6;                                   // o chão, logo abaixo da última linha de texto
      const topoParede = 50;                                 // espaço do telhado em cima (o CSS deixa essa folga)
      const Hm = (base - topoParede) / meia;                 // altura da parede em "meias larguras"
      const D = 0.62, TELHADO = Math.min(0.5, 42 / meia), CAM = 7;
      const f = meia * CAM;
      const cx = W / 2;
      const inclina = 0.08;                                 // vista um pouco de cima: aparece o telhado
      const cg = Math.cos(giro), sg = Math.sin(giro), ci = Math.cos(inclina), si = Math.sin(inclina);
      const proj = ([x, y, z]) => {
        z += D / 2;                                          // gira em volta do meio da casa
        const x1 = x * cg + z * sg, z1 = -x * sg + z * cg - D / 2;
        const y1 = y * ci - z1 * si, z2 = y * si + z1 * ci;
        const prof = CAM - z2;
        return [cx + (f * x1) / prof, base - (f * y1) / prof, prof];
      };
      const P = {
        A: [-1, 0, 0], B: [1, 0, 0], C: [1, Hm, 0], Dd: [-1, Hm, 0],
        E: [-1, 0, -D], F: [1, 0, -D], G: [1, Hm, -D], Hh: [-1, Hm, -D],
        R1: [0, Hm + TELHADO, 0.04], R2: [0, Hm + TELHADO, -D - 0.04],
      };
      const p = Object.fromEntries(Object.entries(P).map(([k, v]) => [k, proj(v)]));
      const profs = Object.values(p).map((x) => x[2]);
      const perto = Math.min(...profs), longe = Math.max(...profs);
      const arestas = [["A", "B"], ["B", "C"], ["C", "Dd"], ["Dd", "A"], ["E", "F"], ["F", "G"], ["G", "Hh"], ["Hh", "E"],
        ["A", "E"], ["B", "F"], ["C", "G"], ["Dd", "Hh"], ["Dd", "R1"], ["C", "R1"], ["Hh", "R2"], ["G", "R2"], ["R1", "R2"]];
      const [r, g, b] = tom(this.nivel);
      const forte = 0.42 + this.nivel * 0.3;
      // Brilho de dentro: a casa "acende" com a carga.
      const luz = c.createRadialGradient(cx, base - (base - topoParede) * 0.45, 10, cx, base - (base - topoParede) * 0.45, meia * 1.2);
      luz.addColorStop(0, `rgba(${r},${g},${b},${0.05 + this.nivel * 0.1})`);
      luz.addColorStop(1, `rgba(${r},${g},${b},0)`);
      c.fillStyle = luz;
      c.fillRect(0, 0, W, Hc);
      // Chão: duas linhas finas, como uma calçada em perspectiva.
      c.lineWidth = 1;
      c.strokeStyle = `rgba(${r},${g},${b},0.16)`;
      for (const z of [0.35, -D - 0.35]) {
        const a = proj([-1.7, 0, z]), d = proj([1.7, 0, z]);
        c.beginPath(); c.moveTo(a[0], a[1]); c.lineTo(d[0], d[1]); c.stroke();
      }
      c.lineCap = "round";
      c.shadowColor = `rgba(${r},${g},${b},0.8)`;
      for (const [u, v] of arestas) {
        const a = p[u], d = p[v];
        const frente = (longe - (a[2] + d[2]) / 2) / Math.max(0.001, longe - perto);   // 1 na frente, 0 no fundo
        c.globalAlpha = Math.min(1, forte * (0.4 + 0.6 * frente));
        c.shadowBlur = frente > 0.6 ? 10 : 0;
        c.lineWidth = frente > 0.6 ? 1.6 : 1;
        c.strokeStyle = `rgb(${r},${g},${b})`;
        c.beginPath(); c.moveTo(a[0], a[1]); c.lineTo(d[0], d[1]); c.stroke();
      }
      c.shadowBlur = 0;
      // O fio que chega na casa, com a energia andando por ele.
      const poste = proj([-1.55, Hm * 0.92, -D / 2]), chegada = proj([-1, Hm * 0.78, -D * 0.2]);
      c.globalAlpha = 0.35;
      c.lineWidth = 1;
      c.beginPath(); c.moveTo(poste[0], poste[1]);
      c.quadraticCurveTo((poste[0] + chegada[0]) / 2, Math.max(poste[1], chegada[1]) + 10, chegada[0], chegada[1]);
      c.stroke();
      const pe = proj([-1.55, 0, -D / 2]);
      c.beginPath(); c.moveTo(pe[0], pe[1]); c.lineTo(poste[0], poste[1] - 6); c.stroke();
      if (this.ativa) {
        const n = 3 + Math.round(this.nivel * 4);
        c.globalAlpha = 0.9;
        c.fillStyle = `rgb(${r},${g},${b})`;
        for (let i = 0; i < n; i++) {
          const t = ((this.correr || 0) + i / n) % 1;
          const mx = (poste[0] + chegada[0]) / 2, my = Math.max(poste[1], chegada[1]) + 10;
          const x = (1 - t) ** 2 * poste[0] + 2 * (1 - t) * t * mx + t * t * chegada[0];
          const y = (1 - t) ** 2 * poste[1] + 2 * (1 - t) * t * my + t * t * chegada[1];
          c.beginPath(); c.arc(x, y, 1.8, 0, Math.PI * 2); c.fill();
        }
      }
      c.globalAlpha = 1;
    }
  }

  window.MWGraficos = { desenharFluxo, criarPerfil, CasaEm3D, desenharTarifaBranca, COR_POSTO, horaCurta, desenharDiasDoMes, desenharTensao,
    desenharUltimosDias, desenharLinha };
})();
