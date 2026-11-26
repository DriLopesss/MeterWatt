/* MeterWatt: o movimento e os detalhes do app.
 *
 * Tudo aqui é enfeite por cima do que já funciona: se este arquivo não carregar, o app continua igual.
 * - Fundo de trilhas de circuito em duas camadas, que andam em velocidades diferentes na rolagem.
 * - Cartões que entram em 3D ao rolar (pela própria rolagem no CSS quando o navegador sabe; senão, por
 *   IntersectionObserver) e que inclinam na direção do ponteiro, com um reflexo de luz.
 * - Troca de tela: a tela nova entra de lado em 3D, na direção da aba; o título "decodifica" as letras.
 * - Entre áreas (entrar na conta, demonstração, sair): uma fileira de disjuntores sobe e mostra a tela nova.
 * - Faísca, a mascote, comenta o consumo de agora; o disco do relógio de luz gira na velocidade da casa.
 * - Osciloscópio em cada medidor: a tensão e a corrente de 60 Hz desenhadas com o atraso do fator de potência.
 * - Tela de erro e sem conexão: apagão, com a luz da lanterna seguindo o ponteiro e um interruptor.
 *
 * Com "reduzir movimento" no aparelho, nada anda: só os desenhos parados. Em aparelho fraco (poucos núcleos
 * ou economia de dados), o fundo e a inclinação dos cartões ficam desligados.
 * Estilos pelo CSSOM (el.style.setProperty): a CSP do site bloqueia o atributo style.
 */
"use strict";
(() => {
  const raiz = document.documentElement;
  const semMovimento = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const leve = semMovimento || (navigator.hardwareConcurrency || 8) <= 4 || (navigator.connection && navigator.connection.saveData)
    || (navigator.deviceMemory && navigator.deviceMemory <= 2);
  const ponteiroFino = matchMedia("(pointer: fine)").matches;
  raiz.classList.toggle("fx-leve", Boolean(leve));
  raiz.classList.toggle("fx-parado", semMovimento);
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const el = (tag, classe, ...filhos) => { const n = document.createElement(tag); if (classe) n.className = classe; n.append(...filhos); return n; };
  const NS = "http://www.w3.org/2000/svg";
  const sv = (tag, attrs = {}) => { const n = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); return n; };
  const numeroBR = (texto) => { const m = String(texto || "").replace(/\./g, "").replace(",", ".").match(/-?\d+(\.\d+)?/); return m ? Number(m[0]) : null; };

  /* ------------------------------------------------------------ fundo em camadas */

  const fundo = el("div", "fundo-circuito", el("i", "fundo-camada fundo-longe"), el("i", "fundo-camada fundo-perto"));
  fundo.setAttribute("aria-hidden", "true");
  document.body.prepend(fundo);
  if (!leve) {
    const [longe, perto] = fundo.children;
    let pedido = 0;
    const mover = () => {
      pedido = 0;
      const y = scrollY;
      longe.style.setProperty("transform", `translate3d(0,${(-y * 0.08).toFixed(1)}px,0)`);
      perto.style.setProperty("transform", `translate3d(0,${(-y * 0.22).toFixed(1)}px,0)`);
    };
    addEventListener("scroll", () => { if (!pedido) pedido = requestAnimationFrame(mover); }, { passive: true });
  }

  /* ------------------------------------------------------------ cartões que entram em 3D */

  const REVELAR = ".bloco, .medidor, .eco-grupo, .aparelho, .faixa-inicio > *, .rel-numero, .qe-fase, .passos-inicio li, .compromissos li";
  const rolagemNoCss = CSS.supports && CSS.supports("animation-timeline: view()");
  raiz.classList.toggle("fx-rolagem-css", Boolean(rolagemNoCss) && !semMovimento);
  const observador = !semMovimento && !rolagemNoCss && "IntersectionObserver" in window
    ? new IntersectionObserver((entradas) => entradas.forEach((e) => {
      if (e.isIntersecting) { e.target.classList.add("revelado"); observador.unobserve(e.target); }
    }), { rootMargin: "0px 0px -8% 0px", threshold: 0.08 })
    : null;
  function prepararRevelar(raizBusca = document) {
    if (!observador) return;
    $$(REVELAR, raizBusca).forEach((n, i) => {
      if (n.dataset.fxRevela) return;
      n.dataset.fxRevela = "1";
      const r = n.getBoundingClientRect();
      if (r.top < innerHeight && r.bottom > 0) { n.classList.add("revelado"); return; }    // já à vista: não esconde
      n.classList.add("revelar");
      n.style.setProperty("--fx-i", String(i % 4));
      observador.observe(n);
    });
  }

  /* ------------------------------------------------------------ inclinação e reflexo no ponteiro */

  const INCLINAR = ".bloco, .medidor, .eco-grupo, .aparelho, .heroi-visor, .calc-resultado, .rel-numero";
  if (!leve && ponteiroFino) {
    let atual = null, pedido = 0, ultimo = null;
    const aplicar = () => {
      pedido = 0;
      if (!atual || !ultimo) return;
      const r = atual.getBoundingClientRect();
      const x = (ultimo.clientX - r.left) / r.width, y = (ultimo.clientY - r.top) / r.height;
      const forca = atual.classList.contains("heroi-visor") ? 10 : Math.max(1.2, Math.min(4, 900 / r.width));
      atual.style.setProperty("--fx-rx", `${((0.5 - y) * forca).toFixed(2)}deg`);
      atual.style.setProperty("--fx-ry", `${((x - 0.5) * forca).toFixed(2)}deg`);
      atual.style.setProperty("--fx-mx", `${(x * 100).toFixed(1)}%`);
      atual.style.setProperty("--fx-my", `${(y * 100).toFixed(1)}%`);
    };
    const soltar = (n) => { if (!n) return; n.classList.remove("inclinado"); ["--fx-rx", "--fx-ry"].forEach((v) => n.style.removeProperty(v)); };
    document.addEventListener("pointermove", (ev) => {
      if (ev.pointerType !== "mouse") return;
      const alvo = ev.target.closest && ev.target.closest(INCLINAR);
      // Dentro de gráficos e campos a inclinação atrapalha a mira: só o reflexo continua.
      const mirando = ev.target.closest && ev.target.closest("svg, input, select, textarea, canvas, .grafico-linha");
      if (alvo !== atual) { soltar(atual); atual = alvo; }
      if (!atual) return;
      atual.classList.add("inclinado");
      atual.classList.toggle("mirando", Boolean(mirando));
      ultimo = ev;
      if (!pedido) pedido = requestAnimationFrame(aplicar);
    }, { passive: true });
    document.addEventListener("pointerleave", () => { soltar(atual); atual = null; });
  }

  /* ------------------------------------------------------------ troca de tela */

  const ORDEM = ["inicio", "painel", "relatorio", "medidores", "aparelhos", "economia", "seguranca", "privacidade", "conta", "avisos",
    "plano", "compartilhar", "integracao", "entrar", "cadastro", "esqueci"];
  let rotaAnterior = null;
  const SIMBOLOS = "▮▯01⚡≈∿#%&$<>/\\";
  function decodificar(h) {
    if (semMovimento || !h || h.dataset.fxDecodificando) return;
    const texto = h.textContent;
    if (!texto || texto.length > 60 || h.children.length) return;
    h.dataset.fxDecodificando = "1";
    h.setAttribute("aria-label", texto);
    const inicio = performance.now(), dur = Math.min(700, 220 + texto.length * 18);
    let escrito = texto;
    const fim = (final) => { if (final != null) h.textContent = final; h.removeAttribute("aria-label"); delete h.dataset.fxDecodificando; };
    const quadro = (agora) => {
      if (h.textContent !== escrito) { fim(null); return; }        // o app trocou o título no meio: fica o dele
      const t = Math.min(1, (agora - inicio) / dur);
      const prontas = Math.floor(t * texto.length);
      escrito = texto.slice(0, prontas) + [...texto.slice(prontas)].map((c) => (c === " " ? " " : SIMBOLOS[(Math.random() * SIMBOLOS.length) | 0])).join("");
      h.textContent = escrito;
      if (t < 1) requestAnimationFrame(quadro);
      else fim(texto);
    };
    requestAnimationFrame(quadro);
  }
  function telaApareceu(sec) {
    const rota = sec.dataset.rota;
    prepararRevelar(sec);
    if (semMovimento || rota === rotaAnterior) { rotaAnterior = rota; return; }
    const de = ORDEM.indexOf(rotaAnterior), para = ORDEM.indexOf(rota);
    sec.classList.remove("fx-entra-direita", "fx-entra-esquerda");
    void sec.offsetWidth;                                   // reinicia a animação
    sec.classList.add(de >= 0 && para >= 0 && para < de ? "fx-entra-esquerda" : "fx-entra-direita");
    setTimeout(() => sec.classList.remove("fx-entra-direita", "fx-entra-esquerda"), 900);
    decodificar($("h1", sec));
    rotaAnterior = rota;
  }
  new MutationObserver((mudancas) => {
    for (const m of mudancas) {
      const n = m.target;
      if (m.type === "attributes" && m.attributeName === "hidden" && n.matches && n.matches("section[data-rota]") && !n.hidden) telaApareceu(n);
      if (m.type === "childList") m.addedNodes.forEach((a) => { if (a.nodeType === 1) { prepararRevelar(a.parentElement || a); ligarOsciloscopios(a); } });
    }
  }).observe(document.body, { subtree: true, attributes: true, attributeFilter: ["hidden"], childList: true });

  /* ------------------------------------------------------------ cortina de disjuntores entre áreas */

  const cortina = el("div", "cortina-disjuntores");
  cortina.setAttribute("aria-hidden", "true");
  for (let i = 0; i < 7; i++) {
    const d = el("i", "", el("b"));
    d.style.setProperty("--k", String(i));
    cortina.append(d);
  }
  document.body.append(cortina);
  let areaAnterior = document.body.dataset.area || null;
  new MutationObserver(() => {
    const area = document.body.dataset.area;
    if (area === areaAnterior) return;
    const vinha = areaAnterior;
    areaAnterior = area;
    if (!vinha || semMovimento) return;                    // primeira tela: sem cortina
    cortina.classList.remove("abre");
    cortina.classList.add("fechada");
    void cortina.offsetWidth;
    requestAnimationFrame(() => { cortina.classList.remove("fechada"); cortina.classList.add("abre"); });
    setTimeout(() => cortina.classList.remove("abre"), 1100);
  }).observe(document.body, { attributes: true, attributeFilter: ["data-area"] });

  /* ------------------------------------------------------------ Faísca e o disco do relógio de luz */

  // Faísca: um raio em pixel art (desenho original do MeterWatt), 12 x 16 quadradinhos.
  const DESENHO = [
    "......AAAA..",
    ".....AAAAA..",
    "....AAAAA...",
    "...AAAAA....",
    "..AAOAAOA...",
    "..AAAAAAAAA.",
    ".AAAAKKAAAA.",
    ".AAAAAAAAA..",
    "...AAAAAA...",
    "....AAAAA...",
    "...AAAAA....",
    "...AAAA.....",
    "..AAAA......",
    "..AAA.......",
    ".AAA........",
    ".AA.........",
  ];
  function desenhoFaisca() {
    const s = sv("svg", { viewBox: "0 0 12 16", class: "faisca-desenho", "aria-hidden": "true", "shape-rendering": "crispEdges" });
    DESENHO.forEach((linha, y) => [...linha].forEach((c, x) => {
      if (c === ".") return;
      s.append(sv("rect", { x, y, width: 1.02, height: 1.02, class: { A: "fa-corpo", O: "fa-olho", K: "fa-boca" }[c] }));
    }));
    return s;
  }
  const relogio = $(".relogio");
  let faisca = null, fala = null, disco = null;
  if (relogio) {
    fala = el("p", "faisca-fala");
    fala.setAttribute("aria-live", "polite");
    faisca = el("div", "faisca-mascote", desenhoFaisca(), el("div", "faisca-balao", el("b", "", "Faísca"), fala));
    relogio.after(faisca);
    disco = el("div", "disco-relogio");
    disco.setAttribute("aria-hidden", "true");
    disco.append(el("i", "disco-roda", el("b")), el("span", "disco-rotulo", "kWh"));
    relogio.append(disco);
  }
  const reais = (v) => Number(v).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  let wattsAntes = null, falaAntes = "";
  document.addEventListener("mw:agora", (ev) => {
    const { watts, unidade, custoHora } = ev.detail || {};
    if (disco) {
      // Uma volta do disco a cada (3600 / watts) segundos, como num relógio de 1 Wh por volta, acelerado 40x.
      const periodo = watts ? Math.max(0.35, Math.min(30, 3600 / watts / 40)) : 0;
      disco.classList.toggle("parado", !periodo);
      if (periodo) disco.style.setProperty("--volta", `${periodo.toFixed(2)}s`);
    }
    if (!fala) return;
    const w = watts == null ? null : Math.round(watts);
    const custo = custoHora != null ? `, ${reais(custoHora)} por hora` : "";
    let frase;
    if (w == null) frase = "Sem sinal do medidor agora. Será que caiu o Wi-Fi da casa?";
    else if (wattsAntes != null && w - wattsAntes >= 900) frase = `Opa! Alguém ligou algo de uns ${(w - wattsAntes).toLocaleString("pt-BR")} ${unidade} agora.`;
    else if (wattsAntes != null && wattsAntes - w >= 900) frase = `Desligaram algo forte: caiu ${(wattsAntes - w).toLocaleString("pt-BR")} ${unidade}.`;
    else if (w < 150) frase = `Casa quase parada: ${w} ${unidade}. Só o básico ligado.`;
    else if (w < 700) frase = `Ritmo tranquilo: ${w} ${unidade}${custo}.`;
    else if (w < 1800) frase = `Tem coisa forte ligada: ${w.toLocaleString("pt-BR")} ${unidade}${custo}.`;
    else frase = `Pico de ${w.toLocaleString("pt-BR")} ${unidade}! Parece chuveiro, forno ou ferro de passar.`;
    wattsAntes = w;
    if (frase !== falaAntes) {
      falaAntes = frase;
      fala.textContent = frase;
      faisca.classList.remove("falou");
      void faisca.offsetWidth;
      faisca.classList.add("falou");
      faisca.classList.toggle("assustada", w != null && w >= 1800);
    }
  });

  /* ------------------------------------------------------------ osciloscópio de cada medidor */

  const osciloscopios = new Set();
  function ligarOsciloscopios(raizBusca) {
    const cartoes = raizBusca.matches && raizBusca.matches(".medidor") ? [raizBusca] : $$(".medidor", raizBusca);
    cartoes.forEach((cartao) => {
      if ($(".osciloscopio", cartao)) return;
      const tela = el("canvas", "osciloscopio-tela");
      const leitura = el("p", "osciloscopio-leitura");
      const caixa = el("figure", "osciloscopio", tela, el("figcaption", "", el("span", "osc-v", "tensão"), el("span", "osc-i", "corrente"), el("span", "osc-p", "potência"), leitura));
      tela.setAttribute("role", "img");
      tela.setAttribute("aria-label", "Osciloscópio: tensão e corrente da rede.");
      const grafico = $(".grafico", cartao);
      if (!grafico) return;
      grafico.before(caixa);
      osciloscopios.add({ cartao, tela, leitura, caixa, visivel: false });
    });
  }
  const verOsc = "IntersectionObserver" in window ? new IntersectionObserver((entradas) => entradas.forEach((e) => {
    osciloscopios.forEach((o) => { if (o.caixa === e.target) o.visivel = e.isIntersecting; });
  })) : null;
  function quadroOsciloscopios(agora) {
    osciloscopios.forEach((o) => {
      if (!o.cartao.isConnected) { osciloscopios.delete(o); return; }
      if (verOsc && !o.observado) { verOsc.observe(o.caixa); o.observado = true; }
      if (verOsc && !o.visivel && o.desenhado) return;
      const ler = (k) => numeroBR($(`[data-medida="${k}"]`, o.cartao)?.textContent);
      const v = ler("tensao"), i = ler("corrente"), fp = ler("fp"), hz = ler("frequencia") || 60;
      desenharOsciloscopio(o, v, i, fp, hz, semMovimento ? 0 : agora / 1000);
      o.desenhado = true;
    });
    if (!semMovimento) requestAnimationFrame(quadroOsciloscopios);
  }
  function desenharOsciloscopio(o, v, i, fp, hz, t) {
    const c = o.tela, dpr = Math.min(2, devicePixelRatio || 1);
    const W = c.clientWidth || 300, H = c.clientHeight || 120;
    if (c.width !== Math.round(W * dpr)) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); }
    const g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    // Grade de osciloscópio: 10 divisões na horizontal, 4 na vertical.
    g.strokeStyle = "rgba(54,227,255,.08)"; g.lineWidth = 1;
    for (let k = 1; k < 10; k++) { g.beginPath(); g.moveTo((W * k) / 10, 0); g.lineTo((W * k) / 10, H); g.stroke(); }
    for (let k = 1; k < 4; k++) { g.beginPath(); g.moveTo(0, (H * k) / 4); g.lineTo(W, (H * k) / 4); g.stroke(); }
    g.strokeStyle = "rgba(255,255,255,.14)"; g.beginPath(); g.moveTo(0, H / 2); g.lineTo(W, H / 2); g.stroke();
    if (!v || i == null) {
      o.leitura.textContent = "Sem leitura agora.";
      o.tela.setAttribute("aria-label", "Osciloscópio sem leitura agora.");
      return;
    }
    const phi = Math.acos(Math.max(-1, Math.min(1, fp ?? 1)));
    const ciclos = 2.5, w = 2 * Math.PI * ciclos, fase = t * 2 * Math.PI * 0.6;   // anda devagar para os olhos acompanharem
    const av = H * 0.36, ai = H * 0.36 * Math.max(0.12, Math.min(1, i / 20));
    const curva = (amp, atraso, cor, larg) => {
      g.beginPath();
      for (let x = 0; x <= W; x += 2) {
        const a = (x / W) * w - fase - atraso;
        const y = H / 2 - amp * Math.sin(a);
        if (x) g.lineTo(x, y); else g.moveTo(x, y);
      }
      g.strokeStyle = cor; g.lineWidth = larg; g.shadowColor = cor; g.shadowBlur = 10; g.stroke(); g.shadowBlur = 0;
    };
    // Potência instantânea v·i (a média dela é a potência ativa): área magenta suave.
    g.beginPath();
    g.moveTo(0, H / 2);
    for (let x = 0; x <= W; x += 3) {
      const a = (x / W) * w - fase;
      const p = Math.sin(a) * Math.sin(a - phi);
      g.lineTo(x, H / 2 - p * H * 0.3);
    }
    g.lineTo(W, H / 2); g.closePath();
    g.fillStyle = "rgba(255,79,216,.16)"; g.fill();
    curva(av, 0, "#36e3ff", 2);
    curva(ai, phi, "#ffb020", 2);
    const graus = (phi * 180) / Math.PI;
    const atrasoMs = (phi / (2 * Math.PI * hz)) * 1000;
    o.leitura.textContent = `φ ${graus.toLocaleString("pt-BR", { maximumFractionDigits: 1 })}° · a corrente atrasa ${atrasoMs.toLocaleString("pt-BR", { maximumFractionDigits: 2 })} ms · ${hz.toLocaleString("pt-BR")} Hz`;
    o.tela.setAttribute("aria-label", `Osciloscópio: tensão de ${v.toLocaleString("pt-BR")} V e corrente de ${i.toLocaleString("pt-BR")} A, `
      + `fator de potência ${(fp ?? 1).toLocaleString("pt-BR")}: a corrente atrasa ${graus.toFixed(0)} graus em relação à tensão.`);
  }
  ligarOsciloscopios(document);
  requestAnimationFrame(quadroOsciloscopios);

  /* ------------------------------------------------------------ apagão: telas de erro e sem conexão */

  $$("#area-erro, #area-sem-conexao").forEach((tela) => {
    tela.classList.add("apagao");
    const luz = el("div", "apagao-luz");
    luz.setAttribute("aria-hidden", "true");
    const interruptor = el("button", "interruptor", el("i"));
    interruptor.type = "button";
    interruptor.setAttribute("aria-pressed", "false");
    interruptor.setAttribute("aria-label", "Acender a luz");
    interruptor.addEventListener("click", () => {
      const acesa = interruptor.getAttribute("aria-pressed") !== "true";
      interruptor.setAttribute("aria-pressed", String(acesa));
      interruptor.setAttribute("aria-label", acesa ? "Apagar a luz" : "Acender a luz");
      tela.classList.toggle("acesa", acesa);
    });
    tela.prepend(luz);
    tela.append(interruptor);
    const mover = (ev) => {
      const r = tela.getBoundingClientRect();
      tela.style.setProperty("--lx", `${(ev.clientX - r.left).toFixed(0)}px`);
      tela.style.setProperty("--ly", `${(ev.clientY - r.top).toFixed(0)}px`);
    };
    tela.addEventListener("pointermove", mover, { passive: true });
    tela.addEventListener("pointerdown", mover, { passive: true });
  });

  /* ------------------------------------------------------------ começo */

  prepararRevelar(document);
  $$("section[data-rota]").forEach((s) => { if (!s.hidden) { rotaAnterior = s.dataset.rota; prepararRevelar(s); } });
})();
