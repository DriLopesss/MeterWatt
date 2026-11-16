"""Avisos: medidor sem sinal, meta, consumo fora do normal e resumo da semana."""
from datetime import datetime

from conftest import INICIO, conta_pronta, leitura_pzem, parear, preencher_baldes

EMAIL = "ana@exemplo.com.br"


def avisos(cliente):
    return cliente.get("/api/avisos").get_json()


def test_medidor_sem_sinal_avisa_uma_vez_por_queda(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    relogio.avancar(1)
    plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(100, 1))
    relogio.avancar(20 * 60)
    assert plataforma.alertas.verificar() == 0            # 20 minutos ainda é tolerado
    relogio.avancar(15 * 60)
    assert plataforma.alertas.verificar() == 1
    assert plataforma.alertas.verificar() == 0            # não repete
    a = avisos(cliente)["avisos"][0]
    assert a["tipo"] == "medidor_offline" and "sem sinal" in a["titulo"] and not a["lido"]
    email = plataforma.mensageiro.ultimo_para(EMAIL)
    assert "sem sinal" in email["assunto"] and "#avisos" in email["texto"]
    assert cliente.get("/api/conta/eu").get_json()["avisos_nao_lidos"] == 1
    assert cliente.post("/api/avisos/lidos").status_code == 200
    assert cliente.get("/api/conta/eu").get_json()["avisos_nao_lidos"] == 0
    # voltou e caiu de novo: é outra queda, avisa de novo
    plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(100, 2))
    relogio.avancar(40 * 60)
    assert plataforma.alertas.verificar() == 1


def test_medidor_que_nunca_mediu_nao_avisa(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    parear(cliente, plataforma)
    relogio.avancar(3600)
    assert plataforma.alertas.verificar() == 0


def test_desligar_o_email_mantem_o_aviso_no_aplicativo(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    assert cliente.put("/api/avisos/config/medidor_offline", {"por_email": False}).status_code == 200
    assert cliente.put("/api/avisos/config/inexistente", {"por_email": False}).status_code == 400
    assert cliente.put("/api/avisos/config/meta", {"por_email": "nao"}).status_code == 400
    relogio.avancar(1)
    plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(100, 1))
    antes = len(plataforma.mensageiro.enviados)
    relogio.avancar(40 * 60)
    assert plataforma.alertas.verificar() == 1
    assert len(plataforma.mensageiro.enviados) == antes
    dados = avisos(cliente)
    assert len(dados["avisos"]) == 1
    assert {c["tipo"]: c["por_email"] for c in dados["config"]}["medidor_offline"] is False


def test_meta_avisa_em_cada_nivel_uma_vez_por_mes(cliente, plataforma, relogio):
    relogio.t = plataforma.inicio_do_mes(INICIO) + 10 * 86400
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    cliente.put("/api/conta/preferencias", {"tarifa_kwh": 1.0, "meta_mensal_reais": 95})
    preencher_baldes(plataforma, mid, plataforma.inicio_do_mes(relogio.t), relogio.t, 125)   # previsão: R$ 90
    plataforma.db().execute("UPDATE medidores SET ultima_mensagem = ?", (relogio.t,)).connection.commit()
    assert plataforma.alertas.verificar() == 1
    assert avisos(cliente)["avisos"][0]["titulo"] == "A conta está chegando perto da meta"
    assert plataforma.alertas.verificar() == 0
    cliente.put("/api/conta/preferencias", {"tarifa_kwh": 1.0, "meta_mensal_reais": 80})
    assert plataforma.alertas.verificar() == 1
    a = avisos(cliente)["avisos"][0]
    assert a["titulo"] == "A previsão passou da meta do mês" and "R$ 10,00 acima" in a["texto"]


def test_consumo_fora_do_normal(cliente, plataforma, relogio):
    relogio.t = INICIO + 8 * 86400 + 600                  # 12h10
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    hora = int(relogio.t // 3600 * 3600) - 3600           # das 11h às 12h de hoje
    preencher_baldes(plataforma, mid, relogio.t - 8 * 86400, hora, 300)
    preencher_baldes(plataforma, mid, hora, hora + 3600, 1500)       # hoje, 5x o normal
    plataforma.db().execute("UPDATE medidores SET ultima_mensagem = ?", (relogio.t,)).connection.commit()
    assert plataforma.alertas.verificar() == 1
    a = avisos(cliente)["avisos"][0]
    assert a["tipo"] == "consumo_incomum" and "1,50 kWh" in a["texto"] and "0,30 kWh" in a["texto"]
    assert plataforma.alertas.verificar() == 0


def test_consumo_normal_nao_avisa(cliente, plataforma, relogio):
    relogio.t = INICIO + 8 * 86400 + 600
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    preencher_baldes(plataforma, mid, relogio.t - 8 * 86400, relogio.t, lambda t: 300 + (t // 300 % 3) * 40)
    plataforma.db().execute("UPDATE medidores SET ultima_mensagem = ?", (relogio.t,)).connection.commit()
    assert plataforma.alertas.verificar() == 0


def test_resumo_semanal_na_segunda(cliente, plataforma, relogio):
    zona = plataforma.cfg.zona
    segunda = datetime(2026, 11, 16, 9, 0, tzinfo=zona).timestamp()
    relogio.t = segunda - 86400                           # domingo
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    cliente.put("/api/conta/preferencias", {"tarifa_kwh": 1.0, "meta_mensal_reais": 200})
    inicio = plataforma.inicio_do_dia(segunda, 14)
    preencher_baldes(plataforma, mid, inicio, inicio + 7 * 86400, 250)                    # semana retrasada: 6 kWh/dia
    preencher_baldes(plataforma, mid, inicio + 7 * 86400, plataforma.inicio_do_dia(segunda), 200)   # semana passada: 4,8 kWh/dia
    plataforma.db().execute("UPDATE medidores SET ultima_mensagem = ?", (segunda,)).connection.commit()
    assert plataforma.alertas.verificar() == 0            # domingo: ainda não
    relogio.t = segunda - 3 * 3600                        # segunda, 6h: ainda não
    assert plataforma.alertas.verificar() == 0
    relogio.t = segunda
    plataforma.db().execute("UPDATE medidores SET ultima_mensagem = ?", (segunda,)).connection.commit()
    assert plataforma.alertas.verificar() == 1
    assert plataforma.alertas.verificar() == 0
    # a sessão do domingo ainda vale (menos de 24 h)
    texto = [a for a in avisos(cliente)["avisos"] if a["tipo"] == "resumo_semanal"][0]["texto"]
    assert "33,6 kWh" in texto and "R$ 33,60" in texto and "20% a menos" in texto and "42,0 kWh" in texto
    assert "Consumo fantasma: 200 W" in texto and "Previsão da conta do mês" in texto
    assert "Resumo da semana" in plataforma.mensageiro.ultimo_para(EMAIL)["assunto"]


def test_sem_autorizacao_de_consumo_nao_ha_avisos(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    relogio.avancar(1)
    plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(100, 1))
    cliente.put("/api/privacidade/consumo", {"aceito": False})
    relogio.avancar(40 * 60)
    assert plataforma.alertas.verificar() == 0


def test_avisos_de_uma_conta_nao_aparecem_em_outra(cliente, plataforma, relogio, novo_cliente):
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    bia = conta_pronta(novo_cliente(), plataforma, email="bia@exemplo.com.br")
    relogio.avancar(1)
    plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(100, 1))
    relogio.avancar(40 * 60)
    plataforma.alertas.verificar()
    assert len(avisos(cliente)["avisos"]) == 1 and avisos(bia)["avisos"] == []


def test_apagar_consumo_apaga_os_avisos(cliente, plataforma, relogio):
    from conftest import SENHA
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    relogio.avancar(1)
    plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/telemetria", leitura_pzem(100, 1))
    relogio.avancar(40 * 60)
    plataforma.alertas.verificar()
    assert cliente.post("/api/privacidade/apagar-consumo", {"senha": SENHA}).status_code == 200
    assert avisos(cliente)["avisos"] == []


def test_resumo_nao_compara_com_semana_medida_pela_metade(cliente, plataforma, relogio):
    zona = plataforma.cfg.zona
    segunda = datetime(2026, 11, 16, 9, 0, tzinfo=zona).timestamp()
    relogio.t = segunda
    conta_pronta(cliente, plataforma)
    mid = parear(cliente, plataforma)
    semana = plataforma.inicio_do_dia(segunda, 7)
    preencher_baldes(plataforma, mid, semana - 86400, plataforma.inicio_do_dia(segunda), 200)   # o medidor chegou um dia antes
    plataforma.db().execute("UPDATE medidores SET ultima_mensagem = ?", (segunda,)).connection.commit()
    assert plataforma.alertas.verificar() == 1
    texto = avisos(cliente)["avisos"][0]["texto"]
    assert "33,6 kWh" in texto and "semana anterior" not in texto
