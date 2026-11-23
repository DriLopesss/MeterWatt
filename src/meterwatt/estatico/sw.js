/* MeterWatt: o "service worker" do app instalado no celular ou no computador.
 *
 * - Guarda a casca do app (página, estilo, código, fontes e ícones) para ele abrir mesmo sem internet.
 *   A lista de arquivos e a versão vêm do servidor, na rota /sw.js: a versão é um resumo do conteúdo
 *   dos arquivos. Mudou um arquivo, muda a versão, e o navegador troca a casca inteira de uma vez.
 * - Nunca guarda nada de /api/: os dados da conta não ficam gravados no aparelho.
 * - Recebe as notificações (Web Push) e, quando a pessoa toca numa delas, abre o app na tela certa.
 *
 * Quando a inscrição de notificações muda sozinha (pushsubscriptionchange), não há como avisar o
 * servidor daqui, porque falta o token CSRF da sessão. O app confere a inscrição toda vez que abre.
 */
"use strict";

const MW = self.MW || { versao: "dev", arquivos: [] };
const CAIXA = `meterwatt-${MW.versao}`;
const DESTINO_PADRAO = "/#avisos";

self.addEventListener("install", (ev) => {
  ev.waitUntil((async () => {
    const caixa = await caches.open(CAIXA);
    // "reload": busca no servidor, sem passar pelo cache HTTP, para a casca nova não misturar arquivos velhos.
    await caixa.addAll(MW.arquivos.map((url) => new Request(url, { cache: "reload" })));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (ev) => {
  ev.waitUntil((async () => {
    for (const nome of await caches.keys()) {
      if (nome.startsWith("meterwatt-") && nome !== CAIXA) await caches.delete(nome);
    }
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (ev) => {
  const pedido = ev.request;
  if (pedido.method !== "GET") return;
  const url = new URL(pedido.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  if (pedido.mode === "navigate") {
    // Só a página do app (as telas ficam depois do "#"). O resto segue para o servidor.
    if (url.pathname === "/") ev.respondWith(daCaixa("/", pedido));
    return;
  }
  if (MW.arquivos.includes(url.pathname)) ev.respondWith(daCaixa(url.pathname, pedido));
});

/* A casca vem da caixa desta versão, para página, estilo e código serem sempre da mesma versão.
 * Se a caixa ainda não tem o arquivo, vai ao servidor. */
async function daCaixa(chave, pedido) {
  const guardado = await caches.match(chave, { cacheName: CAIXA, ignoreSearch: true, ignoreVary: true });
  return guardado || fetch(pedido);
}

/* ------------------------------------------------------------------ notificações */

function urlDoApp(endereco) {
  try {
    const url = new URL(endereco || DESTINO_PADRAO, self.location.origin);
    return url.origin === self.location.origin ? url : new URL(DESTINO_PADRAO, self.location.origin);
  } catch {
    return new URL(DESTINO_PADRAO, self.location.origin);
  }
}

self.addEventListener("push", (ev) => {
  let dados = {};
  try {
    dados = ev.data ? ev.data.json() : {};
  } catch {
    dados = { texto: ev.data ? ev.data.text() : "" };
  }
  const opcoes = {
    body: dados.texto || "Abra o app para ver.",
    icon: "/estatico/app/icone-192.png",
    badge: "/estatico/app/selo-notificacao-96.png",
    lang: "pt-BR",
    timestamp: Date.parse(dados.quando) || Date.now(),
    data: { url: urlDoApp(dados.url).href },
  };
  if (dados.tag) {
    opcoes.tag = String(dados.tag);   // um aviso novo do mesmo assunto substitui o anterior na bandeja
    opcoes.renotify = true;
  }
  ev.waitUntil((async () => {
    // Sempre mostra: o Safari (e o Chrome) cortam a permissão de quem recebe e não mostra nada.
    await self.registration.showNotification(dados.titulo || "MeterWatt", opcoes);
    if (Number.isInteger(dados.nao_lidos)) {
      try {
        if (self.navigator.setAppBadge) await self.navigator.setAppBadge(dados.nao_lidos);   // número no ícone do app
      } catch { /* sem suporte */ }
      // Com o app aberto, o sino já mostra o aviso novo, sem esperar a próxima atualização.
      for (const janela of await self.clients.matchAll({ type: "window" })) {
        janela.postMessage({ tipo: "aviso", nao_lidos: dados.nao_lidos });
      }
    }
  })());
});

self.addEventListener("notificationclick", (ev) => {
  ev.notification.close();
  const destino = urlDoApp(ev.notification.data && ev.notification.data.url);
  ev.waitUntil((async () => {
    const janelas = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const aberta = janelas.find((j) => new URL(j.url).origin === self.location.origin);
    if (aberta) {
      // O app já está aberto: traz para a frente e troca de tela sem recarregar.
      await aberta.focus().catch(() => {});
      aberta.postMessage({ tipo: "abrir", rota: destino.hash.replace(/^#/, "") });
      return;
    }
    await self.clients.openWindow(destino.href);
  })());
});
