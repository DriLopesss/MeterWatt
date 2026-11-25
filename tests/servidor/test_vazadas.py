"""Senhas vazadas (Pwned Passwords): só os 5 primeiros caracteres do SHA-1 saem; falha do serviço não bloqueia."""
import hashlib
import urllib.error

from conftest import SENHA, conta_pronta

from meterwatt.vazadas import SenhasVazadas

VAZADA = "Primavera-verao-2026"


def servico(vazadas, pedidos):
    """O serviço de mentira: responde como o de verdade, com finais parecidos e o "padding" (contagem 0)."""
    def consultar(prefixo):
        pedidos.append(prefixo)
        linhas = ["0018A45C4D1DEF81644B54AB7F969B88D65:1", "00D4F6E8FA6EECAD2A3AA415EEC418D38EC:0"]
        for senha, vezes in vazadas.items():
            h = hashlib.sha1(senha.encode()).hexdigest().upper()
            if h[:5] == prefixo:
                linhas.append(f"{h[5:]}:{vezes}")
        return "\r\n".join(linhas)
    return consultar


def test_so_o_comeco_do_resumo_sai():
    pedidos = []
    v = SenhasVazadas(True, servico({VAZADA: 4120}, pedidos))
    assert v.vezes(VAZADA) == 4120
    assert pedidos == [hashlib.sha1(VAZADA.encode()).hexdigest().upper()[:5]] and len(pedidos[0]) == 5
    assert v.vezes(SENHA) == 0
    assert SenhasVazadas(False, servico({VAZADA: 1}, [])).vezes(VAZADA) is None      # desligado: nem consulta


def test_padding_nao_conta_e_falha_nao_bloqueia():
    def padding(prefixo):
        h = hashlib.sha1(VAZADA.encode()).hexdigest().upper()
        return f"{h[5:]}:0"
    assert SenhasVazadas(True, padding).vezes(VAZADA) == 0

    def fora_do_ar(_prefixo):
        raise urllib.error.URLError("sem rede")
    assert SenhasVazadas(True, fora_do_ar).vezes(VAZADA) is None


def test_cadastro_e_troca_recusam_senha_vazada(cliente, plataforma):
    plataforma.vazadas = SenhasVazadas(True, servico({VAZADA: 4120, "Outra-senha-vazada-9": 1}, []))
    r = cliente.post("/api/conta/cadastro", {"nome": "Bia", "email": "bia@exemplo.com.br", "senha": VAZADA,
                                             "aceites": {"termos": True, "privacidade": True, "consumo": True}})
    assert r.status_code == 400 and "vazamentos" in r.get_json()["erro"] and "4.120 vezes" in r.get_json()["erro"]
    conta_pronta(cliente, plataforma)
    r = cliente.post("/api/conta/senha", {"atual": SENHA, "nova": "Outra-senha-vazada-9"})
    assert r.status_code == 400 and "uma vez" in r.get_json()["erro"]


def test_servico_fora_do_ar_deixa_cadastrar(cliente, plataforma):
    def fora_do_ar(_prefixo):
        raise OSError("tempo esgotado")
    plataforma.vazadas = SenhasVazadas(True, fora_do_ar)
    conta_pronta(cliente, plataforma)
    assert cliente.get("/api/conta/eu").status_code == 200
