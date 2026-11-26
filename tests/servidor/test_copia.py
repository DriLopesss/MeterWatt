"""Cópias de segurança: cifradas, restauráveis e com limpeza das antigas."""
import os
import sqlite3

import pytest

from conftest import conta_pronta
from meterwatt import config, copia


def test_backup_cifrado_e_restauracao(cliente, plataforma, tmp_path):
    conta_pronta(cliente, plataforma)
    arquivo = copia.fazer(plataforma.cfg)
    bruto = open(arquivo, "rb").read()
    assert b"ana@exemplo.com.br" not in bruto and b"SQLite format" not in bruto      # nada legível sem a chave
    if os.name != "nt":   # o Windows não tem as permissões 600 do Linux (lá vale a da pasta do usuário)
        assert oct(os.stat(arquivo).st_mode)[-3:] == "600"
    destino = str(tmp_path / "restaurado.sqlite3")
    r = copia.restaurar(plataforma.cfg, arquivo, destino)
    assert r["contas"] == 1
    conn = sqlite3.connect(destino)
    assert conn.execute("SELECT email FROM usuarios").fetchone()[0] == "ana@exemplo.com.br"
    conn.close()
    with pytest.raises(FileExistsError):
        copia.restaurar(plataforma.cfg, arquivo, destino)                         # nunca sobrescreve


def test_outra_chave_ou_arquivo_estragado_nao_abre(cliente, plataforma, ambiente, tmp_path):
    arquivo = copia.fazer(plataforma.cfg)
    outra = config.carregar({**ambiente, "MW_CHAVE_CRIPTO": config.gerar_chave()})
    with pytest.raises(ValueError):
        copia.restaurar(outra, arquivo, str(tmp_path / "a.sqlite3"))
    with open(arquivo, "r+b") as f:
        f.seek(100)
        f.write(b"XXXX")
    with pytest.raises(ValueError):
        copia.restaurar(plataforma.cfg, arquivo, str(tmp_path / "b.sqlite3"))
    assert not os.path.exists(tmp_path / "a.sqlite3") and not os.path.exists(tmp_path / "b.sqlite3")


def test_guarda_so_as_mais_novas(plataforma, ambiente):
    cfg = config.carregar({**ambiente, "MW_BACKUP_MANTER": "3"})
    for i in range(5):
        copia.fazer(cfg, agora=1_800_000_000 + i * 86400)
    nomes = copia.listar(cfg)
    assert len(nomes) == 3 and nomes == sorted(nomes)
    assert not copia.precisa(cfg) and copia.precisa(cfg, agora=os.path.getmtime(nomes[-1]) + 25 * 3600)
