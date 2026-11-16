"""Planos e cobrança: provedor simulado, Mercado Pago (com HTTP de mentira) e limite de medidores."""
import hashlib
import hmac
import json

import pytest

from conftest import SENHA, Cliente, conta_pronta, parear
from meterwatt import config
from meterwatt.mensageiro import MensageiroMemoria
from meterwatt.pagamentos import FalhaNoProvedor, ProvedorMercadoPago
from meterwatt.plataforma import Plataforma
from meterwatt.web import criar_app

EMAIL = "ana@exemplo.com.br"


def assinar_no_simulado(cliente, aprovado=True):
    link = cliente.post("/api/plano/assinar").get_json()["link"]
    referencia = link.split("#pagamento-teste/")[1]
    return cliente.post("/api/plano/pagamento-teste", {"referencia": referencia, "aprovado": aprovado})


def test_plano_gratis_limita_a_um_medidor(cliente, plataforma):
    conta_pronta(cliente, plataforma)
    estado = cliente.get("/api/plano").get_json()
    assert estado["plano"] == "gratis" and estado["modo_teste"] and estado["limite_gratis"] == 1 and estado["assinatura"] is None
    parear(cliente, plataforma)
    r = cliente.post("/api/medidores/codigo")
    assert r.status_code == 403 and r.get_json()["codigo"] == "limite_plano"


def test_assinar_libera_mais_medidores_e_cancelar_vale_ate_o_fim(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    parear(cliente, plataforma)
    r = assinar_no_simulado(cliente)
    assert r.status_code == 200 and r.get_json()["plano"] == "casa" and r.get_json()["assinatura"]["status"] == "ativa"
    assert "ativada" in plataforma.mensageiro.ultimo_para(EMAIL)["assunto"]
    assert cliente.get("/api/conta/eu").get_json()["plano"] == "casa"
    parear(cliente, plataforma, mid="mw-segundo")
    assert cliente.post("/api/plano/assinar").status_code == 409            # já tem assinatura
    # cancelar: sem data de próxima cobrança conhecida, o plano volta a grátis na hora
    r = cliente.post("/api/plano/cancelar")
    assert r.status_code == 200 and r.get_json()["plano"] == "gratis"
    assert "cancelada" in plataforma.mensageiro.ultimo_para(EMAIL)["assunto"]
    assert len(cliente.get("/api/medidores").get_json()["medidores"]) == 2     # nada é apagado
    assert cliente.post("/api/medidores/codigo").status_code == 403
    eventos = [a["evento"] for a in cliente.get("/api/privacidade/atividade").get_json()["atividade"]]
    assert "assinatura_ativada" in eventos and "assinatura_cancelada" in eventos


def test_cancelada_continua_valendo_ate_o_fim_do_periodo_pago(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    assinar_no_simulado(cliente)
    db = plataforma.db()
    with db:
        db.execute("UPDATE assinaturas SET proxima_cobranca = ?", (relogio() + 10 * 86400,))
    assert cliente.post("/api/plano/cancelar").get_json()["plano"] == "casa"
    relogio.avancar(86400 * 5)
    from conftest import entrar
    entrar(cliente)
    assert cliente.get("/api/plano").get_json()["plano"] == "casa"
    relogio.avancar(86400 * 6)
    entrar(cliente)
    estado = cliente.get("/api/plano").get_json()
    assert estado["plano"] == "gratis"
    assert cliente.post("/api/plano/assinar").status_code == 200              # pode assinar de novo


def test_pagamento_recusado_e_referencia_de_outra_conta(cliente, plataforma, novo_cliente):
    conta_pronta(cliente, plataforma)
    r = assinar_no_simulado(cliente, aprovado=False)
    assert r.get_json()["plano"] == "gratis"
    link = cliente.post("/api/plano/assinar").get_json()["link"]
    assert cliente.post("/api/plano/assinar").get_json()["link"] == link       # pendente: devolve o mesmo link
    bia = conta_pronta(novo_cliente(), plataforma, email="bia@exemplo.com.br")
    r = bia.post("/api/plano/pagamento-teste", {"referencia": link.split("/")[-1], "aprovado": True})
    assert r.status_code == 404
    assert cliente.get("/api/plano").get_json()["plano"] == "gratis"


def test_producao_recusa_pagamento_simulado(tmp_path):
    base = {"MW_MODO": "producao", "MW_PASTA_INSTANCIA": str(tmp_path), "MW_CHAVE_CRIPTO": config.gerar_chave(),
            "MW_URL_PUBLICA": "https://painel.exemplo.com.br", "MW_SMTP_HOST": "smtp.exemplo.com.br"}
    assert config.carregar(base).pagamento_provedor == "desligado"
    with pytest.raises(config.ConfiguracaoInvalida):
        config.carregar({**base, "MW_PAGAMENTO_PROVEDOR": "simulado"})
    with pytest.raises(config.ConfiguracaoInvalida):
        config.carregar({**base, "MW_PAGAMENTO_PROVEDOR": "mercadopago"})


# ------------------------------------------------------------ Mercado Pago com HTTP de mentira

class MercadoPagoFalso:
    def __init__(self):
        self.pedidos, self.status, self.fora_do_ar = [], {}, False

    def __call__(self, metodo, url, cabecalhos, corpo=None):
        self.pedidos.append((metodo, url, cabecalhos, corpo))
        if self.fora_do_ar:
            raise FalhaNoProvedor("fora_do_ar")
        assert cabecalhos["Authorization"] == "Bearer TEST-token"
        if metodo == "POST" and url.endswith("/preapproval"):
            self.status["2c938084abc"] = "pending"
            return 201, {"id": "2c938084abc", "status": "pending", "init_point": "https://www.mercadopago.com.br/subscriptions/checkout?preapproval_id=2c938084abc"}
        ident = url.rsplit("/", 1)[1]
        if metodo == "PUT":
            self.status[ident] = corpo["status"]
        if ident not in self.status:
            return 404, {}
        return 200, {"id": ident, "status": self.status[ident], "next_payment_date": "2026-12-10T12:00:00.000-03:00"}


@pytest.fixture
def mp(ambiente, relogio):
    falso = MercadoPagoFalso()
    ambiente.update({"MW_PAGAMENTO_PROVEDOR": "mercadopago", "MW_MP_ACCESS_TOKEN": "TEST-token", "MW_MP_WEBHOOK_SEGREDO": "segredo-do-webhook"})
    cfg = config.carregar(ambiente)
    provedor = ProvedorMercadoPago(cfg.mp_access_token, cfg.mp_webhook_segredo, cfg.url_publica, http=falso)
    p = Plataforma(cfg, MensageiroMemoria(), relogio=relogio, provedor_de_pagamento=provedor)
    p.publicar = lambda *a: None
    app = criar_app(p)
    return p, Cliente(app), falso, app


def aviso_do_mp(app, ident, segredo="segredo-do-webhook", tipo="subscription_preapproval"):
    ts, pedido = "1794845000", "req-123"
    v1 = hmac.new(segredo.encode(), f"id:{ident};request-id:{pedido};ts:{ts};".encode(), hashlib.sha256).hexdigest()
    return app.test_client().post(f"/api/pagamentos/webhook/mercadopago?data.id={ident}&type={tipo}",
                                  json={"type": tipo, "action": "updated", "data": {"id": ident}},
                                  headers={"x-signature": f"ts={ts},v1={v1}", "x-request-id": pedido})


def test_mercado_pago_fluxo_completo(mp):
    p, cliente, falso, app = mp
    conta_pronta(cliente, p)
    r = cliente.post("/api/plano/assinar")
    assert r.status_code == 200 and r.get_json()["link"].startswith("https://www.mercadopago.com.br/")
    metodo, url, _, corpo = falso.pedidos[0]
    assert (metodo, url) == ("POST", "https://api.mercadopago.com/preapproval")
    assert corpo["payer_email"] == EMAIL and corpo["auto_recurring"] == {
        "frequency": 1, "frequency_type": "months", "transaction_amount": 14.9, "currency_id": "BRL"}
    assert corpo["back_url"].endswith("/#plano") and EMAIL not in corpo["external_reference"]
    assert cliente.get("/api/plano").get_json()["plano"] == "gratis"            # ainda não pagou
    # a pessoa paga na página do Mercado Pago, que avisa o servidor
    falso.status["2c938084abc"] = "authorized"
    assert aviso_do_mp(app, "2c938084abc").status_code == 200
    estado = cliente.get("/api/plano").get_json()
    assert estado["plano"] == "casa" and estado["assinatura"]["proxima_cobranca"].startswith("2026-12-10")
    assert aviso_do_mp(app, "2c938084abc").status_code == 200                    # aviso repetido não quebra nada
    # cancelar pelo aplicativo cancela no Mercado Pago e vale até o fim do período pago
    r = cliente.post("/api/plano/cancelar").get_json()
    assert falso.status["2c938084abc"] == "cancelled"
    assert r["assinatura"]["status"] == "cancelada" and r["plano"] == "casa" and r["assinatura"]["valida_ate"].startswith("2026-12-10")


def test_webhook_com_assinatura_errada_e_recusado(mp):
    p, cliente, falso, app = mp
    conta_pronta(cliente, p)
    cliente.post("/api/plano/assinar")
    falso.status["2c938084abc"] = "authorized"
    assert aviso_do_mp(app, "2c938084abc", segredo="outro-segredo").status_code == 401
    assert app.test_client().post("/api/pagamentos/webhook/mercadopago?data.id=2c938084abc", json={"type": "subscription_preapproval"}).status_code == 401
    assert cliente.get("/api/plano").get_json()["plano"] == "gratis"
    assert aviso_do_mp(app, "id-desconhecido").status_code == 200                # assinado, mas não é nosso: ignora


def test_voltar_da_pagina_de_pagamento_atualiza_sem_webhook(mp):
    p, cliente, falso, _ = mp
    conta_pronta(cliente, p)
    cliente.post("/api/plano/assinar")
    falso.status["2c938084abc"] = "authorized"
    assert cliente.post("/api/plano/atualizar").get_json()["plano"] == "casa"


def test_excluir_conta_cancela_a_cobranca_ou_nao_exclui(mp):
    p, cliente, falso, _ = mp
    conta_pronta(cliente, p)
    cliente.post("/api/plano/assinar")
    falso.status["2c938084abc"] = "authorized"
    cliente.post("/api/plano/atualizar")
    falso.fora_do_ar = True
    r = cliente.post("/api/privacidade/excluir-conta", {"senha": SENHA})
    assert r.status_code == 502 and r.get_json()["codigo"] == "pagamento_indisponivel"
    assert p.db().execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 1   # a conta continua lá
    falso.fora_do_ar = False
    assert cliente.post("/api/privacidade/excluir-conta", {"senha": SENHA}).status_code == 200
    assert falso.status["2c938084abc"] == "cancelled"
    assert p.db().execute("SELECT COUNT(*) FROM assinaturas").fetchone()[0] == 0


def test_mercado_pago_fora_do_ar_ao_assinar(mp):
    p, cliente, falso, _ = mp
    conta_pronta(cliente, p)
    falso.fora_do_ar = True
    assert cliente.post("/api/plano/assinar").status_code == 502
    assert p.db().execute("SELECT COUNT(*) FROM assinaturas").fetchone()[0] == 0


def test_exportar_inclui_assinaturas_sem_dados_de_cartao(cliente, plataforma):
    conta_pronta(cliente, plataforma)
    assinar_no_simulado(cliente)
    dados = json.loads(cliente.post("/api/privacidade/exportar").data)
    assert dados["assinaturas"][0]["status"] == "ativa" and "referencia" not in dados["assinaturas"][0]
