"""Defesas da camada web: CSRF, origem, JSON, cabeçalhos, erros e cookie."""
from conftest import ORIGEM, SENHA, cadastrar, conta_pronta


def test_cabecalhos_de_seguranca(cliente):
    r = cliente.get("/api/publico/info")
    h = r.headers
    assert "default-src 'self'" in h["Content-Security-Policy"] and "unsafe-inline" not in h["Content-Security-Policy"]
    assert h["X-Frame-Options"] == "DENY" and h["X-Content-Type-Options"] == "nosniff"
    assert h["Referrer-Policy"] == "no-referrer" and h["Cache-Control"] == "no-store"
    assert "camera=()" in h["Permissions-Policy"]
    info = r.get_json()
    assert set(info["finalidades"]) == {"termos", "privacidade", "consumo", "historico_longo", "novidades"}


def test_cookie_httponly_samesite(cliente, plataforma):
    cadastrar(cliente, plataforma)
    r = cliente.post("/api/conta/entrar", {"email": "ana@exemplo.com.br", "senha": SENHA})
    cookie = r.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    assert "csrf" in r.get_json() and "token" not in r.get_json()


def test_csrf_obrigatorio(cliente, plataforma):
    conta_pronta(cliente, plataforma)
    bom = cliente.csrf
    cliente.csrf = None
    assert cliente.post("/api/medidores/codigo").get_json()["codigo"] == "csrf"
    cliente.csrf = "x" * 43
    assert cliente.post("/api/medidores/codigo").status_code == 403
    cliente.csrf = bom
    assert cliente.post("/api/medidores/codigo").status_code == 201


def test_outra_origem_recusada(cliente, plataforma):
    conta_pronta(cliente, plataforma)
    r = cliente.post("/api/medidores/codigo", headers={"Origin": "https://malicioso.exemplo"})
    assert r.status_code == 403 and r.get_json()["codigo"] == "origem"
    r = cliente.post("/api/conta/entrar", {"email": "a@b.co", "senha": "x"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_so_aceita_json(cliente, plataforma):
    r = cliente.http.post("/api/conta/entrar", data="email=a&senha=b", headers={"Origin": ORIGEM},
                          content_type="application/x-www-form-urlencoded")
    assert r.status_code == 415


def test_corpo_grande_recusado(cliente):
    r = cliente.post("/api/conta/cadastro", {"nome": "x" * 70000})
    assert r.status_code == 413


def test_erros_em_json_e_404_da_pagina(cliente):
    r = cliente.get("/api/nao-existe")
    assert r.status_code == 404 and r.get_json()["erro"]
    assert cliente.get("/qualquer-coisa").status_code == 404


def test_limite_de_formularios_por_ip(cliente):
    for _ in range(10):
        cliente.post("/api/conta/esqueci", {"email": "ninguem@exemplo.com.br"})
    assert cliente.post("/api/conta/esqueci", {"email": "ninguem@exemplo.com.br"}).status_code == 429


def test_sair_exige_csrf(cliente, plataforma):
    conta_pronta(cliente, plataforma)
    bom = cliente.csrf
    cliente.csrf = None
    assert cliente.post("/api/conta/sair").status_code == 403
    cliente.csrf = bom
    assert cliente.post("/api/conta/sair").status_code == 200
