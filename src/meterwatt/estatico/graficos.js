/* MeterWatt: os gráficos do painel.
 *
 * - Para onde vai agora: o consumo de agora saindo da entrada para cada parte (circuitos medidos,
 *   imóveis ou aparelhos que o Detetive reconhece), em faixas com espessura pela potência.
 * - Perfil do dia: hoje hora a hora contra o normal da casa, e o mapa dos últimos 14 dias.
 * - A casa em 3D: a casa em linhas em volta do número de "Sua casa agora", com a cor pela carga.
 *
 * Cores das marcas: paleta validada pelo método do skill de dataviz no fundo dos cartões (#171428):
 * energia #c78602, sempre ligado #ee3dc9 (o mesmo magenta do consumo fantasma) e resto #7d7899.
 * As partes têm a mesma cor por tipo: quem diz qual é qual é o rótulo ao lado, nunca só a cor.
 * Texto nunca usa a cor da série. Nada de innerHTML: o que vem do servidor entra como texto.
 */
"use strict";
(() => {
  const NS = "http://www.w3.org/2000/svg";
  const semMovimento = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const COR = { energia: "#c78602", base: "#ee3dc9", resto: "#7d7899", normal: "#b7b2d8", grade: "rgba(255,255,255,.08)" };
  const COR_DO_TIPO = { circuito: COR.energia, aparelho: COR.energia, entrada: COR.energia, base: COR.base, resto: COR.resto };
  // Rampa sequencial (uma cor, do escuro para o claro), com luminosidade crescente: mapa de 14 dias.
  const RAMPA = ["#382b19", "#4f3a1c", "#684a1d", "#815a1d", "#9b6b1a", "#b67c13", "#d28e00", "#eda101", "#ffb84a"];
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
          "stroke-width": Math.min(3, Math.max(1.5, hs[i] / 5)).toFixed(1) });
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

    botoes.forEach((b) => b.addEventListener("click", () => {
      vista = b.dataset.vista;
      botoes.forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      foco = null;
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
      for (let i = 0; i <= 2; i++) {
        const v = (max / 2) * i;
        const casas = [0, 1, 2].find((c) => Math.abs(v * 10 ** c - Math.round(v * 10 ** c)) < 1e-9) ?? 2;
        svg.append(s("line", { x1: m.e, x2: W - m.d, y1: y(v), y2: y(v), class: "grade" }),
          s("text", { x: m.e - 6, y: y(v) + 4, class: "eixo", "text-anchor": "end" }, num(v, casas)));
      }
      svg.append(s("text", { x: m.e - 6, y: 10, class: "eixo", "text-anchor": "end" }, "kWh"));
      if (foco != null) svg.append(s("rect", { x: m.e + foco * slot, y: m.t, width: slot, height: ph, class: "foco" }));
      hoje.forEach((v, h) => {
        if (v == null || v <= 0) return;
        const x = m.e + h * slot + (slot - bw) / 2;
        svg.append(s("path", { d: colunaPath(x, y(v), bw, m.t + ph - y(v), 4), fill: COR.energia,
          class: h === dados.hora_atual ? "barra-parcial" : null }));
      });
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
      if (detalhes.open) montarTabela();
    }

    let largUltima = 0;
    new ResizeObserver(() => { const w = grafico.clientWidth; if (w && Math.abs(w - largUltima) > 8) { largUltima = w; desenhar(); } }).observe(grafico);
    return { atualizar(d) { dados = d; desenhar(); } };
  }

  /* ------------------------------------------------------------ Tarifa Branca: o dia útil típico */

  // Do mais barato ao mais caro: o mesmo caminho de cores da casa em 3D (frio quando a casa está calma,
  // magenta no pico). Conferidas no validador de paleta sobre o fundo #171428 (contraste, daltonismo).
  const COR_POSTO = { fora: "#14a3bf", intermediario: "#c78602", ponta: "#ee3dc9" };
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
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, "aria-hidden": "true" });
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
    dados.forEach((v, i) => {
      if (v == null || v <= 0) return;
      const x = m.e + i * slot + (slot - bw) / 2;
      svg.append(s("path", { d: colunaPath(x, y(v), bw, m.t + ph - y(v), Math.min(3, bw / 2)), fill: COR_POSTO[r.postos[i]] },
        s("title", {}, `${meiaHora(i)} às ${meiaHora(i + 1)}: ${kwh(v)} (${NOME_POSTO[r.postos[i]]})`)));
    });
    [0, 6, 12, 18, 24].forEach((h) => svg.append(s("text", { x: m.e + h * 2 * slot, y: H - 6, class: "eixo",
      "text-anchor": h === 0 ? "start" : h === 24 ? "end" : "middle" }, `${h}h`)));
    caixa.replaceChildren(svg);
    const pct = (k) => `${Math.round(r.mes.percentual[k] * 100)}%`;
    caixa.setAttribute("aria-label", `Consumo de um dia útil típico, de meia em meia hora. Ponta das ${horaCurta(r.ponta.inicio)} às ` +
      `${horaCurta(r.ponta.fim)}. No mês, ${pct("ponta")} do consumo cai na ponta, ${pct("intermediario")} no intermediário e ` +
      `${pct("fora")} fora de ponta. A tabela abaixo tem os totais.`);
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

  window.MWGraficos = { desenharFluxo, criarPerfil, CasaEm3D, desenharTarifaBranca, COR_POSTO, horaCurta };
})();
