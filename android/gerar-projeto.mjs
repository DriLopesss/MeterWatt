// Gera o projeto Android do MeterWatt (um Trusted Web Activity) em android/projeto/.
//
// O app da Play Store é uma "casca" Android que abre o site em tela cheia, sem barra de endereço,
// provando para o Android (pelo /.well-known/assetlinks.json do servidor) que o site e o app são do
// mesmo dono. Nome, cores, ícones e atalhos vêm do manifesto do site; o resto, de android/app.json.
// Usa o @bubblewrap/core, o mesmo motor do Bubblewrap, só que sem perguntas.
//
//   cd android && npm ci
//   node gerar-projeto.mjs              # projeto de teste (domínio de exemplo), para conferir que compila
//   node gerar-projeto.mjs --publicar   # para a Play Store: exige o domínio em app.json
//
// Depois, em android/projeto: ./gradlew bundleRelease (o .aab da Play) e assembleRelease (o .apk).
// A assinatura com a chave de envio é feita fora daqui (veja .github/workflows/android.yml).
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import bubblewrap from "@bubblewrap/core";

const { ConsoleLog, TwaGenerator, TwaManifest } = bubblewrap;

const AQUI = path.dirname(fileURLToPath(import.meta.url));
const ESTATICO = path.join(AQUI, "..", "src", "meterwatt", "estatico");
const DESTINO = path.join(AQUI, "projeto");
const DOMINIO_DE_EXEMPLO = "meterwatt.exemplo.com.br";
const COR = "#0b0a12";
// O gerador confere o tipo dos ícones que baixa.
const TIPOS = { ".png": "image/png", ".webp": "image/webp", ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json" };

const publicar = process.argv.includes("--publicar");
const app = JSON.parse(fs.readFileSync(path.join(AQUI, "app.json"), "utf8"));
const dominio = (process.env.MW_ANDROID_DOMINIO || app.dominio || "").trim().toLowerCase();
if (publicar && !/^[a-z0-9.-]+\.[a-z]{2,}$/.test(dominio)) {
  console.error("Para gerar o app da Play Store, escreva o domínio do site em android/app.json (\"dominio\"), "
    + "por exemplo painel.meterwatt.com.br, sem https://.");
  process.exit(2);
}
const host = dominio || DOMINIO_DE_EXEMPLO;
// A Play exige um número de versão maior a cada envio. No GitHub, é o número da execução.
const versaoCodigo = Number.parseInt(process.env.MW_ANDROID_VERSAO_CODIGO || "1", 10);

// O gerador baixa os ícones pelo endereço que está no manifesto. Para não depender do site no ar,
// um servidor local entrega os arquivos do próprio repositório, só durante a geração.
const servidor = http.createServer((pedido, resposta) => {
  const caminho = decodeURIComponent(new URL(pedido.url, "http://local").pathname);
  const arquivo = caminho === "/manifest.webmanifest"
    ? path.join(ESTATICO, "manifest.webmanifest")
    : path.join(ESTATICO, caminho.replace(/^\/estatico\//, ""));
  if (!arquivo.startsWith(ESTATICO + path.sep) || !fs.existsSync(arquivo) || !fs.statSync(arquivo).isFile()) {
    resposta.writeHead(404).end();
    return;
  }
  const tipo = TIPOS[path.extname(arquivo)] || "application/octet-stream";
  resposta.writeHead(200, { "Content-Type": tipo }).end(fs.readFileSync(arquivo));
});
await new Promise((pronto) => servidor.listen(0, "127.0.0.1", pronto));
const manifesto = JSON.parse(fs.readFileSync(path.join(ESTATICO, "manifest.webmanifest"), "utf8"));

// O gerador também guarda no app uma cópia do manifesto (o ChromeOS a usa para abrir a versão web),
// baixada do site no ar. Aqui a cópia sai do repositório: o app é gerado igual com o site fora do ar.
class Gerador extends TwaGenerator {
  async writeWebManifest(twa, destino) {
    const pasta = path.join(destino, "app", "src", "main", "res", "raw");
    await fs.promises.mkdir(pasta, { recursive: true });
    await fs.promises.writeFile(path.join(pasta, "web_app_manifest.json"),
      JSON.stringify({ ...manifesto, start_url: twa.startUrl }));
  }
}
const local = `http://127.0.0.1:${servidor.address().port}`;

try {
  const base = TwaManifest.fromWebManifestJson(new URL(`${local}/manifest.webmanifest`), manifesto).toJson();
  const publico = (endereco) => endereco.replaceAll(local, `https://${host}`);
  const doApp = (endereco) => {
    const url = new URL(publico(endereco));
    url.searchParams.set("app", "android");
    return url.toString();
  };
  const twa = new TwaManifest({
    ...base,
    // Os atalhos (toque longo no ícone) abrem o site no ar, não o servidor local desta geração.
    shortcuts: base.shortcuts.map((atalho) => ({ ...atalho, url: doApp(atalho.url) })),
    packageId: app.pacote,
    host,
    name: app.nome,
    launcherName: app.nome,
    // ?app=android: o site sabe que está dentro do app da Play (lá, não vende assinatura).
    // #painel: quem não entrou na conta vai para a tela de entrada, não para a página de divulgação.
    startUrl: "/?app=android#painel",
    themeColorDark: COR,
    navigationColor: COR,
    navigationColorDark: COR,
    enableNotifications: true,           // as notificações do site viram notificações do app (Android 13+ pede permissão)
    fallbackType: "customtabs",
    minSdkVersion: 24,
    appVersionCode: versaoCodigo,
    appVersion: app.versao_nome,
    signingKey: { path: "./envio.keystore", alias: app.chave_alias },
    webManifestUrl: `https://${host}/manifest.webmanifest`,
    fullScopeUrl: `https://${host}/`,
    launchHandlerClientMode: "navigate-existing",
    generatorApp: "MeterWatt (android/gerar-projeto.mjs)",
  });
  const problema = twa.validate();
  if (problema) throw new Error(problema);

  fs.rmSync(DESTINO, { recursive: true, force: true });
  await new Gerador().createTwaProject(DESTINO, twa, new ConsoleLog("android"));
  // O modelo do Bubblewrap ainda lista o jcenter(), obsoleto no Gradle; as bibliotecas estão no Maven Central.
  const raiz = path.join(DESTINO, "build.gradle");
  fs.writeFileSync(raiz, fs.readFileSync(raiz, "utf8").replaceAll("jcenter()", "mavenCentral()"));
  // O que foi usado, para conferir (e para o Bubblewrap, se um dia quiserem), com os endereços do site no ar.
  const usado = path.join(DESTINO, "twa-manifest.json");
  await twa.saveToFile(usado);
  fs.writeFileSync(usado, publico(fs.readFileSync(usado, "utf8")));
  console.log(`Projeto Android gerado em android/projeto: ${app.pacote}, abre https://${host}${twa.startUrl}, `
    + `versão ${app.versao_nome} (${versaoCodigo})${dominio ? "" : " [domínio de exemplo: só para conferir que compila]"}.`);
} finally {
  servidor.closeAllConnections();     // o gerador deixa conexões abertas (keep-alive), que prenderiam o processo
  servidor.close();
}
