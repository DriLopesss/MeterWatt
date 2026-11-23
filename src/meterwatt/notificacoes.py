"""Notificações no celular e no computador (Web Push), direto com o serviço do próprio navegador.

Como funciona:
1. No app, a pessoa toca em "Ligar notificações". O navegador pede permissão e devolve uma inscrição:
   um endereço no serviço de notificações dele (Google para Chrome e Android, Apple para iPhone e Safari,
   Mozilla para Firefox, Microsoft para Edge) e duas chaves que só aquele aparelho tem.
2. O servidor guarda a inscrição cifrada, ligada à conta.
3. Quando nasce um aviso (alertas.py), o servidor cifra a mensagem com as chaves do aparelho
   (RFC 8291, no formato aes128gcm da RFC 8188) e entrega no endereço, com o pedido assinado pela chave
   do servidor (VAPID, RFC 8292). O serviço do navegador só repassa: ele não consegue ler o conteúdo.

Tudo com o pacote cryptography, que o projeto já usa: nenhuma biblioteca nova, nenhum serviço pago.

Segurança:
- O endereço vem do navegador, ou seja, de fora. Para o servidor não virar ponte para a rede interna
  (SSRF), só aceitamos HTTPS nos domínios dos serviços de notificação conhecidos, e o envio não segue
  redirecionamentos.
- Endereço e chaves ficam cifrados no banco. Na cópia dos dados (LGPD) vão só o aparelho e as datas.
- A inscrição sai junto quando a pessoa sai da conta naquele aparelho, troca ou redefine a senha,
  ativa a verificação em duas etapas ou encerra as outras sessões: quem perdeu o acesso à conta
  também para de receber os avisos dela.
"""
import base64
import hmac
import json
import logging
import os
import struct
import urllib.error
import urllib.request
from hashlib import sha256
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .erros import ErroDeNegocio, NaoEncontrado
from .seguranca import Limitador, resumir_navegador

log = logging.getLogger("meterwatt.notificacoes")

# Serviços de notificação dos navegadores. Um endereço fora daqui é recusado.
HOSTS_DE_PUSH = {"fcm.googleapis.com", "updates.push.services.mozilla.com"}
SUFIXOS_DE_PUSH = (".push.apple.com", ".notify.windows.com")
TAMANHO_MAXIMO_ENDPOINT = 2048
TAMANHO_DO_REGISTRO = 4096          # o corpo cifrado inteiro cabe num registro (os serviços aceitam até 4 KB)
CABECALHO_CIFRA = 86                # sal (16) + tamanho do registro (4) + tamanho da chave (1) + chave (65)
BLOCO_DE_PREENCHIMENTO = 256        # o tamanho cifrado sobe de 256 em 256 bytes: o serviço não deduz o aviso pelo tamanho
VALIDADE_JWT_S = 12 * 3600          # a Apple recusa mais de 24 h e pede para não renovar mais de uma vez por hora
RENOVAR_JWT_ANTES_S = 3600
FALHAS_PARA_DESISTIR = 3
# Por quanto tempo o serviço guarda a notificação se o aparelho estiver desligado.
VALIDADE_POR_TIPO = {"medidor_offline": 6 * 3600, "consumo_incomum": 6 * 3600, "meta": 24 * 3600,
                     "resumo_semanal": 48 * 3600, "teste": 10 * 60}
SISTEMAS = ("Android", "iPhone", "iPad", "Windows", "Mac", "Linux")


# ------------------------------------------------------------------ base64url

def b64u(dados):
    return base64.urlsafe_b64encode(dados).decode("ascii").rstrip("=")


def de_b64u(texto):
    if not isinstance(texto, str):
        raise ValueError("não é texto")
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


# ------------------------------------------------------------------ cifra da mensagem (RFC 8291 + RFC 8188)

def _hkdf(sal, chave, info, tamanho):
    """HKDF-SHA-256 com um bloco só (todos os usos aqui pedem até 32 bytes)."""
    prk = hmac.new(sal, chave, sha256).digest()
    return hmac.new(prk, info + b"\x01", sha256).digest()[:tamanho]


def _ponto(chave_publica):
    return chave_publica.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def cifrar(texto, p256dh, auth, sal=None, chave_efemera=None, preenchimento=None):
    """Cifra `texto` (bytes) para o aparelho dono de `p256dh` (65 bytes) e `auth` (16 bytes).

    Devolve o corpo do pedido: cabeçalho aes128gcm + um único registro cifrado. `sal` e `chave_efemera`
    só são passados nos testes (para conferir com o exemplo da RFC 8291); no uso normal, são novos a cada envio.
    """
    publica_do_aparelho = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), p256dh)
    efemera = chave_efemera or ec.generate_private_key(ec.SECP256R1())
    publica_do_servidor = _ponto(efemera.public_key())
    segredo = efemera.exchange(ec.ECDH(), publica_do_aparelho)
    ikm = _hkdf(auth, segredo, b"WebPush: info\x00" + p256dh + publica_do_servidor, 32)
    sal = sal or os.urandom(16)
    chave = _hkdf(sal, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(sal, ikm, b"Content-Encoding: nonce\x00", 12)
    if preenchimento is None:   # completa até o próximo múltiplo do bloco, sem passar do tamanho do registro
        cheio = CABECALHO_CIFRA + len(texto) + 1 + 16
        alvo = min(-(-cheio // BLOCO_DE_PREENCHIMENTO) * BLOCO_DE_PREENCHIMENTO, TAMANHO_DO_REGISTRO)
        preenchimento = max(0, alvo - cheio)
    registro = texto + b"\x02" + b"\x00" * preenchimento    # 0x02: este é o último (e único) registro
    if CABECALHO_CIFRA + len(registro) + 16 > TAMANHO_DO_REGISTRO:
        raise ValueError("mensagem grande demais para uma notificação")
    cifrado = AESGCM(chave).encrypt(nonce, registro, None)
    return sal + struct.pack(">IB", TAMANHO_DO_REGISTRO, len(publica_do_servidor)) + publica_do_servidor + cifrado


# ------------------------------------------------------------------ assinatura do servidor (VAPID, RFC 8292)

class Vapid:
    def __init__(self, chave_privada_b64u, contato, relogio):
        valor = int.from_bytes(de_b64u(chave_privada_b64u), "big")
        self._privada = ec.derive_private_key(valor, ec.SECP256R1())
        self.chave_publica = b64u(_ponto(self._privada.public_key()))
        self.contato = contato
        self._relogio = relogio
        self._guardados = {}   # público do JWT (origem do serviço) -> (cabeçalho pronto, validade)

    def autorizacao(self, endpoint):
        u = urlparse(endpoint)
        publico = f"{u.scheme}://{u.netloc}"
        agora = int(self._relogio())
        guardado = self._guardados.get(publico)
        if guardado and guardado[1] - agora > RENOVAR_JWT_ANTES_S:
            return guardado[0]
        validade = agora + VALIDADE_JWT_S
        compacto = lambda d: b64u(json.dumps(d, separators=(",", ":")).encode())   # noqa: E731
        assinado = f"{compacto({'typ': 'JWT', 'alg': 'ES256'})}.{compacto({'aud': publico, 'exp': validade, 'sub': self.contato})}"
        r, s = decode_dss_signature(self._privada.sign(assinado.encode("ascii"), ec.ECDSA(hashes.SHA256())))
        cabecalho = f"vapid t={assinado}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}, k={self.chave_publica}"
        self._guardados[publico] = (cabecalho, validade)
        return cabecalho


# ------------------------------------------------------------------ envio

class _SemRedirecionar(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None   # um redirecionamento vira erro: o envio nunca sai do endereço que conferimos


_abridor = urllib.request.build_opener(_SemRedirecionar)


def enviar_http(endpoint, corpo, cabecalhos, tempo_limite=10):
    """Entrega no serviço de notificações. Devolve o código HTTP (0 se não deu para conectar)."""
    pedido = urllib.request.Request(endpoint, data=corpo, headers=cabecalhos, method="POST")
    try:
        with _abridor.open(pedido, timeout=tempo_limite) as resposta:
            return resposta.status
    except urllib.error.HTTPError as erro:
        return erro.code
    except (urllib.error.URLError, OSError, ValueError):
        return 0


def endpoint_aceito(endpoint):
    if not isinstance(endpoint, str) or not 0 < len(endpoint) <= TAMANHO_MAXIMO_ENDPOINT:
        return False
    try:
        u = urlparse(endpoint)
        porta = u.port
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return (u.scheme == "https" and porta in (None, 443) and not u.username and not u.password
            and (host in HOSTS_DE_PUSH or any(host.endswith(s) for s in SUFIXOS_DE_PUSH)))


def _aparelho(agente, instalado):
    nome = resumir_navegador(agente)
    if not instalado:
        return nome
    sistema = next((s for s in SISTEMAS if nome == s or nome.endswith(f" no {s}")), None)
    return f"App no {sistema}" if sistema else "App instalado"


# ------------------------------------------------------------------ inscrições

class Notificacoes:
    def __init__(self, plataforma, transporte=None):
        self.p = plataforma
        cfg = plataforma.cfg
        self.vapid = Vapid(cfg.vapid_chave_privada, cfg.vapid_contato, plataforma.agora) if cfg.vapid_chave_privada else None
        self.transporte = transporte or enviar_http
        self.limite_teste = Limitador(5, 15 * 60)

    @property
    def disponivel(self):
        return self.vapid is not None

    def _exigir_disponivel(self):
        if not self.vapid:
            raise ErroDeNegocio("Este servidor ainda não foi configurado para mandar notificações.", 409, "sem_notificacoes")

    def _ler_inscricao(self, dados):
        """Confere o que o navegador mandou. Devolve (endpoint, p256dh, auth) em bytes prontos para cifrar."""
        endpoint = dados.get("endpoint")
        chaves = dados.get("keys") if isinstance(dados.get("keys"), dict) else {}
        if not endpoint_aceito(endpoint):
            raise ErroDeNegocio("Este navegador usa um serviço de notificações que o MeterWatt não reconhece. "
                                "Tente no Chrome, no Edge, no Firefox ou no Safari.", 400, "servico_desconhecido")
        try:
            p256dh, auth = de_b64u(chaves.get("p256dh")), de_b64u(chaves.get("auth"))
            if len(p256dh) != 65 or len(auth) != 16:
                raise ValueError
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), p256dh)
        except (ValueError, TypeError):
            raise ErroDeNegocio("As chaves de notificação deste navegador vieram incompletas. Tente ligar de novo.", 400) from None
        return endpoint, p256dh, auth

    def _hmac(self, endpoint):
        return self.p.cofre.hmac("push:" + endpoint)

    def _cifrar_dados(self, endpoint, p256dh, auth):
        return self.p.cofre.cifrar(json.dumps({"endpoint": endpoint, "p256dh": b64u(p256dh), "auth": b64u(auth)}))

    def _abrir_dados(self, linha):
        d = json.loads(self.p.cofre.decifrar(linha["dados_cifrados"]))
        return d["endpoint"], de_b64u(d["p256dh"]), de_b64u(d["auth"])

    def _lista(self, conn, uid):
        linhas = conn.execute("SELECT id, navegador, criada_em, ultimo_envio FROM push_inscricoes WHERE usuario_id = ? "
                              "ORDER BY criada_em", (uid,)).fetchall()
        return [{"id": l["id"], "aparelho": l["navegador"], "desde": self.p.iso(l["criada_em"]),
                 "ultimo_envio": self.p.iso(l["ultimo_envio"])} for l in linhas]

    def estado(self, sessao):
        conn = self.p.db()
        return {"disponivel": self.disponivel, "chave": self.vapid.chave_publica if self.vapid else None,
                "aparelhos": self._lista(conn, sessao["usuario_id"])}

    def inscrever(self, sessao, dados, agente=None, ip=None):
        """Liga as notificações neste aparelho. Se o aparelho estava em outra conta, passa para esta."""
        self._exigir_disponivel()
        endpoint, p256dh, auth = self._ler_inscricao(dados)
        conn = self.p.db()
        uid, agora = sessao["usuario_id"], self.p.agora()
        nome = _aparelho(agente, dados.get("instalado") is True)
        chave = self._hmac(endpoint)
        with conn:
            antes = conn.execute("SELECT id, usuario_id FROM push_inscricoes WHERE endpoint_hmac = ?", (chave,)).fetchone()
            if antes and antes["usuario_id"] != uid:
                conn.execute("DELETE FROM push_inscricoes WHERE id = ?", (antes["id"],))
                antes = None
            if antes:
                conn.execute("UPDATE push_inscricoes SET sessao_id = ?, dados_cifrados = ?, navegador = ?, atualizada_em = ?, "
                             "falhas = 0 WHERE id = ?", (sessao["sessao_id"], self._cifrar_dados(endpoint, p256dh, auth),
                                                          nome, agora, antes["id"]))
                novo_id = antes["id"]
            else:
                novo_id = conn.execute(
                    "INSERT INTO push_inscricoes (usuario_id, sessao_id, endpoint_hmac, dados_cifrados, navegador, criada_em, "
                    "atualizada_em) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (uid, sessao["sessao_id"], chave, self._cifrar_dados(endpoint, p256dh, auth), nome, agora, agora)).lastrowid
                self.p.auditar(conn, uid, "notificacoes_ligadas", nome, ip)
        return {"id": novo_id, "aparelhos": self._lista(conn, uid)}

    def conferir(self, sessao, dados, agente=None):
        """O app confere, ao abrir, se a inscrição que o navegador tem ainda vale para esta conta.

        Devolve ligado=False quando o aparelho foi removido (por outro aparelho, por troca de senha) ou
        quando está em outra conta. Nesse último caso, a inscrição da outra conta sai: o aparelho agora
        está com esta pessoa, e os avisos da outra conta não devem mais aparecer nele."""
        if not self.vapid:
            return {"ligado": False, "id": None}
        try:
            endpoint, p256dh, auth = self._ler_inscricao(dados)
        except ErroDeNegocio:
            return {"ligado": False, "id": None}
        conn = self.p.db()
        uid = sessao["usuario_id"]
        with conn:
            linha = conn.execute("SELECT id, usuario_id FROM push_inscricoes WHERE endpoint_hmac = ?",
                                 (self._hmac(endpoint),)).fetchone()
            if not linha:
                return {"ligado": False, "id": None}
            if linha["usuario_id"] != uid:
                conn.execute("DELETE FROM push_inscricoes WHERE id = ?", (linha["id"],))
                return {"ligado": False, "id": None}
            conn.execute("UPDATE push_inscricoes SET sessao_id = ?, dados_cifrados = ?, navegador = ?, atualizada_em = ? "
                         "WHERE id = ?", (sessao["sessao_id"], self._cifrar_dados(endpoint, p256dh, auth),
                                          _aparelho(agente, dados.get("instalado") is True), self.p.agora(), linha["id"]))
        return {"ligado": True, "id": linha["id"]}

    def desligar(self, sessao, endpoint, ip=None):
        """Desliga neste aparelho (botão Desligar, ou antes de sair da conta)."""
        if not isinstance(endpoint, str) or not endpoint:
            raise ErroDeNegocio("Pedido inválido.")
        conn = self.p.db()
        with conn:
            linha = conn.execute("SELECT id, navegador FROM push_inscricoes WHERE endpoint_hmac = ? AND usuario_id = ?",
                                 (self._hmac(endpoint), sessao["usuario_id"])).fetchone()
            if linha:
                conn.execute("DELETE FROM push_inscricoes WHERE id = ?", (linha["id"],))
                self.p.auditar(conn, sessao["usuario_id"], "notificacoes_desligadas", linha["navegador"], ip)
        return {"aparelhos": self._lista(conn, sessao["usuario_id"])}

    def remover(self, sessao, inscricao_id, ip=None):
        """Tira um aparelho da lista (de qualquer aparelho da conta)."""
        conn = self.p.db()
        with conn:
            linha = conn.execute("SELECT navegador FROM push_inscricoes WHERE id = ? AND usuario_id = ?",
                                 (inscricao_id, sessao["usuario_id"])).fetchone()
            if not linha:
                raise NaoEncontrado("Aparelho não encontrado.")
            conn.execute("DELETE FROM push_inscricoes WHERE id = ?", (inscricao_id,))
            self.p.auditar(conn, sessao["usuario_id"], "notificacoes_desligadas", linha["navegador"], ip)
        return {"aparelhos": self._lista(conn, sessao["usuario_id"])}

    def testar(self, sessao, endpoint):
        self._exigir_disponivel()
        uid, agora = sessao["usuario_id"], self.p.agora()
        if self.limite_teste.esgotado(uid, agora):
            raise ErroDeNegocio("Já foram vários testes seguidos. Espere alguns minutos.", 429)
        conn = self.p.db()
        linha = conn.execute("SELECT * FROM push_inscricoes WHERE endpoint_hmac = ? AND usuario_id = ?",
                             (self._hmac(endpoint) if isinstance(endpoint, str) else "", uid)).fetchone()
        if not linha:
            raise NaoEncontrado("As notificações não estão ligadas neste aparelho.")
        self.limite_teste.registrar(uid, agora)
        status = self._entregar_para(conn, linha, "teste", {
            "titulo": "Notificações ligadas", "texto": "É assim que os avisos do MeterWatt vão chegar neste aparelho.",
            "tag": "teste", "url": "/#avisos"})
        if status not in (200, 201, 202):
            raise ErroDeNegocio("O serviço de notificações do navegador não aceitou o envio agora. "
                                "Tente desligar e ligar de novo neste aparelho.", 502, "falha_no_envio")
        return {"ok": True}

    # ------------------------------------------------------------ entrega dos avisos

    def entregar(self, uid, tipo, titulo, texto, tag=None):
        """Manda um aviso para todos os aparelhos da conta. Devolve quantos aceitaram."""
        if not self.vapid:
            return 0
        conn = self.p.db()
        linhas = conn.execute("SELECT * FROM push_inscricoes WHERE usuario_id = ?", (uid,)).fetchall()
        if not linhas:
            return 0
        dados = {"titulo": titulo, "texto": texto if len(texto) <= 1500 else texto[:1499] + "…", "tag": tag or tipo,
                 "url": "/#avisos", "quando": self.p.iso(self.p.agora()), "nao_lidos": self.p.alertas.nao_lidos(conn, uid)}
        return sum(1 for linha in linhas if self._entregar_para(conn, linha, tipo, dados) in (200, 201, 202))

    def _entregar_para(self, conn, linha, tipo, dados):
        try:
            endpoint, p256dh, auth = self._abrir_dados(linha)
        except (ValueError, KeyError):   # cifrado com outra chave: não há como usar
            with conn:
                conn.execute("DELETE FROM push_inscricoes WHERE id = ?", (linha["id"],))
            return 0
        if not endpoint_aceito(endpoint):   # a lista de serviços mudou depois da inscrição
            with conn:
                conn.execute("DELETE FROM push_inscricoes WHERE id = ?", (linha["id"],))
            return 0
        cabecalhos = {"Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                      "TTL": str(VALIDADE_POR_TIPO.get(tipo, 24 * 3600)), "Urgency": "high" if tipo == "teste" else "normal",
                      "Authorization": self.vapid.autorizacao(endpoint)}
        tag = str(dados.get("tag") or "")
        if 0 < len(tag) <= 32 and all(c.isascii() and (c.isalnum() or c in "-_") for c in tag):
            cabecalhos["Topic"] = tag   # o serviço troca a notificação ainda não entregue pela mais nova
        try:
            corpo = cifrar(json.dumps(dados, ensure_ascii=False).encode("utf-8"), p256dh, auth)
            status = self.transporte(endpoint, corpo, cabecalhos)
        except Exception:
            log.exception("Falha ao preparar ou enviar uma notificação")
            status = 0
        with conn:
            if status in (200, 201, 202):
                conn.execute("UPDATE push_inscricoes SET ultimo_envio = ?, falhas = 0 WHERE id = ?", (self.p.agora(), linha["id"]))
            elif status in (404, 410):     # o aparelho cancelou a inscrição ou ela venceu
                conn.execute("DELETE FROM push_inscricoes WHERE id = ?", (linha["id"],))
            elif status in (400, 403, 413):
                # Pedido recusado por algo da inscrição (chave do servidor trocada, por exemplo). Falta de
                # internet do servidor ou serviço fora do ar (0, 429, 5xx) não contam: não é culpa do aparelho.
                conn.execute("UPDATE push_inscricoes SET falhas = falhas + 1 WHERE id = ?", (linha["id"],))
                falhas = conn.execute("SELECT falhas FROM push_inscricoes WHERE id = ?", (linha["id"],)).fetchone()[0]
                if falhas >= FALHAS_PARA_DESISTIR:
                    conn.execute("DELETE FROM push_inscricoes WHERE id = ?", (linha["id"],))
        if status not in (200, 201, 202):
            log.warning("Notificação recusada pelo serviço (%s): %s", status, urlparse(endpoint).hostname)
        return status

    # ------------------------------------------------------------ chamados por contas.py e privacidade.py

    def remover_da_sessao(self, conn, sessao_id):
        conn.execute("DELETE FROM push_inscricoes WHERE sessao_id = ?", (sessao_id,))

    def remover_outros(self, conn, uid, sessao_id_atual):
        conn.execute("DELETE FROM push_inscricoes WHERE usuario_id = ? AND (sessao_id IS NULL OR sessao_id != ?)",
                     (uid, sessao_id_atual))

    def remover_todos(self, conn, uid):
        conn.execute("DELETE FROM push_inscricoes WHERE usuario_id = ?", (uid,))

    def exportar(self, conn, uid):
        return [{"aparelho": a["aparelho"], "ligadas_desde": a["desde"], "ultima_notificacao": a["ultimo_envio"]}
                for a in self._lista(conn, uid)]
