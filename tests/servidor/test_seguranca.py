"""Peças de segurança isoladas: senhas, TOTP, tokens, códigos, cofre e limitador."""
import pytest

from meterwatt import config
from meterwatt.seguranca import (Cofre, Limitador, codigo_totp, confere_senha, confere_totp, hash_senha,
                                 normalizar_codigo_backup, normalizar_codigo_pareamento, novo_codigo_pareamento,
                                 novos_codigos_backup, problema_na_senha, resumir_ip, resumir_navegador,
                                 token_com_formato_valido, novo_token)

RFC_SEGREDO = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"   # "12345678901234567890" (RFC 6238, SHA-1)


def test_totp_vetores_da_rfc_6238():
    assert codigo_totp(RFC_SEGREDO, 59) == "287082"
    assert codigo_totp(RFC_SEGREDO, 1111111109) == "081804"
    assert codigo_totp(RFC_SEGREDO, 1234567890) == "005924"


def test_totp_janela_e_replay():
    t = 1_800_000_000
    passo = confere_totp(RFC_SEGREDO, codigo_totp(RFC_SEGREDO, t - 30), t)
    assert passo == (t - 30) // 30                       # um passo de tolerância para o relógio do celular
    assert confere_totp(RFC_SEGREDO, codigo_totp(RFC_SEGREDO, t - 90), t) is None
    assert confere_totp(RFC_SEGREDO, codigo_totp(RFC_SEGREDO, t), t, ultimo_passo=t // 30) is None   # código já usado
    assert confere_totp(RFC_SEGREDO, "12 34 56", t) is None
    assert confere_totp(RFC_SEGREDO, "abcdef", t) is None
    assert confere_totp(RFC_SEGREDO, None, t) is None


def test_senha_argon2id():
    h = hash_senha("Lampada-azul-2026")
    assert h.startswith("$argon2id$")
    assert confere_senha(h, "Lampada-azul-2026")[0]
    assert not confere_senha(h, "lampada-azul-2026")[0]
    assert not confere_senha("lixo", "x")[0]


@pytest.mark.parametrize("senha", ["curta", "senha12345", "aaaaaaaaaaaa", "1212121212", "x" * 129])
def test_senhas_fracas_recusadas(senha):
    assert problema_na_senha(senha, "ana@exemplo.com.br")


def test_senha_com_o_email_recusada():
    assert problema_na_senha("anabeatriz-2026", "anabeatriz@exemplo.com.br")
    assert problema_na_senha("Lampada-azul-2026", "ana@exemplo.com.br") is None


def test_codigos():
    c = novo_codigo_pareamento()
    assert len(c) == 9 and c[4] == "-"
    assert normalizar_codigo_pareamento(c.lower().replace("-", " ")) == c.replace("-", "")
    assert normalizar_codigo_pareamento("1234-567") is None
    assert normalizar_codigo_pareamento(None) is None
    codigos = novos_codigos_backup()
    assert len(set(codigos)) == 10
    assert normalizar_codigo_backup(codigos[0].upper()) == normalizar_codigo_backup(codigos[0])
    t = novo_token()
    assert token_com_formato_valido(t) and not token_com_formato_valido("x" * 10) and not token_com_formato_valido(None)


def test_cofre():
    cofre = Cofre(config.gerar_chave().encode())
    dado = cofre.cifrar("SEGREDO")
    assert b"SEGREDO" not in dado and cofre.decifrar(dado) == "SEGREDO"
    with pytest.raises(ValueError):
        Cofre(config.gerar_chave().encode()).decifrar(dado)
    assert cofre.hmac("a") == cofre.hmac("a") != cofre.hmac("b")


def test_limitador():
    lim = Limitador(3, 60)
    for _ in range(3):
        assert not lim.esgotado("k", 100)
        lim.registrar("k", 100)
    assert lim.esgotado("k", 100) > 0
    assert not lim.esgotado("k", 161)
    lim.registrar("k", 200)
    lim.zerar("k")
    assert not lim.esgotado("k", 200)


def test_ip_e_navegador_resumidos():
    assert resumir_ip("203.0.113.77") == "203.0.113.0/24"
    assert resumir_ip("2001:db8:abcd:12::1").endswith("/48")
    assert resumir_ip("lixo") is None
    nav = resumir_navegador("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36")
    assert "Chrome" in nav and "Windows" in nav and "130" not in nav


def test_producao_recusa_configuracao_insegura(tmp_path):
    with pytest.raises(config.ConfiguracaoInvalida):
        config.carregar({"MW_MODO": "producao", "MW_PASTA_INSTANCIA": str(tmp_path)})
    cfg = config.carregar({"MW_MODO": "producao", "MW_PASTA_INSTANCIA": str(tmp_path),
                           "MW_CHAVE_CRIPTO": config.gerar_chave(), "MW_URL_PUBLICA": "https://painel.exemplo.com.br",
                           "MW_SMTP_HOST": "smtp.exemplo.com.br"})
    assert cfg.cookie_seguro and cfg.email_modo == "smtp"
