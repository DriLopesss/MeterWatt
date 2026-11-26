/* MeterWatt: "Por dentro do MeterWatt", a vitrine 3D da página inicial.
 *
 * O medidor de verdade (caixa, placa do PZEM-004T, ESP32, tampa com o visor, anel de corrente e o fio de
 * entrada) montado em 3D só com CSS. A rolagem conduz a câmera: o medidor aparece, se desmonta camada por
 * camada, o anel abraça o fio, ele se monta de novo e, no fim, o número do visor salta para fora do palco,
 * por cima da página (a "quarta parede"), com anéis de luz saindo dele.
 *
 * - Nada de biblioteca: cada peça é uma caixa de seis faces (div) com transform 3D. ~70 elementos.
 * - O texto de cada passo também está na lista "Como funciona" logo abaixo: quem usa leitor de tela, ou
 *   pediu para reduzir o movimento, lê a mesma coisa sem animação nenhuma.
 * - Só anima enquanto a seção está na tela. O ponteiro inclina o medidor (no computador).
 * - Estilos pelo CSSOM (el.style.setProperty): a CSP do site bloqueia o atributo style.
 */
"use strict";
(() => {
  const palco = document.getElementById("vitrine");
  if (!palco) return;
  const semMovimento = matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.documentElement.classList.toggle("vitrine-estatica", semMovimento);

  const el = (tag, classe, ...filhos) => {
    const n = document.createElement(tag);
    if (classe) n.className = classe;
    n.append(...filhos);
    return n;
  };
  const px = (v) => `${v.toFixed(2)}px`;
  const lerp = (a, b, t) => a + (b - a) * t;
  const passo = (p, a, b) => Math.max(0, Math.min(1, (p - a) / (b - a)));          // 0 antes de a, 1 depois de b
  const suave = (t) => t * t * (3 - 2 * t);
  const molas = (t) => 1 - (1 - t) ** 3;

  // Uma caixa de seis faces. Eixos: x para a direita, y para baixo, z para quem olha.
  function caixa(classe, w, h, d, { sem = [] } = {}) {
    const c = el("div", `cubo ${classe}`);
    const faces = {
      frente: [w, h, `translateZ(${d / 2}px)`],
      tras: [w, h, `rotateY(180deg) translateZ(${d / 2}px)`],
      direita: [d, h, `rotateY(90deg) translateZ(${w / 2}px)`],
      esquerda: [d, h, `rotateY(-90deg) translateZ(${w / 2}px)`],
      topo: [w, d, `rotateX(90deg) translateZ(${h / 2}px)`],
      base: [w, d, `rotateX(-90deg) translateZ(${h / 2}px)`],
    };
    for (const [nome, [fw, fh, t]] of Object.entries(faces)) {
      if (sem.includes(nome)) continue;
      const f = el("div", `face face-${nome}`);
      f.style.setProperty("width", `${fw}px`);
      f.style.setProperty("height", `${fh}px`);
      f.style.setProperty("margin", `${-fh / 2}px 0 0 ${-fw / 2}px`);
      f.style.setProperty("transform", t);
      c.append(f);
    }
    return c;
  }
  function posicionar(no, x, y, z, extra = "") {
    no.style.setProperty("transform", `translate3d(${px(x)},${px(y)},${px(z)}) ${extra}`);
    return no;
  }

  /* ------------------------------------------------------------ o modelo */

  const cena = el("div", "vitrine-cena");
  const modelo = el("div", "vitrine-modelo");
  cena.append(modelo);

  // Camadas, de trás para a frente. Cada uma tem a posição montada (z0) e o quanto anda ao desmontar (dz).
  const camadas = [];
  function camada(no, z0, dz, extra = {}) {
    const g = el("div", "camada");
    g.append(no);
    modelo.append(g);
    camadas.push({ g, z0, dz, ...extra });
    return g;
  }

  // Caixa (o fundo e as laterais; a frente é a tampa).
  const fundo = caixa("caixa-fundo", 280, 184, 46, { sem: ["frente"] });
  camada(fundo, -12, -150);

  // Placa do PZEM-004T: azul, com o transformador, o borne de parafusos e os chips.
  const pzem = el("div", "peca");
  pzem.append(posicionar(caixa("placa placa-pzem", 150, 96, 3), 0, 0, 0),
    posicionar(caixa("trafo", 40, 30, 26), -38, -12, 15),
    posicionar(caixa("borne", 84, 18, 16), 18, 31, 10),
    posicionar(caixa("chip", 22, 16, 4), 44, -22, 4),
    posicionar(caixa("chip chip-b", 14, 14, 4), 20, -24, 4),
    posicionar(caixa("led-placa", 5, 5, 4), 62, 28, 4));
  camada(posicionar(pzem, -50, 6, 0), 0, -40, { rotulo: "pzem" });

  // ESP32 em cima de espaçadores: a placa preta, a blindagem de metal e a antena.
  const esp = el("div", "peca");
  esp.append(posicionar(caixa("placa placa-esp", 96, 58, 3), 0, 0, 0),
    posicionar(caixa("blindagem", 44, 32, 6), -10, -2, 5),
    posicionar(caixa("antena", 22, 32, 1), 34, -2, 2),
    posicionar(caixa("usb", 14, 10, 6), -46, 18, 4),
    posicionar(caixa("chip chip-c", 12, 12, 4), 30, 18, 4));
  camada(posicionar(esp, 78, -10, 0), 22, 70, { rotulo: "esp" });

  // Tampa fumê com o visor (o número vem da demonstração do topo da página) e o LED.
  const tampa = el("div", "peca");
  // Só a frente: com o verso e as bordas finas, o navegador cortava e ordenava mal as faces translúcidas.
  const tampaPlaca = caixa("tampa", 280, 184, 5, { sem: ["tras", "topo", "base", "esquerda", "direita"] });
  const visor = el("div", "tampa-visor");
  const numero = el("span", "tampa-numero", "0");
  const unidade = el("span", "tampa-unidade", "W");
  visor.append(numero, unidade);
  const marca = el("div", "tampa-marca", "MeterWatt");
  const led = el("div", "tampa-led");
  // O visor, a marca e o LED são pintados na própria face (plana): peças soltas na frente dela o navegador
  // às vezes ordena errado. Para sair da tela no fim, quem anda é uma cópia do visor por cima da página.
  tampaPlaca.querySelector(".face-frente").append(visor, marca, led);
  tampa.append(tampaPlaca);
  camada(tampa, 46, 190, { rotulo: "tampa" });

  // O fio de entrada (vertical, à direita) e o anel de corrente em volta dele, ligado ao PZEM.
  const ladoFio = 230;
  const fio = el("div", "fio");
  for (const r of [0, 60, 120]) fio.append(posicionar(el("div", "fio-plano"), 0, 0, 0, `rotateY(${r}deg)`));
  const anel = el("div", "anel-ct");
  for (let i = 0; i < 7; i++) anel.append(posicionar(el("div", "anel-fatia"), 0, 0, (i - 3) * 3.2));
  const conjuntoFio = el("div", "conjunto-fio");
  conjuntoFio.append(fio, posicionar(anel, 0, 30, 0, "rotateX(90deg)"));
  const gFio = el("div", "camada camada-fio");
  gFio.append(conjuntoFio);
  modelo.append(gFio);

  // Faíscas que saem do medidor e passam por cima do texto no fim.
  const faiscas = Array.from({ length: 14 }, (_, i) => {
    const f = el("i", "faisca");
    f.style.setProperty("--a", `${(i / 14) * 360}deg`);
    f.style.setProperty("--d", `${(i % 5) * 0.11}s`);
    return f;
  });
  const fugaFaiscas = el("div", "fuga-faiscas", ...faiscas);
  const fugaNumero = el("span", "tampa-numero"), fugaUnidade = el("span", "tampa-unidade");
  const fuga = el("div", "fuga-visor", fugaNumero, fugaUnidade);
  fuga.setAttribute("aria-hidden", "true");

  /* ------------------------------------------------------------ os passos */

  const passos = [
    { de: 0.0, ate: 0.16, titulo: "Por dentro do MeterWatt", texto: "Um medidor pequeno, que vai dentro do quadro de luz. Role para abrir." },
    { de: 0.16, ate: 0.36, titulo: "PZEM-004T", texto: "Mede tensão, corrente, potência, fator de potência e frequência, várias vezes por segundo.", alvo: "pzem" },
    { de: 0.36, ate: 0.54, titulo: "ESP32", texto: "Manda uma leitura por segundo pelo Wi-Fi, criptografada (TLS), com uma senha só deste medidor.", alvo: "esp" },
    { de: 0.54, ate: 0.72, titulo: "O anel abraça o fio", texto: "O sensor de corrente envolve o fio de entrada: nada é cortado. A instalação é com eletricista e o disjuntor desligado.", alvo: "fio" },
    { de: 0.72, ate: 0.86, titulo: "Pareia em um minuto", texto: "O medidor entra no Wi-Fi da casa e é ligado à sua conta por um código de 8 letras." },
    { de: 0.86, ate: 1.01, titulo: "A casa aparece na tela", texto: "Watts agora, reais por hora, previsão da conta e o que fica ligado sem ninguém usar." },
  ];
  const legendas = el("ol", "vitrine-passos");
  legendas.setAttribute("aria-hidden", "true");          // a lista "Como funciona" diz o mesmo para leitores de tela
  const itens = passos.map((s, i) => {
    const li = el("li", "vitrine-passo", el("span", "vitrine-num", String(i + 1).padStart(2, "0")), el("strong", "", s.titulo), el("p", "", s.texto));
    legendas.append(li);
    return li;
  });
  const barra = el("div", "vitrine-progresso", el("i"));
  const aneis = el("div", "vitrine-aneis", el("i"), el("i"), el("i"), el("i"));
  const quadro = palco.querySelector(".vitrine-palco");
  quadro.append(aneis, cena, legendas, barra);
  document.body.append(fugaFaiscas, fuga);

  /* ------------------------------------------------------------ o quadro de cada instante */

  let progresso = 0, alvoProgresso = 0, inclinaX = 0, inclinaY = 0, alvoIX = 0, alvoIY = 0, ligado = false, quadroPedido = 0, t0 = performance.now();

  function desenhar(agora) {
    quadroPedido = 0;
    progresso = lerp(progresso, alvoProgresso, semMovimento ? 1 : 0.14);       // a câmera chega macia
    inclinaX = lerp(inclinaX, alvoIX, 0.08);
    inclinaY = lerp(inclinaY, alvoIY, 0.08);
    const p = progresso;
    const t = (agora - t0) / 1000;

    const abre = suave(passo(p, 0.12, 0.34)) * (1 - suave(passo(p, 0.7, 0.84)));   // desmontado no meio
    const entra = molas(passo(p, 0, 0.12));
    const fioVem = suave(passo(p, 0.5, 0.62)) * (1 - suave(passo(p, 0.74, 0.84)));
    const mergulho = suave(passo(p, 0.86, 1));

    // A câmera: gira devagar, olha de cima quando abre, de lado quando o fio vem, de frente no fim.
    const giroY = lerp(-28, 0, suave(passo(p, 0.7, 0.86))) + Math.sin(t * 0.5) * 6 * (1 - mergulho) + lerp(0, -38, fioVem) + abre * 18;
    const giroX = lerp(14, 8, entra) + abre * 32 - fioVem * 18 - mergulho * 8;
    const escala = lerp(0.8, 1.45, entra) * lerp(1, 2.1, mergulho);
    modelo.style.setProperty("transform",
      `translateZ(${px(lerp(-300, 0, entra) + mergulho * 120)}) rotateX(${(giroX + inclinaX).toFixed(2)}deg) rotateY(${(giroY + inclinaY).toFixed(2)}deg) scale(${escala.toFixed(3)})`);

    camadas.forEach((c) => {
      const foco = c.rotulo && passos.some((s, i) => s.alvo === c.rotulo && itens[i].classList.contains("ativo"));
      c.g.style.setProperty("transform", `translateZ(${px(c.z0 + c.dz * abre)})`);
      c.g.classList.toggle("em-foco", Boolean(foco));
    });
    gFio.style.setProperty("transform", `translate3d(${px(lerp(ladoFio + 220, ladoFio, Math.max(entra * 0.3, fioVem)))},0,${px(lerp(-40, 60, fioVem))})`);
    gFio.style.setProperty("opacity", String(fioVem.toFixed(3)));
    gFio.style.setProperty("display", fioVem > 0.01 ? "block" : "none");     // fora da cena: planos escondidos ainda confundem a ordem 3D
    anel.style.setProperty("--brilho", String(fioVem.toFixed(3)));

    const ativo = passos.findIndex((s) => p >= s.de && p < s.ate);
    itens.forEach((li, i) => li.classList.toggle("ativo", i === ativo));
    barra.firstChild.style.setProperty("transform", `scaleX(${p.toFixed(4)})`);
    aneis.style.setProperty("--fora", mergulho.toFixed(3));
    quadro.style.setProperty("--mergulho", mergulho.toFixed(3));

    // A quarta parede: no fim, o número do visor sai do palco por cima da página.
    const fora = passo(p, 0.9, 1);
    fugaFaiscas.classList.toggle("soltas", fora > 0.25 && !semMovimento);
    fuga.classList.toggle("solta", fora > 0.01 && !semMovimento);
    if (fora > 0.01) {
      const r = visor.getBoundingClientRect();
      const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      fugaFaiscas.style.setProperty("left", px(cx));
      fugaFaiscas.style.setProperty("top", px(cy));
      // A cópia nasce em cima do visor e cresce até passar da borda do palco, por cima do texto.
      const k = r.width / 168;
      fuga.style.setProperty("transform", `translate3d(${px(cx)},${px(cy)},0) translate(-50%,-50%) scale(${(k * (1 + fora * 1.5)).toFixed(3)}) rotateX(${((1 - fora) * 10).toFixed(1)}deg)`);
      fuga.style.setProperty("opacity", String(Math.min(1, fora * 3).toFixed(3)));
      fugaNumero.textContent = numero.textContent;
      fugaUnidade.textContent = unidade.textContent;
    }

    const parado = Math.abs(progresso - alvoProgresso) < 0.0005 && Math.abs(inclinaX - alvoIX) < 0.05 && Math.abs(inclinaY - alvoIY) < 0.05;
    if (ligado && (!parado || !semMovimento)) quadroPedido = requestAnimationFrame(desenhar);
  }

  function medir() {
    if (semMovimento) return;                  // parado: fica aberto, sem seguir a rolagem
    const r = palco.getBoundingClientRect();
    const total = r.height - innerHeight;
    alvoProgresso = total > 0 ? Math.max(0, Math.min(1, -r.top / total)) : 0;
    if (ligado && !quadroPedido) quadroPedido = requestAnimationFrame(desenhar);
  }

  // O número do visor acompanha a casa simulada do topo da página.
  function lerDemo() {
    const fonte = document.getElementById("visor-demo");
    const texto = fonte && fonte.getAttribute("aria-label");
    const m = texto && texto.match(/([\d.]+)\s*(\w+)/);
    if (m) { numero.textContent = m[1]; unidade.textContent = m[2]; }
  }

  new IntersectionObserver((entradas) => {
    ligado = entradas[0].isIntersecting && !document.hidden;
    if (ligado) { medir(); lerDemo(); }
    else { fugaFaiscas.classList.remove("soltas"); fuga.classList.remove("solta"); }
  }, { rootMargin: "100px 0px" }).observe(palco);
  addEventListener("scroll", medir, { passive: true });
  addEventListener("resize", medir);
  setInterval(() => { if (ligado) lerDemo(); }, 700);
  if (!semMovimento) {
    palco.addEventListener("pointermove", (ev) => {
      if (ev.pointerType !== "mouse") return;
      alvoIY = ((ev.clientX / innerWidth) - 0.5) * 22;
      alvoIX = -((ev.clientY / innerHeight) - 0.5) * 14;
      if (ligado && !quadroPedido) quadroPedido = requestAnimationFrame(desenhar);
    });
    palco.addEventListener("pointerleave", () => { alvoIX = 0; alvoIY = 0; });
  } else {
    alvoProgresso = 0.3;     // parado, aberto, para mostrar as peças
    progresso = 0.3;
  }
  requestAnimationFrame(desenhar);
})();
