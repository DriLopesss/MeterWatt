"""Servidor web: a API JSON e a página do aplicativo.

Defesas de cada pedido:
- sessão num cookie HttpOnly + SameSite=Strict (prefixo __Host- quando há HTTPS);
- token CSRF obrigatório no cabeçalho X-CSRF-Token em tudo que altera dados;
- pedidos que alteram dados precisam ser JSON e vir da própria origem
  (cabeçalhos Origin e Sec-Fetch-Site);
- corpo de no máximo 64 KB;
- cabeçalhos de segurança (CSP sem nada de fora, HSTS, sem enquadramento, etc.);
- erros sempre em JSON, sem detalhes internos.
"""
import functools
import hmac
import json
import logging
import os

from flask import Flask, Response, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from . import VERSAO
from .erros import ErroDeNegocio
from .privacidade import FINALIDADES, VERSAO_PRIVACIDADE, VERSAO_TERMOS

PASTA_ESTATICA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "estatico")
METODOS_QUE_ALTERAM = {"POST", "PUT", "PATCH", "DELETE"}
log = logging.getLogger("meterwatt.web")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def criar_app(plataforma):
    cfg = plataforma.cfg
    app = Flask(__name__, static_folder=PASTA_ESTATICA, static_url_path="/estatico")
    app.config.update(MAX_CONTENT_LENGTH=64 * 1024, JSON_SORT_KEYS=False)
    app.json.ensure_ascii = False
    app.plataforma = plataforma
    if cfg.confiar_proxy:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    nome_cookie = "__Host-mw_sessao" if cfg.cookie_seguro else "mw_sessao"
    contas, medidores, privacidade = plataforma.contas, plataforma.medidores, plataforma.privacidade

    # ------------------------------------------------------------ apoio

    def ip():
        return request.remote_addr

    def agente():
        return request.headers.get("User-Agent", "")

    def corpo():
        dados = request.get_json(silent=True)
        return dados if isinstance(dados, dict) else {}

    def gravar_cookie(resposta, token, etapa):
        idade = 5 * 60 if etapa == "aguardando_2fa" else cfg.sessao_maxima_dias * 86400
        resposta.set_cookie(nome_cookie, token, max_age=idade, path="/", secure=cfg.cookie_seguro,
                            httponly=True, samesite="Strict")
        return resposta

    def apagar_cookie(resposta):
        resposta.delete_cookie(nome_cookie, path="/", secure=cfg.cookie_seguro, httponly=True, samesite="Strict")
        return resposta

    def ok(dados=None, status=200):
        return jsonify(dados if dados is not None else {"ok": True}), status

    def limite_de_formularios():
        agora = plataforma.agora()
        espera = plataforma.limite_formularios_ip.esgotado(ip() or "-", agora)
        if espera:
            raise ErroDeNegocio("Muitos pedidos deste endereço. Tente de novo mais tarde.", 429, "muitas_tentativas")
        plataforma.limite_formularios_ip.registrar(ip() or "-", agora)

    def exige_sessao(etapa="completa", liberado_sem_2fa=False):
        """Protege a rota. `etapa` diz em que ponto da entrada a sessão precisa estar."""
        def decorador(funcao):
            @functools.wraps(funcao)
            def envolvida(*args, **kwargs):
                sessao = contas.sessao_atual(request.cookies.get(nome_cookie))
                if not sessao:
                    raise ErroDeNegocio("Entre na sua conta para continuar.", 401, "sem_sessao")
                if sessao["etapa"] != etapa:
                    # Não apaga o cookie: quem está no meio da entrada continua de onde parou.
                    raise ErroDeNegocio("Conclua a entrada na sua conta para continuar.", 401, "etapa_" + sessao["etapa"])
                if request.method in METODOS_QUE_ALTERAM:
                    enviado = request.headers.get("X-CSRF-Token", "")
                    if not enviado or not hmac.compare_digest(enviado, sessao["csrf"]):
                        raise ErroDeNegocio("A página ficou desatualizada. Recarregue e tente de novo.", 403, "csrf")
                if cfg.exigir_2fa and etapa == "completa" and not liberado_sem_2fa and not sessao["totp_ativado_em"]:
                    raise ErroDeNegocio("Ative a verificação em duas etapas para usar o painel.", 403, "2fa_obrigatorio")
                g.sessao = sessao
                return funcao(*args, **kwargs)
            return envolvida
        return decorador

    # ------------------------------------------------------------ filtros de todo pedido

    @app.before_request
    def conferir_origem():
        if request.method not in METODOS_QUE_ALTERAM:
            return None
        site = request.headers.get("Sec-Fetch-Site")
        if site and site not in ("same-origin", "none"):
            raise ErroDeNegocio("Pedido de outro site recusado.", 403, "origem")
        origem = request.headers.get("Origin")
        permitidas = {cfg.origem_publica, request.host_url.rstrip("/")}
        if origem and origem not in permitidas:
            raise ErroDeNegocio("Pedido de outro site recusado.", 403, "origem")
        if request.path.startswith("/api/") and not request.is_json:
            raise ErroDeNegocio("Envie os dados em JSON.", 415, "formato")
        return None

    @app.after_request
    def cabecalhos(resposta):
        h = resposta.headers
        h["Content-Security-Policy"] = CSP
        h["X-Content-Type-Options"] = "nosniff"
        h["Referrer-Policy"] = "no-referrer"
        h["X-Frame-Options"] = "DENY"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        h["Cross-Origin-Resource-Policy"] = "same-origin"
        if cfg.cookie_seguro:
            h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.path.startswith("/api/") or request.path == "/":
            h["Cache-Control"] = "no-store"
        return resposta

    @app.teardown_appcontext
    def fechar(_erro):
        plataforma.fechar_conexao_da_thread()

    @app.errorhandler(ErroDeNegocio)
    def erro_de_negocio(erro):
        resposta = jsonify({"erro": erro.mensagem, "codigo": erro.codigo})
        resposta.status_code = erro.status
        if erro.codigo == "sem_sessao":
            apagar_cookie(resposta)
        return resposta

    @app.errorhandler(HTTPException)
    def erro_http(erro):
        if request.path.startswith("/api/"):
            mensagens = {404: "Não encontrado.", 405: "Método não permitido.", 413: "Pedido grande demais."}
            return jsonify({"erro": mensagens.get(erro.code, "Pedido inválido."), "codigo": None}), erro.code
        if erro.code == 404:
            # A própria página mostra a tela de erro.
            return send_from_directory(PASTA_ESTATICA, "index.html"), 404
        return erro

    @app.errorhandler(Exception)
    def erro_inesperado(erro):
        log.exception("Erro inesperado em %s %s", request.method, request.path)
        return jsonify({"erro": "Algo deu errado do nosso lado. Tente de novo em instantes.", "codigo": "interno"}), 500

    # ------------------------------------------------------------ página

    @app.get("/")
    def pagina():
        return send_from_directory(PASTA_ESTATICA, "index.html")

    # ------------------------------------------------------------ público

    @app.get("/api/publico/info")
    def info():
        return ok({
            "versao": VERSAO, "modo": cfg.modo, "exigir_2fa": cfg.exigir_2fa,
            "termos_versao": VERSAO_TERMOS, "privacidade_versao": VERSAO_PRIVACIDADE,
            "finalidades": {f: {k: v for k, v in i.items()} for f, i in FINALIDADES.items()},
            "retencao": privacidade.regras_de_retencao(), "contato": privacidade.contato(),
            "email_de_teste": cfg.email_modo == "arquivo",
        })

    # ------------------------------------------------------------ conta

    @app.post("/api/conta/cadastro")
    def cadastro():
        limite_de_formularios()
        d = corpo()
        contas.cadastrar(d.get("nome"), d.get("email"), d.get("senha"), d.get("aceites"), ip())
        return ok({"mensagem": "Se o e-mail puder ser usado, você vai receber um link de confirmação em instantes."}, 202)

    @app.post("/api/conta/confirmar")
    def confirmar():
        contas.confirmar_email(corpo().get("token"), ip())
        return ok({"mensagem": "E-mail confirmado. Agora é só entrar."})

    @app.post("/api/conta/reenviar")
    def reenviar():
        limite_de_formularios()
        contas.reenviar_confirmacao(corpo().get("email"), ip())
        return ok({"mensagem": "Se houver uma conta esperando confirmação, enviamos um link novo."}, 202)

    @app.post("/api/conta/entrar")
    def entrar():
        d = corpo()
        r = contas.entrar(d.get("email"), d.get("senha"), ip(), agente())
        resposta, _ = ok({"etapa": r["etapa"], "csrf": r["csrf"]})
        return gravar_cookie(resposta, r["token"], r["etapa"])

    @app.post("/api/conta/2fa")
    @exige_sessao(etapa="aguardando_2fa")
    def segunda_etapa():
        r = contas.verificar_2fa(g.sessao, corpo().get("codigo"), ip(), agente())
        resposta, _ = ok({"etapa": r["etapa"], "csrf": r["csrf"]})
        return gravar_cookie(resposta, r["token"], r["etapa"])

    @app.get("/api/conta/2fa/etapa")
    @exige_sessao(etapa="aguardando_2fa")
    def etapa_2fa():
        return ok({"etapa": "aguardando_2fa", "csrf": g.sessao["csrf"]})

    @app.post("/api/conta/sair")
    def sair():
        sessao = contas.sessao_atual(request.cookies.get(nome_cookie))
        if sessao:
            if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), sessao["csrf"]):
                raise ErroDeNegocio("A página ficou desatualizada. Recarregue e tente de novo.", 403, "csrf")
            contas.sair(sessao, ip())
        resposta, _ = ok()
        return apagar_cookie(resposta)

    @app.get("/api/conta/eu")
    @exige_sessao(liberado_sem_2fa=True)
    def eu():
        return ok(contas.eu(g.sessao))

    @app.post("/api/conta/esqueci")
    def esqueci():
        limite_de_formularios()
        contas.pedir_redefinicao(corpo().get("email"), ip())
        return ok({"mensagem": "Se houver uma conta com esse e-mail, enviamos um link para criar uma senha nova."}, 202)

    @app.post("/api/conta/redefinir")
    def redefinir():
        d = corpo()
        contas.redefinir_senha(d.get("token"), d.get("senha"), ip())
        resposta, _ = ok({"mensagem": "Senha nova criada. Entre com ela."})
        return apagar_cookie(resposta)

    @app.post("/api/conta/senha")
    @exige_sessao(liberado_sem_2fa=True)
    def trocar_senha():
        d = corpo()
        contas.trocar_senha(g.sessao, d.get("atual"), d.get("nova"), ip())
        return ok({"mensagem": "Senha trocada. As outras sessões foram encerradas."})

    @app.post("/api/conta/2fa/iniciar")
    @exige_sessao(liberado_sem_2fa=True)
    def iniciar_2fa():
        return ok(contas.iniciar_2fa(g.sessao))

    @app.post("/api/conta/2fa/ativar")
    @exige_sessao(liberado_sem_2fa=True)
    def ativar_2fa():
        return ok(contas.ativar_2fa(g.sessao, corpo().get("codigo"), ip()))

    @app.post("/api/conta/2fa/desativar")
    @exige_sessao()
    def desativar_2fa():
        d = corpo()
        contas.desativar_2fa(g.sessao, d.get("senha"), d.get("codigo"), ip())
        return ok({"mensagem": "Verificação em duas etapas desativada."})

    @app.post("/api/conta/2fa/codigos")
    @exige_sessao()
    def codigos_backup():
        d = corpo()
        return ok(contas.novos_codigos_backup(g.sessao, d.get("senha"), d.get("codigo"), ip()))

    @app.get("/api/conta/sessoes")
    @exige_sessao(liberado_sem_2fa=True)
    def sessoes():
        return ok({"sessoes": contas.listar_sessoes(g.sessao)})

    @app.delete("/api/conta/sessoes/<int:sessao_id>")
    @exige_sessao(liberado_sem_2fa=True)
    def encerrar_sessao(sessao_id):
        contas.encerrar_sessao(g.sessao, sessao_id, ip())
        return ok()

    @app.post("/api/conta/sessoes/encerrar-outras")
    @exige_sessao(liberado_sem_2fa=True)
    def encerrar_outras():
        return ok({"encerradas": contas.encerrar_outras(g.sessao, ip())})

    @app.put("/api/conta/preferencias")
    @exige_sessao()
    def preferencias():
        contas.preferencias(g.sessao, corpo(), ip())
        return ok()

    # ------------------------------------------------------------ medidores

    @app.get("/api/medidores")
    @exige_sessao()
    def lista_medidores():
        return ok(medidores.listar(g.sessao))

    @app.post("/api/medidores/codigo")
    @exige_sessao()
    def codigo_pareamento():
        return ok(medidores.gerar_codigo(g.sessao, ip()), 201)

    @app.patch("/api/medidores/<mid>")
    @exige_sessao()
    def renomear(mid):
        medidores.renomear(g.sessao, mid, corpo().get("apelido"), ip())
        return ok()

    @app.delete("/api/medidores/<mid>")
    @exige_sessao()
    def remover(mid):
        medidores.remover(g.sessao, mid, ip())
        return ok()

    @app.get("/api/medidores/<mid>/historico")
    @exige_sessao()
    def historico(mid):
        return ok(medidores.historico(g.sessao, mid, request.args.get("periodo", "2h")))

    @app.get("/api/consumo/dias")
    @exige_sessao()
    def consumo_dias():
        try:
            dias = int(request.args.get("dias", "30"))
        except ValueError:
            dias = 30
        return ok(medidores.consumo_por_dia(g.sessao, dias))

    @app.get("/api/fantasma")
    @exige_sessao()
    def fantasma():
        return ok(medidores.fantasma(g.sessao))

    @app.get("/api/meta")
    @exige_sessao()
    def meta():
        return ok(medidores.meta(g.sessao))

    # ------------------------------------------------------------ privacidade (sempre liberado:
    # os direitos do titular não dependem da verificação em duas etapas)

    @app.get("/api/privacidade")
    @exige_sessao(liberado_sem_2fa=True)
    def privacidade_estado():
        return ok(privacidade.estado(g.sessao))

    @app.put("/api/privacidade/<finalidade>")
    @exige_sessao(liberado_sem_2fa=True)
    def privacidade_alterar(finalidade):
        aceito = corpo().get("aceito")
        if not isinstance(aceito, bool):
            raise ErroDeNegocio("Informe se autoriza (true) ou não (false).", 400)
        privacidade.alterar(g.sessao, finalidade, aceito, ip())
        return ok(privacidade.estado(g.sessao))

    @app.post("/api/privacidade/exportar")
    @exige_sessao(liberado_sem_2fa=True)
    def exportar():
        dados = privacidade.exportar(g.sessao, ip())
        texto = json.dumps(dados, ensure_ascii=False, indent=2)
        nome = f"meterwatt-meus-dados-{plataforma.dia_local(plataforma.agora())}.json"
        return Response(texto, mimetype="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{nome}"'})

    @app.get("/api/privacidade/atividade")
    @exige_sessao(liberado_sem_2fa=True)
    def atividade():
        return ok({"atividade": privacidade.atividade(g.sessao)})

    @app.post("/api/privacidade/apagar-consumo")
    @exige_sessao(liberado_sem_2fa=True)
    def apagar_consumo():
        d = corpo()
        privacidade.apagar_consumo(g.sessao, d.get("senha"), d.get("codigo"), ip())
        return ok({"mensagem": "Os dados de consumo foram apagados. Os medidores continuam pareados."})

    @app.post("/api/privacidade/excluir-conta")
    @exige_sessao(liberado_sem_2fa=True)
    def excluir_conta():
        d = corpo()
        privacidade.excluir_conta(g.sessao, d.get("senha"), d.get("codigo"), ip())
        resposta, _ = ok({"mensagem": "Conta excluída."})
        return apagar_cookie(resposta)

    return app
