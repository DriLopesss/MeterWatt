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
const ROTAS_APP = new Set(["painel", "medidores", "seguranca", "privacidade"]);
const DOCUMENTOS = new Set(["politica", "termos"]);
const TITULOS = { entrar: "Entrar", cadastro: "Criar conta", painel: "Painel", medidores: "Medidores", seguranca: "Segurança",
  privacidade: "Privacidade", politica: "Política de privacidade", termos: "Termos de uso", "2fa": "Verificação" };

function ir(rota) {
  if (location.hash !== `#${rota}`) location.hash = rota;
  else mostrar();
}

function pararTimers() {
  estado.timers.forEach(clearInterval);
  estado.timers = [];
}

async function mostrar() {
  const [rota, token] = location.hash.replace(/^#\/?/, "").split("/");
  const alvo = rota || (estado.eu ? "painel" : "entrar");
  pararTimers();
  fecharDialogos();

  let area;
  if (DOCUMENTOS.has(alvo)) area = estado.eu ? "app" : "publica";
  else if (ROTAS_PUBLICAS.has(alvo)) area = "publica";
  else if (ROTAS_APP.has(alvo)) area = "app";
  else area = "erro";

  if (area === "app" && !estado.eu) return ir("entrar");
  if (area === "publica" && estado.eu && alvo !== "confirmar" && alvo !== "redefinir") return ir("painel");

  document.body.dataset.tela = alvo;
  document.body.dataset.area = area;
  $("#area-publica").hidden = area !== "publica";
  $("#area-app").hidden = area !== "app";
  $("#area-erro").hidden = area !== "erro";
  document.title = `${TITULOS[alvo] || "Página não encontrada"} | MeterWatt`;
  if (area === "erro") {
    $("#area-erro .botao").setAttribute("href", estado.eu ? "#painel" : "#entrar");
    $("#area-erro .botao").textContent = estado.eu ? "Voltar ao painel" : "Ir para a entrada";
    return;
  }

  const raiz = area === "app" ? $("#area-app") : $("#area-publica");
  $$("section[data-rota]", raiz).forEach((s) => { s.hidden = s.dataset.rota !== alvo; });
  $$(".abas a").forEach((a) => (a.dataset.aba === alvo ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  if (estado.rota !== null && estado.rota !== alvo) (area === "app" ? $("#conteudo-app") : $("#conteudo")).focus({ preventScroll: true });
  estado.rota = alvo;
  window.scrollTo({ top: 0 });
  particulas.ligar(area === "publica");

  const telas = { painel: telaPainel, medidores: telaMedidores, seguranca: telaSeguranca, privacidade: telaPrivacidade,
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
  if (resposta.etapa === "aguardando_2fa") return ir("2fa");
  await carregarEu();
  ir("painel");
}

async function carregarEu() {
  estado.eu = await api("GET", "/api/conta/eu");
  estado.csrf = estado.eu.csrf;
  $("#nome-usuario").textContent = estado.eu.nome;
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
  try { await api("POST", "/api/conta/sair"); } catch { /* sai mesmo assim */ }
  estado.eu = null;
  estado.csrf = null;
  ir("entrar");
}

/* ------------------------------------------------------------------ painel */

const visor = {
  atual: "",
  mostrar(texto, unidade) {
    const caixa = $("#visor-agora");
    const chave = `${texto}|${unidade}`;
    if (chave === this.atual) return;
    const antigo = this.atual.split("|")[0];
    this.atual = chave;
    const larguraIgual = antigo.length === texto.length && caixa.children.length;
    if (!larguraIgual) {
      caixa.replaceChildren(...[...texto].map((c) => el("span", { class: `digito${c === " " ? " vazio" : ""}` }, el("span", {}, c === " " ? "" : c))),
        el("span", { class: "relogio-unidade" }, unidade));
      caixa.setAttribute("aria-label", `${texto.trim()} ${unidade}`);
      return;
    }
    [...texto].forEach((c, i) => {
      if (c === antigo[i]) return;
      const casa = caixa.children[i];
      const velho = casa.firstElementChild;
      const novo = el("span", { class: semMovimento ? "" : "entra" }, c === " " ? "" : c);
      if (semMovimento) { velho.replaceWith(novo); return; }
      velho.className = "sai";
      casa.append(novo);
      setTimeout(() => velho.remove(), 450);
    });
    caixa.lastElementChild.textContent = unidade;
    caixa.setAttribute("aria-label", `${texto.trim()} ${unidade}`);
  },
};

async function telaPainel() {
  await Promise.all([atualizarAgora(), atualizarMeta(), atualizarFantasma(), atualizarDias()]);
  estado.timers.push(setInterval(() => { if (!document.hidden) atualizarAgora().catch(() => {}); }, 2000));
  estado.timers.push(setInterval(() => { if (!document.hidden) { atualizarMeta().catch(() => {}); atualizarFantasma().catch(() => {}); } }, 60000));
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
  const online = r.medidores.filter((m) => m.online && m.potencia != null);
  const total = online.reduce((s, m) => s + m.potencia, 0);
  const unidade = online.length && online.every((m) => m.unidade === "VA") ? "VA" : "W";
  const texto = online.length ? String(Math.round(total)) : "---";
  visor.mostrar(texto, unidade);
  $(".relogio").dataset.estado = online.length ? "ok" : "sem-sinal";
  const custoHora = online.reduce((s, m) => (m.custo_por_hora == null ? s : (s ?? 0) + m.custo_por_hora), null);
  const simulado = online.some((m) => m.simulado);
  $("#agora-sub").textContent = online.length
    ? `${custoHora != null ? `${fmt.reais(custoHora)} por hora neste ritmo` : "Informe a tarifa para ver em reais"}${simulado ? " (dados simulados)" : ""}`
    : "Nenhum medidor enviando leituras agora.";
  const kwh = r.medidores.reduce((s, m) => s + m.kwh_hoje, 0);
  const custo = r.medidores.reduce((s, m) => (m.custo_hoje == null ? s : (s ?? 0) + m.custo_hoje), null);
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

function ligarPreferencias() {
  formulario($("#form-preferencias"), async (d) => {
    const meta = numeroDigitado(d.meta_mensal_reais);
    const tarifa = numeroDigitado(d.tarifa_kwh);
    if (Number.isNaN(meta) || Number.isNaN(tarifa)) throw new Error("Use só números, como 180 ou 0,92.");
    await api("PUT", "/api/conta/preferencias", { meta_mensal_reais: meta, tarifa_kwh: tarifa });
    estado.eu.meta_mensal_reais = meta;
    estado.eu.tarifa_kwh = tarifa;
    avisar("Meta e tarifa salvas.");
    await Promise.all([atualizarAgora(), atualizarMeta(), atualizarFantasma(), atualizarDias()]);
  });
}

/* ------------------------------------------------------------------ medidores */

async function telaMedidores() {
  await desenharMedidores();
  estado.timers.push(setInterval(() => { if (!document.hidden && !$(".renomear:not([hidden])")) desenharMedidores(true).catch(() => {}); }, 3000));
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
  const estadoTexto = !m.online ? `Sem sinal ${m.segundos_desde_mensagem != null ? "desde " + fmt.relativo(new Date(Date.now() - m.segundos_desde_mensagem * 1000)) : ""}`
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

  const cartao = el("article", { class: "medidor", "data-id": m.id },
    el("div", { class: "medidor-topo" },
      el("h2", {}, el("span", { class: "ponto", "aria-hidden": "true" }), m.apelido),
      m.simulado ? el("span", { class: "etiqueta simulado" }, "simulado") : null,
      m.energia_estimada ? el("span", { class: "etiqueta estimado", title: "Medidor só de corrente: potência aparente e consumo estimado" }, "estimado") : null,
      el("span", { class: "medidor-id" }, m.id)),
    el("p", { class: "dica medidor-estado" }),
    el("dl", { class: "medidas" },
      medida("potencia", "Potência", true), medida("tensao", "Tensão"), medida("corrente", "Corrente"), medida("fp", "Fator de potência"),
      medida("frequencia", "Frequência"), medida("hoje", "Hoje"), medida("custo", "Custo hoje"), medida("sinal", "Sinal do Wi-Fi")),
    el("div", { class: "grafico" }, el("div", { class: "periodos", role: "group", "aria-label": "Período do gráfico" }, botoesPeriodo), canvas),
    formNome,
    el("div", { class: "medidor-acoes" },
      el("button", { type: "button", class: "discreto", onclick: () => { formNome.hidden = false; campoNome.focus(); } }, "Renomear"),
      el("button", { type: "button", class: "discreto perigo", onclick: () => removerMedidor(m) }, "Remover")),
  );
  preencherMedidas(cartao, m);
  requestAnimationFrame(() => desenharGrafico(canvas, m.id, periodo));
  return cartao;
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
  try { r = await api("POST", "/api/medidores/codigo"); } catch (e) { avisar(e.message, true); return; }
  const antes = new Set(estado.medidores.map((m) => m.id));
  const fimCodigo = Date.now() + r.validade_s * 1000;
  $("#parear-codigo").textContent = r.codigo;
  const status = $("#parear-status");
  status.className = "parear-status";
  status.textContent = "Esperando o medidor…";
  d.showModal();
  const relogio = setInterval(() => {
    const s = Math.max(0, Math.round((fimCodigo - Date.now()) / 1000));
    $("#parear-tempo").textContent = s ? `Vale por mais ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` : "Este código expirou. Feche e gere outro.";
    if (!s) clearInterval(relogio);
  }, 1000);
  const consulta = setInterval(async () => {
    try {
      const lista = await api("GET", "/api/medidores");
      const novo = lista.medidores.find((m) => !antes.has(m.id));
      if (novo) {
        clearInterval(consulta);
        status.className = "parear-status ok";
        status.textContent = `${novo.apelido} pareado!`;
        setTimeout(() => { if (d.open) d.close(); }, 1600);
      }
    } catch { /* tenta de novo */ }
  }, 3000);
  d.addEventListener("close", () => { clearInterval(relogio); clearInterval(consulta); if (estado.rota === "medidores") desenharMedidores(); }, { once: true });
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
        try {
          desenharAutorizacoes(await api("PUT", `/api/privacidade/${f.id}`, { aceito: novo }));
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
    if (dados) { avisar("Consumo apagado."); telaPrivacidade(); }
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
  $("#confirmar-botao").classList.toggle("perigo", /Excluir|Apagar|Remover|Desativar|Parar/.test(botao));
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
  ligarFormulariosPublicos();
  ligarPreferencias();
  ligarSeguranca();
  ligarPrivacidade();
  $("#botao-parear").addEventListener("click", abrirPareamento);
  $$('[data-acao="sair"]').forEach((b) => b.addEventListener("click", sair));
  addEventListener("hashchange", mostrar);
  try {
    estado.info = await api("GET", "/api/publico/info");
    $("#aviso-teste").hidden = !estado.info.email_de_teste;
  } catch { /* segue sem as informações públicas */ }
  try {
    await carregarEu();
  } catch (e) {
    estado.eu = null;
    if (e.codigo === "etapa_aguardando_2fa" && !location.hash) location.hash = "2fa";
  }
  await mostrar();
}

document.addEventListener("DOMContentLoaded", iniciar);
