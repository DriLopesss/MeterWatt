"""Limites de tentativas guardados no banco: valem depois de reiniciar o servidor e entre vários processos.

Antes ficavam só na memória (seguranca.Limitador): reiniciar o servidor zerava as contagens de login,
de códigos da verificação em duas etapas e de pareamento. Agora cada tentativa vira uma linha na tabela
`tentativas`, com a chave guardada como HMAC (e-mails e endereços de rede não ficam legíveis no banco).

A gravação usa uma conexão própria, que confirma na hora: uma tentativa errada continua contando mesmo
quando a operação que a registrou é desfeita (por exemplo, a senha errada dentro de "apagar consumo").
Se o banco estiver ocupado, a contagem cai para a memória, como antes: um limite nunca derruba o login.
"""
import sqlite3
import threading

from . import banco
from .seguranca import Limitador

ESPERA_BANCO_MS = 2000


class LimitadorBanco:
    def __init__(self, plataforma, nome, maximo, janela_s):
        self.p = plataforma
        self.nome = nome
        self.maximo = maximo
        self.janela = janela_s
        self.memoria = Limitador(maximo, janela_s)     # reserva, se o banco não responder
        self._local = threading.local()

    def _conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = banco.abrir(self.p.cfg.arquivo_banco)
            conn.execute(f"PRAGMA busy_timeout = {ESPERA_BANCO_MS}")
            self._local.conn = conn
        return conn

    def _chave(self, chave):
        return self.p.cofre.hmac(f"limite:{self.nome}:{chave}")

    def registrar(self, chave, agora):
        try:
            conn = self._conn()
            with conn:
                conn.execute("INSERT INTO tentativas (nome, chave, instante) VALUES (?, ?, ?)", (self.nome, self._chave(chave), agora))
        except sqlite3.Error:
            self.memoria.registrar(chave, agora)

    def esgotado(self, chave, agora):
        """Segundos até liberar, ou 0 se ainda há tentativas."""
        espera_memoria = self.memoria.esgotado(chave, agora)
        try:
            linhas = self._conn().execute(
                "SELECT instante FROM tentativas WHERE nome = ? AND chave = ? AND instante > ? ORDER BY instante DESC LIMIT ?",
                (self.nome, self._chave(chave), agora - self.janela, self.maximo)).fetchall()
        except sqlite3.Error:
            return espera_memoria
        if len(linhas) < self.maximo:
            return espera_memoria
        mais_antiga = linhas[-1][0]                    # a mais velha das últimas `maximo`
        return max(espera_memoria, max(1, int(mais_antiga + self.janela - agora) + 1))

    def zerar(self, chave):
        self.memoria.zerar(chave)
        try:
            conn = self._conn()
            with conn:
                conn.execute("DELETE FROM tentativas WHERE nome = ? AND chave = ?", (self.nome, self._chave(chave)))
        except sqlite3.Error:
            pass

    def fechar_conexao_da_thread(self):
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
