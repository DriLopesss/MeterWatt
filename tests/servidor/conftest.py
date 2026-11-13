"""Apoio dos testes do aplicativo com contas (src/meterwatt)."""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from meterwatt import config  # noqa: E402
from meterwatt.mensageiro import MensageiroMemoria  # noqa: E402
from meterwatt.plataforma import Plataforma  # noqa: E402
from meterwatt.seguranca import codigo_totp  # noqa: E402
from meterwatt.web import criar_app  # noqa: E402

INICIO = datetime(2026, 11, 10, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo")).timestamp()
SENHA = "Lampada-azul-2026"
ORIGEM = "http://localhost:8000"


class Relogio:
    def __init__(self, t=INICIO):
        self.t = t

    def __call__(self):
        return self.t

    def avancar(self, segundos):
        self.t += segundos


class Cliente:
    """Navegador de mentira: guarda o cookie (o test client faz isso) e o token CSRF."""

    def __init__(self, app):
        self.http = app.test_client()
        self.csrf = None

    def _cab(self, extra=None):
        cab = {"Origin": ORIGEM, "Sec-Fetch-Site": "same-origin"}
        if self.csrf:
            cab["X-CSRF-Token"] = self.csrf
        cab.update(extra or {})
        return cab

    def get(self, url, **kw):
        return self.http.get(url, headers=self._cab(kw.pop("headers", None)), **kw)

    def enviar(self, metodo, url, dados=None, headers=None):
        r = self.http.open(url, method=metodo, json=dados if dados is not None else {}, headers=self._cab(headers))
        corpo = r.get_json(silent=True) or {}
        if isinstance(corpo, dict) and corpo.get("csrf"):
            self.csrf = corpo["csrf"]
        return r

    def post(self, url, dados=None, **kw):
        return self.enviar("POST", url, dados, **kw)

    def put(self, url, dados=None, **kw):
        return self.enviar("PUT", url, dados, **kw)

    def patch(self, url, dados=None, **kw):
        return self.enviar("PATCH", url, dados, **kw)

    def delete(self, url, dados=None, **kw):
        return self.enviar("DELETE", url, dados, **kw)


@pytest.fixture
def relogio():
    return Relogio()


@pytest.fixture
def ambiente(tmp_path):
    return {"MW_PASTA_INSTANCIA": str(tmp_path / "instancia"), "MW_TARIFA_PADRAO": "1.00"}


@pytest.fixture
def plataforma(ambiente, relogio):
    cfg = config.carregar(ambiente)
    p = Plataforma(cfg, MensageiroMemoria(), relogio=relogio)
    p.publicados = []
    p.publicar = lambda topico, msg: p.publicados.append((topico, msg))
    yield p
    p.fechar_conexao_da_thread()


@pytest.fixture
def app(plataforma):
    a = criar_app(plataforma)
    a.testing = True
    return a


@pytest.fixture
def novo_cliente(app):
    return lambda: Cliente(app)


@pytest.fixture
def cliente(novo_cliente):
    return novo_cliente()


def link_do_email(plataforma, email, rota):
    m = plataforma.mensageiro.ultimo_para(email)
    assert m, f"nenhum e-mail para {email}"
    marcador = f"/#{rota}/"
    assert marcador in m["texto"], m["texto"]
    return m["texto"].split(marcador, 1)[1].split()[0]


def cadastrar(cliente, plataforma, email="ana@exemplo.com.br", nome="Ana", senha=SENHA, confirmar=True, **aceites):
    escolhas = {"termos": True, "privacidade": True, "consumo": True}
    escolhas.update(aceites)
    r = cliente.post("/api/conta/cadastro", {"nome": nome, "email": email, "senha": senha, "aceites": escolhas})
    assert r.status_code == 202, r.get_json()
    if confirmar:
        token = link_do_email(plataforma, email, "confirmar")
        assert cliente.post("/api/conta/confirmar", {"token": token}).status_code == 200


def entrar(cliente, email="ana@exemplo.com.br", senha=SENHA):
    return cliente.post("/api/conta/entrar", {"email": email, "senha": senha})


def conta_pronta(cliente, plataforma, email="ana@exemplo.com.br", **aceites):
    cadastrar(cliente, plataforma, email=email, **aceites)
    r = entrar(cliente, email)
    assert r.status_code == 200 and r.get_json()["etapa"] == "completa"
    return cliente


def ativar_2fa(cliente, relogio):
    r = cliente.post("/api/conta/2fa/iniciar")
    assert r.status_code == 200
    segredo = r.get_json()["segredo"].replace(" ", "")
    r = cliente.post("/api/conta/2fa/ativar", {"codigo": codigo_totp(segredo, relogio())})
    assert r.status_code == 200, r.get_json()
    return segredo, r.get_json()["codigos_backup"]


def parear(cliente, plataforma, mid="mw-a1b2c3"):
    r = cliente.post("/api/medidores/codigo")
    assert r.status_code == 201, r.get_json()
    codigo = r.get_json()["codigo"]
    assert plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/pareamento", f'{{"codigo":"{codigo}"}}'.encode()) == "pareado"
    return mid


def leitura_pzem(potencia, energia_wh, tensao=220.0, **extra):
    import json
    d = {"sensor": "pzem004t", "sensor_ok": True, "tensao_v": tensao, "corrente_a": round(potencia / tensao, 3),
         "potencia_w": potencia, "energia_wh": energia_wh, "frequencia_hz": 60.0, "fp": 0.95, "rssi": -60, "fw": "3.2.0"}
    d.update(extra)
    return json.dumps(d).encode()
