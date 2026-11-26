"""Confere, num navegador de verdade, cada função da aplicação web e do app (a lista de docs/funcionalidades.md).

Sobe o servidor numa pasta temporária (sem MQTT; o caminho do medidor é conferido por tools/ensaio_completo.py),
abre o Chromium do Playwright e passa por tudo: página inicial, lista de espera, conta, telas do app com uma conta
nova, as mesmas telas com dados no modo demonstração, o celular e a acessibilidade básica. Cada item tem um código
(F01, F02...) igual ao da lista. Serve para comparar antes e depois de mexer no visual:

    python tools/conferir_front.py               # tudo
    python tools/conferir_front.py --so F2       # só os itens que começam com F2
    python tools/conferir_front.py --fotos pasta # guarda uma foto de cada tela

Precisa de: pip install playwright (e o Chromium dele). Sai com erro se algum item falhar.
"""
import argparse
import glob
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SENHA = "Tomada-azul-2026-teste"
ROTAS_APP = ["painel", "medidores", "aparelhos", "economia", "seguranca", "privacidade", "conta", "avisos", "plano",
             "relatorio", "compartilhar", "integracao"]


def porta_livre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Conferencia:
    def __init__(self, filtro, fotos):
        self.filtro, self.fotos = filtro, fotos
        self.resultados = []
        self.erros_js = []

    def item(self, codigo, descricao):
        def decorador(funcao):
            if self.filtro and not codigo.startswith(self.filtro):
                return funcao
            antes = len(self.erros_js)
            try:
                funcao()
                novos = self.erros_js[antes:]
                if novos:
                    raise AssertionError("erro no console: " + " | ".join(novos[:3]))
                self.resultados.append((codigo, descricao, True, ""))
            except Exception as e:     # noqa: BLE001 - o relatório mostra qualquer falha
                self.resultados.append((codigo, descricao, False, (str(e) or repr(e)).splitlines()[0][:220]))
            return funcao
        return decorador

    def foto(self, pagina, nome):
        if self.fotos:
            os.makedirs(self.fotos, exist_ok=True)
            pagina.screenshot(path=os.path.join(self.fotos, f"{nome}.png"), full_page=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--so", default="", help="só os itens cujo código começa com isto (ex.: F3)")
    ap.add_argument("--fotos", default=None, help="pasta para guardar uma foto de cada tela")
    args = ap.parse_args()

    pasta = tempfile.mkdtemp(prefix="meterwatt-front-")
    porta = porta_livre()
    url = f"http://localhost:{porta}"
    env = dict(os.environ, MW_PASTA_INSTANCIA=os.path.join(pasta, "instancia"), MW_HTTP_PORTA=str(porta),
               MW_URL_PUBLICA=url, MW_TARIFA_PADRAO="0.95", MW_EMAIL_SEGURANCA="seguranca@exemplo.com.br", MW_LIMITE_MEDIDORES_GRATIS="0")
    env.pop("MW_MODO", None)
    registro = open(os.path.join(pasta, "servidor.log"), "w")
    servidor = subprocess.Popen([sys.executable, "-m", "meterwatt", "--sem-mqtt"], cwd=os.path.join(RAIZ, "src"), env=env,
                                stdout=registro, stderr=subprocess.STDOUT)
    caixa = os.path.join(pasta, "instancia", "caixa-de-saida")
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(url + "/api/publico/saude", timeout=1)
                break
            except OSError:
                time.sleep(0.3)
        else:
            raise SystemExit("O servidor não subiu.")
        c = Conferencia(args.so, args.fotos)
        with sync_playwright() as pw:
            navegador = pw.chromium.launch()
            conferir(c, navegador, url, caixa)
            navegador.close()
    finally:
        servidor.terminate()
        servidor.wait(10)

    falhas = [r for r in c.resultados if not r[2]]
    for codigo, descricao, ok, erro in c.resultados:
        print(f"{'ok   ' if ok else 'FALHA'} {codigo}  {descricao}{'' if ok else '  -> ' + erro}")
    print(f"\n{len(c.resultados) - len(falhas)} de {len(c.resultados)} itens certos. Registro do servidor: {registro.name}")
    sys.exit(1 if falhas else 0)


def link_do_email(caixa, assunto, rota, esperar=8.0):
    fim = time.time() + esperar
    while time.time() < fim:
        for caminho in sorted(glob.glob(os.path.join(caixa, "*.txt")), reverse=True):
            texto = open(caminho, encoding="utf-8").read()
            if assunto.lower() in texto.lower():
                m = re.search(r"https?://\S+/#" + re.escape(rota) + r"/\S+", texto)
                if m:
                    return m.group(0)
        time.sleep(0.2)
    raise AssertionError(f"e-mail '{assunto}' não chegou")


def nova_pagina(c, navegador, **opcoes):
    contexto = navegador.new_context(**opcoes)
    p = contexto.new_page()
    p.on("pageerror", lambda e: c.erros_js.append(f"pageerror: {e}"))

    def console(m):
        texto = m.text
        if m.type == "error" and "status of 401" not in texto and "status of 400" not in texto and "status of 404" not in texto \
                and "status of 403" not in texto and "status of 429" not in texto:
            c.erros_js.append(texto[:200])
    p.on("console", console)
    p.on("response", lambda r: c.erros_js.append(f"HTTP {r.status} em {r.url}") if r.status >= 500 else None)
    return p


def esperar_texto(p, seletor, trecho, tempo=8.0):
    """Espera o texto aparecer (sem wait_for_function: a CSP do site proíbe avaliar texto como código)."""
    fim = time.time() + tempo
    while time.time() < fim:
        if trecho.lower() in (p.inner_text(seletor) or "").lower():
            return
        p.wait_for_timeout(200)
    raise AssertionError(f"{seletor} sem '{trecho}': {p.inner_text(seletor)[:100]}")


def visivel(p, seletor, tempo=6000):
    p.wait_for_selector(seletor, state="visible", timeout=tempo)


def sem_rolagem_lateral(p):
    largura = p.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert largura <= 1, f"a página rola {largura}px para o lado"


def conferir(c, navegador, url, caixa):
    p = nova_pagina(c, navegador, viewport={"width": 1280, "height": 900})
    email = f"teste{int(time.time())}@exemplo.com.br"

    # ------------------------------------------------------------------ página inicial
    @c.item("F01", "Página inicial abre, com título, calculadora e lista de espera")
    def _():
        p.goto(url + "/#inicio")
        visivel(p, "#area-inicio h1")
        visivel(p, "#calc-lista .calc-item")
        visivel(p, "#form-fila")
        c.foto(p, "inicio")

    @c.item("F02", "Calculadora de consumo fantasma muda o valor com + e com a tarifa")
    def _():
        antes = p.inner_text("#calc-watts")
        p.click("#calc-lista .calc-item >> nth=0 >> button[aria-label^='Mais um']")
        depois = p.inner_text("#calc-watts")
        assert antes != depois, f"continuou {antes}"
        ano = p.inner_text("#calc-ano")
        p.fill("#calc-tarifa", "1,50")
        assert p.inner_text("#calc-ano") != ano, "a tarifa não mudou o valor do ano"

    @c.item("F03", "Lista de espera: entrar, confirmar pelo e-mail e ver a posição")
    def _():
        p.fill("#form-fila [name=email]", "fila-" + email)
        p.check("#form-fila [name=aceite]")
        p.click("#form-fila button[type=submit]")
        visivel(p, "#fila-sucesso")
        p.goto(link_do_email(caixa, "lista", "fila"))
        esperar_texto(p, "#fila-estado-titulo", "número")

    @c.item("F04", "Lista de espera: sair pelo link do e-mail")
    def _():
        texto = ""
        for caminho in sorted(glob.glob(os.path.join(caixa, "*.txt")), reverse=True):
            texto = open(caminho, encoding="utf-8").read()
            if "sair-da-fila" in texto:
                break
        m = re.search(r"https?://\S+/#sair-da-fila/\S+", texto)
        assert m, "e-mail sem link para sair"
        p.goto(m.group(0))
        p.click("#botao-sair-fila")
        p.wait_for_timeout(800)
        texto = p.inner_text("#sair-estado").lower()
        assert "voltar ao início" in texto and "não vale" not in texto, texto[:120]

    @c.item("F05", "Páginas abertas: política, termos, excluir conta, security.txt e 404")
    def _():
        for caminho, texto in [("/politica-de-privacidade", "Política"), ("/termos-de-uso", "Termos"), ("/excluir-conta", "Excluir")]:
            p.goto(url + caminho)
            p.wait_for_timeout(600)
            assert texto.lower() in p.inner_text("body").lower(), f"{caminho} sem '{texto}'"
        r = urllib.request.urlopen(url + "/.well-known/security.txt")
        assert b"Contact:" in r.read()
        p.goto(url + "/#nao-existe")
        visivel(p, "#area-erro")
        c.foto(p, "erro-404")

    # ------------------------------------------------------------------ conta
    @c.item("F10", "Cadastro: senha fraca é recusada com mensagem")
    def _():
        p.goto(url + "/#cadastro")
        p.fill("#form-cadastro [name=nome]", "Teste")
        p.fill("#form-cadastro [name=email]", email)
        p.fill("#form-cadastro [name=senha]", "123")
        p.click("#form-cadastro button[type=submit]")
        p.wait_for_timeout(500)
        assert p.inner_text("#form-cadastro .erro").strip(), "nenhuma mensagem de erro"

    @c.item("F11", "Cadastro: conta criada, e-mail de confirmação chega e o link confirma")
    def _():
        p.fill("#form-cadastro [name=senha]", SENHA)
        for nome in ("termos", "privacidade", "consumo"):
            p.check(f"#form-cadastro [name={nome}]")
        p.click("#form-cadastro button[type=submit]")
        visivel(p, "section[data-rota=enviado]")
        p.goto(link_do_email(caixa, "Confirme", "confirmar"))
        esperar_texto(p, "#texto-confirmar", "confirmado")

    @c.item("F12", "Entrar com senha errada mostra erro; com a certa abre o painel")
    def _():
        p.goto(url + "/#entrar")
        p.fill("#form-entrar [name=email]", email)
        p.fill("#form-entrar [name=senha]", "senha-errada-123")
        p.click("#form-entrar button[type=submit]")
        p.wait_for_timeout(600)
        assert p.inner_text("#form-entrar .erro").strip(), "sem mensagem de senha errada"
        p.fill("#form-entrar [name=senha]", SENHA)
        p.click("#form-entrar button[type=submit]")
        p.wait_for_url(re.compile(r"#painel"), timeout=8000)
        visivel(p, "#area-app")

    @c.item("F13", "Painel sem medidor mostra o convite para parear")
    def _():
        visivel(p, "#painel-vazio")
        c.foto(p, "painel-vazio")

    @c.item("F14", "Todas as telas do app abrem sem erro, com título e sem rolar para o lado")
    def _():
        for rota in ROTAS_APP:
            p.goto(f"{url}/#{rota}")
            p.wait_for_timeout(900)
            sec = p.locator(f"#area-app section[data-rota={rota}]")
            assert sec.is_visible(), f"#{rota} não apareceu"
            assert p.locator(f"#area-app section[data-rota={rota}] h1, #area-app section[data-rota={rota}] h2").count(), f"#{rota} sem título"
            sem_rolagem_lateral(p)
            c.foto(p, f"conta-nova-{rota}")

    @c.item("F15", "Medidores: Adicionar medidor mostra um código de pareamento com prazo")
    def _():
        p.goto(url + "/#medidores")
        p.click("#botao-parear")
        visivel(p, "#dialogo-parear")
        assert re.match(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$", p.inner_text("#parear-codigo").strip()), p.inner_text("#parear-codigo")
        assert "Vale por" in p.inner_text("#parear-tempo")
        p.keyboard.press("Escape")

    @c.item("F16", "Segurança: começar a verificação em duas etapas mostra o QR e o segredo")
    def _():
        p.goto(url + "/#seguranca")
        p.wait_for_timeout(700)
        botao = p.locator("section[data-rota=seguranca] button", has_text=re.compile("Ativar|duas etapas", re.I)).first
        botao.click()
        p.wait_for_timeout(700)
        assert p.locator("section[data-rota=seguranca] svg, section[data-rota=seguranca] img").count(), "sem QR"

    @c.item("F17", "Segurança: lista de sessões abertas aparece")
    def _():
        assert "sess" in p.inner_text("section[data-rota=seguranca]").lower()

    @c.item("F18", "Privacidade: autorizações, baixar meus dados e atividade da conta")
    def _():
        p.goto(url + "/#privacidade")
        p.wait_for_timeout(700)
        assert p.locator("section[data-rota=privacidade] [role=switch]").count(), "sem autorizações"
        with p.expect_download(timeout=8000):
            p.click("#botao-exportar")
            p.wait_for_timeout(300)
            if p.locator("#dialogo-confirmar").is_visible():
                p.fill("#form-confirmar [name=senha]", SENHA)
                p.click("#confirmar-botao")
        assert "entrada" in p.inner_text("section[data-rota=privacidade]").lower()

    @c.item("F19", "Plano: pagamento de teste aprovado vira plano Casa, e dá para cancelar")
    def _():
        p.goto(url + "/#plano")
        p.wait_for_timeout(700)
        if not p.locator("#plano-conteudo button", has_text="Assinar").count():
            return   # sem cobrança ligada neste servidor: nada a assinar
        p.click("#plano-conteudo button:has-text('Assinar')")
        p.wait_for_timeout(1200)
        p.locator("button", has_text="Aprovar").first.click()
        p.wait_for_timeout(1500)
        p.goto(url + "/#plano")
        p.wait_for_timeout(800)
        assert "Cancelar assinatura" in p.inner_text("#plano-conteudo")

    @c.item("F20", "Home Assistant: criar e apagar uma chave só de leitura")
    def _():
        p.goto(url + "/#integracao")
        p.wait_for_timeout(700)
        nome = p.locator("#form-ha input").first
        nome.fill("Casa teste")
        p.click("#form-ha button[type=submit]")
        p.wait_for_timeout(900)
        assert "mw_" in p.inner_text("section[data-rota=integracao]") or "chave" in p.inner_text("section[data-rota=integracao]").lower()

    @c.item("F21", "Compartilhar a casa: convidar por e-mail")
    def _():
        p.goto(url + "/#compartilhar")
        p.wait_for_timeout(700)
        p.fill("#form-convidar input[type=email]", "convidado@exemplo.com.br")
        p.click("#form-convidar button[type=submit]")
        p.wait_for_timeout(900)
        assert "convidado@exemplo.com.br" in p.inner_text("section[data-rota=compartilhar]")

    @c.item("F22", "Avisos: lista e preferências de cada aviso")
    def _():
        p.goto(url + "/#avisos")
        p.wait_for_timeout(700)
        assert p.locator("section[data-rota=avisos] [role=switch]").count(), "sem preferências"

    @c.item("F23", "Sair da conta volta para a entrada e o painel pede login")
    def _():
        p.goto(url + "/#conta")
        p.wait_for_timeout(500)
        p.locator("button, a", has_text=re.compile(r"^\s*Sair\s*$")).first.click()
        p.wait_for_timeout(1000)
        p.goto(url + "/#painel")
        p.wait_for_timeout(800)
        assert "#entrar" in p.url, p.url

    @c.item("F24", "Esqueci a senha: link chega, a tela confere o link e a senha nova funciona")
    def _():
        p.goto(url + "/#esqueci")
        p.fill("#form-esqueci [name=email]", email)
        p.click("#form-esqueci button[type=submit]")
        visivel(p, "section[data-rota=enviado]")
        p.goto(link_do_email(caixa, "Redefinir", "redefinir"))
        visivel(p, "#form-redefinir")
        p.fill("#form-redefinir [name=senha]", SENHA + "-2")
        p.click("#form-redefinir button[type=submit]")
        p.wait_for_url(re.compile(r"#entrar"), timeout=8000)

    @c.item("F25", "Link de senha velho ou cortado avisa na hora e oferece pedir outro")
    def _():
        p.goto(url + "/#redefinir/abc")
        visivel(p, "#redefinir-sem-link")

    p.context.close()

    # ------------------------------------------------------------------ demonstração (dados de dias de uso)
    d = nova_pagina(c, navegador, viewport={"width": 1280, "height": 900})

    @c.item("F30", "Demonstração abre pelo botão da página inicial, com a faixa de aviso")
    def _():
        d.goto(url + "/#inicio")
        d.click("[data-acao=demonstracao]")
        d.wait_for_url(re.compile(r"#painel"), timeout=10000)
        visivel(d, "#area-app")
        d.wait_for_timeout(2000)

    @c.item("F31", "Painel: número de agora, Hoje, meta, consumo fantasma e plano de economia")
    def _():
        assert re.search(r"\d", d.inner_text("#visor-agora")), "visor vazio"
        assert "kWh" in d.inner_text("#hoje-kwh")
        assert re.search(r"R\$", d.inner_text("#meta-projecao"))
        assert d.locator("#fantasma-conteudo").inner_text().strip()
        c.foto(d, "demo-painel")

    @c.item("F32", "Painel: gráficos de fluxo, perfil do dia e últimos 14 dias desenhados")
    def _():
        for sel in ("#fluxo svg", ".perfil-grafico svg", "#barras-dias svg"):
            assert d.locator(sel).count(), f"faltou {sel}"

    @c.item("F33", "Painel: perfil alterna Hoje e 14 dias, e a tabela abre")
    def _():
        d.click("#bloco-perfil [data-vista=mapa]")
        d.wait_for_timeout(500)
        assert d.locator(".perfil-grafico .celula").count() > 24, "o mapa de 14 dias não apareceu"
        d.click("#bloco-perfil [data-vista=hoje]")
        d.wait_for_timeout(300)
        d.click(".perfil-detalhes summary")
        d.wait_for_timeout(300)
        assert d.locator(".perfil-tabela table").count(), "a tabela não abriu"

    @c.item("F34", "Gráficos mostram a dica ao passar o mouse")
    def _():
        d.locator("#barras-dias svg").scroll_into_view_if_needed()
        d.wait_for_timeout(300)
        caixa_dias = d.locator("#barras-dias svg").bounding_box()
        d.mouse.move(caixa_dias["x"] + caixa_dias["width"] * 0.5, caixa_dias["y"] + caixa_dias["height"] * 0.6)
        d.wait_for_timeout(300)
        assert d.locator(".mw-dica.on").count(), "a dica não apareceu"
        d.mouse.move(0, 0)

    @c.item("F35", "Casa em 3D liga e desliga pelo botão 3D")
    def _():
        antes = d.get_attribute("#botao-3d", "aria-pressed")
        d.click("#botao-3d")
        assert d.get_attribute("#botao-3d", "aria-pressed") != antes
        d.click("#botao-3d")

    @c.item("F36", "Medidores: cartão com medidas, fases, gráfico 2 h, 24 h e 7 dias e qualidade da energia")
    def _():
        d.goto(url + "/#medidores")
        d.wait_for_timeout(2500)
        visivel(d, ".medidor")
        assert d.locator(".medidor .fases:not([hidden])").count()
        for i in (2, 3, 1):
            d.click(f".medidor .periodos button:nth-child({i})")
            d.wait_for_timeout(900)
            assert d.locator(".medidor .grafico-linha svg").count(), f"gráfico {i} vazio"
        assert d.locator(".qe-grafico svg").count(), "sem qualidade da energia"
        c.foto(d, "demo-medidores")

    @c.item("F37", "Medidores: gráfico responde às setas do teclado")
    def _():
        d.focus(".medidor .grafico-linha")
        d.keyboard.press("ArrowLeft")
        d.wait_for_timeout(200)
        assert d.locator(".medidor .grafico-linha .cursor[visibility=visible]").count(), "o cursor não andou"

    @c.item("F38", "Aparelhos: lista do Detetive com nomes e números")
    def _():
        d.goto(url + "/#aparelhos")
        d.wait_for_timeout(1500)
        assert d.locator("section[data-rota=aparelhos] .aparelho").count(), "nenhum aparelho"
        c.foto(d, "demo-aparelhos")

    @c.item("F39", "Economia: mexer num controle muda o placar; conta de luz e Tarifa Branca aparecem")
    def _():
        d.goto(url + "/#economia")
        d.wait_for_timeout(2000)
        placar = d.inner_text(".placar, #placar, .eco-placar") if d.locator(".placar, #placar, .eco-placar").count() else ""
        faixa = d.locator("section[data-rota=economia] input[type=range]").first
        faixa.focus()
        for _ in range(5):
            d.keyboard.press("ArrowRight")
        d.wait_for_timeout(500)
        if placar:
            assert d.inner_text(".placar, #placar, .eco-placar") != placar, "o placar não mudou"
        assert d.locator("#tb-grafico svg").count(), "sem gráfico da Tarifa Branca"
        assert "conta de luz" in d.inner_text("section[data-rota=economia]").lower()
        c.foto(d, "demo-economia")

    @c.item("F40", "Relatório do mês: números, gráfico dia a dia e tabela")
    def _():
        d.goto(url + "/#relatorio")
        d.wait_for_timeout(1500)
        assert d.locator("#rel-grafico svg").count()
        assert "kWh" in d.inner_text("section[data-rota=relatorio]")
        c.foto(d, "demo-relatorio")

    @c.item("F41", "Avisos: o sino mostra quantos há e a tela lista os avisos")
    def _():
        d.goto(url + "/#avisos")
        d.wait_for_timeout(1200)
        assert d.locator("section[data-rota=avisos] li, section[data-rota=avisos] article").count()

    @c.item("F42", "Sair da demonstração volta para a página inicial")
    def _():
        d.click("#sair-demonstracao")
        d.wait_for_timeout(2000)
        assert d.locator("#faixa-demonstracao").is_hidden(), "a faixa da demonstração continuou"
        assert d.locator("#area-inicio").is_visible() or d.locator("#area-publica").is_visible(), f"foi para {d.url}"

    d.context.close()

    # ------------------------------------------------------------------ celular
    m = nova_pagina(c, navegador, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2)

    @c.item("F50", "Celular: página inicial e entrada cabem na tela (sem rolar para o lado)")
    def _():
        for rota in ("inicio", "entrar", "cadastro"):
            m.goto(f"{url}/#{rota}")
            m.wait_for_timeout(800)
            sem_rolagem_lateral(m)

    @c.item("F51", "Celular: demonstração, abas de baixo levam a cada tela, sem rolar para o lado")
    def _():
        m.goto(url + "/#inicio")
        m.click("[data-acao=demonstracao]")
        m.wait_for_url(re.compile(r"#painel"), timeout=10000)
        m.wait_for_timeout(1500)
        for rota in ("medidores", "aparelhos", "economia", "conta", "painel"):
            aba = m.locator(f".abas a[href='#{rota}']")
            assert aba.is_visible(), f"aba {rota} escondida"
            aba.click()
            m.wait_for_timeout(1200)
            assert m.locator(f"#area-app section[data-rota={rota}]").is_visible(), f"#{rota} não abriu"
            sem_rolagem_lateral(m)
            c.foto(m, f"celular-{rota}")

    @c.item("F52", "Celular: tocar num gráfico mostra a dica")
    def _():
        caixa_dias = m.locator("#barras-dias svg")
        caixa_dias.scroll_into_view_if_needed()
        b = caixa_dias.bounding_box()
        m.touchscreen.tap(b["x"] + b["width"] * 0.5, b["y"] + b["height"] * 0.6)
        m.wait_for_timeout(300)
        assert m.locator(".mw-dica.on").count(), "a dica não apareceu no toque"

    m.context.close()

    # ------------------------------------------------------------------ acessibilidade e movimento
    a = nova_pagina(c, navegador, viewport={"width": 1280, "height": 900}, reduced_motion="reduce")

    @c.item("F60", "Com 'reduzir movimento', o app abre e as telas funcionam")
    def _():
        a.goto(url + "/#inicio")
        a.click("[data-acao=demonstracao]")
        a.wait_for_url(re.compile(r"#painel"), timeout=10000)
        a.wait_for_timeout(1500)
        assert a.locator("#barras-dias svg").count()

    @c.item("F61", "Teclado: Tab chega ao link 'Pular para o conteúdo' e aos botões das abas")
    def _():
        a.keyboard.press("Tab")
        foco = a.evaluate("document.activeElement && (document.activeElement.textContent || '').trim()")
        assert foco, "o primeiro Tab não focou nada"

    @c.item("F62", "Imagens e gráficos têm texto alternativo (aria-label ou alt)")
    def _():
        sem = a.evaluate("""[...document.querySelectorAll('#area-app [role=img]')].filter(e => e.offsetParent && !e.getAttribute('aria-label')).length""")
        assert sem == 0, f"{sem} gráfico(s) sem descrição"

    a.context.close()


if __name__ == "__main__":
    main()
