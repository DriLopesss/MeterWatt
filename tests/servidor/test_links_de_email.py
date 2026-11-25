"""Links do e-mail que não valem mais: a mensagem diz por quê, e a caixa de saída de teste não confunde."""
import os

from conftest import cadastrar, link_do_email
from meterwatt.mensageiro import MensageiroArquivo

EMAIL = "ana@exemplo.com.br"


def _erro(r):
    assert r.status_code == 400, r.get_json()
    return r.get_json()["erro"]


def test_confirmar_duas_vezes_nao_da_erro(cliente, plataforma):
    cadastrar(cliente, plataforma, confirmar=False)
    token = link_do_email(plataforma, EMAIL, "confirmar")
    r = cliente.post("/api/conta/confirmar", {"token": token})
    assert r.status_code == 200 and r.get_json()["situacao"] == "confirmado"
    r = cliente.post("/api/conta/confirmar", {"token": token})      # abriu o mesmo e-mail de novo
    assert r.status_code == 200 and r.get_json()["situacao"] == "ja_confirmado"


def test_link_de_confirmacao_trocado_por_um_mais_novo(cliente, plataforma, relogio):
    cadastrar(cliente, plataforma, confirmar=False)
    velho = link_do_email(plataforma, EMAIL, "confirmar")
    relogio.avancar(120)
    assert cliente.post("/api/conta/reenviar", {"email": EMAIL}).status_code == 202
    novo = link_do_email(plataforma, EMAIL, "confirmar")
    assert novo != velho
    assert "mais novo" in _erro(cliente.post("/api/conta/confirmar", {"token": velho}))
    assert cliente.post("/api/conta/confirmar", {"token": novo}).status_code == 200
    # Depois de confirmar, o link velho também não assusta ninguém.
    assert cliente.post("/api/conta/confirmar", {"token": velho}).get_json()["situacao"] == "ja_confirmado"


def test_link_de_confirmacao_vencido_ou_cortado(cliente, plataforma, relogio):
    cadastrar(cliente, plataforma, confirmar=False)
    token = link_do_email(plataforma, EMAIL, "confirmar")
    assert "completo" in _erro(cliente.post("/api/conta/confirmar", {"token": token[:-5]}))
    relogio.avancar(25 * 3600)
    assert "24 horas" in _erro(cliente.post("/api/conta/confirmar", {"token": token}))


def test_conferir_link_de_senha_antes_de_digitar(cliente, plataforma, relogio):
    cadastrar(cliente, plataforma)
    cliente.post("/api/conta/esqueci", {"email": EMAIL})
    velho = link_do_email(plataforma, EMAIL, "redefinir")
    r = cliente.post("/api/conta/redefinir/conferir", {"token": velho})
    assert r.status_code == 200 and r.get_json()["vale"] is True
    assert cliente.post("/api/conta/redefinir/conferir", {"token": velho}).status_code == 200   # conferir não gasta

    relogio.avancar(120)
    cliente.post("/api/conta/esqueci", {"email": EMAIL})
    novo = link_do_email(plataforma, EMAIL, "redefinir")
    assert "mais novo" in _erro(cliente.post("/api/conta/redefinir/conferir", {"token": velho}))
    assert cliente.post("/api/conta/redefinir", {"token": novo, "senha": "Tomada-verde-2027"}).status_code == 200
    assert "já foi usado" in _erro(cliente.post("/api/conta/redefinir/conferir", {"token": novo}))
    assert "completo" in _erro(cliente.post("/api/conta/redefinir/conferir", {"token": "abc"}))


def test_link_de_senha_vencido(cliente, plataforma, relogio):
    cadastrar(cliente, plataforma)
    cliente.post("/api/conta/esqueci", {"email": EMAIL})
    token = link_do_email(plataforma, EMAIL, "redefinir")
    relogio.avancar(31 * 60)
    assert "30 minutos" in _erro(cliente.post("/api/conta/redefinir/conferir", {"token": token}))


def test_caixa_de_saida_guarda_os_antigos_ao_ligar(tmp_path, capsys):
    m = MensageiroArquivo(str(tmp_path))
    m.enviar(EMAIL, "Confirme o seu e-mail no MeterWatt", "Abra: http://localhost:8000/#confirmar/abc123\n")
    assert "link: http://localhost:8000/#confirmar/abc123" in capsys.readouterr().out
    pasta = tmp_path / "caixa-de-saida"
    assert len(list(pasta.glob("*.txt"))) == 1

    MensageiroArquivo(str(tmp_path))                 # servidor ligado de novo
    assert list(pasta.glob("*.txt")) == []
    assert len(list((pasta / "antigos").glob("*.txt"))) == 1
    assert os.path.isdir(pasta / "antigos")
