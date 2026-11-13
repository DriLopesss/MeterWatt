"""Junta configuração, banco, cifra, e-mail e os serviços (contas, medidores, privacidade)."""
import os
import threading
import time
from datetime import datetime, timedelta

from . import banco
from . import mensageiro as mod_mensageiro
from .seguranca import Cofre, Limitador, resumir_ip


class Plataforma:
    def __init__(self, cfg, mensageiro=None, relogio=time.time):
        self.cfg = cfg
        self.relogio = relogio
        self.cofre = Cofre(cfg.chave_cripto)
        self.mensageiro = mensageiro or mod_mensageiro.criar(cfg)
        self._local = threading.local()

        conn = banco.abrir(cfg.arquivo_banco)
        banco.migrar(conn)
        conn.close()
        for sufixo in ("", "-wal", "-shm"):
            try:
                os.chmod(cfg.arquivo_banco + sufixo, 0o600)   # o banco é legível só pelo dono
            except OSError:
                pass

        # Trocado pelo cliente MQTT quando ele conecta (veja mqtt.py).
        self.publicar = lambda topico, mensagem: None

        # Limites de tentativas (na memória). Valores pensados para uma pessoa errando,
        # não para um robô.
        self.limite_login_email = Limitador(5, 15 * 60)
        self.limite_login_ip = Limitador(30, 15 * 60)
        self.limite_formularios_ip = Limitador(10, 60 * 60)
        self.limite_exportar = Limitador(5, 60 * 60)
        self.limite_confirmacao = Limitador(5, 15 * 60)   # senha pedida de novo para ações sensíveis

        from .contas import Contas
        from .medidores import Medidores
        from .privacidade import Privacidade
        self.contas = Contas(self)
        self.medidores = Medidores(self)
        self.privacidade = Privacidade(self)

    # ------------------------------------------------------------ tempo

    def agora(self):
        return self.relogio()

    def iso(self, instante):
        if instante is None:
            return None
        return datetime.fromtimestamp(instante, self.cfg.zona).isoformat(timespec="seconds")

    def inicio_do_dia(self, instante, dias_atras=0):
        d = datetime.fromtimestamp(instante, self.cfg.zona).replace(hour=0, minute=0, second=0, microsecond=0)
        return (d - timedelta(days=dias_atras)).timestamp()

    def inicio_do_mes(self, instante):
        d = datetime.fromtimestamp(instante, self.cfg.zona)
        return d.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()

    def inicio_do_proximo_mes(self, instante):
        d = datetime.fromtimestamp(instante, self.cfg.zona).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return (d.replace(year=d.year + 1, month=1) if d.month == 12 else d.replace(month=d.month + 1)).timestamp()

    def dia_local(self, instante):
        return datetime.fromtimestamp(instante, self.cfg.zona).strftime("%Y-%m-%d")

    # ------------------------------------------------------------ banco

    def db(self):
        """Uma conexão por thread (o servidor web e o MQTT rodam em threads diferentes)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = banco.abrir(self.cfg.arquivo_banco)
            self._local.conn = conn
        return conn

    def fechar_conexao_da_thread(self):
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def auditar(self, conn, usuario_id, evento, detalhe=None, ip=None):
        dados = (self.agora(), usuario_id, evento, detalhe, resumir_ip(ip) if ip else None)
        sql = "INSERT INTO auditoria (instante, usuario_id, evento, detalhe, ip_resumido) VALUES (?, ?, ?, ?, ?)"
        if conn.in_transaction:
            conn.execute(sql, dados)
        else:
            with conn:
                conn.execute(sql, dados)

    def consentimento(self, conn, usuario_id, finalidade):
        """A decisão mais recente da pessoa sobre essa finalidade."""
        linha = conn.execute(
            "SELECT aceito FROM consentimentos WHERE usuario_id = ? AND finalidade = ? "
            "ORDER BY registrado_em DESC, id DESC LIMIT 1", (usuario_id, finalidade)).fetchone()
        return bool(linha and linha["aceito"])

    def tarifa_de(self, usuario):
        return usuario["tarifa_kwh"] if usuario["tarifa_kwh"] is not None else self.cfg.tarifa_padrao

    def enviar_email(self, para, assunto, texto):
        try:
            self.mensageiro.enviar(para, assunto, texto)
        except Exception:   # um e-mail que não sai não pode derrubar o pedido
            pass
