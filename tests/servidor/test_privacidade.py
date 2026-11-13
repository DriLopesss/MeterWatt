"""LGPD: autorizações, cópia dos dados, apagar consumo, excluir conta, atividade e retenção."""
import json

from conftest import SENHA, ativar_2fa, conta_pronta, entrar, leitura_pzem, parear
from meterwatt.seguranca import codigo_totp


def test_estado_e_historico_de_autorizacoes(cliente, plataforma):
    conta_pronta(cliente, plataforma, novidades=True)
    e = cliente.get("/api/privacidade").get_json()
    atuais = {f["id"]: f["aceito"] for f in e["finalidades"]}
    assert atuais == {"termos": True, "privacidade": True, "consumo": True, "historico_longo": False, "novidades": True}
    assert cliente.put("/api/privacidade/termos", {"aceito": False}).status_code == 400   # não revogável: é sair da conta
    assert cliente.put("/api/privacidade/inexistente", {"aceito": True}).status_code in (400, 404)
    assert cliente.put("/api/privacidade/historico_longo", {"aceito": "sim"}).status_code == 400
    assert cliente.put("/api/privacidade/historico_longo", {"aceito": True}).status_code == 200
    e = cliente.get("/api/privacidade").get_json()
    assert len([h for h in e["historico"] if h["finalidade"] == "Histórico de até 2 anos"]) == 2   # registra cada decisão


def test_exportar(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    relogio.avancar(1)
    plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(150, 10))
    r = cliente.post("/api/privacidade/exportar")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    dados = json.loads(r.data)
    assert dados["conta"]["email"] == "ana@exemplo.com.br"
    assert dados["medidores"][0]["leituras_recentes"][0]["potencia_w"] == 150
    texto = r.data.decode()
    for proibido in ("argon2", "senha_hash", "token", "totp", "csrf", "codigo_hash"):
        assert proibido not in texto
    for _ in range(4):
        cliente.post("/api/privacidade/exportar")
    assert cliente.post("/api/privacidade/exportar").status_code == 429


def test_apagar_consumo(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    relogio.avancar(1)
    plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(150, 10))
    assert cliente.post("/api/privacidade/apagar-consumo", {"senha": "errada-errada"}).status_code == 403
    assert cliente.post("/api/privacidade/apagar-consumo", {"senha": SENHA}).status_code == 200
    db = plataforma.db()
    assert db.execute("SELECT COUNT(*) FROM leituras").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM medidores").fetchone()[0] == 1


def test_excluir_conta_com_2fa(cliente, plataforma, relogio, novo_cliente):
    conta_pronta(cliente, plataforma)
    segredo, _ = ativar_2fa(cliente, relogio)
    parear(cliente, plataforma)
    relogio.avancar(60)
    assert cliente.post("/api/privacidade/excluir-conta", {"senha": SENHA}).status_code == 403   # falta o código
    r = cliente.post("/api/privacidade/excluir-conta", {"senha": SENHA, "codigo": codigo_totp(segredo, relogio())})
    assert r.status_code == 200
    db = plataforma.db()
    for tabela in ("usuarios", "sessoes", "medidores", "consentimentos", "codigos_backup", "auditoria", "codigos_pareamento"):
        assert db.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0] == 0, tabela
    linha = db.execute("SELECT email_hmac FROM exclusoes").fetchone()
    assert linha and "ana" not in linha["email_hmac"]
    assert "excluída" in plataforma.mensageiro.ultimo_para("ana@exemplo.com.br")["assunto"]
    assert cliente.get("/api/conta/eu").status_code == 401
    assert entrar(novo_cliente()).status_code == 401


def test_confirmacao_de_identidade_tem_limite(cliente, plataforma):
    conta_pronta(cliente, plataforma)
    for _ in range(5):
        assert cliente.post("/api/privacidade/apagar-consumo", {"senha": "errada-errada"}).status_code == 403
    assert cliente.post("/api/privacidade/apagar-consumo", {"senha": SENHA}).status_code == 429


def test_atividade_visivel_e_sem_dados_de_outros(cliente, plataforma, novo_cliente):
    conta_pronta(cliente, plataforma)
    conta_pronta(novo_cliente(), plataforma, email="bia@exemplo.com.br")
    eventos = [a["evento"] for a in cliente.get("/api/privacidade/atividade").get_json()["atividade"]]
    assert eventos[:3] == ["login", "email_confirmado", "cadastro"]
    assert all(a["rede"] in (None, "127.0.0.0/24") for a in cliente.get("/api/privacidade/atividade").get_json()["atividade"])


def test_retencao(cliente, plataforma, relogio, novo_cliente):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    relogio.avancar(1)
    plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(150, 10))
    nao_confirmada = novo_cliente()
    nao_confirmada.post("/api/conta/cadastro", {"nome": "Caio", "email": "caio@exemplo.com.br", "senha": SENHA,
                                                "aceites": {"termos": True, "privacidade": True, "consumo": True}})
    db = plataforma.db()
    relogio.avancar(49 * 3600)
    r = plataforma.privacidade.reter()
    assert r["leituras"] == 1 and r["consumo_5min"] == 0
    relogio.avancar(6 * 86400)
    assert plataforma.privacidade.reter()["contas_nao_confirmadas"] == 1
    relogio.avancar(85 * 86400)                        # passou de 90 dias
    assert plataforma.privacidade.reter()["consumo_5min"] == 1
    assert db.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 1


def test_retencao_respeita_historico_longo(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma, historico_longo=True)
    mid = parear(cliente, plataforma)
    relogio.avancar(1)
    plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(150, 10))
    relogio.avancar(100 * 86400)
    assert plataforma.privacidade.reter()["consumo_5min"] == 0
    relogio.avancar(700 * 86400)
    assert plataforma.privacidade.reter()["consumo_5min"] == 1
