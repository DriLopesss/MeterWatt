/* MeterWatt: aplicativo com contas.
 *
 * Regras deste arquivo:
 * - Nada de innerHTML com dado vindo do servidor: tudo entra por textContent (função el()).
 * - O token da sessão fica num cookie HttpOnly que o JavaScript nem enxerga. Aqui só
 *   circula o token CSRF, enviado no cabeçalho X-CSRF-Token.
 * - As rotas ficam depois do "#": links de e-mail com token nunca chegam ao servidor
 *   nem aparecem em registros de acesso.
 */
"use strict";

const estado = { csrf: null, eu: null, info: null, medidores: [], periodo: {}, timers: [], rota: null };
const $ = (sel, raiz = document) => raiz.querySelector(sel);
const $$ = (sel, raiz = document) => Array.from(raiz.querySelectorAll(sel));
const semMovimento = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ------------------------------------------------------------------ utilidades */

function el(tag, attrs = {}, ...filhos) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const f of filhos.flat()) if (f !== null && f !== undefined && f !== false) n.append(f instanceof Node ? f : String(f));
  return n;
}

const fmt = {
  num: (v, casas = 0) => v == null ? "—" : Number(v).toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas }),
  reais: (v) => v == null ? "—" : Number(v).toLocaleString("pt-BR", { style: "currency", currency: "BRL" }),
  kwh: (v) => v == null ? "—" : `${fmt.num(v, v < 10 ? 2 : 1)} kWh`,
  quando: (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    return d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  },
  relativo: (iso) => {
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 90) return "agora há pouco";
    if (s < 3600) return `há ${Math.round(s / 60)} min`;
    if (s < 86400) return `há ${Math.round(s / 3600)} h`;
    return `há ${Math.round(s / 86400)} dia(s)`;
  },
};

function numeroDigitado(texto) {
  if (texto == null || String(texto).trim() === "") return null;
  const n = Number(String(texto).replace(/\./g, "").replace(",", "."));
  return Number.isFinite(n) ? n : NaN;
}

function avisar(texto, ruim = false) {
  const a = $("#aviso");
  a.textContent = texto;
  a.classList.toggle("ruim", ruim);
  a.hidden = false;
  clearTimeout(avisar.t);
  avisar.t = setTimeout(() => { a.hidden = true; }, 4500);
}

function baixar(nome, conteudo, tipo) {
  const url = URL.createObjectURL(new Blob([conteudo], { type: tipo }));
  const a = el("a", { href: url, download: nome });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/* ------------------------------------------------------------------ API */

class ErroApi extends Error {
  constructor(status, corpo) {
    super((corpo && corpo.erro) || "Não foi possível falar com o servidor. Confira a conexão e tente de novo.");
    this.status = status;
    this.codigo = corpo && corpo.codigo;
  }
}

async function api(metodo, caminho, dados, { cru = false } = {}) {
  const opcoes = { method: metodo, credentials: "same-origin", headers: { Accept: "application/json" } };
  if (metodo !== "GET") {
    opcoes.headers["Content-Type"] = "application/json";
    opcoes.body = JSON.stringify(dados || {});
    if (estado.csrf) opcoes.headers["X-CSRF-Token"] = estado.csrf;
  }
  let r;
  try {
    r = await fetch(caminho, opcoes);
  } catch {
    throw new ErroApi(0, null);
  }
  if (cru && r.ok) return r;
  const corpo = await r.json().catch(() => null);
  if (!r.ok) {
    const erro = new ErroApi(r.status, corpo);
    if (erro.codigo === "sem_sessao" && estado.eu) {
      estado.eu = null;
      estado.csrf = null;
      avisar("A sessão terminou. Entre de novo.");
      ir("entrar");
    } else if (erro.codigo === "2fa_obrigatorio") {
      $("#faixa-2fa").hidden = false;
    }
    throw erro;
  }
  if (corpo && corpo.csrf) estado.csrf = corpo.csrf;
  return corpo;
}

/* Liga um formulário à API: desabilita o botão, mostra o erro no lugar certo. */
function formulario(form, enviar) {
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const erro = $(".erro", form);
    const botao = $("[type=submit]", form);
    erro.textContent = "";
    $$("[aria-invalid]", form).forEach((c) => c.removeAttribute("aria-invalid"));
    const vazio = $$("input[required]", form).find((c) => (c.type === "checkbox" ? !c.checked : !c.value.trim()));
    if (vazio) {
      vazio.setAttribute("aria-invalid", "true");
      vazio.focus();
      erro.textContent = vazio.type === "checkbox" ? "Marque as autorizações obrigatórias para continuar." : "Preencha este campo.";
      return;
    }
    botao.disabled = true;
    try {
      await enviar(Object.fromEntries(new FormData(form)), form);
    } catch (e) {
      erro.textContent = e.message;
    } finally {
      botao.disabled = false;
    }
  });
}

/* ------------------------------------------------------------------ rotas */

const ROTAS_PUBLICAS = new Set(["entrar", "cadastro", "enviado", "confirmar", "esqueci", "redefinir", "2fa"]);
const ROTAS_APP = new Set(["painel", "medidores", "aparelhos", "economia", "conta", "avisos", "plano", "pagamento-teste", "seguranca", "privacidade"]);
// No celular, estas telas ficam dentro da aba "Conta".
const DENTRO_DE_CONTA = new Set(["conta", "seguranca", "privacidade", "plano", "avisos", "pagamento-teste"]);
const ROTAS_INICIO = new Set(["inicio", "fila", "convite", "sair-da-fila"]);
const DOCUMENTOS = new Set(["politica", "termos", "excluir-conta"]);
const TITULOS = { entrar: "Entrar", cadastro: "Criar conta", painel: "Painel", medidores: "Medidores", seguranca: "Segurança",
  privacidade: "Privacidade", politica: "Política de privacidade", termos: "Termos de uso", "2fa": "Verificação",
  inicio: "Energia que você enxerga", fila: "Lista de espera", "sair-da-fila": "Sair da lista", aparelhos: "Aparelhos",
  avisos: "Avisos", plano: "Plano", "pagamento-teste": "Pagamento de teste", economia: "E se eu…?", conta: "Conta",
  "excluir-conta": "Excluir a conta" };

function ir(rota) {
  if (location.hash !== `#${rota}`) location.hash = rota;
  else mostrar();
}

function pararTimers() {
  estado.timers.forEach(clearInterval);
  estado.timers = [];
}

async function mostrar() {
  const [rotaBruta, token] = location.hash.replace(/^#\/?/, "").split("/");
  let rota = rotaBruta;
  if (rota === "convite") {            // link de convite: guarda o código e mostra a página inicial
    estado.convite = token;
    rota = "inicio";
  }
  const alvo = rota || (estado.eu ? "painel" : "inicio");
  pararTimers();
  fecharDialogos();
  $("#area-sem-conexao").hidden = !estado.semConexao;
  if (estado.semConexao) {           // abriu sem internet: não dá para saber se a conta está aberta
    document.body.dataset.tela = "sem-conexao";
    ["#area-publica", "#area-app", "#area-erro", "#area-inicio"].forEach((a) => { $(a).hidden = true; });
    demo.ligar(false);
    particulas.ligar(false);
    document.title = "Sem conexão | MeterWatt";
    estado.timers.push(setInterval(() => { if (!document.hidden && navigator.onLine) reconectar(); }, 20000));
    return;
  }

  let area;
  if (DOCUMENTOS.has(alvo)) area = estado.eu ? "app" : "publica";
  else if (ROTAS_PUBLICAS.has(alvo)) area = "publica";
  else if (ROTAS_APP.has(alvo)) area = "app";
  else if (ROTAS_INICIO.has(alvo)) area = "inicio";
  else area = "erro";

  if (area === "app" && !estado.eu) return ir("entrar");
  if (area === "publica" && estado.eu && alvo !== "confirmar" && alvo !== "redefinir") return ir("painel");

  document.body.dataset.tela = alvo;
  document.body.dataset.area = area;
  $("#area-publica").hidden = area !== "publica";
  $("#area-app").hidden = area !== "app";
  $("#area-erro").hidden = area !== "erro";
  $("#area-inicio").hidden = area !== "inicio";
  demo.ligar(alvo === "inicio");
  document.title = `${TITULOS[alvo] || "Página não encontrada"} | MeterWatt`;
  if (area === "erro") {
    $("#area-erro .botao").setAttribute("href", estado.eu ? "#painel" : "#inicio");
    $("#area-erro .botao").textContent = estado.eu ? "Voltar ao painel" : "Ir para o início";
    return;
  }

  const raiz = area === "app" ? $("#area-app") : area === "inicio" ? $("#area-inicio") : $("#area-publica");
  $$("section[data-rota]", raiz).forEach((s) => { s.hidden = s.dataset.rota !== alvo; });
  $$(".abas a").forEach((a) => {
    const atual = a.dataset.aba === alvo || (a.dataset.aba === "conta" && DENTRO_DE_CONTA.has(alvo));
    if (atual) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  if (estado.rota !== null && estado.rota !== alvo) $(area === "app" ? "#conteudo-app" : area === "inicio" ? "#conteudo-inicio" : "#conteudo").focus({ preventScroll: true });
  estado.rota = alvo;
  window.scrollTo({ top: 0 });
  particulas.ligar(area === "publica" || area === "inicio");

  const telas = { painel: telaPainel, medidores: telaMedidores, seguranca: telaSeguranca, privacidade: telaPrivacidade,
    aparelhos: telaAparelhos, avisos: telaAvisos, plano: telaPlano, "pagamento-teste": () => telaPagamentoTeste(token),
    economia: () => telaEconomia(token), conta: telaConta,
    inicio: telaInicio, fila: () => telaFila(token), "sair-da-fila": () => { estado.tokenFila = token; },
    confirmar: () => confirmarEmail(token), redefinir: () => { estado.tokenRedefinir = token; }, "2fa": tela2fa };
  if (DOCUMENTOS.has(alvo)) return mostrarDocumento(alvo, raiz);
  if (telas[alvo]) {
    try {
      await telas[alvo]();
    } catch (e) {
      if (e.codigo !== "sem_sessao" && e.codigo !== "2fa_obrigatorio") avisar(e.message, true);
    }
  }
}

/* ------------------------------------------------------------------ documentos */

function mostrarDocumento(nome, raiz) {
  const destino = $(`section[data-rota="${nome}"]`, raiz);
  destino.replaceChildren($(`#doc-${nome}`).content.cloneNode(true));
  const info = estado.info || {};
  const valores = { ...(info.retencao || {}), ...(info.contato || {}), termos_versao: info.termos_versao, privacidade_versao: info.privacidade_versao };
  $$("[data-info]", destino).forEach((s) => { if (valores[s.dataset.info] != null) s.textContent = valores[s.dataset.info]; });
  destino.append(el("p", { class: "rodape-form" }, el("a", { href: estado.eu ? "#privacidade" : "#entrar" }, "Voltar")));
}

/* ------------------------------------------------------------------ entrada e cadastro */

function forcaDaSenha(s) {
  if (!s) return 0;
  let pontos = 0;
  if (s.length >= 10) pontos++;
  if (s.length >= 14) pontos++;
  if (/[a-z]/.test(s) && /[A-Z]/.test(s)) pontos++;
  if (/\d/.test(s) && /[^A-Za-z0-9]/.test(s)) pontos++;
  if (new Set(s).size < 5) pontos = 1;
  return Math.max(1, Math.min(4, pontos));
}

async function depoisDeEntrar(resposta) {
  limparEconomia();
  push.servidor = null;
  if (resposta.etapa === "aguardando_2fa") return ir("2fa");
  await carregarEu();
  ir("painel");
  conferirNotificacoes().then(redesenharNotificacoes).catch(() => {});
}

async function carregarEu() {
  estado.eu = await api("GET", "/api/conta/eu");
  estado.csrf = estado.eu.csrf;
  $("#nome-usuario").textContent = estado.eu.nome;
  atualizarSino(estado.eu.avisos_nao_lidos);
  $("#faixa-2fa").hidden = !(estado.eu.exigir_2fa && !estado.eu.dois_fatores);
}

function ligarFormulariosPublicos() {
  $$(".ver-senha").forEach((b) => b.addEventListener("click", () => {
    const campo = b.previousElementSibling;
    const mostrar = campo.type === "password";
    campo.type = mostrar ? "text" : "password";
    b.textContent = mostrar ? "ocultar" : "ver";
    b.setAttribute("aria-label", mostrar ? "Ocultar senha" : "Mostrar senha");
  }));
  const senhaCadastro = $("#form-cadastro [name=senha]");
  senhaCadastro.addEventListener("input", () => { $("#form-cadastro .forca").dataset.nivel = forcaDaSenha(senhaCadastro.value); });

  formulario($("#form-entrar"), async (d, form) => {
    $(".reenviar", form).hidden = true;
    try {
      await depoisDeEntrar(await api("POST", "/api/conta/entrar", { email: d.email, senha: d.senha }));
      form.reset();
    } catch (e) {
      if (e.codigo === "email_nao_confirmado") $(".reenviar", form).hidden = false;
      throw e;
    }
  });
  $("#botao-reenviar").addEventListener("click", async () => {
    const email = $("#form-entrar [name=email]").value;
    try {
      const r = await api("POST", "/api/conta/reenviar", { email });
      $("#texto-enviado").textContent = r.mensagem;
      ir("enviado");
    } catch (e) { avisar(e.message, true); }
  });

  formulario($("#form-cadastro"), async (d, form) => {
    const aceites = {};
    ["termos", "privacidade", "consumo", "historico_longo", "novidades"].forEach((f) => { aceites[f] = !!d[f]; });
    if (d.senha.length < 10) throw new Error("A senha precisa ter pelo menos 10 caracteres.");
    const r = await api("POST", "/api/conta/cadastro", { nome: d.nome, email: d.email, senha: d.senha, aceites });
    form.reset();
    $("#texto-enviado").textContent = `${r.mensagem} Abra o link para ativar a conta.`;
    ir("enviado");
  });

  formulario($("#form-esqueci"), async (d, form) => {
    const r = await api("POST", "/api/conta/esqueci", { email: d.email });
    form.reset();
    $("#texto-enviado").textContent = r.mensagem;
    ir("enviado");
  });

  formulario($("#form-redefinir"), async (d, form) => {
    const r = await api("POST", "/api/conta/redefinir", { token: estado.tokenRedefinir, senha: d.senha });
    form.reset();
    estado.eu = null;
    avisar(r.mensagem);
    ir("entrar");
  });

  formulario($("#form-2fa"), async (d, form) => {
    await depoisDeEntrar(await api("POST", "/api/conta/2fa", { codigo: d.codigo }));
    form.reset();
  });
}

async function confirmarEmail(token) {
  const texto = $("#texto-confirmar");
  texto.textContent = "Um instante…";
  history.replaceState(null, "", "#confirmar");   // o token sai da barra de endereço
  try {
    const r = await api("POST", "/api/conta/confirmar", { token });
    texto.textContent = r.mensagem;
  } catch (e) {
    texto.textContent = e.message;
  }
}

async function tela2fa() {
  try {
    await api("GET", "/api/conta/2fa/etapa");
    $("#form-2fa [name=codigo]").focus();
  } catch {
    ir("entrar");
  }
}

async function sair() {
  if (window.MW_DEMONSTRACAO && !window.MW_DEMONSTRACAO_ARQUIVO) return sairDaDemonstracao();
  await desligarNotificacoesAoSair();
  try { await api("POST", "/api/conta/sair"); } catch { /* sai mesmo assim */ }
  estado.eu = null;
  estado.csrf = null;
  limparEconomia();          // o que foi ajustado no "E se eu…?" não passa para a próxima conta
  ir("entrar");
}

/* ------------------------------------------------------------------ painel */

/* O "relógio de luz": dígitos que viram quando o número muda. Serve ao painel, à página
 * inicial e ao "E se eu…?". `prefixo` aparece antes dos dígitos (ex.: "R$"). */
function criarVisor(caixa) {
  let atual = { texto: "", unidade: "", prefixo: "" };
  return {
    mostrar(texto, unidade, prefixo = "") {
      if (texto === atual.texto && unidade === atual.unidade && prefixo === atual.prefixo) return;
      const casas = $$(".digito", caixa);
      if (texto.length !== atual.texto.length || prefixo !== atual.prefixo || casas.length !== texto.length) {
        caixa.replaceChildren(
          ...(prefixo ? [el("span", { class: "relogio-prefixo" }, prefixo)] : []),
          ...[...texto].map((c) => el("span", { class: /[.,]/.test(c) ? "digito sinal" : "digito" }, el("span", {}, c))),
          el("span", { class: "relogio-unidade" }, unidade));
      } else {
        [...texto].forEach((c, i) => {
          if (c === atual.texto[i]) return;
          const velho = casas[i].lastElementChild;
          const novo = el("span", { class: semMovimento ? "" : "entra" }, c);
          if (semMovimento) { velho.replaceWith(novo); return; }
          $$(".sai", casas[i]).forEach((x) => x.remove());          // arrastando rápido, não acumula
          velho.className = "sai";
          casas[i].append(novo);
          setTimeout(() => velho.remove(), 450);
        });
        $(".relogio-unidade", caixa).textContent = unidade;
      }
      atual = { texto, unidade, prefixo };
      caixa.setAttribute("aria-label", `${prefixo} ${texto} ${unidade}`.trim());
    },
  };
}

const visor = criarVisor($("#visor-agora"));

async function telaPainel() {
  prepararCasa3d();
  await Promise.all([atualizarAgora(), atualizarMeta(), atualizarFantasma(), atualizarDias(),
    atualizarPerfil().catch(() => {}), atualizarFluxo().catch(() => {})]);
  await atualizarEconomiaPainel().catch(() => {});
  desenharConviteApp();
  estado.timers.push(setInterval(() => { if (!document.hidden) atualizarAgora().catch(() => {}); }, 2000));
  estado.timers.push(setInterval(() => { if (!document.hidden) atualizarFluxo().catch(() => {}); }, 5000));
  estado.timers.push(setInterval(() => {
    if (document.hidden) return;
    atualizarMeta().catch(() => {});
    atualizarPerfil().catch(() => {});
    atualizarFantasma().then(atualizarEconomiaPainel).catch(() => {});
  }, 60000));
  const f = $("#form-preferencias");
  f.elements.meta_mensal_reais.value = estado.eu.meta_mensal_reais != null ? fmt.num(estado.eu.meta_mensal_reais, 2) : "";
  f.elements.tarifa_kwh.value = estado.eu.tarifa_kwh != null ? fmt.num(estado.eu.tarifa_kwh, 4) : "";
  f.elements.tarifa_kwh.placeholder = estado.eu.tarifa_padrao != null ? `padrão: ${fmt.num(estado.eu.tarifa_padrao, 2)}` : "ex.: 0,92";
}

async function atualizarAgora() {
  const r = await api("GET", "/api/medidores");
  estado.medidores = r.medidores;
  const vazio = r.medidores.length === 0;
  $("#painel-vazio").hidden = !vazio;
  $(".relogio").hidden = vazio;
  $(".grade-painel").hidden = vazio;
  if (vazio) return;
  // O total é o das entradas: um medidor de circuito mede uma parte do que a entrada já mede.
  const daCasa = r.medidores.filter((m) => m.papel !== "circuito");
  const online = daCasa.filter((m) => m.online && m.potencia != null);
  const total = online.reduce((s, m) => s + m.potencia, 0);
  if (estado.casa3d) estado.casa3d.definir(online.length ? total : 0, estado.faixa);
  const unidade = online.length && online.every((m) => m.unidade === "VA") ? "VA" : "W";
  const texto = online.length ? String(Math.round(total)) : "---";
  visor.mostrar(texto, unidade);
  $(".relogio").dataset.estado = online.length ? "ok" : "sem-sinal";
  const custoHora = online.reduce((s, m) => (m.custo_por_hora == null ? s : (s ?? 0) + m.custo_por_hora), null);
  const simulado = online.some((m) => m.simulado);
  $("#agora-sub").textContent = online.length
    ? `${custoHora != null ? `${fmt.reais(custoHora)} por hora neste ritmo` : "Informe a tarifa para ver em reais"}${simulado ? " (dados simulados)" : ""}`
    : "Nenhum medidor enviando leituras agora.";
  const kwh = daCasa.reduce((s, m) => s + m.kwh_hoje, 0);
  const custo = daCasa.reduce((s, m) => (m.custo_hoje == null ? s : (s ?? 0) + m.custo_hoje), null);
  $("#hoje-kwh").textContent = fmt.kwh(kwh);
  $("#hoje-custo").textContent = custo != null ? `${fmt.reais(custo)} até agora` : "Informe a tarifa para ver em reais";
}

async function atualizarMeta() {
  const m = await api("GET", "/api/meta");
  const bloco = $("#bloco-meta");
  const C = 2 * Math.PI * 50;
  const arco = (fracao) => `${Math.max(0, Math.min(1, fracao)) * C} ${C}`;
  bloco.dataset.status = m.status || "";
  if (m.sem_tarifa) {
    $("#meta-projecao").textContent = "—";
    $("#meta-linha").textContent = "";
    $("#meta-mensagem").textContent = "Informe a tarifa da sua conta de luz para acompanhar o gasto em reais.";
    $(".anel-gasto", bloco).style.strokeDasharray = arco(0);
    $(".anel-projecao", bloco).style.strokeDasharray = arco(0);
    return;
  }
  $("#meta-projecao").textContent = fmt.reais(m.projecao);
  if (!m.meta) {
    $("#meta-linha").textContent = `previsão para o mês | ${fmt.reais(m.gasto_ate_agora)} até agora`;
    $("#meta-mensagem").textContent = "Defina quanto quer gastar no mês. A gente avisa quando o ritmo passar do combinado.";
    $(".anel-gasto", bloco).style.strokeDasharray = arco(0);
    $(".anel-projecao", bloco).style.strokeDasharray = arco(0);
    return;
  }
  $("#meta-linha").textContent = `previsão para o mês, de uma meta de ${fmt.reais(m.meta)}`;
  $(".anel-gasto", bloco).style.strokeDasharray = arco(m.gasto_ate_agora / m.meta);
  $(".anel-projecao", bloco).style.strokeDasharray = arco(m.projecao / m.meta);
  const dias = Math.ceil(m.dias_restantes);
  const mensagens = {
    ok: `No ritmo dos últimos dias, sobra ${fmt.reais(m.meta - m.projecao)}. Já foram ${fmt.reais(m.gasto_ate_agora)}.`,
    atencao: `Perto do limite: dá para gastar até ${fmt.reais(m.folga_diaria)} por dia nos ${dias} dias que faltam.`,
    acima: m.folga_diaria != null && m.folga_diaria > 0
      ? `Neste ritmo, passa ${fmt.reais(m.projecao - m.meta)} da meta. Para caber, o limite é ${fmt.reais(m.folga_diaria)} por dia (a média tem sido ${fmt.reais(m.media_diaria)}).`
      : `Neste ritmo, passa ${fmt.reais(m.projecao - m.meta)} da meta.`,
  };
  $("#meta-mensagem").textContent = mensagens[m.status] + (m.base_de_dias < 3 ? " A previsão melhora depois de alguns dias de medição." : "");
}

async function atualizarFantasma() {
  const f = await api("GET", "/api/fantasma");
  estado.fantasma = f;
  const caixa = $("#fantasma-conteudo");
  if (!f.disponivel) {
    caixa.replaceChildren(el("p", { class: "fantasma-texto" },
      "O que a casa gasta parada, com todo mundo dormindo. ", f.motivo || "Ainda sem dados suficientes."));
    return;
  }
  const fatia = f.fatia_do_consumo;
  const barra = el("i");
  caixa.replaceChildren(
    el("div", { class: "fantasma-numero" },
      el("p", { class: "valor-grande" }, `${fmt.num(f.watts)} ${f.estimado ? "VA" : "W"}`),
      el("p", { class: "valor-sub" }, "sempre ligados")),
    el("p", { class: "fantasma-texto" },
      f.custo_mes != null
        ? `Isso dá ${fmt.kwh(f.kwh_mes)} e ${fmt.reais(f.custo_mes)} por mês, ou ${fmt.reais(f.custo_ano)} por ano, sem ninguém usar nada.`
        : `Isso dá ${fmt.kwh(f.kwh_mes)} por mês sem ninguém usar nada.`),
    fatia != null ? el("div", {}, el("p", { class: "fantasma-texto" }, `${fmt.num(fatia * 100)}% de tudo o que a casa gastou na última semana.`),
      el("div", { class: "fantasma-fatia" }, barra)) : null,
    el("p", { class: "dica" }, `Base de ${f.noites} noite(s) medida(s)${f.estimado ? "; medidor só de corrente: valor estimado" : ""}.`),
  );
  requestAnimationFrame(() => { barra.style.width = `${Math.round((fatia || 0) * 100)}%`; });
}

async function atualizarDias() {
  const r = await api("GET", "/api/consumo/dias?dias=14");
  const max = Math.max(0.001, ...r.dias.map((d) => d.kwh));
  const caixa = $("#barras-dias");
  const hoje = r.dias[r.dias.length - 1].dia;
  caixa.replaceChildren(...r.dias.map((d, i) => {
    const barra = el("i");
    barra.style.height = `${Math.max(1, (d.kwh / max) * 100)}%`;
    barra.style.animationDelay = `${i * 30}ms`;
    const [, mes, dia] = d.dia.split("-");
    return el("div", { class: `barra${d.dia === hoje ? " hoje" : ""}`, "data-valor": `${fmt.kwh(d.kwh)}${d.custo != null ? ` | ${fmt.reais(d.custo)}` : ""}`, title: `${dia}/${mes}` },
      barra, el("small", {}, dia));
  }));
  const total = r.dias.reduce((s, d) => s + d.kwh, 0);
  caixa.setAttribute("aria-label", `Consumo por dia nos últimos 14 dias: total de ${fmt.kwh(total)}`);
  // Resumo ao lado do "Hoje": ontem e a média dos últimos 7 dias completos.
  const completos = r.dias.slice(0, -1).filter((d) => d.kwh > 0);
  const ontem = r.dias[r.dias.length - 2];
  const semana = completos.slice(-7);
  const media = semana.length ? semana.reduce((s, d) => s + d.kwh, 0) / semana.length : null;
  $("#hoje-comparacao").replaceChildren(
    el("div", {}, el("dt", {}, "Ontem"), el("dd", {}, ontem && ontem.kwh > 0 ? fmt.kwh(ontem.kwh) : "—")),
    el("div", {}, el("dt", {}, "Média por dia"), el("dd", {}, media != null ? fmt.kwh(media) : "—")),
    el("div", {}, el("dt", {}, "Média em reais"), el("dd", {}, media != null && r.tarifa_kwh != null ? fmt.reais(media * r.tarifa_kwh) : "—")));
}

/* A casa em 3D em volta do número de agora. Fica guardado, neste aparelho, se a pessoa preferiu esconder. */
function prepararCasa3d() {
  if (!window.MWGraficos) return;
  if (!estado.casa3d) {
    estado.casa3d = new MWGraficos.CasaEm3D($("#casa-3d"), $(".relogio"), $("#visor-agora"));
    const ligada = lembrar("mw-casa-3d") !== "0";               // sem armazenamento (aba anônima): fica ligada
    const aplicar = (sim) => {
      $("#botao-3d").setAttribute("aria-pressed", String(sim));
      $(".relogio").classList.toggle("com-3d", sim);
      estado.casa3d.ligar(sim);
    };
    aplicar(ligada);
    $("#botao-3d").addEventListener("click", () => {
      const sim = $("#botao-3d").getAttribute("aria-pressed") !== "true";
      aplicar(sim);
      lembrar("mw-casa-3d", sim ? "1" : "0");
    });
  }
  requestAnimationFrame(() => { estado.casa3d.medir(); estado.casa3d.agendar(); });
}

const NOTAS_DO_FLUXO = {
  circuitos: "Cada circuito tem o seu medidor. O resto da casa é a entrada menos os circuitos.",
  imoveis: "Um medidor de entrada por imóvel. O número grande lá em cima soma todos.",
};

async function atualizarFluxo() {
  const r = await api("GET", "/api/agora/partes");
  const caixa = $("#fluxo"), nota = $("#fluxo-nota"), aviso = $("#fluxo-aviso");
  aviso.hidden = !r.aviso;
  aviso.textContent = r.aviso || "";
  if (!r.disponivel) {
    caixa.replaceChildren(el("p", { class: "fluxo-vazio" }, r.motivo));
    $("#fluxo-total").textContent = "";
    nota.textContent = "";
    return;
  }
  $("#fluxo-total").textContent = `${fmt.num(r.total)} ${r.unidade}${r.custo_por_hora != null ? `, ${fmt.reais(r.custo_por_hora)} por hora` : ""}`;
  window.MWGraficos.desenharFluxo(caixa, r);
  const aparelhos = r.partes.some((p) => p.tipo === "aparelho");
  const base = r.partes.some((p) => p.tipo === "base");
  const paraODetetive = el("a", { href: "#aparelhos" }, "Aparelhos");
  if (NOTAS_DO_FLUXO[r.fonte]) nota.replaceChildren(NOTAS_DO_FLUXO[r.fonte]);
  else if (r.fonte === "detetive") {
    nota.replaceChildren(aparelhos
      ? "Os aparelhos são palpites do Detetive: degraus de potência que ainda não desligaram. Sempre ligado é o mínimo das madrugadas."
      : "O Detetive não reconheceu nenhum aparelho ligado agora. Sempre ligado é o mínimo das madrugadas.");
  } else {
    nota.replaceChildren(base ? "Sempre ligado é o mínimo das madrugadas. " : "Com duas madrugadas medidas, aparece o que fica sempre ligado. ",
      "Para ver aparelho por aparelho, ligue o Detetive em ", paraODetetive, ".");
  }
}

// "das 19h às 20h", com a concordância da meia-noite e da 1h.
function deHoraEmHora(h) {
  const fim = (h + 1) % 24;
  const de = h === 0 ? "da meia-noite" : h === 1 ? "da 1h" : `das ${h}h`;
  const ate = fim === 0 ? "à meia-noite" : fim === 1 ? "à 1h" : `às ${fim}h`;
  return `${de} ${ate}`;
}

async function atualizarPerfil() {
  const r = await api("GET", "/api/consumo/horas?dias=14");
  estado.faixa = r.faixa;
  if (!window.MWGraficos) return;
  estado.perfil = estado.perfil || window.MWGraficos.criarPerfil($("#bloco-perfil"));
  estado.perfil.atualizar(r);
  const partes = [];
  const tipo = r.normal && r.normal.tipo === "fim_de_semana" ? "um fim de semana" : "um dia útil";
  if (r.comparacao) {
    // A comparação usa só as horas inteiras antes desta: a hora que está correndo fica de fora.
    const { hoje_kwh: hoje, normal_kwh: normal, horas } = r.comparacao;
    const dif = normal > 0 ? (hoje - normal) / normal : 0;
    const pct = Math.round(Math.abs(dif) * 100);
    const periodo = horas === r.hora_atual ? `Da meia-noite até as ${r.hora_atual}h` : `Nas ${horas} horas medidas até as ${r.hora_atual}h`;
    partes.push(pct < 5 ? `${periodo}, hoje está perto do normal de ${tipo}: ${fmt.kwh(hoje)}.`
      : `${periodo}, hoje gastou ${fmt.kwh(hoje)}: ${pct}% ${dif < 0 ? "abaixo" : "acima"} do normal de ${tipo} no mesmo horário.`);
  }
  if (r.pico) {
    partes.push(`O horário mais pesado costuma ser ${deHoraEmHora(r.pico.hora)}: ${fmt.kwh(r.pico.kwh)}`
      + (r.pico.custo_mes != null ? `, cerca de ${fmt.reais(r.pico.custo_mes)} por mês só nessa hora.` : "."));
  }
  if (!r.normal) partes.push("Com alguns dias de medição, aparece aqui o normal da casa para comparar com hoje.");
  $("#perfil-resumo").textContent = partes.join(" ");
}

function ligarPreferencias() {
  formulario($("#form-preferencias"), async (d) => {
    const meta = numeroDigitado(d.meta_mensal_reais);
    const tarifa = numeroDigitado(d.tarifa_kwh);
    if (Number.isNaN(meta) || Number.isNaN(tarifa)) throw new Error("Use só números, como 180 ou 0,92.");
    await api("PUT", "/api/conta/preferencias", { meta_mensal_reais: meta, tarifa_kwh: tarifa });
    estado.eu.meta_mensal_reais = meta;
    estado.eu.tarifa_kwh = tarifa;
    avisar("Meta e tarifa salvas.");
    await Promise.all([atualizarAgora(), atualizarMeta(), atualizarFantasma(), atualizarDias(),
      atualizarPerfil().catch(() => {}), atualizarFluxo().catch(() => {})]);
  });
}

/* ------------------------------------------------------------------ medidores */

async function telaMedidores() {
  await desenharMedidores();
  let voltas = 0;
  estado.timers.push(setInterval(() => {
    if (document.hidden || $(".renomear:not([hidden])")) return;
    voltas++;
    desenharMedidores(true).then(() => {
      if (voltas % 10) return;
      // A cada 30 s, o gráfico das últimas 2 horas anda junto com os números.
      $$("#lista-medidores .medidor").forEach((cartao) => {
        const id = cartao.dataset.id;
        if ((estado.periodo[id] || "2h") === "2h") desenharGrafico($("canvas", cartao), id, "2h");
      });
    }).catch(() => {});
  }, 3000));
}

async function desenharMedidores(soNumeros = false) {
  const r = await api("GET", "/api/medidores");
  estado.medidores = r.medidores;
  const lista = $("#lista-medidores");
  $("#medidores-dica").hidden = !r.medidores.some((m) => m.simulado);
  if (!r.medidores.length) {
    lista.replaceChildren(el("div", { class: "vazio" }, el("h2", {}, "Nenhum medidor por aqui"),
      el("p", {}, "Toque em Adicionar medidor para receber o código de pareamento. Leva menos de um minuto."),
    ));
    return;
  }
  if (soNumeros && lista.children.length === r.medidores.length && r.medidores.every((m, i) => lista.children[i].dataset.id === m.id)) {
    r.medidores.forEach((m, i) => preencherMedidas(lista.children[i], m));
    return;
  }
  lista.replaceChildren(...r.medidores.map(cartaoMedidor));
}

function preencherMedidas(cartao, m) {
  cartao.dataset.online = m.online;
  cartao.dataset.sensor = m.sensor_ok === false ? "falha" : "ok";
  const estadoTexto = !m.online ? (m.segundos_desde_mensagem != null
      ? `Sem sinal. Última mensagem ${fmt.relativo(new Date(Date.now() - m.segundos_desde_mensagem * 1000))}.`
      : "Ainda não mandou nenhuma mensagem.")
    : m.sensor_ok === false ? `Medidor conectado, mas o sensor não respondeu (${m.erro_sensor})` : "Medindo agora";
  $(".medidor-estado", cartao).textContent = estadoTexto;
  const valores = {
    potencia: m.potencia != null ? `${fmt.num(m.potencia)} ${m.unidade}` : "—",
    tensao: m.tensao_v != null ? `${fmt.num(m.tensao_v, 1)} V` : "—",
    corrente: m.corrente_a != null ? `${fmt.num(m.corrente_a, 2)} A` : "—",
    fp: m.fp != null ? fmt.num(m.fp, 2) : "—",
    frequencia: m.frequencia_hz != null ? `${fmt.num(m.frequencia_hz, 1)} Hz` : "—",
    hoje: fmt.kwh(m.kwh_hoje),
    custo: m.custo_hoje != null ? fmt.reais(m.custo_hoje) : "—",
    sinal: m.rssi != null ? `${m.rssi} dBm` : "—",
  };
  Object.entries(valores).forEach(([k, v]) => { const dd = $(`[data-medida="${k}"]`, cartao); if (dd) dd.textContent = v; });
}

function cartaoMedidor(m) {
  const medida = (chave, rotulo, destaque) => el("div", { class: destaque ? "destaque" : null }, el("dt", {}, rotulo), el("dd", { "data-medida": chave }, "—"));
  const canvas = el("canvas", { "aria-label": `Gráfico de consumo de ${m.apelido}`, role: "img" });
  const periodo = estado.periodo[m.id] || "2h";
  const botoesPeriodo = ["2h", "24h", "7d"].map((p) => el("button", {
    type: "button", "aria-pressed": String(p === periodo),
    onclick: (ev) => {
      estado.periodo[m.id] = p;
      $$("button", ev.target.parentElement).forEach((b) => b.setAttribute("aria-pressed", String(b === ev.target)));
      desenharGrafico(canvas, m.id, p);
    },
  }, { "2h": "2 horas", "24h": "24 horas", "7d": "7 dias" }[p]));
  const campoNome = el("input", { value: m.apelido, maxlength: "40", "aria-label": "Nome do medidor" });
  const formNome = el("form", { class: "renomear", hidden: true, onsubmit: async (ev) => {
    ev.preventDefault();
    try {
      await api("PATCH", `/api/medidores/${encodeURIComponent(m.id)}`, { apelido: campoNome.value });
      avisar("Nome salvo.");
      await desenharMedidores();
    } catch (e) { avisar(e.message, true); }
  } }, campoNome, el("button", { class: "secundario", type: "submit" }, "Salvar nome"),
  el("button", { class: "discreto", type: "button", onclick: () => { formNome.hidden = true; } }, "Cancelar"));

  const entrada = m.papel === "circuito" ? estado.medidores.find((x) => x.id === m.dentro_de) : null;
  const cartao = el("article", { class: "medidor", "data-id": m.id },
    el("div", { class: "medidor-topo" },
      el("h2", {}, el("span", { class: "ponto", "aria-hidden": "true" }), m.apelido),
      m.papel === "circuito" ? el("span", { class: "etiqueta circuito" }, entrada ? `circuito de ${entrada.apelido}` : "circuito") : null,
      m.simulado ? el("span", { class: "etiqueta simulado" }, "simulado") : null,
      m.energia_estimada ? el("span", { class: "etiqueta estimado", title: "Medidor só de corrente: potência aparente e consumo estimado" }, "estimado") : null,
      el("span", { class: "medidor-id" }, m.id)),
    el("p", { class: "dica medidor-estado" }),
    el("dl", { class: "medidas" },
      medida("potencia", "Potência", true), medida("tensao", "Tensão"), medida("corrente", "Corrente"), medida("fp", "Fator de potência"),
      medida("frequencia", "Frequência"), medida("hoje", "Hoje"), medida("custo", "Custo hoje"), medida("sinal", "Sinal do Wi-Fi")),
    el("div", { class: "grafico" }, el("div", { class: "periodos", role: "group", "aria-label": "Período do gráfico" }, botoesPeriodo), canvas),
    estado.medidores.length > 1 ? papelDoMedidor(m) : null,
    formNome,
    el("div", { class: "medidor-acoes" },
      el("button", { type: "button", class: "discreto", onclick: () => { formNome.hidden = false; campoNome.focus(); } }, "Renomear"),
      el("button", { type: "button", class: "discreto perigo", onclick: () => removerMedidor(m) }, "Remover")),
  );
  preencherMedidas(cartao, m);
  requestAnimationFrame(() => desenharGrafico(canvas, m.id, periodo));
  return cartao;
}

/* Entrada ou circuito: com mais de um medidor, cada um diz o que mede, para o total não somar a mesma
 * energia duas vezes (o medidor do ar-condicionado já está dentro do medidor da casa). */
function papelDoMedidor(m) {
  const entradas = estado.medidores.filter((x) => x.papel === "casa" && x.id !== m.id);
  const nome = `papel-${m.id}`;
  const selecao = entradas.length > 1
    ? el("select", { "aria-label": "Dentro de qual medidor de entrada", onchange: () => salvar("circuito") },
      entradas.map((e) => el("option", { value: e.id, selected: e.id === m.dentro_de }, e.apelido)))
    : null;
  async function salvar(papel) {
    const dados = { papel };
    if (papel === "circuito" && selecao) dados.dentro_de = selecao.value;
    try {
      await api("PATCH", `/api/medidores/${encodeURIComponent(m.id)}`, dados);
      avisar(papel === "casa" ? `${m.apelido} agora soma no total da conta.` : `${m.apelido} agora é um circuito: não soma de novo no total.`);
    } catch (e) { avisar(e.message, true); }
    await desenharMedidores();
  }
  const opcao = (valor, titulo, detalhe, ...extra) => el("label", { class: "papel-opcao" },
    el("input", { type: "radio", name: nome, value: valor, checked: m.papel === valor, onchange: () => salvar(valor) }),
    el("span", { class: "papel-texto" }, el("strong", {}, titulo), el("small", {}, detalhe), ...extra));
  return el("fieldset", { class: "papel" }, el("legend", {}, "O que este medidor mede"),
    opcao("casa", "A entrada de um imóvel", "Soma no total da conta."),
    entradas.length
      ? opcao("circuito", entradas.length === 1 ? `Um circuito dentro de ${entradas[0].apelido}` : "Um circuito dentro de",
        "Parte do que a entrada já mede: não soma de novo.", selecao)
      : null);
}

async function desenharGrafico(canvas, id, periodo) {
  let h;
  try { h = await api("GET", `/api/medidores/${encodeURIComponent(id)}/historico?periodo=${periodo}`); } catch { return; }
  const dpr = window.devicePixelRatio || 1;
  const larg = canvas.clientWidth, alt = canvas.clientHeight;
  if (!larg) return;
  canvas.width = larg * dpr;
  canvas.height = alt * dpr;
  const c = canvas.getContext("2d");
  c.scale(dpr, dpr);
  c.clearRect(0, 0, larg, alt);
  const margem = { e: 46, d: 8, t: 10, b: 22 };
  const w = larg - margem.e - margem.d, ht = alt - margem.t - margem.b;
  const fim = h.agora, inicio = fim - { "2h": 7200, "24h": 86400, "7d": 604800 }[periodo];
  const max = Math.max(10, ...h.pontos.map((p) => p[1])) * 1.1;
  const x = (t) => margem.e + ((t - inicio) / (fim - inicio)) * w;
  const y = (v) => margem.t + ht - (v / max) * ht;
  c.font = "11px 'JetBrains Mono', monospace";
  c.fillStyle = "#8a85b0";
  c.strokeStyle = "rgba(255,255,255,.07)";
  for (let i = 0; i <= 3; i++) {
    const v = (max / 3) * i;
    c.beginPath(); c.moveTo(margem.e, y(v)); c.lineTo(larg - margem.d, y(v)); c.stroke();
    c.fillText(i === 3 ? `${Math.round(v)} ${h.unidade}` : `${Math.round(v)}`, 4, y(v) + (i === 3 ? 12 : 4));
  }
  for (let i = 0; i <= 4; i++) {
    const t = inicio + ((fim - inicio) / 4) * i;
    const d = new Date(t * 1000);
    const rot = periodo === "7d" ? d.toLocaleDateString("pt-BR", { weekday: "short" }) : d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
    c.fillText(rot, Math.min(x(t) - 14, larg - 44), alt - 4);
  }
  if (!h.pontos.length) {
    c.fillStyle = "#b7b2d8";
    c.font = "14px system-ui";
    c.fillText("Sem leituras neste período", margem.e + 12, margem.t + ht / 2);
    return;
  }
  const buraco = { "2h": 30, "24h": 700, "7d": 4000 }[periodo];
  const grad = c.createLinearGradient(0, margem.t, 0, margem.t + ht);
  grad.addColorStop(0, "rgba(54,227,255,.35)");
  grad.addColorStop(1, "rgba(54,227,255,0)");
  let trechos = [[]];
  h.pontos.forEach((p, i) => {
    if (i && p[0] - h.pontos[i - 1][0] > buraco) trechos.push([]);
    trechos[trechos.length - 1].push(p);
  });
  trechos = trechos.filter((t) => t.length);
  for (const t of trechos) {
    c.beginPath();
    c.moveTo(x(t[0][0]), y(0));
    t.forEach((p) => c.lineTo(x(p[0]), y(p[1])));
    c.lineTo(x(t[t.length - 1][0]), y(0));
    c.fillStyle = grad;
    c.fill();
    c.beginPath();
    t.forEach((p, i) => (i ? c.lineTo(x(p[0]), y(p[1])) : c.moveTo(x(p[0]), y(p[1]))));
    c.strokeStyle = "#36e3ff";
    c.lineWidth = 1.6;
    c.stroke();
  }
}

async function removerMedidor(m) {
  const ok = await confirmar({ titulo: `Remover ${m.apelido}?`,
    texto: "O medidor sai da sua conta e todas as leituras dele são apagadas. Para usar de novo, é só parear outra vez.",
    botao: "Remover medidor", semSenha: true });
  if (!ok) return;
  try {
    await api("DELETE", `/api/medidores/${encodeURIComponent(m.id)}`);
    avisar("Medidor removido.");
    await desenharMedidores();
  } catch (e) { avisar(e.message, true); }
}

async function abrirPareamento() {
  const d = $("#dialogo-parear");
  let r;
  try {
    r = await api("POST", "/api/medidores/codigo");
  } catch (e) {
    avisar(e.message, true);
    if (e.codigo === "limite_plano") ir("plano");
    return;
  }
  const antes = new Set(estado.medidores.map((m) => m.id));
  const fimCodigo = Date.now() + r.validade_s * 1000;
  $("#parear-codigo").textContent = r.codigo;
  const status = $("#parear-status");
  status.className = "parear-status";
  status.textContent = "Esperando o medidor…";
  d.showModal();
  const contar = () => {
    const s = Math.max(0, Math.round((fimCodigo - Date.now()) / 1000));
    $("#parear-tempo").textContent = s ? `Vale por mais ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` : "Este código expirou. Feche e gere outro.";
    if (!s) clearInterval(relogio);
  };
  const relogio = setInterval(contar, 1000);
  contar();                    // sem esperar 1 s: o texto de um código anterior não aparece
  const consulta = setInterval(async () => {
    try {
      const lista = await api("GET", "/api/medidores");
      const novo = lista.medidores.find((m) => !antes.has(m.id));
      if (novo) {
        clearInterval(consulta);
        status.className = "parear-status ok";
        status.textContent = `${novo.apelido} pareado!`;
        const entradas = lista.medidores.filter((m) => m.papel === "casa" && m.id !== novo.id);
        if (!entradas.length) { setTimeout(() => { if (d.open) d.close(); }, 1600); return; }
        perguntarPapel(d, novo, entradas);
      }
    } catch { /* tenta de novo */ }
  }, 3000);
  d.addEventListener("close", () => {
    clearInterval(relogio); clearInterval(consulta);
    $("#parear-papel").hidden = true;
    if (estado.rota === "medidores") desenharMedidores();
  }, { once: true });
}

/* Logo depois de parear o segundo medidor: ele mede outro imóvel ou um circuito de dentro da casa? */
function perguntarPapel(dialogo, novo, entradas) {
  const caixa = $("#parear-papel");
  const escolher = async (dados) => {
    try {
      await api("PATCH", `/api/medidores/${encodeURIComponent(novo.id)}`, dados);
    } catch (e) { avisar(e.message, true); }
    if (dialogo.open) dialogo.close();
  };
  caixa.replaceChildren(
    el("p", { class: "parear-pergunta" }, `O que o ${novo.apelido} mede?`),
    el("div", { class: "botoes" },
      ...entradas.map((e) => el("button", { type: "button", class: "secundario", onclick: () => escolher({ papel: "circuito", dentro_de: e.id }) },
        `Um circuito dentro de ${e.apelido}`)),
      el("button", { type: "button", class: "secundario", onclick: () => escolher({ papel: "casa" }) }, "A entrada de outro imóvel")),
    el("p", { class: "dica" }, "Um circuito (o ar-condicionado, a cozinha) já passa pelo medidor da entrada, então não soma de novo no total. Dá para mudar depois, em Medidores."));
  caixa.hidden = false;
  caixa.querySelector("button").focus();
}

/* ------------------------------------------------------------------ segurança */

async function telaSeguranca() {
  await carregarEu();
  desenharDoisFatores();
  await desenharSessoes();
}

function desenharDoisFatores(codigosNovos) {
  const caixa = $("#dois-fatores");
  const eu = estado.eu;
  if (codigosNovos) {
    caixa.replaceChildren(
      el("p", { class: "estado-2fa ligado" }, "Ativada"),
      el("p", {}, "Guarde estes códigos de backup num lugar seguro, fora do celular. Cada um serve uma vez, se você perder o aparelho. Eles não vão aparecer de novo."),
      el("ul", { class: "codigos-backup" }, codigosNovos.map((c) => el("li", {}, c))),
      el("div", { class: "botoes" },
        el("button", { class: "secundario", type: "button", onclick: () => baixar("meterwatt-codigos-de-backup.txt",
          `Códigos de backup do MeterWatt (${eu.email})\nCada código vale uma vez.\n\n${codigosNovos.join("\n")}\n`, "text/plain") }, "Baixar como texto"),
        el("button", { class: "primario", type: "button", onclick: () => desenharDoisFatores() }, "Já guardei")),
    );
    return;
  }
  if (eu.dois_fatores) {
    caixa.replaceChildren(
      el("p", { class: "estado-2fa ligado" }, "Ativada"),
      el("p", { class: "texto" }, `Além da senha, a entrada pede o código do aplicativo autenticador. Restam ${eu.codigos_backup_restantes} código(s) de backup.`),
      el("div", { class: "botoes" },
        el("button", { class: "secundario", type: "button", onclick: async () => {
          let novos = null;
          await confirmar({ titulo: "Gerar códigos de backup novos", texto: "Os códigos antigos param de valer.", botao: "Gerar códigos", pedirCodigo: true,
            enviar: async (d) => { novos = (await api("POST", "/api/conta/2fa/codigos", d)).codigos_backup; } });
          if (novos) desenharDoisFatores(novos);
        } }, "Gerar códigos de backup novos"),
        eu.exigir_2fa ? null : el("button", { class: "secundario perigo", type: "button", onclick: async () => {
          const dados = await confirmar({ titulo: "Desativar a verificação em duas etapas?", texto: "A conta fica protegida só pela senha.", botao: "Desativar", pedirCodigo: true,
            enviar: (d) => api("POST", "/api/conta/2fa/desativar", d) });
          if (!dados) return;
          avisar("Verificação em duas etapas desativada.");
          await carregarEu();
          desenharDoisFatores();
        } }, "Desativar")),
    );
    return;
  }
  caixa.replaceChildren(
    el("p", { class: "estado-2fa" }, "Desativada"),
    el("p", { class: "texto" }, "Com ela, quem descobrir a sua senha ainda precisa do seu celular para entrar. Funciona com Google Authenticator, Microsoft Authenticator, Authy, 2FAS e similares."),
    el("div", {}, el("button", { class: "primario", type: "button", onclick: iniciarDoisFatores }, "Ativar verificação em duas etapas")),
  );
}

async function iniciarDoisFatores() {
  let r;
  try { r = await api("POST", "/api/conta/2fa/iniciar"); } catch (e) { avisar(e.message, true); return; }
  const campo = el("input", { name: "codigo", inputmode: "numeric", autocomplete: "one-time-code", maxlength: "7", class: "codigo-grande", required: true, "aria-label": "Código de 6 números" });
  const form = el("form", { class: "form-estreito", novalidate: true },
    el("label", {}, "Digite o código que aparece no aplicativo", campo),
    el("p", { class: "erro", role: "alert" }),
    el("div", { class: "botoes" }, el("button", { class: "primario", type: "submit" }, "Confirmar e ativar"),
      el("button", { class: "discreto", type: "button", onclick: () => desenharDoisFatores() }, "Cancelar")));
  formulario(form, async (d) => {
    const resp = await api("POST", "/api/conta/2fa/ativar", { codigo: d.codigo });
    await carregarEu();
    desenharDoisFatores(resp.codigos_backup);
    avisar("Verificação em duas etapas ativada.");
  });
  $("#dois-fatores").replaceChildren(
    el("div", { class: "ativacao" },
      el("img", { class: "qr", src: r.qr, alt: "QR code para o aplicativo autenticador" }),
      el("div", { class: "form-estreito" },
        el("p", {}, "1. No aplicativo autenticador, toque em adicionar e leia o QR code."),
        el("p", { class: "dica" }, "Não dá para ler? Digite esta chave:"),
        el("p", { class: "segredo" }, r.segredo),
        el("p", {}, "2. Confirme com o código de 6 números."),
        form)),
  );
  campo.focus();
}

async function desenharSessoes() {
  const r = await api("GET", "/api/conta/sessoes");
  $("#encerrar-outras").hidden = r.sessoes.length < 2;
  $("#lista-sessoes").replaceChildren(...r.sessoes.map((s) => el("li", {},
    el("div", { class: "info" }, el("strong", {}, s.navegador || "Navegador desconhecido"),
      el("small", {}, `Rede ${s.rede || "desconhecida"} | aberta em ${fmt.quando(s.criada_em)} | último uso ${fmt.relativo(s.ultimo_uso)}`)),
    s.atual ? el("span", { class: "atual" }, "Este aparelho")
      : el("button", { class: "discreto perigo", type: "button", onclick: async () => {
        try { await api("DELETE", `/api/conta/sessoes/${s.id}`); avisar("Sessão encerrada."); await desenharSessoes(); } catch (e) { avisar(e.message, true); }
      } }, "Encerrar"))));
}

function ligarSeguranca() {
  formulario($("#form-senha"), async (d, form) => {
    const r = await api("POST", "/api/conta/senha", d);
    form.reset();
    avisar(r.mensagem);
    await desenharSessoes();
  });
  $("#encerrar-outras").addEventListener("click", async () => {
    try {
      const r = await api("POST", "/api/conta/sessoes/encerrar-outras");
      avisar(`${r.encerradas} sessão(ões) encerrada(s).`);
      await desenharSessoes();
    } catch (e) { avisar(e.message, true); }
  });
}

/* ------------------------------------------------------------------ privacidade */

const NOMES_RETENCAO = {
  leituras_detalhadas_horas: ["Leituras de segundo a segundo", "horas"],
  consumo_5_minutos_dias: ["Consumo de 5 em 5 minutos", "dias"],
  consumo_5_minutos_dias_com_historico_longo: ["… com histórico longo autorizado", "dias"],
  atividade_da_conta_dias: ["Atividade da conta", "dias"],
  conta_nao_confirmada_dias: ["Conta que nunca confirmou o e-mail", "dias"],
  backup_copias: ["O que você apaga sai das cópias de segurança em até", "dias"],
};
const EVENTOS_DE_ALERTA = new Set(["login_falhou", "login_2fa_falhou", "cadastro_repetido", "pareamento_recusado", "login_codigo_backup"]);

async function telaPrivacidade() {
  const [p, a] = await Promise.all([api("GET", "/api/privacidade"), api("GET", "/api/privacidade/atividade")]);
  desenharAutorizacoes(p);
  $("#retencao").replaceChildren(...Object.entries(NOMES_RETENCAO).filter(([k]) => p.retencao[k] != null)
    .flatMap(([k, [nome, unidade]]) => [el("dt", {}, nome), el("dd", {}, `${p.retencao[k]} ${unidade}`)]));
  $("#atividade").replaceChildren(...a.atividade.map((ev) => el("li", {},
    el("time", { datetime: ev.quando }, fmt.quando(ev.quando)),
    el("span", { class: EVENTOS_DE_ALERTA.has(ev.evento) ? "alerta" : null }, ev.descricao, ev.detalhe ? ` (${ev.detalhe})` : "", ev.rede ? ` | rede ${ev.rede}` : ""))));
  $("#contato-encarregado").textContent = p.email_privacidade ? ` | Encarregado de dados: ${p.email_privacidade}` : "";
}

function desenharAutorizacoes(p) {
  $("#lista-autorizacoes").replaceChildren(...p.finalidades.map((f) => {
    const chave = el("button", {
      type: "button", class: "chave", role: "switch", "aria-checked": String(f.aceito), "aria-label": f.nome, disabled: !f.revogavel,
      onclick: async () => {
        const novo = !f.aceito;
        if (f.id === "consumo" && !novo) {
          const ok = await confirmar({ titulo: "Parar de guardar o consumo?", semSenha: true, botao: "Parar de guardar",
            texto: "Os medidores param de registrar leituras novas e o painel fica parado. O que já foi guardado continua até você apagar ou até passar o prazo. Dá para voltar a autorizar quando quiser." });
          if (!ok) return;
        }
        if (f.id === "aparelhos" && !novo) {
          const ok = await confirmar({ titulo: "Desligar o Detetive?", semSenha: true, botao: "Desligar e apagar",
            texto: "Tudo o que ele guardou é apagado agora: os aparelhos, os nomes que você deu e os horários em que cada um ligou. Ações do plano de economia ligadas a um aparelho continuam, mas sem a conferência por aparelho." });
          if (!ok) return;
        }
        try {
          desenharAutorizacoes(await api("PUT", `/api/privacidade/${f.id}`, { aceito: novo }));
          if (f.id === "consumo") estado.eu.consentimento_consumo = novo;
          limparEconomia();                       // os números do "E se eu…?" mudam com as autorizações
          avisar(novo ? "Autorização registrada." : "Autorização revogada.");
        } catch (e) { avisar(e.message, true); }
      },
    });
    return el("li", {},
      el("div", {}, el("h3", {}, f.nome), el("p", {}, f.descricao),
        el("small", {}, f.revogavel ? (f.desde ? `${f.aceito ? "Autorizado" : "Não autorizado"} em ${fmt.quando(f.desde)}` : "")
          : "Necessário para usar o serviço. Para sair, exclua a conta.")),
      chave);
  }));
}

function ligarPrivacidade() {
  $("#botao-exportar").addEventListener("click", async (ev) => {
    ev.target.disabled = true;
    try {
      const r = await api("POST", "/api/privacidade/exportar", {}, { cru: true });
      const nome = (r.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/);
      baixar(nome ? nome[1] : "meterwatt-meus-dados.json", await r.text(), "application/json");
      avisar("Cópia dos dados baixada.");
    } catch (e) { avisar(e.message, true); } finally { ev.target.disabled = false; }
  });
  $("#botao-apagar-consumo").addEventListener("click", async () => {
    const dados = await confirmar({ titulo: "Apagar todo o consumo?", botao: "Apagar consumo", pedirCodigo: estado.eu.dois_fatores,
      texto: "Todas as leituras e totais de todos os medidores são apagados. Não tem como desfazer.",
      enviar: (d) => api("POST", "/api/privacidade/apagar-consumo", d) });
    if (dados) { avisar("Consumo apagado."); limparEconomia(); telaPrivacidade(); }
  });
  $("#botao-excluir-conta").addEventListener("click", async () => {
    const dados = await confirmar({ titulo: "Excluir a conta?", botao: "Excluir para sempre", pedirCodigo: estado.eu.dois_fatores, frase: true,
      texto: "A conta, os medidores, o consumo, as sessões e as autorizações são apagados agora. Os medidores param de enviar para cá. Se quiser, baixe uma cópia dos dados antes.",
      enviar: (d) => api("POST", "/api/privacidade/excluir-conta", d) });
    if (dados) {
      estado.eu = null;
      estado.csrf = null;
      avisar("Conta excluída. Até mais.");
      ir("entrar");
    }
  });
}

/* ------------------------------------------------------------------ avisos */

function atualizarSino(n) {
  const conta = $("#sino-conta");
  conta.hidden = !n;
  conta.textContent = n > 9 ? "9+" : String(n || "");
  $("#sino").setAttribute("aria-label", n ? `Avisos: ${n} não lido(s)` : "Avisos");
  // No app instalado, o número também aparece no ícone da tela inicial (quando o sistema deixa).
  if (window.MW_DEMONSTRACAO) return;
  try {
    if (n && navigator.setAppBadge) navigator.setAppBadge(n).catch(() => {});
    else if (!n && navigator.clearAppBadge) navigator.clearAppBadge().catch(() => {});
  } catch { /* sem suporte */ }
}

async function telaAvisos() {
  const [avisos] = await Promise.all([api("GET", "/api/avisos"), carregarNotificacoes()]);
  desenharAvisos(avisos);
  desenharNotificacoes();
  if (estado.eu.avisos_nao_lidos) {
    await api("POST", "/api/avisos/lidos");
    estado.eu.avisos_nao_lidos = 0;
    atualizarSino(0);
  }
}

function desenharAvisos(r) {
  $("#lista-avisos").replaceChildren(...(r.avisos.length ? r.avisos.map((a) => el("li", { class: a.lido ? null : "novo" },
    el("div", {}, el("h3", {}, a.titulo), el("p", {}, a.texto)),
    el("time", { datetime: a.quando }, fmt.quando(a.quando))))
    : [el("li", { class: "vazio-linha" }, "Nenhum aviso por enquanto. Quando um medidor cair, a meta apertar ou o consumo fugir do normal, aparece aqui.")]));
  const chave = (c, campo, rotulo) => el("div", { class: "canal" }, el("span", { "aria-hidden": "true" }, rotulo),
    el("button", { type: "button", class: "chave", role: "switch", "aria-checked": String(c[campo]), "aria-label": `${c.nome}: ${rotulo.toLowerCase()}`,
      onclick: async () => {
        try { desenharAvisos(await api("PUT", `/api/avisos/config/${c.tipo}`, { [campo]: !c[campo] })); } catch (e) { avisar(e.message, true); }
      } }));
  $("#avisos-config").replaceChildren(...r.config.map((c) => el("li", {},
    el("div", {}, el("h3", {}, c.nome), el("p", {}, c.descricao)),
    el("div", { class: "canais" }, chave(c, "por_push", "Notificação"), chave(c, "por_email", "E-mail")))));
}

/* O bloco "Notificações" da tela Avisos: como está neste aparelho e a lista dos aparelhos ligados. */
function desenharNotificacoes() {
  const caixa = $("#notificacoes-aqui");
  const s = push.servidor;
  const linhas = [];
  const botao = (texto, classe, acao) => el("button", { type: "button", class: classe, onclick: (ev) => acao(ev.currentTarget) }, texto);
  if (s && s.demonstracao) {
    linhas.push(el("p", {}, "Na demonstração, as notificações ficam desligadas: nada sai do seu navegador. No app de verdade, é aqui que você liga os avisos no celular."));
  } else if (s && !s.disponivel) {
    linhas.push(el("p", {}, "Este servidor ainda não foi configurado para mandar notificações. Os avisos continuam aparecendo aqui e por e-mail."));
  } else if (!push.suportado && app.ios && !app.instalado) {
    linhas.push(el("p", {}, "No iPhone e no iPad, as notificações chegam com o MeterWatt instalado na tela de início. Instale e ligue por lá."),
      el("div", { class: "botoes" }, botao("Como instalar", "secundario", () => $("#dialogo-instalar").showModal())));
  } else if (!push.suportado) {
    linhas.push(el("p", {}, "Este navegador não recebe notificações. No celular, use o Chrome no Android ou instale o app no iPhone."));
  } else if (push.inscricao) {
    linhas.push(el("p", { class: "estado-aparelho", "data-ligado": "" }, "Ligadas neste aparelho"),
      el("p", {}, "Os avisos chegam aqui mesmo com o app fechado. Escolha abaixo quais avisos viram notificação."),
      el("div", { class: "botoes" }, botao("Mandar um teste", "secundario", testarNotificacao), botao("Desligar neste aparelho", "discreto", desligarNotificacoes)));
  } else if (typeof Notification !== "undefined" && Notification.permission === "denied") {
    linhas.push(el("p", { class: "estado-aparelho" }, "Bloqueadas neste aparelho"),
      el("p", {}, app.instalado
        ? "As notificações do MeterWatt estão bloqueadas nas configurações do aparelho. Libere em Ajustes (ou Configurações), Notificações, MeterWatt, e volte aqui."
        : "O navegador bloqueou as notificações deste site. Libere nas permissões do site (o ícone ao lado do endereço) e volte aqui."));
  } else {
    linhas.push(el("p", { class: "estado-aparelho" }, "Desligadas neste aparelho"),
      el("p", {}, "Ligue para saber na hora quando um medidor ficar sem sinal, a meta apertar ou o consumo fugir do normal, mesmo com o app fechado."),
      el("div", { class: "botoes" }, botao("Ligar notificações", "primario", ligarNotificacoes)));
  }
  caixa.replaceChildren(...linhas);
  const aparelhos = (s && s.aparelhos) || [];
  const lista = $("#notificacoes-aparelhos");
  lista.hidden = !aparelhos.length;
  lista.replaceChildren(...aparelhos.map((a) => el("li", {},
    el("div", { class: "info" }, el("strong", {}, a.aparelho || "Aparelho"),
      el("small", {}, `Ligadas desde ${fmt.quando(a.desde).split(",")[0]}${a.ultimo_envio ? ` | última notificação ${fmt.relativo(a.ultimo_envio)}` : ""}`)),
    a.id === push.id ? el("span", { class: "atual" }, "Este aparelho")
      : el("button", { class: "discreto perigo", type: "button", onclick: () => removerAparelho(a) }, "Remover"))));
}

/* ------------------------------------------------------------------ Detetive de aparelhos */

async function telaAparelhos() {
  await desenharAparelhos();
  estado.timers.push(setInterval(() => { if (!document.hidden && !$("#aparelhos-conteudo form:not([hidden])")) desenharAparelhos().catch(() => {}); }, 15000));
}

async function desenharAparelhos() {
  const r = await api("GET", "/api/aparelhos");
  const caixa = $("#aparelhos-conteudo");
  if (!r.autorizado) {
    caixa.replaceChildren(el("div", { class: "vazio" },
      el("h2", {}, "O Detetive está desligado"),
      el("p", {}, "Saber qual aparelho ligou, e quando, revela mais da rotina da casa do que o consumo total. Por isso ele só funciona se você ligar, e tudo o que ele guardar é apagado quando você desligar."),
      el("button", { class: "primario", type: "button", onclick: async () => {
        try {
          await api("PUT", "/api/privacidade/aparelhos", { aceito: true });
          avisar("Detetive ligado. Os primeiros aparelhos aparecem depois de se repetirem 3 vezes.");
          await desenharAparelhos();
        } catch (e) { avisar(e.message, true); }
      } }, "Ligar o Detetive")));
    return;
  }
  if (!r.tem_medidor_compativel) {
    caixa.replaceChildren(el("div", { class: "vazio" }, el("h2", {}, "Esperando um medidor"),
      el("p", {}, "O Detetive precisa de um medidor de potência (PZEM) enviando leituras a cada 1 ou 2 segundos.")));
    return;
  }
  if (!r.aparelhos.length) {
    caixa.replaceChildren(el("div", { class: "vazio" }, el("h2", {}, "Observando a casa"),
      el("p", {}, r.em_estudo ? `${r.em_estudo} padrão(ões) em observação. Cada um aparece aqui depois de se repetir ${r.ciclos_para_aparecer} vezes.`
        : "Ainda não vimos nenhum aparelho ligar e desligar. Deixe o medidor funcionando por algumas horas.")));
    return;
  }
  caixa.replaceChildren(el("div", { class: "lista-aparelhos" }, r.aparelhos.map(cartaoAparelho)));
  if (r.em_estudo) caixa.append(el("p", { class: "dica" }, `Mais ${r.em_estudo} padrão(ões) em observação.`));
}

function cartaoAparelho(a) {
  const campo = el("input", { value: a.nome || "", maxlength: "40", placeholder: "ex.: Geladeira", "aria-label": "Nome do aparelho" });
  const form = el("form", { class: "renomear", hidden: true, onsubmit: async (ev) => {
    ev.preventDefault();
    try {
      await api("PATCH", `/api/aparelhos/${a.id}`, { nome: campo.value });
      avisar("Nome salvo.");
      await desenharAparelhos();
    } catch (e) { avisar(e.message, true); }
  } }, campo, el("button", { class: "secundario", type: "submit" }, "Salvar nome"),
  el("button", { class: "discreto", type: "button", onclick: () => { form.hidden = true; } }, "Cancelar"));
  const dado = (rotulo, valor) => el("div", {}, el("dt", {}, rotulo), el("dd", {}, valor));
  return el("article", { class: `aparelho${a.ligado_agora ? " ligado" : ""}` },
    el("div", { class: "medidor-topo" },
      el("h2", {}, el("span", { class: "ponto", "aria-hidden": "true" }), a.nome || `Aparelho de ${fmt.num(a.potencia_w)} W`),
      a.ligado_agora ? el("span", { class: "etiqueta ligado" }, "ligado agora") : null),
    !a.nome && a.palpite ? el("p", { class: "dica" }, `Pela potência e pelo tempo ligado, parece ${a.palpite}. É só um palpite: dê o nome certo.`) : null,
    el("dl", { class: "medidas" },
      dado("Potência", `${fmt.num(a.potencia_w)} W`),
      dado("Ligou na semana", `${a.vezes_na_semana} vez(es)`),
      dado("Fica ligado", a.duracao_media_min >= 90 ? `${fmt.num(a.duracao_media_min / 60, 1)} h`
        : a.duracao_media_min < 1 ? `${Math.round(a.duracao_media_min * 60)} s` : `${fmt.num(a.duracao_media_min, a.duracao_media_min < 10 ? 1 : 0)} min`),
      dado("Por mês", a.kwh_mes != null ? fmt.kwh(a.kwh_mes) : "—"),
      dado("Custo por mês", a.custo_mes != null ? fmt.reais(a.custo_mes) : "—"),
      dado("Última vez", a.ultima_vez ? fmt.relativo(a.ultima_vez) : "—")),
    form,
    el("div", { class: "medidor-acoes" },
      el("button", { type: "button", class: "discreto", onclick: () => { form.hidden = false; campo.focus(); } }, a.nome ? "Renomear" : "Dar um nome"),
      el("button", { type: "button", class: "discreto", onclick: async () => {
        const ok = await confirmar({ titulo: "Isso não é um aparelho?", semSenha: true, botao: "Esconder",
          texto: "O grupo some da lista. O consumo total da casa não muda." });
        if (!ok) return;
        try { await api("PATCH", `/api/aparelhos/${a.id}`, { oculto: true }); await desenharAparelhos(); } catch (e) { avisar(e.message, true); }
      } }, "Não é um aparelho")));
}

/* ------------------------------------------------------------------ E se eu…? (simulador de economia) */

/* Como a tela funciona:
 * - Cada "ideia" é uma ação que pode entrar no plano. As fórmulas estão em economia-calculo.js,
 *   as mesmas do servidor. A ideia lê os próprios parâmetros e sabe se já está no plano (`salva`).
 * - O que a pessoa ajusta fica em eco.valores, por chave. Assim a tela pode ser redesenhada
 *   inteira depois de salvar sem perder nada do que foi arrastado.
 * - O placar soma as ideias com economia, descontando a sobreposição de banho e posição verão,
 *   do mesmo jeito que o servidor soma o plano. */

const ECO_APARELHOS = [
  { nome: "Ar-condicionado", potencia_w: 900 },
  { nome: "Ferro de passar", potencia_w: 1100 },
  { nome: "Secador de cabelo", potencia_w: 1800 },
  { nome: "Aquecedor", potencia_w: 1500 },
];
const ECO_FONTES = { detetive: "medido pelo Detetive", medido: "medido nas madrugadas", tipico: "valor típico", ajustado: "ajustado por você",
  plano: "como no seu plano" };
const eco = { dados: null, valores: {}, extras: [], ideias: [], grupos: [], visor: null, timer: 0, anuncio: 0, ajustarAberto: false, nExtras: 0, nControles: 0 };

const limitar = (v, [min, max]) => Math.min(max, Math.max(min, v));
const limparNome = (nome) => String(nome || "").normalize("NFC").split(/\s+/).filter(Boolean).join(" ");
const chaveOutro = (nome) => `usar_menos:outro:${limparNome(nome).toLowerCase()}`;
const diaMes = (iso) => new Date(iso).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
const minutos = (m) => `${fmt.num(m, Number.isInteger(m) ? 0 : 1)} min`;
const chaveDe = (ideia) => (typeof ideia.chave === "function" ? ideia.chave() : ideia.chave);
const planoPorChave = (chave) => eco.dados.plano.find((a) => a.chave === chave) || null;
const emDinheiro = (kwh) => (eco.dados.tarifa_kwh != null ? fmt.reais(kwh * eco.dados.tarifa_kwh) : fmt.kwh(kwh));

function limparEconomia() {
  eco.valores = {};
  eco.extras = [];
  eco.dados = null;
}

function valorDe(chave, inicial) {
  if (!eco.valores[chave]) eco.valores[chave] = inicial();
  return eco.valores[chave];
}

async function telaEconomia(foco) {
  eco.visor = eco.visor || criarVisor($("#visor-economia"));
  aplicarEconomia(await api("GET", "/api/economia"));
  const alvo = foco && $(`.eco-grupo[data-grupo="${CSS.escape(foco)}"]`);
  if (alvo) {
    alvo.scrollIntoView({ block: "start" });
    const primeiro = $("input, button", alvo);
    if (primeiro) primeiro.focus({ preventScroll: true });
  }
}

/* Redesenha a tela a partir do que veio do servidor. `focoChave`: ideia que recebe o foco. */
function aplicarEconomia(dados, focoChave = null) {
  eco.dados = dados;
  prepararChuveiro();
  desenharIdeias();
  desenharPlanoEconomia();
  desenharTarifaEconomia();
  atualizarPlacar();
  if (focoChave) {
    const caixa = $(`[data-chave="${CSS.escape(focoChave)}"]`);
    const alvo = caixa && ($(".eco-acoes button", caixa) || $("input", caixa));
    if (alvo) alvo.focus({ preventScroll: true });
  }
}

/* ---------- peças de montar */

/* Controle deslizante com rótulo e valor ao lado. */
function controle({ rotulo, min, max, passo = 1, valor, texto, aoMudar }) {
  const id = `eco-c${++eco.nControles}`;
  const entrada = el("input", { type: "range", id, min, max, step: passo });
  const saida = el("output", { for: id });
  entrada.value = valor;
  const pintar = () => {
    const v = Number(entrada.value);
    const mn = Number(entrada.min), mx = Number(entrada.max);
    saida.textContent = texto(v);
    entrada.setAttribute("aria-valuetext", texto(v));
    entrada.style.setProperty("--p", `${mx > mn ? ((v - mn) / (mx - mn)) * 100 : 0}%`);
    entrada.disabled = mx <= mn;
  };
  entrada.addEventListener("input", () => { pintar(); aoMudar(Number(entrada.value)); });
  entrada.addEventListener("change", anunciarPlacar);
  pintar();
  return {
    raiz: el("div", { class: "eco-controle" }, el("label", { for: id }, rotulo), saida, entrada),
    entrada,
    novoMaximo(max) {         // muda o limite sem redesenhar (o foco continua onde está)
      entrada.max = max;
      if (Number(entrada.value) > max) { entrada.value = max; aoMudar(max); }
      pintar();
    },
  };
}

function novaIdeia(def) {
  const ideia = { aparelho_id: null, ...def, dom: {}, r: null };
  ideia.salva = planoPorChave(chaveDe(ideia));
  eco.ideias.push(ideia);
  return ideia;
}

/* O pedaço de uma ideia: título opcional, controles, efeito e o rodapé com economia e botões. */
function blocoIdeia(ideia, { titulo = null, filhos = [] }) {
  ideia.dom.ganho = el("p", { class: "eco-ganho" });
  ideia.dom.acoes = el("div", { class: "eco-acoes" });
  ideia.dom.erro = el("p", { class: "erro", role: "alert" });
  ideia.dom.raiz = el("div", { class: "eco-sub", "data-chave": chaveDe(ideia) },
    titulo ? el("h3", {}, titulo) : null, ...filhos,
    el("div", { class: "eco-rodape" }, ideia.dom.ganho, ideia.dom.acoes), ideia.dom.erro);
  return ideia.dom.raiz;
}

function grupo({ id, titulo, fonte = null, contexto, filhos }) {
  const etiqueta = el("span", { class: `etiqueta${fonte === "tipico" ? " estimado" : ""}` }, fonte ? ECO_FONTES[fonte] : "");
  etiqueta.hidden = !fonte;
  const caixa = el("article", { class: "eco-grupo", "data-grupo": id },
    el("div", { class: "eco-grupo-topo" }, el("h2", {}, titulo), etiqueta),
    contexto, ...filhos);
  caixa.etiqueta = etiqueta;
  return caixa;
}

function calcularIdeia(ideia) {
  const parametros = ideia.ler();
  if (!parametros) return { ok: false, inativa: true, kwh: 0 };
  const v = MWEconomia.validar(ideia.tipo, parametros);
  if (!v.ok) return { ok: false, erro: v.erro, kwh: 0 };
  return { ok: true, p: v.p, kwh: MWEconomia.kwhMes(ideia.tipo, v.p) };
}

function recalcular(ideia) {
  const r = calcularIdeia(ideia);
  ideia.r = r;
  const { ganho, erro } = ideia.dom;
  ganho.toggleAttribute("data-zero", !(r.kwh > 0));
  const tarifa = eco.dados.tarifa_kwh;
  ganho.replaceChildren(...(tarifa != null
    ? [el("strong", {}, fmt.reais(r.kwh * tarifa)), " por mês", el("small", {}, fmt.kwh(r.kwh))]
    : [el("strong", {}, fmt.kwh(r.kwh)), " por mês"]));
  erro.textContent = r.ok || r.inativa ? "" : r.erro;
  ideia.dom.raiz.toggleAttribute("data-inativa", !!r.inativa);
  desenharAcoes(ideia);
  return r;
}

function mudouDoSalvo(ideia) {
  const s = ideia.salva, r = ideia.r;
  if (!s || !r || !r.ok) return false;
  return Object.keys(r.p).some((k) => {
    const a = r.p[k], b = s.parametros[k];
    return a == null || b == null ? a != b : Math.abs(a - b) > 1e-6;   // eslint-disable-line eqeqeq
  });
}

function desenharAcoes(ideia) {
  const s = ideia.salva, r = ideia.r;
  const pode = r.ok && r.kwh > 0;
  const mudou = mudouDoSalvo(ideia);
  const marca = `${s ? s.id : ""}|${pode}|${mudou}`;
  ideia.dom.raiz.toggleAttribute("data-no-plano", !!s);
  if (ideia.dom.acoes.dataset.marca === marca) return;          // nada mudou: não troca os botões
  ideia.dom.acoes.dataset.marca = marca;
  ideia.dom.acoes.replaceChildren(...[
    s ? el("span", { class: "selo-plano" }, `No plano desde ${diaMes(s.desde)}`) : null,
    pode && (!s || mudou) ? el("button", { type: "button", class: "verde", onclick: (ev) => salvarIdeia(ideia, ev.currentTarget) },
      s ? "Atualizar o plano" : "Vou tentar") : null,
    s ? el("button", { type: "button", class: "discreto", onclick: (ev) => removerIdeia(ideia, ev.currentTarget) }, "Tirar do plano") : null,
  ].filter(Boolean));       // replaceChildren escreveria "null" na tela
}

function tituloDe(ideia) {
  if (ideia.tipo !== "usar_menos") return ideia.titulo;
  return `Usar menos (${limparNome(ideia.nomeAtual())})`;
}

async function salvarIdeia(ideia, botao) {
  const r = calcularIdeia(ideia);
  if (!r.ok) { ideia.dom.erro.textContent = r.erro || ""; return; }
  const corpo = { tipo: ideia.tipo, parametros: r.p };
  if (ideia.aparelho_id) corpo.aparelho_id = ideia.aparelho_id;
  let chave = chaveDe(ideia);
  if (ideia.tipo === "usar_menos" && !ideia.aparelho_id) {
    corpo.nome = limparNome(ideia.nomeAtual());
    if (!corpo.nome) {
      ideia.dom.erro.textContent = "Dê um nome ao aparelho para pôr no plano.";
      if (ideia.dom.nome) ideia.dom.nome.focus();
      return;
    }
    chave = chaveOutro(corpo.nome);
  }
  const titulo = tituloDe(ideia);
  const jaEstava = !!planoPorChave(chave);
  botao.disabled = true;
  try {
    const novo = await api("POST", "/api/economia/plano", corpo);
    if (chave !== chaveDe(ideia)) {          // linha "Outro aparelho": passa a ser a linha do plano
      eco.valores[chave] = { ...eco.valores[chaveDe(ideia)], nome: corpo.nome };
      eco.extras = eco.extras.filter((x) => x.id !== ideia.extraId);
    }
    avisar(jaEstava ? `Plano atualizado: ${titulo}.` : `“${titulo}” entrou no plano. Daqui a alguns dias dá para conferir se a economia apareceu.`);
    aplicarEconomia(novo, chave);
  } catch (e) {
    ideia.dom.erro.textContent = e.message;
    botao.disabled = false;
  }
}

async function removerIdeia(ideia, botao) {
  const titulo = tituloDe(ideia);
  botao.disabled = true;
  try {
    const novo = await api("DELETE", `/api/economia/plano/${ideia.salva.id}`);
    // Um aparelho com nome dado pela pessoa continua na lista, para dar para voltar atrás.
    if (ideia.tipo === "usar_menos" && !ideia.aparelho_id && !eco.extras.some((x) => x.chave === chaveDe(ideia))) {
      eco.extras.push({ id: `x${++eco.nExtras}`, chave: chaveDe(ideia), nome: limparNome(ideia.nomeAtual()), potencia_w: ideia.r.p ? ideia.r.p.potencia_w : 1000, editavel: true });
    }
    avisar(`“${titulo}” saiu do plano.`);
    aplicarEconomia(novo, chaveDe(ideia));
  } catch (e) {
    ideia.dom.erro.textContent = e.message;
    botao.disabled = false;
  }
}

/* ---------- os cartões */

function desenharIdeias() {
  eco.ideias = [];
  eco.grupos = [];
  $("#eco-cartoes").replaceChildren(grupoChuveiro(), grupoFantasma(), grupoUsarMenos(), grupoGeladeira());
  eco.grupos.forEach((atualizar) => atualizar());
}

/* Banho mais curto e posição verão dividem os dados do chuveiro (do Detetive ou típicos).
 * Com o chuveiro no plano, valem os dados que foram para o plano; `daMedicao` volta para a
 * medição mais recente (que muda justamente quando a pessoa passa a tomar banhos mais curtos). */
function prepararChuveiro(daMedicao = false) {
  const d = eco.dados, C = MWEconomia.CAMPOS.chuveiro_verao;
  const medicao = JSON.stringify(d.chuveiro);
  const atual = eco.valores.chuveiro;
  if (atual && !daMedicao && (atual.fonte === "ajustado" || atual.medicao === medicao)) return;
  const verao = daMedicao ? null : planoPorChave("chuveiro_verao");
  const salvo = daMedicao ? null : (planoPorChave("banho_curto") || verao || {}).parametros;
  const base = salvo || d.chuveiro;
  const c = {
    potencia_w: limitar(base.potencia_w, C.potencia_w),
    banhos_semana: limitar(base.banhos_semana, C.banhos_semana),
    minutos_banho: limitar(base.minutos_banho, C.minutos_banho),
  };
  c.potencia_verao_w = verao ? verao.parametros.potencia_verao_w : estimarVerao(c.potencia_w);
  c.verao_estimado = !verao;
  const igual = ["potencia_w", "banhos_semana", "minutos_banho"].every((k) => Math.abs(c[k] - d.chuveiro[k]) < 1e-6);
  c.fonte = igual ? d.chuveiro.fonte : "plano";
  c.medicao = medicao;
  eco.valores.chuveiro = c;
  const banho = eco.valores.banho_curto;                  // os minutos a menos não podem passar da duração do banho
  if (banho) banho.minutos_a_menos = Math.min(banho.minutos_a_menos, Math.max(0, Math.ceil(c.minutos_banho) - 1));
}

// Sem medição da posição verão: costuma ficar perto de 55% da inverno.
const estimarVerao = (p) => limitar(Math.round((p * 0.55) / 100) * 100, [300, p - 100]);

function grupoChuveiro() {
  const c = eco.valores.chuveiro, C = MWEconomia.CAMPOS.chuveiro_verao;
  const maxMenos = () => Math.max(0, Math.min(10, Math.ceil(c.minutos_banho) - 1));
  const vb = valorDe("banho_curto", () => {
    const s = planoPorChave("banho_curto");
    return { minutos_a_menos: s ? s.parametros.minutos_a_menos : Math.min(2, maxMenos()) };
  });
  const vv = valorDe("chuveiro_verao", () => {
    const s = planoPorChave("chuveiro_verao");
    return { porcentagem: s ? s.parametros.porcentagem : 50 };
  });
  const banho = novaIdeia({ chave: "banho_curto", tipo: "banho_curto", titulo: "Banho mais curto",
    ler: () => ({ minutos_a_menos: vb.minutos_a_menos, potencia_w: c.potencia_w, banhos_semana: c.banhos_semana, minutos_banho: c.minutos_banho }) });
  const verao = novaIdeia({ chave: "chuveiro_verao", tipo: "chuveiro_verao", titulo: "Chuveiro na posição verão",
    ler: () => ({ porcentagem: vv.porcentagem, potencia_w: c.potencia_w, potencia_verao_w: c.potencia_verao_w, banhos_semana: c.banhos_semana, minutos_banho: c.minutos_banho }) });

  const contexto = el("p", { class: "eco-contexto" });
  const medicao = el("p", { class: "eco-nota" });
  const efeitoBanho = el("p", { class: "eco-efeito" });
  const efeitoVerao = el("p", { class: "eco-efeito" });
  const juntas = el("p", { class: "eco-nota" });
  const erroAjuste = el("p", { class: "erro", role: "alert" });
  let caixa = null;

  const atualizar = () => {
    contexto.textContent = `Seu chuveiro: ${fmt.num(c.potencia_w)} W, ${fmt.num(c.banhos_semana, Number.isInteger(c.banhos_semana) ? 0 : 1)} banhos por semana, ${minutos(c.minutos_banho)} cada.`;
    caixa.etiqueta.textContent = ECO_FONTES[c.fonte];
    caixa.etiqueta.className = `etiqueta${c.fonte === "tipico" ? " estimado" : ""}`;
    const b = eco.dados.chuveiro;
    medicao.hidden = !(c.fonte === "plano" && b.fonte === "detetive");
    if (!medicao.hidden) {
      medicao.replaceChildren(`A medição mais recente: ${fmt.num(b.potencia_w)} W, ${fmt.num(b.banhos_semana)} banhos por semana, ${minutos(b.minutos_banho)} cada. `,
        el("button", { type: "button", class: "link", onclick: () => {
          prepararChuveiro(true);
          desenharIdeias();
          atualizarPlacar();
          const alvo = $('[data-chave="banho_curto"] input[type=range]');
          if (alvo) alvo.focus();
        } }, "Usar a medição"));
    }
    const rb = recalcular(banho), rv = recalcular(verao);
    efeitoBanho.textContent = vb.minutos_a_menos > 0
      ? `O banho de ${minutos(c.minutos_banho)} passa a ${minutos(Math.round((c.minutos_banho - vb.minutos_a_menos) * 10) / 10)}.`
      : "Arraste para ver quanto alguns minutos a menos economizam.";
    const n = Math.round((c.banhos_semana * vv.porcentagem) / 100);
    efeitoVerao.textContent = `${fmt.num(n)} de ${fmt.num(Math.round(c.banhos_semana))} banhos por semana na posição verão, que usa cerca de ${fmt.num(c.potencia_verao_w)} W${c.verao_estimado ? " (estimativa)" : ""}.`;
    const soma = rb.kwh + rv.kwh;
    const total = rb.ok && rv.ok ? MWEconomia.totalKwhMes([{ tipo: "banho_curto", parametros: rb.p }, { tipo: "chuveiro_verao", parametros: rv.p }]) : soma;
    juntas.hidden = !(rb.kwh > 0 && rv.kwh > 0 && soma - total > 0.005);
    juntas.textContent = `Juntas, as duas economizam ${emDinheiro(total)} por mês, e não ${emDinheiro(soma)}: um banho mais curto na posição verão economiza menos. O placar já desconta isso.`;
    agendarPlacar();
  };

  const cBanho = controle({ rotulo: "Minutos a menos em cada banho", min: 0, max: maxMenos(), valor: vb.minutos_a_menos,
    texto: (v) => `${fmt.num(v)} min`, aoMudar: (v) => { vb.minutos_a_menos = v; atualizar(); } });
  const cVerao = controle({ rotulo: "Banhos na posição verão", min: 0, max: 100, passo: 10, valor: vv.porcentagem,
    texto: (v) => `${v}%`, aoMudar: (v) => { vv.porcentagem = v; atualizar(); } });

  // Corrigir os dados do chuveiro: vale para as duas ideias.
  const campo = (rotulo, chave, casas) => {
    const entrada = el("input", { inputmode: "decimal", autocomplete: "off" });
    entrada.value = fmt.num(c[chave], Number.isInteger(c[chave]) ? 0 : casas);
    entrada.addEventListener("input", () => {
      const n = numeroDigitado(entrada.value);
      const [min, max] = C[chave];
      if (n == null || Number.isNaN(n) || n < min || n > max) {
        entrada.setAttribute("aria-invalid", "true");
        erroAjuste.textContent = `${rotulo}: use um número de ${fmt.num(min)} a ${fmt.num(max)}.`;
        return;
      }
      entrada.removeAttribute("aria-invalid");
      erroAjuste.textContent = "";
      c[chave] = n;
      c.fonte = "ajustado";
      if (chave === "potencia_verao_w") c.verao_estimado = false;
      if (chave === "potencia_w" && c.verao_estimado) {
        c.potencia_verao_w = estimarVerao(n);
        caixaVerao.value = fmt.num(c.potencia_verao_w);
      }
      cBanho.novoMaximo(maxMenos());
      atualizar();
    });
    return entrada;
  };
  const caixaVerao = campo("Potência na posição verão", "potencia_verao_w", 0);
  const ajustar = el("details", { class: "eco-ajustar", open: eco.ajustarAberto },
    el("summary", {}, "Corrigir os dados do chuveiro"),
    el("div", { class: "eco-campos" },
      el("label", {}, "Potência (W)", campo("Potência", "potencia_w", 0)),
      el("label", {}, "Na posição verão (W)", caixaVerao),
      el("label", {}, "Banhos por semana", campo("Banhos por semana", "banhos_semana", 1)),
      el("label", {}, "Minutos por banho", campo("Minutos por banho", "minutos_banho", 1))),
    erroAjuste,
    el("p", { class: "dica" }, "A potência vem gravada no chuveiro ou na caixa, como 5.500 W. Somando os banhos de todo mundo da casa."));
  ajustar.addEventListener("toggle", () => { eco.ajustarAberto = ajustar.open; });

  caixa = grupo({ id: "chuveiro", titulo: "Chuveiro", fonte: c.fonte, contexto, filhos: [medicao,
    blocoIdeia(banho, { titulo: "Banho mais curto", filhos: [cBanho.raiz, efeitoBanho] }),
    blocoIdeia(verao, { titulo: "Chuveiro na posição verão", filhos: [cVerao.raiz, efeitoVerao] }),
    juntas, ajustar] });
  eco.grupos.push(atualizar);
  return caixa;
}

function grupoFantasma() {
  const f = eco.dados.fantasma;
  const w = Math.round(f.watts);
  const v = valorDe("fantasma", () => {
    const s = planoPorChave("fantasma");
    return s ? { ...s.parametros } : { watts_cortados: Math.round(w * 0.3), horas_dia: 20 };
  });
  const maximo = Math.max(Math.min(3000, Math.max(10, w)), Math.ceil(v.watts_cortados));
  const ideia = novaIdeia({ chave: "fantasma", tipo: "fantasma", titulo: "Cortar o consumo em espera", ler: () => ({ ...v }) });
  const efeito = el("p", { class: "eco-efeito" });
  const atualizar = () => {
    recalcular(ideia);
    efeito.textContent = v.watts_cortados > 0 && v.horas_dia > 0
      ? `Desligar ${fmt.num(v.watts_cortados)} W de aparelhos em espera, ${fmt.num(v.horas_dia)} horas por dia.`
      : "Arraste para ver quanto dá para cortar.";
    agendarPlacar();
  };
  const cw = controle({ rotulo: "Watts em espera para desligar", min: 0, max: maximo, passo: 1, valor: v.watts_cortados,
    texto: (x) => `${fmt.num(x)} W`, aoMudar: (x) => { v.watts_cortados = x; atualizar(); } });
  const ch = controle({ rotulo: "Horas por dia desligados", min: 0, max: 24, valor: v.horas_dia,
    texto: (x) => `${x} h`, aoMudar: (x) => { v.horas_dia = x; atualizar(); } });
  const d = eco.dados;
  const porque = d.consumo_autorizado === false ? "Sem a autorização do consumo, usamos"
    : !d.medidores ? "Sem medidor ainda, usamos" : "Ainda sem madrugadas medidas na sua casa. Usamos";
  const contexto = el("p", { class: "eco-contexto" }, f.fonte === "medido"
    ? `Com a casa parada, ${fmt.num(w)} W ficam ligados o tempo todo: aparelhos em espera, carregadores, roteador.`
    : `${porque} ${fmt.num(w)} W, um valor típico de aparelhos em espera.`);
  eco.grupos.push(atualizar);
  return grupo({ id: "fantasma", titulo: "Consumo em espera", fonte: f.fonte, contexto, filhos: [
    blocoIdeia(ideia, { filhos: [cw.raiz, ch.raiz, efeito,
      el("p", { class: "dica" }, "Um filtro de linha com interruptor para TV, som, videogame e carregadores costuma resolver a maior parte. Roteador e geladeira ficam ligados.")] })] });
}

/* Linhas do "Usar menos": aparelhos do Detetive, os que estão no plano e os que a pessoa acrescentou. */
function linhasUsarMenos() {
  const d = eco.dados, linhas = [], vistas = new Set();
  const por = (l) => { if (!vistas.has(l.chave)) { vistas.add(l.chave); linhas.push(l); } };
  d.aparelhos.forEach((a) => por({ chave: `usar_menos:${a.id}`, nome: a.rotulo, semNome: !a.nome, aparelho_id: a.id, potencia_w: a.potencia_w,
    minutos_dia: a.minutos_dia, fonte: "detetive" }));
  d.plano.filter((a) => a.tipo === "usar_menos").forEach((a) => por(a.aparelho_id
    ? { chave: a.chave, nome: a.nome, aparelho_id: a.aparelho_id, potencia_w: a.parametros.potencia_w, fonte: "detetive" }
    : { chave: a.chave, nome: a.nome, potencia_w: a.parametros.potencia_w, editavel: true }));
  eco.extras.forEach(por);
  return linhas;
}

function acrescentarAparelho(modelo) {
  const id = `x${++eco.nExtras}`;
  const extra = modelo
    ? { id, chave: chaveOutro(modelo.nome), nome: modelo.nome, potencia_w: modelo.potencia_w, editavel: true }
    : { id, chave: `usar_menos:novo:${id}`, nome: "", potencia_w: 1000, editavel: true, temporaria: true };
  eco.extras.push(extra);
  desenharIdeias();
  const caixa = $(`[data-chave="${CSS.escape(extra.chave)}"]`);
  const alvo = caixa && $(modelo ? "input[type=range]" : "input", caixa);
  if (alvo) alvo.focus();
}

function linhaUsarMenos(linha) {
  const v = valorDe(linha.chave, () => {
    const s = planoPorChave(linha.chave);
    return { minutos_dia: s ? s.parametros.minutos_dia : 0, potencia_w: s ? s.parametros.potencia_w : linha.potencia_w, nome: linha.nome };
  });
  if (!linha.editavel) v.potencia_w = linha.potencia_w;
  const ideia = novaIdeia({ chave: linha.chave, tipo: "usar_menos", titulo: "Usar menos", aparelho_id: linha.aparelho_id || null,
    extraId: linha.id, nomeAtual: () => v.nome,
    ler: () => ({ potencia_w: v.potencia_w, minutos_dia: v.minutos_dia }) });
  const medido = linha.minutos_dia != null;
  const maximo = Math.max(medido ? Math.min(240, Math.max(30, Math.ceil(linha.minutos_dia / 5) * 5)) : 180, Math.ceil(v.minutos_dia));
  const efeito = el("p", { class: "eco-efeito" });
  const atualizar = () => {
    recalcular(ideia);
    efeito.textContent = medido ? `Uso medido: cerca de ${minutos(Math.round(linha.minutos_dia))} por dia.` : "";
    efeito.hidden = !medido;
    agendarPlacar();
  };
  const c = controle({ rotulo: "Minutos a menos por dia", min: 0, max: maximo, passo: maximo <= 60 ? 1 : 5, valor: v.minutos_dia,
    texto: (x) => `${fmt.num(x)} min`, aoMudar: (x) => { v.minutos_dia = x; atualizar(); } });

  const potencia = el("input", { inputmode: "numeric", autocomplete: "off", "aria-label": `Potência${v.nome ? ` de ${v.nome}` : ""} em watts` });
  potencia.value = fmt.num(v.potencia_w);
  potencia.addEventListener("input", () => {
    const n = numeroDigitado(potencia.value);
    v.potencia_w = n == null || Number.isNaN(n) ? null : n;
    potencia.toggleAttribute("aria-invalid", !(v.potencia_w >= 1 && v.potencia_w <= 15000));
    atualizar();
  });
  let topo;
  if (linha.temporaria) {
    const nome = el("input", { maxlength: "40", autocomplete: "off", placeholder: "ex.: bomba d'água" });
    nome.value = v.nome;
    nome.addEventListener("input", () => { v.nome = nome.value; ideia.dom.erro.textContent = ""; });
    ideia.dom.nome = nome;
    topo = el("div", { class: "eco-campos" }, el("label", {}, "Aparelho", nome), el("label", {}, "Potência (W)", potencia));
  } else {
    topo = el("div", { class: "eco-linha-topo" }, el("h3", {}, v.nome),
      linha.editavel ? el("label", { class: "eco-potencia" }, potencia, "W")
        : linha.semNome ? null : el("span", { class: "eco-linha-info" }, `${fmt.num(linha.potencia_w)} W`),   // sem nome, o título já diz a potência
      linha.fonte ? el("span", { class: "etiqueta" }, ECO_FONTES[linha.fonte]) : null);
  }
  const tirar = linha.id && !planoPorChave(linha.chave)
    ? el("button", { type: "button", class: "eco-tirar", "aria-label": `Tirar ${v.nome || "este aparelho"} da lista`,
      onclick: () => { eco.extras = eco.extras.filter((x) => x.id !== linha.id); delete eco.valores[linha.chave]; desenharIdeias(); atualizarPlacar(); } }, "×")
    : null;
  eco.grupos.push(atualizar);
  const caixa = blocoIdeia(ideia, { filhos: [topo, c.raiz, efeito] });
  caixa.classList.add("eco-linha");
  if (tirar) caixa.prepend(tirar);
  return caixa;
}

function grupoUsarMenos() {
  const linhas = linhasUsarMenos();
  const presentes = new Set(linhas.map((l) => l.chave));
  const d = eco.dados;
  const semNome = linhas.some((l) => l.semNome);
  const intro = linhas.length
    ? `Menos minutos por dia de um aparelho que puxa bastante energia.${semNome ? " Dê nome aos aparelhos na aba Aparelhos para reconhecer cada um aqui." : ""}`
    : d.detetive_ligado
      ? "O Detetive ainda não separou aparelhos da sua casa. Escolha um para simular:"
      : "Com o Detetive de aparelhos ligado, os aparelhos medidos na sua casa aparecem aqui. Por enquanto, escolha um para simular:";
  const chips = el("div", { class: "eco-chips", role: "group", "aria-label": "Acrescentar aparelho" },
    ...ECO_APARELHOS.filter((a) => !presentes.has(chaveOutro(a.nome))).map((a) =>
      el("button", { type: "button", class: "chip", "aria-label": `Acrescentar ${a.nome}`, onclick: () => acrescentarAparelho(a) }, `+ ${a.nome}`)),
    el("button", { type: "button", class: "chip", "aria-label": "Acrescentar outro aparelho", onclick: () => acrescentarAparelho(null) }, "+ Outro aparelho"));
  return grupo({ id: "usar-menos", titulo: "Usar menos um aparelho", contexto: el("p", { class: "eco-contexto" }, intro),
    filhos: [el("div", { class: "eco-linhas" }, ...linhas.map(linhaUsarMenos)), chips] });
}

function grupoGeladeira() {
  const g = eco.dados.geladeira;
  const s = planoPorChave("geladeira");
  const atual = limitar(Math.round(g.kwh_mes * 10) / 10, [5, 400]);
  const v = valorDe("geladeira", () => (s
    ? { ligada: true, kwh_mes_atual: s.parametros.kwh_mes_atual, kwh_mes_nova: s.parametros.kwh_mes_nova, preco: s.parametros.preco }
    : { ligada: false, kwh_mes_atual: atual, kwh_mes_nova: Math.max(5, Math.min(35, Math.round(atual * 0.7))), preco: null }));
  const ideia = novaIdeia({ chave: "geladeira", tipo: "geladeira", titulo: "Trocar a geladeira", aparelho_id: g.aparelho_id,
    ler: () => (v.ligada ? { kwh_mes_atual: v.kwh_mes_atual, kwh_mes_nova: v.kwh_mes_nova, preco: v.preco } : null) });
  const efeito = el("p", { class: "eco-efeito" });
  let corpo = null;
  const atualizar = () => {
    const r = recalcular(ideia);
    corpo.hidden = !v.ligada;
    const tarifa = eco.dados.tarifa_kwh;
    const meses = r.ok ? MWEconomia.mesesParaPagar(v.preco, r.kwh, tarifa) : null;
    if (!r.ok || r.kwh <= 0) efeito.textContent = "A nova precisa gastar menos que a atual para haver economia.";
    else if (v.preco == null) efeito.textContent = tarifa != null ? "Informe o preço para ver em quanto tempo a troca se paga." : "";
    else if (meses == null) efeito.textContent = "Informe a tarifa para ver em quanto tempo a troca se paga.";
    else if (meses > 25 * 12) efeito.textContent = "Só pela conta de luz, a troca levaria mais de 25 anos para se pagar. Compensa quando a geladeira já precisa ser trocada de qualquer jeito.";
    else {
      const prazo = meses < 24 ? `cerca de ${Math.ceil(meses)} meses` : `cerca de ${fmt.num(meses / 12, meses < 120 ? 1 : 0)} anos`;
      efeito.textContent = `Com ${fmt.reais(v.preco)}, a troca se paga em ${prazo}, só com a conta de luz.`
        + (meses > 120 ? " Compensa mais quando a geladeira já precisa ser trocada de qualquer jeito." : "");
    }
    agendarPlacar();
  };
  const chave = el("button", { type: "button", class: "chave", role: "switch", "aria-checked": String(v.ligada), "aria-label": "Simular a troca da geladeira",
    onclick: () => { v.ligada = !v.ligada; chave.setAttribute("aria-checked", String(v.ligada)); atualizar(); anunciarPlacar(); } });
  const cNova = controle({ rotulo: "Consumo da nova, na etiqueta do Inmetro", min: 5, max: Math.max(6, Math.round(v.kwh_mes_atual)), valor: v.kwh_mes_nova,
    texto: (x) => `${fmt.num(x)} kWh por mês`, aoMudar: (x) => { v.kwh_mes_nova = x; atualizar(); } });
  const preco = el("input", { inputmode: "decimal", autocomplete: "off", placeholder: "ex.: 3.200" });
  preco.value = v.preco != null ? fmt.num(v.preco, 2) : "";
  preco.addEventListener("input", () => {
    const n = numeroDigitado(preco.value);
    v.preco = n == null ? null : n;
    preco.toggleAttribute("aria-invalid", Number.isNaN(n));
    atualizar();
  });
  corpo = el("div", { class: "eco-geladeira" }, cNova.raiz, el("label", { class: "eco-preco" }, "Preço da nova (R$, opcional)", preco), efeito);
  const contexto = el("div", { class: "eco-chave-linha" },
    el("p", { class: "eco-contexto" }, `Sua geladeira gasta cerca de ${fmt.num(v.kwh_mes_atual, 1)} kWh por mês.${g.fonte === "tipico" ? " Valor típico de uma geladeira com alguns anos de uso." : ""}`),
    el("label", { class: "eco-chave" }, "Simular a troca", chave));
  eco.grupos.push(atualizar);
  return grupo({ id: "geladeira", titulo: "Trocar a geladeira", fonte: g.fonte, contexto, filhos: [blocoIdeia(ideia, { filhos: [corpo] })] });
}

/* ---------- o placar, a tarifa e o plano */

function totalDaTela() {
  return MWEconomia.totalKwhMes(eco.ideias.filter((i) => i.r && i.r.ok && i.r.kwh > 0).map((i) => ({ tipo: i.tipo, parametros: i.r.p })));
}

function agendarPlacar() {
  if (eco.timer) return;
  eco.timer = setTimeout(() => { eco.timer = 0; atualizarPlacar(); }, 90);
}

function atualizarPlacar() {
  clearTimeout(eco.timer);
  eco.timer = 0;
  if (!eco.dados) return;
  const kwh = totalDaTela(), tarifa = eco.dados.tarifa_kwh;
  $("#visor-economia").toggleAttribute("data-zero", !(kwh >= 0.05));
  if (tarifa != null) eco.visor.mostrar(Math.round(kwh * 12 * tarifa).toLocaleString("pt-BR"), "por ano", "R$");
  else eco.visor.mostrar(Math.round(kwh * 12).toLocaleString("pt-BR"), "kWh por ano");
  const sub = $("#eco-placar-sub");
  if (kwh >= 0.05) {
    const dia = eco.dados.consumo_kwh_dia;
    const dias = dia ? (kwh * 12) / dia : null;
    sub.replaceChildren(...(tarifa != null
      ? [`${fmt.reais(kwh * tarifa)} por mês`, el("span", { class: "eco-kwh" }, `, ou ${fmt.kwh(kwh)}`), "."]
      : [`${fmt.kwh(kwh)} por mês.`]),
    dias == null ? "" : el("span", { class: "eco-equivalencia" }, dias < 1
        ? " Num ano, quase um dia inteiro do que a casa gasta."
        : ` Num ano, é o que a casa inteira gasta em ${fmt.num(dias, dias < 10 ? 1 : 0)} dias.`));
  } else {
    sub.textContent = "Mexa nos controles para ver quanto dá para economizar.";
  }
}

function anunciarPlacar() {
  clearTimeout(eco.anuncio);
  eco.anuncio = setTimeout(() => {
    $("#eco-anuncio").textContent = `Economia simulada: ${$("#visor-economia").getAttribute("aria-label")}.`;
  }, 600);
}

function desenharTarifaEconomia() {
  const d = eco.dados, propria = estado.eu.tarifa_kwh != null;
  const entrada = el("input", { name: "tarifa_kwh", inputmode: "decimal", placeholder: "ex.: 0,92", autocomplete: "off" });
  entrada.value = propria ? fmt.num(estado.eu.tarifa_kwh, 4) : "";
  const form = el("form", { class: "linha-form", novalidate: true, hidden: d.tarifa_kwh != null },
    el("label", {}, "Tarifa (R$ por kWh)", entrada),
    el("button", { class: "secundario", type: "submit" }, "Salvar"),
    el("p", { class: "erro", role: "alert" }));
  formulario(form, async (dados) => {
    const t = numeroDigitado(dados.tarifa_kwh);
    if (t == null || Number.isNaN(t)) throw new Error("Use só números, como 0,92.");
    await api("PUT", "/api/conta/preferencias", { meta_mensal_reais: estado.eu.meta_mensal_reais, tarifa_kwh: t });
    estado.eu.tarifa_kwh = t;
    avisar("Tarifa salva.");
    aplicarEconomia(await api("GET", "/api/economia"));
  });
  const linha = d.tarifa_kwh != null
    ? el("p", {}, `Tarifa de ${fmt.reais(d.tarifa_kwh)} por kWh${propria ? "" : ", a padrão deste servidor"}. `,
      el("button", { type: "button", class: "link", onclick: () => { form.hidden = !form.hidden; if (!form.hidden) entrada.focus(); } }, "Mudar"))
    : el("p", {}, "Sem tarifa, os valores ficam em kWh. Informe a da sua conta de luz, com impostos:");
  $("#eco-tarifa").replaceChildren(linha, form);
  $("#eco-tarifa").toggleAttribute("data-sem-tarifa", d.tarifa_kwh == null);   // no celular, sobe para perto do placar
}

/* "Deu certo?": o texto da conferência. `curto` = uma frase, para o painel. */
function textoConferencia(v, curto = false) {
  const desde = diaMes(v.desde);
  if (!v.pronto) return `Conferência: ${Math.min(v.dias_depois, v.minimo)} de ${v.minimo} dias medidos desde ${desde}.`;
  const t = v.total;
  if (!t || t.variacao == null) return "Ainda não dá para comparar.";
  const pct = Math.round(Math.abs(t.variacao) * 100);
  const valores = curto ? "" : `: ${fmt.kwh(t.depois_kwh_dia)} contra ${fmt.kwh(t.antes_kwh_dia)} nas duas semanas antes`;
  if (t.variacao <= -0.02) {
    return `Desde ${desde}, a casa gastou ${pct}% menos por dia${valores}.${t.reais_mes > 0 ? ` Cerca de ${fmt.reais(t.reais_mes)} por mês.` : ""}`;
  }
  if (t.variacao < 0.02) return `Desde ${desde}, o consumo por dia ficou praticamente igual${valores}.`;
  return `Desde ${desde}, a casa gastou ${pct}% mais por dia${valores}.${curto ? "" : " Calor, visitas e aparelhos novos também pesam."}`;
}

function conferencia(v, semMedidor = false) {
  if (!v) return [el("p", { class: "eco-nota" }, "A conferência precisa da autorização para tratar o consumo, em ", el("a", { href: "#privacidade" }, "Privacidade"), ".")];
  if (semMedidor) return [el("p", {}, "A conferência começa quando houver um medidor pareado: ele mede a casa antes e depois das mudanças.")];
  if (!v.pronto) {
    const barra = el("i");
    const feitos = Math.min(v.dias_depois, v.minimo);
    requestAnimationFrame(() => { barra.style.width = `${(feitos / v.minimo) * 100}%`; });
    return [
      el("p", {}, textoConferencia(v)),
      el("div", { class: "eco-progresso", role: "progressbar", "aria-label": "Dias medidos desde o início do plano",
        "aria-valuemin": "0", "aria-valuemax": String(v.minimo), "aria-valuenow": String(feitos) }, barra),
      el("p", { class: "eco-nota" }, v.dias_antes < v.minimo
        ? `Para comparar, também são precisos ${v.minimo} dias medidos nas duas semanas antes de ${diaMes(v.desde)}, e há ${v.dias_antes}. Se o medidor começou a medir há pouco, tire as ações do plano e ponha de novo daqui a alguns dias: a contagem recomeça.`
        : "Só entram dias medidos quase por inteiro. O dia em que a ação entrou no plano e o dia de hoje ficam de fora."),
    ];
  }
  const t = v.total;
  const sinal = !t || t.variacao == null ? "igual" : t.variacao <= -0.02 ? "menos" : t.variacao < 0.02 ? "igual" : "mais";
  const pct = t && t.variacao != null ? Math.round(Math.abs(t.variacao) * 100) : 0;
  return [
    el("div", { class: "eco-veredito", "data-sinal": sinal },
      el("p", { class: "eco-veredito-numero" }, sinal === "igual" ? "=" : `${sinal === "menos" ? "−" : "+"}${pct}%`),
      el("p", {}, textoConferencia(v))),
    ...v.itens.map(barrasAntesDepois),
    el("p", { class: "eco-nota" }, `Comparação de ${v.dias_antes} dias medidos antes com ${v.dias_depois} depois.`),
  ];
}

function barrasAntesDepois(item) {
  const watts = item.unidade === "W";
  const max = Math.max(item.antes || 0, item.depois || 0, 0.001);
  const linha = (rotulo, valor, classe) => {
    const barra = el("i");
    requestAnimationFrame(() => { barra.style.width = `${((valor || 0) / max) * 100}%`; });
    return el("div", { class: `eco-barra ${classe}` }, el("span", {}, rotulo), el("span", { class: "eco-barra-trilho" }, barra),
      el("span", { class: "eco-barra-valor" }, watts ? `${fmt.num(valor)} W` : fmt.kwh(valor)));
  };
  return el("div", { class: "eco-item" },
    el("p", {}, item.rotulo, watts ? null : el("small", {}, " por dia")),
    linha("Antes", item.antes, "antes"),
    linha("Depois", item.depois, item.depois <= item.antes ? "melhor" : "pior"));
}

function desenharPlanoEconomia() {
  const d = eco.dados, caixa = $("#eco-plano");
  const notas = [];
  if (d.consumo_autorizado === false) {
    notas.push(el("p", { class: "eco-nota" }, "Sem a autorização para tratar o consumo, as contas usam valores típicos e a conferência fica desligada. Dá para mudar em ",
      el("a", { href: "#privacidade" }, "Privacidade"), "."));
  } else if (!d.medidores) {
    notas.push(el("p", { class: "eco-nota" }, "Ainda sem medidor pareado: as contas usam valores típicos. Com o MeterWatt medindo, passam a usar os números da sua casa."));
  }
  caixa.toggleAttribute("data-vazio", !d.plano.length);
  if (!d.plano.length) {
    caixa.replaceChildren(el("h2", {}, "Seu plano"),
      el("p", { class: "eco-plano-vazio" }, "Marque “Vou tentar” nas ideias que quiser pôr em prática. O MeterWatt continua medindo e, depois de alguns dias, mostra aqui se a economia apareceu."),
      ...notas);
    return;
  }
  const soma = d.plano.reduce((s, a) => s + a.kwh_mes, 0);
  caixa.replaceChildren(...[
    el("div", { class: "cabecalho-bloco" }, el("h2", {}, "Seu plano"),
      el("p", { class: "eco-plano-total" }, el("strong", {}, emDinheiro(d.plano_total.kwh_mes)), " por mês, pela estimativa")),
    el("ul", { class: "eco-plano-lista" }, ...d.plano.map((a) => el("li", {},
      el("span", {}, a.tipo === "usar_menos" ? `Usar menos (${a.nome})` : a.titulo, el("small", {}, `desde ${diaMes(a.desde)}`)),
      el("span", { class: "eco-plano-valor" }, emDinheiro(a.kwh_mes))))),
    soma - d.plano_total.kwh_mes > 0.01 ? el("p", { class: "eco-nota" }, "O total é menor que a soma porque banho mais curto e posição verão, juntos, economizam menos que separados.") : null,
    el("div", { class: "eco-conferencia" }, el("h3", {}, "Deu certo?"), ...conferencia(d.verificacao, !d.medidores)),
    ...notas].filter(Boolean));
}

/* ---------- o resumo no painel */

async function atualizarEconomiaPainel() {
  const bloco = $("#bloco-economia");
  if ($(".grade-painel").hidden) return;
  const r = await api("GET", "/api/economia/resumo");
  const tarifa = estado.eu.tarifa_kwh ?? estado.eu.tarifa_padrao;
  let titulo, texto, botao;
  if (!r.acoes) {
    titulo = "E se eu…?";
    const f = estado.fantasma;
    if (f && f.disponivel && !f.estimado && f.watts >= 4) {
      const w = Math.round(f.watts / 2);
      const kwh = MWEconomia.kwhMes("fantasma", { watts_cortados: w, horas_dia: 20 });
      texto = `Desligar metade do que fica em espera (${fmt.num(w)} W) por 20 horas ao dia economizaria ${tarifa != null ? `cerca de ${fmt.reais(kwh * 12 * tarifa)}` : fmt.kwh(kwh * 12)} por ano. No simulador dá para testar também banho, chuveiro e geladeira.`;
    } else {
      texto = "Banho mais curto, chuveiro na posição verão, aparelhos em espera: veja quanto cada hábito pesa na conta, com os números da sua casa.";
    }
    botao = "Abrir o simulador";
  } else {
    titulo = "Plano de economia";
    const estimativa = r.reais_mes != null ? fmt.reais(r.reais_mes) : fmt.kwh(r.kwh_mes);
    texto = `${r.acoes} ${r.acoes === 1 ? "ação" : "ações"} no plano, estimativa de ${estimativa} por mês.${r.verificacao ? ` ${textoConferencia(r.verificacao, true)}` : ""}`;
    botao = "Ver o plano";
  }
  bloco.toggleAttribute("data-plano", !!r.acoes);
  bloco.replaceChildren(el("div", { class: "eco-painel-texto" }, el("h2", {}, titulo), el("p", {}, texto)),
    el("a", { class: "botao secundario", href: "#economia" }, botao));
  bloco.hidden = false;
}

/* ------------------------------------------------------------------ app instalado (PWA) e notificações */

const app = {
  pedido: null,       // o pedido de instalação do Chrome e do Edge, guardado para o botão "Instalar"
  ios: /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1),
  instalado: matchMedia("(display-mode: standalone)").matches || navigator.standalone === true,   // rodando como app
  jaInstalou: false,  // instalou agora, por esta aba
  ativo: null,        // o service worker pronto: sem ele não dá para ligar as notificações
  // Aberto pelo app da Play Store? Ele abre com ?app=android (android/gerar-projeto.mjs), e o Android
  // informa a origem android-app://. Lá dentro, a assinatura não é vendida: a Play exige o sistema de
  // cobrança dela para assinaturas vendidas dentro de apps.
  daPlayStore: (() => {
    let sim = /[?&]app=android(&|$)/.test(location.search) || document.referrer.startsWith("android-app://");
    try {
      if (sim) sessionStorage.setItem("mw-play", "1");
      sim = sim || sessionStorage.getItem("mw-play") === "1";
    } catch { /* sem armazenamento: vale o que a URL diz */ }
    return sim;
  })(),
};

const push = {
  suportado: /^https?:$/.test(location.protocol) && window.isSecureContext && "serviceWorker" in navigator
    && "PushManager" in window && "Notification" in window,
  servidor: null,     // GET /api/notificacoes: se o servidor manda notificações, a chave dele e os aparelhos ligados
  inscricao: null,    // a inscrição deste navegador, quando vale para esta conta
  id: null,           // o número dela no servidor (para marcar "Este aparelho")
};

const b64u = (bytes) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const deB64u = (texto) => Uint8Array.from(atob(texto.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));

/* Preferência só deste navegador, como fechar um convite. Pode não haver armazenamento (aba anônima). */
function lembrar(chave, valor) {
  try {
    if (valor === undefined) return localStorage.getItem(chave);
    localStorage.setItem(chave, valor);
  } catch { /* segue sem lembrar */ }
  return null;
}

function registrarServiceWorker() {
  if (!/^https?:$/.test(location.protocol) || !("serviceWorker" in navigator)) return;
  let tinhaDono = !!navigator.serviceWorker.controller;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    // Na primeira instalação nada muda na tela. Depois, é versão nova: a pessoa escolhe a hora de recarregar.
    if (tinhaDono) $("#aviso-versao").hidden = false;
    tinhaDono = true;
  });
  navigator.serviceWorker.addEventListener("message", (ev) => {
    const d = ev.data || {};
    if (d.tipo === "abrir" && typeof d.rota === "string" && /^[a-z0-9-]+$/.test(d.rota)) ir(d.rota);
    if (d.tipo === "aviso" && estado.eu && Number.isInteger(d.nao_lidos)) {
      estado.eu.avisos_nao_lidos = d.nao_lidos;
      atualizarSino(d.nao_lidos);
      if (estado.rota === "avisos") mostrar();
    }
  });
  navigator.serviceWorker.register("/sw.js").catch(() => {});
  navigator.serviceWorker.ready.then((r) => { app.ativo = r; }).catch(() => {});
  // No celular, o app fica aberto por dias: ao voltar para ele, confere se saiu versão nova.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && app.ativo) app.ativo.update().catch(() => {});
  });
}

function ligarAppInstalado() {
  addEventListener("beforeinstallprompt", (ev) => {
    ev.preventDefault();          // o convite aparece no painel e na Conta, na hora que a pessoa escolher
    app.pedido = ev;
    atualizarConvites();
  });
  addEventListener("appinstalled", () => {
    app.pedido = null;
    app.jaInstalou = true;
    atualizarConvites();
    avisar("Pronto: o MeterWatt foi instalado. Abra pelo ícone novo.");
  });
  $("#conta-instalar").addEventListener("click", instalarApp);
  $("#botao-atualizar").addEventListener("click", () => location.reload());
  $("#botao-reconectar").addEventListener("click", reconectar);
  addEventListener("offline", () => { $("#faixa-offline").hidden = false; });
  addEventListener("online", () => {
    $("#faixa-offline").hidden = true;
    if (estado.semConexao) reconectar();
    else if (estado.eu) mostrar();     // os números voltam na hora, sem esperar o próximo ciclo
  });
}

function podeInstalar() {
  return !app.instalado && !app.jaInstalou && (!!app.pedido || app.ios);
}

async function instalarApp() {
  if (!app.pedido) {                // iPhone e iPad: não há botão do sistema, só o passo a passo
    $("#dialogo-instalar").showModal();
    return;
  }
  const pedido = app.pedido;
  app.pedido = null;                // cada pedido só serve uma vez
  try {
    await pedido.prompt();
    const escolha = await pedido.userChoice;
    if (escolha.outcome !== "accepted") avisar("Tudo bem. Dá para instalar depois pelo menu do navegador.");
  } catch { /* o navegador não deixou mostrar de novo */ }
  atualizarConvites();
}

function atualizarConvites() {
  $("#conta-instalar").hidden = !podeInstalar();
  if (estado.rota === "painel") desenharConviteApp();
}

function redesenharNotificacoes() {
  if (estado.rota === "avisos") desenharNotificacoes();
  else if (estado.rota === "painel") desenharConviteApp();
  else if (estado.rota === "conta") telaConta();
}

/* No painel: um convite só, para instalar ou para ligar as notificações. "Agora não" vale para este navegador. */
function desenharConviteApp() {
  const bloco = $("#bloco-app");
  const instalar = podeInstalar();
  const notificar = push.suportado && !!push.servidor && push.servidor.disponivel && !push.inscricao
    && Notification.permission === "default";
  if ($(".grade-painel").hidden || lembrar("mw-convite-app") || (!instalar && !notificar)) {
    bloco.hidden = true;
    return;
  }
  const texto = instalar
    ? "Instale o MeterWatt: ele ganha um ícone próprio, abre em tela cheia e avisa por notificação mesmo fechado."
    : "Ligue as notificações para saber na hora quando um medidor ficar sem sinal, a meta apertar ou o consumo fugir do normal.";
  bloco.replaceChildren(
    el("div", { class: "eco-painel-texto" }, el("h2", {}, instalar ? "Instale o app" : "Avisos na hora"), el("p", {}, texto)),
    el("div", { class: "botoes" },
      instalar
        ? el("button", { type: "button", class: "primario", onclick: instalarApp }, app.pedido ? "Instalar o app" : "Como instalar")
        : el("button", { type: "button", class: "primario", onclick: (ev) => ligarNotificacoes(ev.currentTarget) }, "Ligar notificações"),
      el("button", { type: "button", class: "discreto", onclick: () => { lembrar("mw-convite-app", "1"); bloco.hidden = true; } }, "Agora não")));
  bloco.hidden = false;
}

async function carregarNotificacoes() {
  if (!estado.eu) return;
  try {
    push.servidor = await api("GET", "/api/notificacoes");
  } catch { /* sem conexão ou sem permissão: fica como estava */ }
}

function mesmaChave(inscricao, chave) {
  const atual = inscricao.options && inscricao.options.applicationServerKey;
  return !atual || !chave || b64u(new Uint8Array(atual)) === chave;
}

/* Ao abrir o app, com a conta carregada: a inscrição que este navegador tem ainda vale para esta conta?
 * Não vale quando o aparelho foi removido em outro lugar, quando a senha foi trocada, quando a chave
 * do servidor mudou ou quando a inscrição era de outra conta. Nesses casos, desfaz aqui também. */
async function conferirNotificacoes() {
  push.inscricao = null;
  push.id = null;
  if (!push.suportado || !estado.eu || window.MW_DEMONSTRACAO) return;
  await carregarNotificacoes();
  const registro = await Promise.race([navigator.serviceWorker.ready, new Promise((r) => setTimeout(() => r(null), 5000))]);
  if (!registro) return;
  const inscricao = await registro.pushManager.getSubscription().catch(() => null);
  if (!inscricao) return;
  const s = push.servidor;
  if (s && (!s.disponivel || !mesmaChave(inscricao, s.chave))) {
    await api("POST", "/api/notificacoes/desligar", { endpoint: inscricao.endpoint }).catch(() => {});
    await inscricao.unsubscribe().catch(() => {});
    return;
  }
  try {
    const r = await api("POST", "/api/notificacoes/conferir", { ...inscricao.toJSON(), instalado: app.instalado });
    if (r.ligado) {
      push.inscricao = inscricao;
      push.id = r.id;
    } else {
      await inscricao.unsubscribe().catch(() => {});
    }
  } catch { /* sem conexão agora: confere na próxima vez que o app abrir */ }
}

/* Chamada direto do toque no botão: o Safari só pede a permissão dentro do gesto, então nada de
 * esperar outra coisa antes do subscribe(). */
async function ligarNotificacoes(botao) {
  const s = push.servidor;
  if (!push.suportado || !s || !s.disponivel) {
    avisar("As notificações não estão disponíveis neste aparelho.", true);
    return;
  }
  if (!app.ativo) {
    avisar("O app ainda está se preparando. Espere alguns segundos e tente de novo.", true);
    return;
  }
  if (botao) botao.disabled = true;
  try {
    const inscricao = await app.ativo.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: deB64u(s.chave) });
    const r = await api("PUT", "/api/notificacoes/inscricao", { ...inscricao.toJSON(), instalado: app.instalado });
    push.inscricao = inscricao;
    push.id = r.id;
    push.servidor = { ...s, aparelhos: r.aparelhos };
    avisar("Notificações ligadas neste aparelho.");
  } catch (e) {
    if (e instanceof ErroApi) avisar(e.message, true);
    else if (Notification.permission === "denied") avisar("As notificações ficaram bloqueadas. Dá para liberar nas permissões do site.", true);
    else if (e && e.name === "InvalidStateError") {
      // Inscrição antiga, feita com outra chave do servidor: desfaz para a próxima tentativa dar certo.
      const velha = await app.ativo.pushManager.getSubscription().catch(() => null);
      if (velha) await velha.unsubscribe().catch(() => {});
      avisar("Era preciso refazer a inscrição deste aparelho. Toque de novo em Ligar notificações.", true);
    } else avisar("Não deu para ligar as notificações agora. Tente de novo em instantes.", true);
  } finally {
    if (botao) botao.disabled = false;
    redesenharNotificacoes();
  }
}

async function desligarNotificacoes(botao) {
  const inscricao = push.inscricao;
  if (!inscricao) return;
  if (botao) botao.disabled = true;
  try {
    const r = await api("POST", "/api/notificacoes/desligar", { endpoint: inscricao.endpoint });
    if (push.servidor) push.servidor = { ...push.servidor, aparelhos: r.aparelhos };
    await inscricao.unsubscribe().catch(() => {});
    push.inscricao = null;
    push.id = null;
    avisar("Notificações desligadas neste aparelho.");
  } catch (e) {
    avisar(e.message, true);
  } finally {
    if (botao) botao.disabled = false;
    redesenharNotificacoes();
  }
}

async function testarNotificacao(botao) {
  if (!push.inscricao) return;
  botao.disabled = true;
  try {
    await api("POST", "/api/notificacoes/teste", { endpoint: push.inscricao.endpoint });
    avisar("Teste enviado. A notificação chega em alguns segundos.");
  } catch (e) {
    avisar(e.message, true);
  } finally {
    botao.disabled = false;
  }
}

async function removerAparelho(a) {
  const ok = await confirmar({ titulo: "Desligar as notificações nesse aparelho?", semSenha: true, botao: "Desligar",
    texto: `${a.aparelho || "Esse aparelho"} deixa de receber os avisos do MeterWatt. Para voltar, é só ligar de novo por lá.` });
  if (!ok) return;
  try {
    const r = await api("DELETE", `/api/notificacoes/aparelhos/${a.id}`);
    push.servidor = { ...push.servidor, aparelhos: r.aparelhos };
    avisar("Pronto: esse aparelho não recebe mais notificações.");
  } catch (e) {
    avisar(e.message, true);
  }
  desenharNotificacoes();
}

/* Sair da conta neste aparelho desliga as notificações dela aqui (o servidor também apaga, por garantia). */
async function desligarNotificacoesAoSair() {
  const inscricao = push.inscricao;
  push.inscricao = null;
  push.id = null;
  push.servidor = null;
  if (!inscricao) return;
  await api("POST", "/api/notificacoes/desligar", { endpoint: inscricao.endpoint }).catch(() => {});
  await inscricao.unsubscribe().catch(() => {});
}

/* ------------------------------------------------------------------ demonstração (casa simulada) */

/* "Ver o app funcionando": o app inteiro com uma casa inventada, sem conta e sem o medidor. As
 * respostas vêm de demonstracao.js, carregado só aqui, que responde às chamadas /api/ dentro do
 * navegador. Serve a quem ainda não tem o medidor, e a quem revisa e testa o app na Play Store. */
const DEMONSTRACAO = "mw-demonstracao";

function modoDemonstracao() {
  try { return sessionStorage.getItem(DEMONSTRACAO) === "1"; } catch { return false; }
}

function entrarNaDemonstracao() {
  try {
    sessionStorage.setItem(DEMONSTRACAO, "1");
  } catch {
    avisar("Este navegador não deixou abrir a demonstração (o armazenamento do site está bloqueado).", true);
    return;
  }
  location.hash = "painel";
  location.reload();
}

function sairDaDemonstracao() {
  try { sessionStorage.removeItem(DEMONSTRACAO); } catch { /* recarregar já basta */ }
  location.hash = "entrar";
  location.reload();
}

function carregarScript(endereco) {
  return new Promise((pronto, falhou) => document.head.append(el("script", { src: endereco, onload: pronto, onerror: falhou })));
}

async function prepararDemonstracao() {
  if (modoDemonstracao() && !window.MW_DEMONSTRACAO) {
    try {
      await carregarScript("/estatico/demonstracao.js");
    } catch {
      try { sessionStorage.removeItem(DEMONSTRACAO); } catch { /* segue no app de verdade */ }
      avisar("Não deu para abrir a demonstração agora. Confira a conexão e tente de novo.", true);
    }
  }
  const ligada = !!window.MW_DEMONSTRACAO;
  $("#faixa-demonstracao").hidden = !ligada;
  $("#sair-demonstracao").hidden = !!window.MW_DEMONSTRACAO_ARQUIVO;     // no arquivo único, não há "fora"
  $$(".convite-demo, .convite-demo-inicio").forEach((e) => { e.hidden = ligada; });
}

/* ------------------------------------------------------------------ sem conexão */

async function entrarNoApp() {
  estado.semConexao = false;
  try {
    estado.info = await api("GET", "/api/publico/info");
    $("#aviso-teste").hidden = !estado.info.email_de_teste;
    $("#link-plano").hidden = !estado.info.plano.disponivel;
  } catch { /* segue sem as informações públicas */ }
  try {
    await carregarEu();
  } catch (e) {
    estado.eu = null;
    if (e.status === 0) estado.semConexao = true;          // não deu nem para saber se a conta está aberta
    else if (e.codigo === "etapa_aguardando_2fa" && !location.hash) location.hash = "2fa";
  }
  await mostrar();
  if (estado.eu) conferirNotificacoes().then(redesenharNotificacoes).catch(() => {});
}

let reconectando = false;
async function reconectar() {
  if (reconectando) return;
  reconectando = true;
  const botao = $("#botao-reconectar");
  botao.disabled = true;
  botao.textContent = "Tentando…";
  try {
    await entrarNoApp();
  } finally {
    reconectando = false;
    botao.disabled = false;
    botao.textContent = "Tentar de novo";
  }
}

/* ------------------------------------------------------------------ conta (no celular) */

function telaConta() {
  const eu = estado.eu;
  $("#conta-quem").textContent = `Você entrou como ${eu.nome} (${eu.email}).`;
  $("#conta-instalar").hidden = !podeInstalar();
  const naoLidos = eu.avisos_nao_lidos ? `${eu.avisos_nao_lidos} não lido(s)` : "nenhum novo";
  $("#conta-avisos").textContent = push.suportado && push.servidor && push.servidor.disponivel
    ? `${naoLidos}, notificações ${push.inscricao ? "ligadas" : "desligadas"}` : naoLidos;
  const cobranca = estado.info && estado.info.plano;
  $("#conta-plano-atual").textContent = cobranca && !cobranca.disponivel ? "sem cobrança por enquanto" : eu.plano === "casa" ? "plano Casa" : "plano grátis";
}

/* ------------------------------------------------------------------ plano e cobrança */

async function telaPlano() {
  let r = await api("GET", "/api/plano");
  if (r.assinatura && r.assinatura.status === "pendente") r = await api("POST", "/api/plano/atualizar");   // voltando da página de pagamento
  desenharPlano(r);
}

function desenharPlano(r) {
  $("#plano-teste").hidden = !r.modo_teste || app.daPlayStore;
  estado.eu.plano = r.plano;
  const caixa = $("#plano-conteudo");
  if (!r.disponivel) {
    caixa.replaceChildren(el("div", { class: "vazio" }, el("h2", {}, "Sem cobrança por enquanto"),
      el("p", {}, `O MeterWatt está em fase de testes e a assinatura ainda não foi ligada. Cada conta pode ter até ${r.limite_pago} medidores.`)));
    return;
  }
  const a = r.assinatura;
  const ativa = r.plano === "casa";
  const medidores = (n) => `${n} medidor${n === 1 ? "" : "es"}`;
  const acao = () => {
    if (a && a.status === "ativa") {
      return [el("p", { class: "valor-sub" }, a.proxima_cobranca ? `Próxima cobrança em ${fmt.quando(a.proxima_cobranca).split(",")[0]}.` : "Assinatura ativa."),
        el("button", { class: "secundario perigo", type: "button", onclick: cancelarPlano }, "Cancelar assinatura")];
    }
    if (a && a.status === "cancelada" && ativa) {
      return [el("p", { class: "valor-sub" }, `Assinatura cancelada. O plano Casa continua valendo até ${fmt.quando(a.valida_ate).split(",")[0]}, sem novas cobranças.`)];
    }
    if (app.daPlayStore) return [el("p", { class: "valor-sub" }, "A assinatura não está disponível no app da Play Store.")];
    return [a && a.status === "pendente" ? el("p", { class: "valor-sub" }, "Há um pagamento esperando você terminar.") : null,
      el("button", { class: "primario", type: "button", onclick: assinarPlano }, a && a.status === "pendente" ? "Continuar o pagamento" : "Assinar o plano Casa")];
  };
  caixa.replaceChildren(el("div", { class: "planos" },
    el("article", { class: `bloco plano${ativa ? "" : " vigente"}` },
      el("h2", {}, "Grátis"), el("p", { class: "valor-grande" }, "R$ 0"),
      el("ul", {}, el("li", {}, medidores(r.limite_gratis)), el("li", {}, "Painel, meta do mês, consumo fantasma, avisos e o simulador E se eu…?"), el("li", {}, "Privacidade completa: cópia, exclusão e duas etapas")),
      ativa ? null : el("p", { class: "atual-marca" }, "Seu plano atual")),
    el("article", { class: `bloco plano destaque${ativa ? " vigente" : ""}` },
      el("h2", {}, "Casa"), app.daPlayStore ? null : el("p", { class: "valor-grande" }, `${fmt.reais(r.preco_mensal)} `, el("small", {}, "por mês")),
      el("ul", {}, el("li", {}, `Até ${medidores(r.limite_pago)}: casa, edícula, ar-condicionado em circuito separado`), el("li", {}, "Tudo do plano grátis"), el("li", {}, "Cancele quando quiser, aqui mesmo")),
      ativa ? el("p", { class: "atual-marca" }, "Seu plano atual") : null,
      ...acao())),
  el("p", { class: "dica" }, `Você tem ${medidores(r.medidores)} na conta. `
    + (app.daPlayStore ? "" : "O pagamento é feito na página do Mercado Pago: o MeterWatt nunca vê o número do cartão. ")
    + "Se cancelar, nada é apagado: os medidores e o histórico continuam, só não dá para parear além do limite do plano grátis."));
}

async function assinarPlano(ev) {
  ev.target.disabled = true;
  try {
    const r = await api("POST", "/api/plano/assinar");
    const destino = new URL(r.link, location.href);
    if (destino.origin === location.origin) location.hash = destino.hash;      // página de pagamento de teste
    else if (destino.protocol === "https:") location.href = destino.href;      // página do Mercado Pago
    else throw new Error("O endereço de pagamento veio em formato inesperado.");
  } catch (e) {
    avisar(e.message, true);
    ev.target.disabled = false;
  }
}

async function cancelarPlano() {
  const ok = await confirmar({ titulo: "Cancelar a assinatura?", semSenha: true, botao: "Cancelar assinatura",
    texto: "Não haverá novas cobranças. O plano Casa continua até o fim do período já pago. Nenhum medidor e nenhum dado são apagados." });
  if (!ok) return;
  try {
    desenharPlano(await api("POST", "/api/plano/cancelar"));
    avisar("Assinatura cancelada.");
  } catch (e) { avisar(e.message, true); }
}

function telaPagamentoTeste(referencia) {
  estado.referenciaTeste = referencia;
  $("#teste-valor").textContent = estado.info ? `${fmt.reais(estado.info.plano.preco_mensal)} por mês (de mentira)` : "";
}

function ligarPagamentoTeste() {
  const decidir = async (aprovado) => {
    try {
      const r = await api("POST", "/api/plano/pagamento-teste", { referencia: estado.referenciaTeste, aprovado });
      avisar(r.plano === "casa" ? "Pagamento de teste aprovado: plano Casa ativo." : "Pagamento de teste recusado.");
      ir("plano");
    } catch (e) { avisar(e.message, true); ir("plano"); }
  };
  $("#teste-aprovar").addEventListener("click", () => decidir(true));
  $("#teste-recusar").addEventListener("click", () => decidir(false));
}

/* ------------------------------------------------------------------ página inicial */

/* Valores típicos de consumo em espera, em watts. São referências arredondadas (etiquetas de
 * fabricantes e medições públicas); cada casa é diferente, e é por isso que a página deixa corrigir. */
const APARELHOS_EM_ESPERA = [
  ["Roteador Wi-Fi e modem (sempre ligados)", 10, 1],
  ["TV na tomada, desligada no controle", 1, 1],
  ["Decodificador de TV ou TV box", 8, 0],
  ["Videogame em modo de espera", 8, 0],
  ["Micro-ondas com relógio", 2, 1],
  ["Computador em suspensão, com monitor", 4, 0],
  ["Caixa de som, soundbar ou home theater", 4, 0],
  ["Assistente de voz ou caixinha inteligente", 3, 0],
  ["Ar-condicionado split em espera", 2, 0],
  ["Carregador na tomada, sem o celular", 0.3, 2],
  ["Impressora ligada sem imprimir", 3, 0],
  ["Cafeteira, air fryer ou forno com painel aceso", 1, 0],
];

const demo = (() => {
  let timer = 0, valor = 412;
  const visorDemo = criarVisor($("#visor-demo"));
  const mostrarDemo = (texto) => visorDemo.mostrar(texto, "W");
  const roteiro = [[412, "geladeira e luzes"], [1712, "micro-ondas ligou"], [1698, "micro-ondas ligou"], [398, "micro-ondas desligou"], [5810, "alguém entrou no banho"],
    [5795, "alguém entrou no banho"], [405, "banho acabou"], [262, "geladeira desligou"], [74, "madrugada: só o consumo fantasma"], [71, "madrugada: só o consumo fantasma"]];
  let passo = 0;
  function andar() {
    const [w, legenda] = roteiro[passo % roteiro.length];
    passo++;
    valor = w + Math.round((Math.random() - 0.5) * 6);
    mostrarDemo(String(valor));
    $("#demo-legenda").textContent = `${legenda} (demonstração com dados simulados)`;
  }
  return {
    ligar(sim) {
      clearInterval(timer);
      timer = 0;
      if (!sim) return;
      andar();
      timer = setInterval(() => { if (!document.hidden) andar(); }, 2600);
    },
  };
})();

function calcular() {
  let watts = 0;
  $$("#calc-lista .calc-item").forEach((item) => {
    watts += Number(item.dataset.qtd) * (numeroDigitado($("input", item).value) || 0);
  });
  const tarifa = numeroDigitado($("#calc-tarifa").value);
  const kwhMes = watts * 24 * 365 / 12 / 1000;          // mês médio: um ano dividido por 12
  const valido = Number.isFinite(tarifa) && tarifa > 0 && tarifa <= 10;
  $("#calc-watts").textContent = `${fmt.num(watts, watts % 1 ? 1 : 0)} W`;
  $("#calc-ano").textContent = valido ? `${fmt.reais(kwhMes * 12 * tarifa)} por ano` : "Informe a tarifa";
  $("#calc-detalhe").textContent = `${fmt.num(kwhMes, 1)} kWh por mês${valido ? `, ou ${fmt.reais(kwhMes * tarifa)} em cada conta de luz` : ""}.`;
  estado.calculo = { watts, ano: valido ? kwhMes * 12 * tarifa : null };
}

function ligarInicio() {
  $("#calc-lista").replaceChildren(...APARELHOS_EM_ESPERA.map(([nome, w, qtd]) => {
    const conta = el("output", {}, String(qtd));
    const item = el("div", { class: "calc-item", "data-qtd": String(qtd) });
    const mudar = (d) => {
      const n = Math.max(0, Math.min(9, Number(item.dataset.qtd) + d));
      item.dataset.qtd = String(n);
      conta.textContent = String(n);
      calcular();
    };
    item.append(el("span", { class: "calc-nome" }, nome),
      el("label", { class: "calc-w" }, el("input", { inputmode: "decimal", value: String(w).replace(".", ","), "aria-label": `Watts em espera: ${nome}`, oninput: calcular }), " W"),
      el("div", { class: "calc-qtd" },
        el("button", { type: "button", class: "discreto", "aria-label": `Menos um: ${nome}`, onclick: () => mudar(-1) }, "−"), conta,
        el("button", { type: "button", class: "discreto", "aria-label": `Mais um: ${nome}`, onclick: () => mudar(1) }, "+")));
    return item;
  }));
  $("#calc-tarifa").addEventListener("input", calcular);
  calcular();
  $$("[data-rolar]").forEach((a) => a.addEventListener("click", (ev) => {
    ev.preventDefault();
    const alvo = document.getElementById(a.dataset.rolar);
    alvo.scrollIntoView({ behavior: semMovimento ? "auto" : "smooth", block: "start" });
    if (a.dataset.rolar === "fila") setTimeout(() => $("#form-fila [name=email]").focus({ preventScroll: true }), 400);
  }));
  $("#calc-compartilhar").addEventListener("click", async () => {
    const c = estado.calculo;
    const texto = `Fiz a conta do consumo fantasma lá de casa: ${fmt.num(c.watts, c.watts % 1 ? 1 : 0)} W ligados o dia inteiro sem ninguém usar`
      + `${c.ano != null ? `, uns ${fmt.reais(c.ano)} por ano` : ""}. Faça a sua:`;
    const url = `${location.origin}/?o=calculadora`;
    try {
      if (navigator.share) await navigator.share({ title: "Consumo fantasma", text: texto, url });
      else { await navigator.clipboard.writeText(`${texto} ${url}`); avisar("Texto copiado. É só colar onde quiser."); }
    } catch { /* a pessoa fechou a janela de compartilhar */ }
  });
  formulario($("#form-fila"), async (d, form) => {
    const origem = new URLSearchParams(location.search).get("o");
    const r = await api("POST", "/api/espera", { email: d.email, aceite: !!d.aceite, convite: estado.convite || null, origem });
    form.reset();
    $("#fila-sucesso").textContent = r.mensagem;
    $("#fila-sucesso").hidden = false;
  });
  $("#botao-sair-fila").addEventListener("click", async () => {
    try {
      const r = await api("POST", "/api/espera/sair", { token: estado.tokenFila });
      $("#sair-estado").replaceChildren(el("p", { class: "texto-grande" }, r.mensagem), el("p", {}, el("a", { href: "#inicio" }, "Voltar ao início")));
    } catch (e) {
      $("#sair-estado").replaceChildren(el("p", { class: "texto-grande" }, e.message), el("p", {}, el("a", { href: "#inicio" }, "Voltar ao início")));
    }
  });
}

function telaInicio() {
  $("#inicio-entrar").textContent = estado.eu ? "Abrir o painel" : "Entrar";
  $("#inicio-entrar").setAttribute("href", estado.eu ? "#painel" : "#entrar");
  $("#convite-aviso").hidden = !estado.convite;
  const n = estado.info ? estado.info.na_lista_de_espera : 0;
  $("#fila-total").hidden = n < 10;                       // fila vazia não ajuda ninguém a decidir
  $("#fila-total").textContent = `${n} pessoas já confirmaram o lugar na fila.`;
}

async function telaFila(token) {
  const titulo = $("#fila-estado-titulo"), caixa = $("#fila-estado");
  titulo.textContent = "Confirmando o seu lugar…";
  caixa.replaceChildren();
  try {
    const r = await api("POST", "/api/espera/confirmar", { token });
    titulo.textContent = `Você é o número ${r.posicao} da fila`;
    const campo = el("input", { value: r.link_de_convite, readonly: true, "aria-label": "Seu link de convite" });
    caixa.replaceChildren(
      el("p", { class: "texto-grande" }, `E-mail confirmado. São ${r.total} pessoa(s) na lista${r.indicados ? `, e ${r.indicados} veio(vieram) pelo seu convite` : ""}.`),
      el("p", { class: "texto" }, "Quer subir na fila? Cada pessoa que entrar pelo seu link e confirmar o e-mail passa você para a frente de quem convidou menos."),
      el("div", { class: "renomear" }, campo, el("button", { class: "primario", type: "button", onclick: async () => {
        try { await navigator.clipboard.writeText(r.link_de_convite); avisar("Link copiado."); } catch { campo.select(); }
      } }, "Copiar link")),
      el("p", { class: "dica" }, "Guarde o e-mail que você recebeu: o link dele mostra a sua posição atualizada e também serve para sair da lista."),
      el("p", {}, el("a", { href: "#inicio" }, "Voltar ao início")));
  } catch (e) {
    titulo.textContent = "Não deu para confirmar";
    caixa.replaceChildren(el("p", { class: "texto-grande" }, e.message), el("p", {}, el("a", { href: "#inicio" }, "Voltar ao início")));
  }
}

/* ------------------------------------------------------------------ diálogos */

function fecharDialogos() {
  $$("dialog[open]").forEach((d) => d.close());
}

/* Pede confirmação (e, se for o caso, senha e código). Devolve os dados ou null. */
function confirmar({ titulo, texto, botao, semSenha = false, pedirCodigo = false, frase = false, enviar = null }) {
  const d = $("#dialogo-confirmar");
  const form = $("#form-confirmar");
  form.reset();
  $(".erro", form).textContent = "";
  $("#confirmar-titulo").textContent = titulo;
  $("#confirmar-texto").textContent = texto;
  $("#confirmar-botao").textContent = botao;
  $("#confirmar-botao").classList.toggle("perigo", /Excluir|Apagar|Remover|Desativar|Desligar|Parar/.test(botao));
  $('[data-campo="senha"]', form).hidden = semSenha;
  $('[data-campo="codigo"]', form).hidden = !pedirCodigo;
  $('[data-campo="frase"]', form).hidden = !frase;
  d.showModal();
  (frase ? form.elements.frase : semSenha ? $("#confirmar-botao") : form.elements.senha).focus();
  return new Promise((resolver) => {
    let resultado = null;
    const aoEnviar = async (ev) => {
      ev.preventDefault();
      const erro = $(".erro", form);
      erro.textContent = "";
      if (frase && form.elements.frase.value.trim().toUpperCase() !== "EXCLUIR") { erro.textContent = "Digite EXCLUIR para confirmar."; return; }
      const dados = semSenha ? {} : { senha: form.elements.senha.value, codigo: pedirCodigo ? form.elements.codigo.value : undefined };
      if (!semSenha && !dados.senha) { erro.textContent = "Digite a sua senha."; return; }
      if (pedirCodigo && !dados.codigo) { erro.textContent = "Digite o código do autenticador."; return; }
      $("#confirmar-botao").disabled = true;
      try {
        if (enviar) await enviar(dados);
        resultado = dados;
        d.close();
      } catch (e) {
        erro.textContent = e.message;
      } finally {
        $("#confirmar-botao").disabled = false;
      }
    };
    const cancelar = () => d.close();
    form.addEventListener("submit", aoEnviar);
    $('[data-acao="cancelar"]', form).addEventListener("click", cancelar);
    d.addEventListener("close", () => {
      form.removeEventListener("submit", aoEnviar);
      $('[data-acao="cancelar"]', form).removeEventListener("click", cancelar);
      form.reset();
      resolver(resultado);
    }, { once: true });
  });
}

/* ------------------------------------------------------------------ partículas (telas de entrada) */

const particulas = (() => {
  const canvas = $("#particulas");
  const c = canvas.getContext("2d");
  let pontos = [], ligado = false, quadro = 0, larg = 0, alt = 0;
  const mouse = { x: -9999, y: -9999 };
  const cores = ["255,176,32", "54,227,255", "255,79,216", "167,139,250"];

  function medir() {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    larg = innerWidth; alt = innerHeight;
    canvas.width = larg * dpr; canvas.height = alt * dpr;
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    const n = Math.round(Math.min(110, (larg * alt) / 14000));
    pontos = Array.from({ length: n }, () => ({
      x: Math.random() * larg, y: Math.random() * alt, vx: (Math.random() - .5) * .3, vy: (Math.random() - .5) * .3,
      r: Math.random() * 1.6 + .6, cor: cores[Math.floor(Math.random() * cores.length)],
    }));
  }

  function passo() {
    c.clearRect(0, 0, larg, alt);
    for (const p of pontos) {
      const dx = p.x - mouse.x, dy = p.y - mouse.y, d2 = dx * dx + dy * dy;
      if (d2 < 22000) {   // o ponteiro empurra as partículas, como um campo
        const f = (22000 - d2) / 22000 * .9;
        const d = Math.sqrt(d2) || 1;
        p.vx += (dx / d) * f; p.vy += (dy / d) * f;
      }
      p.vx *= .96; p.vy *= .96;
      p.vx += (Math.random() - .5) * .02; p.vy += (Math.random() - .5) * .02;
      p.x += p.vx; p.y += p.vy;
      if (p.x < -10) p.x = larg + 10; if (p.x > larg + 10) p.x = -10;
      if (p.y < -10) p.y = alt + 10; if (p.y > alt + 10) p.y = -10;
    }
    for (let i = 0; i < pontos.length; i++) {
      for (let j = i + 1; j < pontos.length; j++) {
        const a = pontos[i], b = pontos[j], dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy;
        if (d2 < 9000) {
          c.strokeStyle = `rgba(${a.cor},${(1 - d2 / 9000) * .18})`;
          c.lineWidth = 1;
          c.beginPath(); c.moveTo(a.x, a.y); c.lineTo(b.x, b.y); c.stroke();
        }
      }
    }
    for (const p of pontos) {
      c.fillStyle = `rgba(${p.cor},.85)`;
      c.beginPath(); c.arc(p.x, p.y, p.r, 0, Math.PI * 2); c.fill();
    }
    if (ligado) quadro = requestAnimationFrame(passo);
  }

  addEventListener("pointermove", (e) => { mouse.x = e.clientX; mouse.y = e.clientY; }, { passive: true });
  addEventListener("pointerleave", () => { mouse.x = mouse.y = -9999; });
  addEventListener("resize", () => { if (ligado) medir(); });
  document.addEventListener("visibilitychange", () => {
    if (!ligado) return;
    cancelAnimationFrame(quadro);
    if (!document.hidden) quadro = requestAnimationFrame(passo);
  });

  return {
    ligar(sim) {
      if (sim === ligado) return;
      ligado = sim && !semMovimento;
      cancelAnimationFrame(quadro);
      if (ligado) { medir(); quadro = requestAnimationFrame(passo); }
      else c.clearRect(0, 0, larg, alt);
    },
  };
})();

/* ------------------------------------------------------------------ início */

async function iniciar() {
  await prepararDemonstracao();
  $$('[data-acao="demonstracao"]').forEach((b) => b.addEventListener("click", entrarNaDemonstracao));
  $("#sair-demonstracao").addEventListener("click", sairDaDemonstracao);
  ligarFormulariosPublicos();
  ligarPreferencias();
  ligarSeguranca();
  ligarPrivacidade();
  ligarInicio();
  ligarPagamentoTeste();
  $("#botao-parear").addEventListener("click", abrirPareamento);
  $$('[data-acao="sair"]').forEach((b) => b.addEventListener("click", sair));
  addEventListener("hashchange", mostrar);
  ligarAppInstalado();
  registrarServiceWorker();
  await entrarNoApp();
}

document.addEventListener("DOMContentLoaded", iniciar);
