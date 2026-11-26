"""Notificações no celular (Web Push): cifra, assinatura do servidor, inscrições e entrega dos avisos."""
import http.server
import json
import os
import struct
import threading

import pytest
from conftest import SENHA, ativar_2fa, cadastrar, conta_pronta, entrar, link_do_email
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from meterwatt import config
from meterwatt.mensageiro import MensageiroMemoria
from meterwatt.notificacoes import Vapid, _hkdf, b64u, cifrar, de_b64u, endpoint_aceito, enviar_http
from meterwatt.plataforma import Plataforma
from meterwatt.web import criar_app

EMAIL = "ana@exemplo.com.br"
CHROME_ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Mobile Safari/537.36"
IPHONE_APP = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"


class Aparelho:
    """Um navegador de mentira com chaves de verdade: inscreve-se e decifra o que chega."""

    def __init__(self, endpoint=None):
        self.chave = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)
        self.p256dh = self.chave.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.endpoint = endpoint or "https://fcm.googleapis.com/fcm/send/" + b64u(os.urandom(16))

    def inscricao(self, **extra):
        return {"endpoint": self.endpoint, "keys": {"p256dh": b64u(self.p256dh), "auth": b64u(self.auth)}, **extra}

    def decifrar(self, corpo):
        """O que o navegador faz ao receber (RFC 8291 + RFC 8188), escrito à parte para conferir o servidor."""
        sal, rs, tamanho = corpo[:16], struct.unpack(">I", corpo[16:20])[0], corpo[20]
        publica = corpo[21:21 + tamanho]
        assert rs == 4096 and tamanho == 65
        segredo = self.chave.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), publica))
        ikm = _hkdf(self.auth, segredo, b"WebPush: info\x00" + self.p256dh + publica, 32)
        cek = _hkdf(sal, ikm, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = _hkdf(sal, ikm, b"Content-Encoding: nonce\x00", 12)
        claro = AESGCM(cek).decrypt(nonce, corpo[21 + tamanho:], None).rstrip(b"\x00")
        assert claro.endswith(b"\x02")
        return json.loads(claro[:-1])


class Servico:
    """O serviço de notificações dos navegadores, de mentira: guarda o que recebe e responde o que mandarem."""

    def __init__(self):
        self.recebidos = []
        self.respostas = []

    def __call__(self, endpoint, corpo, cabecalhos):
        self.recebidos.append({"endpoint": endpoint, "corpo": corpo, "cabecalhos": cabecalhos})
        return self.respostas.pop(0) if self.respostas else 201


@pytest.fixture
def servico(plataforma):
    s = Servico()
    plataforma.notificacoes.transporte = s
    return s


def ligar(cliente, aparelho, agente=CHROME_ANDROID, **extra):
    return cliente.put("/api/notificacoes/inscricao", aparelho.inscricao(**extra), headers={"User-Agent": agente})


def uid_de(plataforma, email=EMAIL):
    return plataforma.db().execute("SELECT id FROM usuarios WHERE email = ?", (email,)).fetchone()[0]


def inscricoes(plataforma):
    return plataforma.db().execute("SELECT * FROM push_inscricoes ORDER BY id").fetchall()


# ------------------------------------------------------------------ cifra e assinatura

def test_cifra_confere_com_o_exemplo_da_rfc_8291():
    """Apêndice A da RFC 8291: mesmas chaves e mesmo sal, o mesmo resultado, byte a byte."""
    servidor = ec.derive_private_key(int.from_bytes(de_b64u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
    corpo = cifrar(b"When I grow up, I want to be a watermelon",
                   de_b64u("BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"),
                   de_b64u("BTBZMqHH6r4Tts7J_aSIgg"), sal=de_b64u("DGv6ra1nlYgDCS1FRnbzlw"), chave_efemera=servidor, preenchimento=0)
    assert b64u(corpo) == ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6Tlz"
                           "AC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


def test_cifra_esconde_o_tamanho_e_o_aparelho_decifra():
    aparelho = Aparelho()
    curto = cifrar(json.dumps({"titulo": "Oi"}).encode(), aparelho.p256dh, aparelho.auth)
    longo = cifrar(json.dumps({"titulo": "Resumo", "texto": "á" * 50}, ensure_ascii=False).encode(), aparelho.p256dh, aparelho.auth)
    assert len(curto) == len(longo) == 256          # sobe de 256 em 256: o serviço não sabe qual aviso foi
    assert aparelho.decifrar(longo)["texto"] == "á" * 50
    assert len(cifrar(b"x" * 200, aparelho.p256dh, aparelho.auth)) == 512
    assert cifrar(b"x" * 3000, aparelho.p256dh, aparelho.auth)[:16] != cifrar(b"x" * 3000, aparelho.p256dh, aparelho.auth)[:16]
    with pytest.raises(ValueError):
        cifrar(b"x" * 4000, aparelho.p256dh, aparelho.auth)       # passa dos 4 KB que os serviços aceitam


def test_assinatura_do_servidor_vapid():
    relogio = [1_800_000_000]
    privada = ec.generate_private_key(ec.SECP256R1())
    v = Vapid(b64u(privada.private_numbers().private_value.to_bytes(32, "big")), "mailto:contato@exemplo.com.br", lambda: relogio[0])
    cabecalho = v.autorizacao("https://fcm.googleapis.com/fcm/send/abc")
    t, k = cabecalho.removeprefix("vapid t=").split(", k=")
    assert de_b64u(k) == privada.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    topo, dados, assinatura = t.split(".")
    assert json.loads(de_b64u(topo)) == {"typ": "JWT", "alg": "ES256"}
    assert json.loads(de_b64u(dados)) == {"aud": "https://fcm.googleapis.com", "exp": relogio[0] + 12 * 3600,
                                          "sub": "mailto:contato@exemplo.com.br"}
    bruta = de_b64u(assinatura)
    privada.public_key().verify(encode_dss_signature(int.from_bytes(bruta[:32], "big"), int.from_bytes(bruta[32:], "big")),
                                f"{topo}.{dados}".encode(), ec.ECDSA(hashes.SHA256()))
    # A Apple pede para não renovar mais de uma vez por hora: o mesmo cabeçalho serve por quase 11 horas.
    relogio[0] += 10 * 3600
    assert v.autorizacao("https://fcm.googleapis.com/fcm/send/outro") == cabecalho
    assert v.autorizacao("https://web.push.apple.com/abc") != cabecalho          # outro serviço, outro público
    relogio[0] += 3600 + 1
    assert v.autorizacao("https://fcm.googleapis.com/fcm/send/abc") != cabecalho


@pytest.mark.parametrize("endpoint, aceito", [
    ("https://fcm.googleapis.com/fcm/send/abc", True),
    ("https://fcm.googleapis.com:443/wp/abc", True),
    ("https://updates.push.services.mozilla.com/wpush/v2/abc", True),
    ("https://web.push.apple.com/QGuQyavXutnMH", True),
    ("https://wns2-par02p.notify.windows.com/w/?token=abc", True),
    ("http://fcm.googleapis.com/fcm/send/abc", False),                 # sem HTTPS
    ("https://localhost/fcm/send/abc", False),
    ("https://127.0.0.1/abc", False),
    ("https://169.254.169.254/latest/meta-data", False),
    ("https://fcm.googleapis.com:8443/abc", False),                    # outra porta
    ("https://usuario:senha@fcm.googleapis.com/abc", False),
    ("https://fcm.googleapis.com.exemplo.com/abc", False),
    ("https://exemplo.com/fcm.googleapis.com", False),
    ("https://storage.googleapis.com/balde", False),                   # outro serviço do Google
    ("https://notify.windows.com.exemplo.com/abc", False),
    ("https://fcm.googleapis.com/" + "a" * 3000, False),
    ("", False), (None, False), (42, False),
])
def test_so_aceita_os_servicos_de_notificacao_dos_navegadores(endpoint, aceito):
    assert endpoint_aceito(endpoint) is aceito


def test_envio_nao_segue_redirecionamento():
    """Proteção contra SSRF: um serviço que redireciona não leva o servidor para outro endereço."""
    pedidos = []

    class Tratador(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            pedidos.append(("POST", self.path))
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(302)          # o urllib, por padrão, seguiria este (virando GET)
            self.send_header("Location", "/interno")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            pedidos.append(("GET", self.path))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    servidor = http.server.HTTPServer(("127.0.0.1", 0), Tratador)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    try:
        status = enviar_http(f"http://127.0.0.1:{servidor.server_port}/push", b"x", {"TTL": "60"}, tempo_limite=5)
    finally:
        servidor.shutdown()
    assert status == 302 and pedidos == [("POST", "/push")]


# ------------------------------------------------------------------ configuração

def test_desenvolvimento_cria_a_chave_sozinho_e_producao_so_com_chave(tmp_path):
    pasta = tmp_path / "instancia"
    cfg = config.carregar({"MW_PASTA_INSTANCIA": str(pasta)})
    arquivo = pasta / "vapid-desenvolvimento.key"
    assert cfg.vapid_chave_privada and arquivo.exists()
    if os.name != "nt":   # o Windows não tem as permissões 600 do Linux
        assert arquivo.stat().st_mode & 0o777 == 0o600
    assert config.carregar({"MW_PASTA_INSTANCIA": str(pasta)}).vapid_chave_privada == cfg.vapid_chave_privada
    assert cfg.vapid_contato == "mailto:nao-responda@localhost"
    producao = {"MW_PASTA_INSTANCIA": str(tmp_path / "p"), "MW_MODO": "producao", "MW_CHAVE_CRIPTO": config.gerar_chave(),
                "MW_URL_PUBLICA": "https://painel.exemplo.com.br", "MW_SMTP_HOST": "smtp.exemplo.com.br"}
    cfg = config.carregar(producao)
    assert cfg.vapid_chave_privada is None                       # notificações desligadas, o resto funciona
    assert cfg.vapid_contato == "https://painel.exemplo.com.br"
    chave = config.gerar_chave_vapid()
    cfg = config.carregar({**producao, "MW_VAPID_CHAVE_PRIVADA": chave, "MW_EMAIL_PRIVACIDADE": "privacidade@exemplo.com.br"})
    assert cfg.vapid_chave_privada == chave and cfg.vapid_contato == "mailto:privacidade@exemplo.com.br"
    for ruim in ("nao-e-chave", b64u(b"\x00" * 32), b64u(b"\x01" * 31)):
        with pytest.raises(config.ConfiguracaoInvalida, match="MW_VAPID_CHAVE_PRIVADA"):
            config.carregar({**producao, "MW_VAPID_CHAVE_PRIVADA": ruim})
    with pytest.raises(config.ConfiguracaoInvalida, match="MW_VAPID_CONTATO"):
        config.carregar({**producao, "MW_VAPID_CONTATO": "contato@exemplo.com.br"})


def test_sem_chave_o_servidor_diz_que_nao_manda_notificacoes(tmp_path, relogio):
    cfg = config.carregar({"MW_PASTA_INSTANCIA": str(tmp_path / "i"), "MW_TARIFA_PADRAO": "1.00"})
    cfg = cfg.__class__(**{**cfg.__dict__, "vapid_chave_privada": None})
    p = Plataforma(cfg, MensageiroMemoria(), relogio=relogio)
    from conftest import Cliente
    app = criar_app(p)
    cliente = Cliente(app)
    conta_pronta(cliente, p)
    assert cliente.get("/api/notificacoes").get_json() == {"disponivel": False, "chave": None, "aparelhos": []}
    r = cliente.put("/api/notificacoes/inscricao", Aparelho().inscricao())
    assert r.status_code == 409 and r.get_json()["codigo"] == "sem_notificacoes"
    assert cliente.post("/api/notificacoes/conferir", Aparelho().inscricao()).get_json() == {"ligado": False, "id": None}
    assert p.notificacoes.entregar(1, "meta", "t", "x") == 0
    p.fechar_conexao_da_thread()


# ------------------------------------------------------------------ inscrições

def test_ligar_guarda_cifrado_e_lista_o_aparelho(cliente, plataforma, servico):
    conta_pronta(cliente, plataforma)
    estado = cliente.get("/api/notificacoes").get_json()
    assert estado["disponivel"] and len(de_b64u(estado["chave"])) == 65 and estado["aparelhos"] == []
    aparelho = Aparelho()
    r = ligar(cliente, aparelho)
    assert r.status_code == 200, r.get_json()
    assert [a["aparelho"] for a in r.get_json()["aparelhos"]] == ["Chrome no Android"]
    linha = inscricoes(plataforma)[0]
    assert aparelho.endpoint.encode() not in bytes(linha["dados_cifrados"])           # nada em claro no banco
    assert b64u(aparelho.auth).encode() not in bytes(linha["dados_cifrados"])
    assert linha["endpoint_hmac"] != aparelho.endpoint and linha["sessao_id"] is not None
    # ligar de novo no mesmo aparelho não duplica
    assert ligar(cliente, aparelho).get_json()["id"] == linha["id"]
    assert len(inscricoes(plataforma)) == 1
    # no app instalado, o nome diz isso
    ligar(cliente, Aparelho(), agente=IPHONE_APP, instalado=True)
    nomes = [a["aparelho"] for a in cliente.get("/api/notificacoes").get_json()["aparelhos"]]
    assert nomes == ["Chrome no Android", "App no iPhone"]
    atividade = [a["descricao"] for a in cliente.get("/api/privacidade/atividade").get_json()["atividade"]]
    assert atividade.count("Notificações ligadas num aparelho") == 2


def test_limite_de_aparelhos_por_conta(cliente, plataforma, relogio, servico):
    conta_pronta(cliente, plataforma)
    aparelhos = [Aparelho() for _ in range(12)]
    for aparelho in aparelhos:
        relogio.avancar(60)
        assert ligar(cliente, aparelho).status_code == 200
    assert len(inscricoes(plataforma)) == 10
    # saem os que estão há mais tempo sem atualizar; um aparelho que voltou a abrir o app fica
    assert cliente.post("/api/notificacoes/conferir", aparelhos[2].inscricao()).get_json()["ligado"] is True
    assert cliente.post("/api/notificacoes/conferir", aparelhos[0].inscricao()).get_json()["ligado"] is False
    relogio.avancar(60)
    cliente.post("/api/notificacoes/conferir", aparelhos[3].inscricao())
    relogio.avancar(60)
    ligar(cliente, Aparelho())
    assert cliente.post("/api/notificacoes/conferir", aparelhos[3].inscricao()).get_json()["ligado"] is True
    assert cliente.post("/api/notificacoes/conferir", aparelhos[4].inscricao()).get_json()["ligado"] is False
    plataforma.notificacoes.entregar(uid_de(plataforma), "meta", "Meta", "texto")
    assert len(servico.recebidos) == 10


def test_ligar_exige_sessao_e_token(cliente, plataforma, novo_cliente):
    conta_pronta(cliente, plataforma)
    assert novo_cliente().put("/api/notificacoes/inscricao", Aparelho().inscricao()).status_code == 401
    sem_token = cliente.http.put("/api/notificacoes/inscricao", json=Aparelho().inscricao(),
                                 headers={"Origin": "http://localhost:8000", "Sec-Fetch-Site": "same-origin"})
    assert sem_token.status_code == 403
    outro_site = cliente.put("/api/notificacoes/inscricao", Aparelho().inscricao(), headers={"Sec-Fetch-Site": "cross-site"})
    assert outro_site.status_code == 403


@pytest.mark.parametrize("mexer", [
    lambda d: d.update(endpoint="https://169.254.169.254/latest"),
    lambda d: d.update(endpoint="http://fcm.googleapis.com/fcm/send/x"),
    lambda d: d.pop("keys"),
    lambda d: d["keys"].update(auth=b64u(b"curta")),
    lambda d: d["keys"].update(p256dh=b64u(b"\x04" + b"\x01" * 64)),        # 65 bytes, mas fora da curva
    lambda d: d["keys"].update(p256dh=b64u(os.urandom(33))),
    lambda d: d["keys"].update(p256dh="não é base64"),
    lambda d: d["keys"].update(auth=17),
])
def test_ligar_recusa_endereco_ou_chaves_ruins(cliente, plataforma, servico, mexer):
    conta_pronta(cliente, plataforma)
    dados = Aparelho().inscricao()
    mexer(dados)
    r = cliente.put("/api/notificacoes/inscricao", dados)
    assert r.status_code == 400, r.get_json()
    assert inscricoes(plataforma) == []


def test_conferir_ao_abrir_o_app(cliente, plataforma, novo_cliente, servico):
    conta_pronta(cliente, plataforma)
    aparelho = Aparelho()
    assert cliente.post("/api/notificacoes/conferir", aparelho.inscricao()).get_json() == {"ligado": False, "id": None}
    iid = ligar(cliente, aparelho).get_json()["id"]
    assert cliente.post("/api/notificacoes/conferir", aparelho.inscricao()).get_json() == {"ligado": True, "id": iid}
    # O mesmo navegador passou para outra conta (outra pessoa entrou nele): a inscrição da Ana sai.
    bia = novo_cliente()
    conta_pronta(bia, plataforma, email="bia@exemplo.com.br")
    assert bia.post("/api/notificacoes/conferir", aparelho.inscricao()).get_json() == {"ligado": False, "id": None}
    assert inscricoes(plataforma) == []
    assert cliente.post("/api/notificacoes/conferir", aparelho.inscricao()).get_json()["ligado"] is False
    # E ligar pela outra conta leva o aparelho para ela.
    ligar(cliente, aparelho)
    ligar(bia, aparelho)
    assert [l["usuario_id"] for l in inscricoes(plataforma)] == [uid_de(plataforma, "bia@exemplo.com.br")]


def test_desligar_e_remover_so_mexem_na_propria_conta(cliente, plataforma, novo_cliente, servico):
    conta_pronta(cliente, plataforma)
    celular, computador = Aparelho(), Aparelho()
    ligar(cliente, celular)
    iid = ligar(cliente, computador).get_json()["id"]
    bia = novo_cliente()
    conta_pronta(bia, plataforma, email="bia@exemplo.com.br")
    assert bia.delete(f"/api/notificacoes/aparelhos/{iid}").status_code == 404
    assert bia.post("/api/notificacoes/desligar", {"endpoint": computador.endpoint}).status_code == 200
    assert len(inscricoes(plataforma)) == 2
    assert cliente.post("/api/notificacoes/desligar", {"endpoint": celular.endpoint}).status_code == 200
    r = cliente.delete(f"/api/notificacoes/aparelhos/{iid}")
    assert r.status_code == 200 and r.get_json()["aparelhos"] == []
    assert cliente.post("/api/notificacoes/desligar", {}).status_code == 400


# ------------------------------------------------------------------ quem perde o acesso para de receber

def test_sair_desliga_so_o_aparelho_que_saiu(novo_cliente, plataforma, servico):
    celular, computador = novo_cliente(), novo_cliente()
    conta_pronta(celular, plataforma)
    entrar(computador)
    ligar(celular, Aparelho())
    ligar(computador, Aparelho())
    assert celular.post("/api/conta/sair").status_code == 200
    restantes = inscricoes(plataforma)
    assert len(restantes) == 1 and restantes[0]["sessao_id"] is not None


def test_sessao_que_venceu_continua_recebendo(cliente, plataforma, relogio, servico):
    """Os avisos servem justamente para quando o app está fechado: a sessão vencer não desliga nada."""
    conta_pronta(cliente, plataforma)
    ligar(cliente, Aparelho())
    relogio.avancar(30 * 86400)
    assert cliente.get("/api/conta/eu").status_code == 401          # a sessão venceu e foi apagada
    linha = inscricoes(plataforma)[0]
    assert linha["sessao_id"] is None
    assert plataforma.notificacoes.entregar(linha["usuario_id"], "meta", "Meta", "texto") == 1


def test_encerrar_sessao_trocar_senha_e_ativar_2fa_desligam_os_outros_aparelhos(novo_cliente, plataforma, relogio, servico):
    a, b, c = novo_cliente(), novo_cliente(), novo_cliente()
    conta_pronta(a, plataforma)
    entrar(b), entrar(c)
    for cliente in (a, b, c):
        ligar(cliente, Aparelho())
    sessoes = a.get("/api/conta/sessoes").get_json()["sessoes"]
    outra = [s for s in sessoes if not s["atual"]][0]["id"]
    assert a.delete(f"/api/conta/sessoes/{outra}").status_code == 200
    assert len(inscricoes(plataforma)) == 2
    assert a.post("/api/conta/sessoes/encerrar-outras").status_code == 200
    assert len(inscricoes(plataforma)) == 1
    # trocar a senha: o aparelho de quem trocou continua; um que tinha a sessão vencida, não
    d = novo_cliente()
    entrar(d)
    ligar(d, Aparelho())
    plataforma.db().execute("UPDATE push_inscricoes SET sessao_id = NULL WHERE id = (SELECT MAX(id) FROM push_inscricoes)").connection.commit()
    assert a.post("/api/conta/senha", {"atual": SENHA, "nova": "Outra-senha-forte-99"}).status_code == 200
    assert len(inscricoes(plataforma)) == 1
    e = novo_cliente()
    entrar(e, senha="Outra-senha-forte-99")
    ligar(e, Aparelho())
    ativar_2fa(a, relogio)
    assert len(inscricoes(plataforma)) == 1 and inscricoes(plataforma)[0]["sessao_id"] is not None


def test_redefinir_a_senha_pelo_email_desliga_todos(cliente, plataforma, servico):
    conta_pronta(cliente, plataforma)
    ligar(cliente, Aparelho())
    cliente.post("/api/conta/esqueci", {"email": EMAIL})
    token = link_do_email(plataforma, EMAIL, "redefinir")
    assert cliente.post("/api/conta/redefinir", {"token": token, "senha": "Nova-senha-forte-77"}).status_code == 200
    assert inscricoes(plataforma) == []


def test_excluir_a_conta_apaga_as_inscricoes(cliente, plataforma, servico):
    conta_pronta(cliente, plataforma)
    ligar(cliente, Aparelho())
    assert cliente.post("/api/privacidade/excluir-conta", {"senha": SENHA}).status_code == 200
    assert inscricoes(plataforma) == []


# ------------------------------------------------------------------ entrega dos avisos

def test_aviso_chega_cifrado_em_cada_aparelho(cliente, plataforma, servico):
    conta_pronta(cliente, plataforma)
    celular = Aparelho()
    computador = Aparelho("https://updates.push.services.mozilla.com/wpush/v2/" + b64u(os.urandom(16)))
    ligar(cliente, celular)
    ligar(cliente, computador)
    uid = uid_de(plataforma)
    assert plataforma.alertas.criar(uid, "medidor_offline", "offline:mw-a1b2c3:1700000000", "Casa está sem sinal", "O medidor Casa…")
    assert len(servico.recebidos) == 2
    por_endpoint = {r["endpoint"]: r for r in servico.recebidos}
    for aparelho, publico in ((celular, "https://fcm.googleapis.com"), (computador, "https://updates.push.services.mozilla.com")):
        r = por_endpoint[aparelho.endpoint]
        c = r["cabecalhos"]
        assert c["Content-Encoding"] == "aes128gcm" and c["TTL"] == str(6 * 3600) and c["Urgency"] == "normal"
        assert c["Topic"] == "offline-mw-a1b2c3"
        jwt = c["Authorization"].removeprefix("vapid t=").split(", k=")[0]
        assert json.loads(de_b64u(jwt.split(".")[1]))["aud"] == publico
        dados = aparelho.decifrar(r["corpo"])
        assert dados["titulo"] == "Casa está sem sinal" and dados["url"] == "/#avisos" and dados["tag"] == "offline-mw-a1b2c3"
        assert dados["nao_lidos"] == 1
    assert all(l["ultimo_envio"] is not None for l in inscricoes(plataforma))
    # o e-mail continua saindo
    assert "sem sinal" in plataforma.mensageiro.ultimo_para(EMAIL)["assunto"]


def test_cada_tipo_de_aviso_escolhe_os_canais(cliente, plataforma, servico):
    conta_pronta(cliente, plataforma)
    ligar(cliente, Aparelho())
    uid = uid_de(plataforma)
    assert cliente.put("/api/avisos/config/resumo_semanal", {"por_push": False}).status_code == 200
    assert cliente.put("/api/avisos/config/meta", {"por_email": False}).status_code == 200
    assert cliente.put("/api/avisos/config/meta", {"por_push": "sim"}).status_code == 400
    assert cliente.put("/api/avisos/config/meta", {}).status_code == 400
    config_ = {c["tipo"]: c for c in cliente.get("/api/avisos").get_json()["config"]}
    assert (config_["resumo_semanal"]["por_push"], config_["resumo_semanal"]["por_email"]) == (False, True)
    assert (config_["meta"]["por_push"], config_["meta"]["por_email"]) == (True, False)
    emails = len(plataforma.mensageiro.enviados)
    plataforma.alertas.criar(uid, "resumo_semanal", "resumo:2026-11-16", "Resumo da semana", "A casa gastou 50 kWh.")
    assert servico.recebidos == [] and len(plataforma.mensageiro.enviados) == emails + 1
    plataforma.alertas.criar(uid, "meta", "meta:atencao:2026-11", "A conta está chegando perto da meta", "Previsão: R$ 171,00.")
    assert len(servico.recebidos) == 1 and len(plataforma.mensageiro.enviados) == emails + 1
    assert servico.recebidos[0]["cabecalhos"]["Topic"] == "meta"


@pytest.mark.parametrize("respostas, sobra", [
    ([410], 0), ([404], 0),                     # o navegador cancelou a inscrição
    ([403, 403], 1), ([403, 403, 403], 0),      # recusa por causa da inscrição: desiste na terceira seguida
    ([500, 500, 500, 500], 1), ([0, 0, 0, 0], 1), ([429, 429, 429], 1),   # problema do serviço ou da rede: não conta
    ([403, 403, 201, 403, 403], 1),             # um envio bom zera a contagem
])
def test_respostas_do_servico(cliente, plataforma, servico, respostas, sobra):
    conta_pronta(cliente, plataforma)
    ligar(cliente, Aparelho())
    uid = uid_de(plataforma)
    servico.respostas = list(respostas)
    for i in range(len(respostas)):
        plataforma.notificacoes.entregar(uid, "meta", "Meta", f"aviso {i}")
    assert len(inscricoes(plataforma)) == sobra


def test_falha_na_notificacao_nao_impede_o_email(cliente, plataforma, servico):
    conta_pronta(cliente, plataforma)
    ligar(cliente, Aparelho())

    def quebrado(*args):
        raise RuntimeError("serviço fora do ar")
    plataforma.notificacoes.transporte = quebrado
    assert plataforma.alertas.criar(uid_de(plataforma), "meta", "meta:acima:2026-11", "A previsão passou da meta do mês", "texto")
    assert "passou da meta" in plataforma.mensageiro.ultimo_para(EMAIL)["assunto"]
    assert len(inscricoes(plataforma)) == 1


def test_mandar_um_teste(cliente, plataforma, servico):
    conta_pronta(cliente, plataforma)
    aparelho = Aparelho()
    assert cliente.post("/api/notificacoes/teste", {"endpoint": aparelho.endpoint}).status_code == 404
    ligar(cliente, aparelho)
    assert cliente.post("/api/notificacoes/teste", {"endpoint": aparelho.endpoint}).status_code == 200
    r = servico.recebidos[-1]
    assert r["cabecalhos"]["Urgency"] == "high" and r["cabecalhos"]["TTL"] == "600"
    assert aparelho.decifrar(r["corpo"])["titulo"] == "Notificações ligadas"
    servico.respostas = [410]
    falhou = cliente.post("/api/notificacoes/teste", {"endpoint": aparelho.endpoint})
    assert falhou.status_code == 502 and falhou.get_json()["codigo"] == "falha_no_envio"
    ligar(cliente, aparelho)
    for _ in range(3):
        assert cliente.post("/api/notificacoes/teste", {"endpoint": aparelho.endpoint}).status_code == 200
    assert cliente.post("/api/notificacoes/teste", {"endpoint": aparelho.endpoint}).status_code == 429


def test_copia_dos_dados_lista_os_aparelhos_sem_segredos(cliente, plataforma, servico):
    conta_pronta(cliente, plataforma)
    aparelho = Aparelho()
    ligar(cliente, aparelho)
    texto = cliente.post("/api/privacidade/exportar").get_data(as_text=True)
    dados = json.loads(texto)
    assert [a["aparelho"] for a in dados["aparelhos_com_notificacoes"]] == ["Chrome no Android"]
    assert aparelho.endpoint not in texto and b64u(aparelho.auth) not in texto and b64u(aparelho.p256dh) not in texto


def test_inscricao_que_nao_abre_mais_e_descartada(cliente, plataforma, servico):
    """Se a chave de cifra do servidor mudar, as inscrições antigas não abrem: saem em vez de travar os envios."""
    conta_pronta(cliente, plataforma)
    ligar(cliente, Aparelho())
    plataforma.db().execute("UPDATE push_inscricoes SET dados_cifrados = ?", (b"lixo",)).connection.commit()
    assert plataforma.notificacoes.entregar(uid_de(plataforma), "meta", "Meta", "texto") == 0
    assert inscricoes(plataforma) == [] and servico.recebidos == []


def test_cadastro_sem_confirmar_nao_liga(cliente, plataforma):
    cadastrar(cliente, plataforma, confirmar=False)
    assert ligar(cliente, Aparelho()).status_code == 401
