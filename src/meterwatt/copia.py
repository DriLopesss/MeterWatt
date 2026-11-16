"""Cópias de segurança do banco.

- A cópia é tirada com o banco em uso (API de backup do SQLite), compactada e CIFRADA com a
  MW_CHAVE_CRIPTO. O arquivo sozinho não serve para nada: sem a chave, ninguém lê.
- Fica em instancia/backups. Levar esses arquivos para FORA do servidor (outro provedor,
  outro disco) é com vocês: um backup que mora no mesmo lugar do original cai junto com ele.
- A chave precisa estar guardada em outro lugar. Perdeu a chave, perdeu os backups.
- Restaurar nunca sobrescreve o banco em uso: grava um arquivo novo para você conferir.
"""
import gzip
import os
import sqlite3
import tempfile
import time

from cryptography.fernet import Fernet, InvalidToken

SUFIXO = ".mwbackup"


def pasta(cfg):
    caminho = os.path.join(cfg.pasta_instancia, "backups")
    os.makedirs(caminho, mode=0o700, exist_ok=True)
    return caminho


def listar(cfg):
    p = pasta(cfg)
    return sorted(os.path.join(p, n) for n in os.listdir(p) if n.endswith(SUFIXO))


def fazer(cfg, agora=None):
    """Tira uma cópia e apaga as mais antigas. Devolve o caminho do arquivo novo."""
    agora = time.time() if agora is None else agora
    destino = os.path.join(pasta(cfg), time.strftime("meterwatt-%Y%m%d-%H%M%S", time.localtime(agora)) + SUFIXO)
    descritor, temporario = tempfile.mkstemp(dir=pasta(cfg), suffix=".tmp")
    os.close(descritor)
    try:
        origem = sqlite3.connect(cfg.arquivo_banco, timeout=30)
        copia = sqlite3.connect(temporario)
        try:
            origem.backup(copia)
        finally:
            copia.close()
            origem.close()
        with open(temporario, "rb") as f:
            cifrado = Fernet(cfg.chave_cripto).encrypt(gzip.compress(f.read(), 6))
    finally:
        if os.path.exists(temporario):
            os.remove(temporario)
    with open(destino + ".parcial", "wb") as f:
        f.write(cifrado)
    os.chmod(destino + ".parcial", 0o600)
    os.replace(destino + ".parcial", destino)
    for velho in listar(cfg)[:-cfg.backup_manter]:
        os.remove(velho)
    return destino


def restaurar(cfg, arquivo, destino):
    """Decifra `arquivo` em `destino` (que não pode existir) e confere se o banco está inteiro."""
    if os.path.exists(destino):
        raise FileExistsError(f"{destino} já existe. Escolha outro nome: o restaurar nunca sobrescreve.")
    with open(arquivo, "rb") as f:
        try:
            dados = gzip.decompress(Fernet(cfg.chave_cripto).decrypt(f.read()))
        except InvalidToken:
            raise ValueError("Não foi possível abrir: o arquivo está corrompido ou foi cifrado com outra chave.") from None
    with open(destino, "wb") as f:
        f.write(dados)
    os.chmod(destino, 0o600)
    conn = sqlite3.connect(destino)
    try:
        resultado = conn.execute("PRAGMA integrity_check").fetchone()[0]
        contas = conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]
    finally:
        conn.close()
    if resultado != "ok":
        raise ValueError(f"O banco restaurado não passou na conferência: {resultado}")
    return {"arquivo": destino, "contas": contas}


def precisa(cfg, agora=None, horas=24):
    agora = time.time() if agora is None else agora
    copias = listar(cfg)
    return not copias or agora - os.path.getmtime(copias[-1]) > horas * 3600
