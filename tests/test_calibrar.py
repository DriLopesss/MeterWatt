"""Testes da ferramenta de calibração."""
import csv
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import calibrar  # noqa: E402

T0 = datetime(2026, 11, 20, 14, 0, 0, tzinfo=timezone.utc)
CAMPOS_LEITURAS = ["recebido_em", "dispositivo", "sensor", "sensor_ok", "tensao_v", "corrente_a",
                   "potencia_w", "va", "energia_wh", "frequencia_hz", "fp", "rssi", "uptime_s", "simulado"]


def leitura(seg, v, i, p, fp=1.0, disp="mw-A", ok=True, simulado=False):
    return {"recebido_em": (T0 + timedelta(seconds=seg)).isoformat(timespec="seconds"), "dispositivo": disp,
            "sensor": "pzem004t-v3", "sensor_ok": ok, "tensao_v": v if ok else "", "corrente_a": i if ok else "",
            "potencia_w": p if ok else "", "fp": fp if ok else "", "simulado": simulado}


def medida(ini, fim, carga, v, i, p="", resistiva="sim"):
    return {"inicio": (T0 + timedelta(seconds=ini)).isoformat(timespec="seconds"),
            "fim": (T0 + timedelta(seconds=fim)).isoformat(timespec="seconds"),
            "carga": carga, "resistiva": resistiva, "tensao_ref_v": v, "corrente_ref_a": i,
            "potencia_ref_w": p, "observacao": ""}


def gravar(tmp_path, leituras, medidas):
    a, b = tmp_path / "leituras.csv", tmp_path / "medidas.csv"
    for caminho, campos, linhas in ((a, CAMPOS_LEITURAS, leituras), (b, calibrar.CAMPOS_MEDIDAS, medidas)):
        with open(caminho, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=campos)
            w.writeheader()
            w.writerows(linhas)
    return calibrar.carregar_leituras(a), calibrar.carregar_medidas(b)


def test_erros_calculados_por_janela(tmp_path):
    leituras = ([leitura(s, 127.0, 0.0, 0.0) for s in range(0, 40, 5)] +            # sem carga
                [leitura(s, 126.0, 10.2, 1285.0) for s in range(100, 160, 5)] +      # chaleira
                [leitura(s, 126.0, 99.0, 9999.0) for s in range(300, 320, 5)])       # fora de qualquer janela
    medidas = [medida(0, 40, "sem carga", "127,0", "0"), medida(100, 160, "chaleira", "126,0", "10,0")]
    r = calibrar.analisar(*gravar(tmp_path, leituras, medidas), meta_pct=5)
    assert r["dispositivo"] == "mw-A" and not r["simulado"]
    vazio, chaleira = r["linhas"]
    assert vazio["sem_carga"] and vazio["erros"]["corrente"] is None and vazio["situacao"] == "dentro da meta"
    assert chaleira["n"] == 12
    assert chaleira["erros"]["tensao"] == pytest.approx(0.0)
    assert chaleira["erros"]["corrente"] == pytest.approx(2.0)
    assert chaleira["potencia_ref_w"] == pytest.approx(1260.0) and chaleira["origem_p"] == "V × I"
    assert chaleira["erros"]["potencia"] == pytest.approx(1.98, abs=0.01)
    assert chaleira["situacao"] == "dentro da meta"


def test_fora_da_meta_e_meta_ajustavel(tmp_path):
    leituras = [leitura(s, 127.0, 10.8, 1370.0) for s in range(0, 60, 5)]
    dados = gravar(tmp_path, leituras, [medida(0, 60, "ferro", 127.0, 10.0)])
    assert calibrar.analisar(*dados, meta_pct=5)["linhas"][0]["situacao"] == "fora da meta"     # 8% de erro
    assert calibrar.analisar(*dados, meta_pct=10)["linhas"][0]["situacao"] == "dentro da meta"


def test_medidor_acusando_corrente_sem_carga(tmp_path):
    leituras = [leitura(s, 127.0, 0.3, 30.0) for s in range(0, 60, 5)]
    r = calibrar.analisar(*gravar(tmp_path, leituras, [medida(0, 60, "sem carga", 127.0, 0)]))
    assert r["linhas"][0]["situacao"] == "fora da meta"


def test_carga_nao_resistiva_nao_inventa_potencia(tmp_path):
    leituras = [leitura(s, 127.0, 2.0, 190.0, fp=0.75) for s in range(0, 60, 5)]
    r = calibrar.analisar(*gravar(tmp_path, leituras, [medida(0, 60, "geladeira", 127.0, 2.0, resistiva="nao")]))
    linha = r["linhas"][0]
    assert linha["potencia_ref_w"] is None and linha["erros"]["potencia"] is None
    assert linha["erros"]["corrente"] == pytest.approx(0.0) and linha["fp"] == pytest.approx(0.75)


def test_avisos_de_poucas_leituras_falhas_e_simulador(tmp_path):
    leituras = ([leitura(0, 127.0, 5.0, 635.0, simulado=True), leitura(5, 127.0, 5.0, 635.0, simulado=True)] +
                [leitura(s, 0, 0, 0, ok=False) for s in range(10, 60, 5)])               # falhas não contam
    r = calibrar.analisar(*gravar(tmp_path, leituras, [medida(0, 60, "lâmpada", 127.0, 5.0),
                                                       medida(500, 560, "esquecida", 127.0, 5.0)]))
    assert r["simulado"]
    assert r["linhas"][0]["n"] == 2 and r["linhas"][0]["situacao"] == "poucas leituras"
    assert r["linhas"][1]["n"] == 0 and r["linhas"][1]["situacao"] == "sem leituras"
    texto = calibrar.montar_relatorio(r)
    assert "simulador" in texto and "Poucas leituras" in texto and "sem leituras" in texto


def test_dois_medidores_exigem_escolha(tmp_path):
    leituras = [leitura(s, 127, 1, 127, disp=d) for s in range(0, 60, 5) for d in ("mw-A", "mw-B")]
    dados = gravar(tmp_path, leituras, [medida(0, 60, "lâmpada", 127.0, 1.0)])
    with pytest.raises(SystemExit, match="mw-A, mw-B"):
        calibrar.analisar(*dados)
    assert calibrar.analisar(*dados, dispositivo="mw-B")["linhas"][0]["n"] == 12


def test_relatorio_completo_pela_linha_de_comando(tmp_path):
    leituras = [leitura(s, 126.4, 10.1, 1270.0) for s in range(0, 60, 5)]
    gravar(tmp_path, leituras, [medida(0, 60, "chaleira", "126,0", "10,0", p="1265")])
    saida = tmp_path / "docs" / "calibracao.md"
    calibrar.main(["relatorio", "--leituras", str(tmp_path / "leituras.csv"),
                   "--medidas", str(tmp_path / "medidas.csv"), "--saida", str(saida), "--meta", "2"])
    texto = saida.read_text(encoding="utf-8")
    assert "| chaleira | 12 | 126,0 V | 126,4 V | +0,3% | 10,000 A | 10,100 A | +1,0% | 1265 W (instrumento) | 1270 W | +0,4% | 1,00 | dentro da meta |" in texto
    assert "Nenhuma carga ficou fora da meta de 2,0%" in texto


def test_anotar_grava_as_medidas(tmp_path, monkeypatch):
    respostas = iter(["chaleira", "", "", "", "127,4", "10,2", "", "tomada da cozinha", ""])
    monkeypatch.setattr("builtins.input", lambda _texto="": next(respostas))
    caminho = tmp_path / "docs" / "medidas.csv"
    calibrar.anotar(str(caminho))
    m = calibrar.carregar_medidas(caminho)
    assert len(m) == 1 and m[0]["carga"] == "chaleira" and m[0]["resistiva"]
    assert m[0]["tensao_ref_v"] == 127.4 and m[0]["corrente_ref_a"] == 10.2 and m[0]["potencia_ref_w"] is None
    assert m[0]["observacao"] == "tomada da cozinha" and m[0]["fim"] >= m[0]["inicio"]
