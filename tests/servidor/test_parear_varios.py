"""Vários medidores na mesma conta, e o mesmo aparelho pareado de novo."""
import pytest

from conftest import conta_pronta
from meterwatt import config


def _codigo(cliente):
    r = cliente.post("/api/medidores/codigo")
    assert r.status_code == 201, r.get_json()
    return r.get_json()["codigo"]


def _parear(plataforma, codigo, mid):
    return plataforma.medidores.processar_mqtt(f"meterwatt/{mid}/pareamento", f'{{"codigo":"{codigo}"}}'.encode())


def _situacao(cliente, codigo):
    return cliente.post("/api/medidores/codigo/situacao", {"codigo": codigo}).get_json()


@pytest.fixture
def ambiente(tmp_path):
    return {"MW_PASTA_INSTANCIA": str(tmp_path / "instancia"), "MW_TARIFA_PADRAO": "1.00", "MW_LIMITE_MEDIDORES_GRATIS": "0"}


def test_muitos_medidores_sem_limite(cliente, plataforma):
    conta_pronta(cliente, plataforma)
    for i in range(12):
        codigo = _codigo(cliente)
        assert _parear(plataforma, codigo, f"mw-casa{i:02d}") == "pareado"
        s = _situacao(cliente, codigo)
        assert s["situacao"] == "pareado" and s["novo"] is True and s["medidor"] == f"mw-casa{i:02d}"
    assert len(cliente.get("/api/medidores").get_json()["medidores"]) == 12


def test_mesmo_aparelho_de_novo_avisa_que_ja_estava(cliente, plataforma, relogio):
    conta_pronta(cliente, plataforma)
    primeiro = _codigo(cliente)
    assert _situacao(cliente, primeiro) == {"situacao": "esperando"}
    _parear(plataforma, primeiro, "mw-casa01")
    relogio.avancar(60)
    segundo = _codigo(cliente)
    assert _parear(plataforma, segundo, "mw-casa01") == "pareado"
    s = _situacao(cliente, segundo)
    assert s["situacao"] == "pareado" and s["novo"] is False
    assert len(cliente.get("/api/medidores").get_json()["medidores"]) == 1


def test_situacao_so_da_propria_conta(cliente, plataforma, novo_cliente):
    conta_pronta(cliente, plataforma)
    codigo = _codigo(cliente)
    outra = conta_pronta(novo_cliente(), plataforma, email="bia@exemplo.com.br")
    assert outra.post("/api/medidores/codigo/situacao", {"codigo": codigo}).status_code == 404


def test_limite_zero_quer_dizer_sem_limite(tmp_path):
    cfg = config.carregar({"MW_PASTA_INSTANCIA": str(tmp_path), "MW_LIMITE_MEDIDORES_GRATIS": "3"})
    assert cfg.limite_medidores_gratis == 3 and cfg.limite_medidores_pago is None   # plano Casa: sem limite
    cfg = config.carregar({"MW_PASTA_INSTANCIA": str(tmp_path), "MW_LIMITE_MEDIDORES_GRATIS": "0"})
    assert cfg.limite_medidores_gratis is None
    with pytest.raises(config.ConfiguracaoInvalida):
        config.carregar({"MW_PASTA_INSTANCIA": str(tmp_path), "MW_LIMITE_MEDIDORES_GRATIS": "-1"})
