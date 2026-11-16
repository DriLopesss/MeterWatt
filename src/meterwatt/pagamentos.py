"""Planos e cobrança da mensalidade.

O MeterWatt nunca vê nem guarda número de cartão: a pessoa paga na página do provedor
(Mercado Pago) e o servidor só fica sabendo se a assinatura está ativa.

Provedores:
- "simulado" (desenvolvimento): uma página de mentira dentro do próprio aplicativo, para
  testar o fluxo sem dinheiro. É recusado em produção.
- "mercadopago": assinatura recorrente (API /preapproval). Com as credenciais de TESTE do
  Mercado Pago, nenhum dinheiro de verdade circula.
- "desligado": sem cobrança e sem limite de medidores (padrão em produção até configurar).

O que o plano muda hoje: só a quantidade de medidores por conta. O resto é decisão do negócio.

[ADVOGADO/CONTADOR] Antes de cobrar de verdade: direito de arrependimento em 7 dias (CDC,
art. 49), emissão de nota fiscal e guarda dos registros fiscais.
"""
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime

from .erros import ErroDeNegocio
from .seguranca import novo_token

log = logging.getLogger("meterwatt.pagamentos")
STATUS_MP = {"authorized": "ativa", "pending": "pendente", "paused": "pausada", "cancelled": "cancelada"}


class FalhaNoProvedor(Exception):
    pass


class ProvedorSimulado:
    nome = "simulado"

    def __init__(self, url_publica):
        self.url = url_publica
        self.estado = {}          # id -> status (na memória: é só para testes)

    def criar(self, referencia, email, valor, motivo):
        ident = "sim-" + referencia[:16]
        self.estado[ident] = "pendente"
        return {"id": ident, "link": f"{self.url}/#pagamento-teste/{referencia}", "status": "pendente", "proxima_cobranca": None}

    def consultar(self, ident):
        return {"id": ident, "status": self.estado.get(ident, "cancelada"), "proxima_cobranca": None}

    def cancelar(self, ident):
        self.estado[ident] = "cancelada"

    def decidir(self, ident, aprovado):
        self.estado[ident] = "ativa" if aprovado else "cancelada"


def _http(metodo, url, cabecalhos, corpo=None):
    dados = json.dumps(corpo).encode() if corpo is not None else None
    pedido = urllib.request.Request(url, data=dados, method=metodo, headers=cabecalhos)
    try:
        with urllib.request.urlopen(pedido, timeout=15) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise FalhaNoProvedor(type(e).__name__) from None


class ProvedorMercadoPago:
    nome = "mercadopago"
    API = "https://api.mercadopago.com"

    def __init__(self, token, segredo_webhook, url_publica, http=_http):
        self.token, self.segredo, self.url, self.http = token, segredo_webhook, url_publica, http

    def _pedir(self, metodo, caminho, corpo=None):
        status, dados = self.http(metodo, self.API + caminho,
                                  {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}, corpo)
        if status not in (200, 201):
            log.error("Mercado Pago respondeu %s em %s %s", status, metodo, caminho.split("/")[1])   # sem dados da pessoa no registro
            raise FalhaNoProvedor(f"http_{status}")
        return dados

    @staticmethod
    def _traduzir(dados):
        proxima = None
        if dados.get("next_payment_date"):
            try:
                proxima = datetime.fromisoformat(dados["next_payment_date"].replace("Z", "+00:00")).timestamp()
            except ValueError:
                proxima = None
        return {"id": str(dados.get("id", "")), "status": STATUS_MP.get(dados.get("status"), "pendente"),
                "link": dados.get("init_point"), "proxima_cobranca": proxima}

    def criar(self, referencia, email, valor, motivo):
        return self._traduzir(self._pedir("POST", "/preapproval", {
            "reason": motivo, "external_reference": referencia, "payer_email": email,
            "auto_recurring": {"frequency": 1, "frequency_type": "months", "transaction_amount": valor, "currency_id": "BRL"},
            "back_url": f"{self.url}/#plano", "status": "pending"}))

    def consultar(self, ident):
        if not ident.isalnum():
            raise FalhaNoProvedor("id_invalido")
        return self._traduzir(self._pedir("GET", f"/preapproval/{ident}"))

    def cancelar(self, ident):
        if not ident.isalnum():
            raise FalhaNoProvedor("id_invalido")
        self._pedir("PUT", f"/preapproval/{ident}", {"status": "cancelled"})

    def webhook_valido(self, assinatura, id_do_pedido, id_do_dado):
        """Confere o cabeçalho x-signature ("ts=...,v1=...") como o Mercado Pago descreve:
        HMAC-SHA256 de "id:<data.id>;request-id:<x-request-id>;ts:<ts>;" com o segredo do webhook."""
        if not self.segredo or not assinatura or not id_do_dado:
            return False
        partes = dict(p.strip().split("=", 1) for p in assinatura.split(",") if "=" in p)
        ts, v1 = partes.get("ts"), partes.get("v1")
        if not ts or not v1:
            return False
        texto = f"id:{str(id_do_dado).lower()};request-id:{id_do_pedido or ''};ts:{ts};"
        esperado = hmac.new(self.segredo.encode(), texto.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(esperado, v1)


def criar_provedor(cfg):
    if cfg.pagamento_provedor == "mercadopago":
        return ProvedorMercadoPago(cfg.mp_access_token, cfg.mp_webhook_segredo, cfg.url_publica)
    if cfg.pagamento_provedor == "simulado":
        return ProvedorSimulado(cfg.url_publica)
    return None


class Pagamentos:
    def __init__(self, plataforma, provedor=None):
        self.p = plataforma
        self.provedor = provedor if provedor is not None else criar_provedor(plataforma.cfg)

    # ------------------------------------------------------------ plano atual

    def _assinatura(self, conn, uid):
        """A assinatura que vale: a ativa; senão a cancelada ainda dentro do período pago; senão a pendente mais nova."""
        agora = self.p.agora()
        linhas = conn.execute("SELECT * FROM assinaturas WHERE usuario_id = ? ORDER BY criada_em DESC", (uid,)).fetchall()
        for l in linhas:
            if l["status"] in ("ativa", "pausada"):
                return l
        for l in linhas:
            if l["status"] == "cancelada" and l["valida_ate"] and l["valida_ate"] > agora:
                return l
        for l in linhas:
            if l["status"] == "pendente" and agora - l["criada_em"] < 3600:
                return l
        return None

    def plano_de(self, conn, uid):
        a = self._assinatura(conn, uid)
        if a and (a["status"] == "ativa" or (a["status"] == "cancelada" and a["valida_ate"] and a["valida_ate"] > self.p.agora())):
            return "casa"
        return "gratis"

    def limite_de_medidores(self, conn, uid):
        c = self.p.cfg
        if self.provedor is None:
            return c.limite_medidores_pago          # sem cobrança configurada, ninguém é barrado
        return c.limite_medidores_pago if self.plano_de(conn, uid) == "casa" else c.limite_medidores_gratis

    def estado(self, sessao):
        conn = self.p.db()
        uid = sessao["usuario_id"]
        c = self.p.cfg
        a = self._assinatura(conn, uid)
        return {
            "disponivel": self.provedor is not None, "modo_teste": getattr(self.provedor, "nome", None) == "simulado",
            "plano": self.plano_de(conn, uid), "preco_mensal": c.plano_preco,
            "limite_gratis": c.limite_medidores_gratis, "limite_pago": c.limite_medidores_pago,
            "medidores": conn.execute("SELECT COUNT(*) FROM medidores WHERE usuario_id = ?", (uid,)).fetchone()[0],
            "assinatura": None if a is None else {
                "status": a["status"], "valor": a["valor"], "desde": self.p.iso(a["criada_em"]),
                "proxima_cobranca": self.p.iso(a["proxima_cobranca"]), "valida_ate": self.p.iso(a["valida_ate"]),
                "link_pagamento": a["link_pagamento"] if a["status"] == "pendente" else None},
        }

    # ------------------------------------------------------------ assinar e cancelar

    def assinar(self, sessao, ip=None):
        if self.provedor is None:
            raise ErroDeNegocio("A assinatura ainda não está disponível.", 503, "pagamento_desligado")
        conn = self.p.db()
        uid = sessao["usuario_id"]
        atual = self._assinatura(conn, uid)
        if atual and atual["status"] in ("ativa", "pausada"):
            raise ErroDeNegocio("Você já tem uma assinatura ativa.", 409)
        if atual and atual["status"] == "pendente" and atual["link_pagamento"]:
            return {"link": atual["link_pagamento"]}
        referencia = novo_token()
        valor = self.p.cfg.plano_preco
        try:
            r = self.provedor.criar(referencia, sessao["email"], valor, "MeterWatt Casa (mensal)")
        except FalhaNoProvedor:
            raise ErroDeNegocio("Não conseguimos falar com o serviço de pagamento agora. Tente de novo em instantes.", 502) from None
        agora = self.p.agora()
        with conn:
            conn.execute(
                "INSERT INTO assinaturas (usuario_id, provedor, referencia, id_externo, status, valor, criada_em, atualizada_em, link_pagamento) "
                "VALUES (?, ?, ?, ?, 'pendente', ?, ?, ?, ?)",
                (uid, self.provedor.nome, referencia, r["id"] or None, valor, agora, agora, r["link"]))
            self.p.auditar(conn, uid, "assinatura_iniciada", None, ip)
        return {"link": r["link"]}

    def _aplicar(self, conn, linha, novo):
        """Grava o status vindo do provedor. Devolve True se mudou."""
        agora = self.p.agora()
        status = novo["status"]
        if status == "pendente" and linha["status"] != "pendente":
            return False                                   # nunca volta para pendente
        valida_ate = linha["valida_ate"]
        if status == "cancelada" and linha["status"] in ("ativa", "pausada"):
            valida_ate = linha["proxima_cobranca"]          # o que já foi pago vale até o fim do período
        mudou = status != linha["status"]
        with conn:
            conn.execute("UPDATE assinaturas SET status = ?, atualizada_em = ?, proxima_cobranca = COALESCE(?, proxima_cobranca), "
                         "valida_ate = ? WHERE id = ?", (status, agora, novo.get("proxima_cobranca"), valida_ate, linha["id"]))
            if mudou:
                self.p.auditar(conn, linha["usuario_id"], {"ativa": "assinatura_ativada", "cancelada": "assinatura_cancelada",
                                                           "pausada": "assinatura_pausada"}.get(status, "assinatura_iniciada"))
        if mudou and status in ("ativa", "cancelada"):
            u = conn.execute("SELECT nome, email FROM usuarios WHERE id = ?", (linha["usuario_id"],)).fetchone()
            if status == "ativa":
                self.p.enviar_email(u["email"], "Assinatura do MeterWatt ativada", (
                    f"Olá, {u['nome']}.\n\nA sua assinatura do plano Casa está ativa. Você pode cancelar quando quiser em "
                    f"{self.p.cfg.url_publica}/#plano\n\nEquipe MeterWatt\n"))
            else:
                self.p.enviar_email(u["email"], "Assinatura do MeterWatt cancelada", (
                    f"Olá, {u['nome']}.\n\nA sua assinatura foi cancelada e não haverá novas cobranças. "
                    "Os seus medidores e o histórico continuam na conta.\n\nEquipe MeterWatt\n"))
        return mudou

    def sincronizar(self, id_externo):
        """Pergunta ao provedor como está a assinatura e grava. Pode ser chamado quantas vezes for."""
        conn = self.p.db()
        linha = conn.execute("SELECT * FROM assinaturas WHERE id_externo = ?", (id_externo,)).fetchone()
        if not linha or self.provedor is None or linha["provedor"] != self.provedor.nome:
            return False
        return self._aplicar(conn, linha, self.provedor.consultar(linha["id_externo"]))

    def atualizar(self, sessao):
        """Usado quando a pessoa volta da página de pagamento."""
        conn = self.p.db()
        a = self._assinatura(conn, sessao["usuario_id"])
        if a and a["id_externo"] and a["status"] in ("pendente", "ativa", "pausada"):
            try:
                self.sincronizar(a["id_externo"])
            except FalhaNoProvedor:
                pass
        return self.estado(sessao)

    def cancelar(self, sessao, ip=None):
        conn = self.p.db()
        a = self._assinatura(conn, sessao["usuario_id"])
        if not a or a["status"] not in ("ativa", "pausada", "pendente"):
            raise ErroDeNegocio("Não há assinatura para cancelar.", 400)
        self._cancelar_no_provedor(a)
        self._aplicar(conn, a, {"status": "cancelada"})
        return self.estado(sessao)

    def _cancelar_no_provedor(self, a):
        if self.provedor is None or a["provedor"] != self.provedor.nome or not a["id_externo"]:
            return
        try:
            self.provedor.cancelar(a["id_externo"])
        except FalhaNoProvedor:
            raise ErroDeNegocio("Não conseguimos cancelar a assinatura no serviço de pagamento agora. Nada foi alterado: "
                                "tente de novo em instantes.", 502, "pagamento_indisponivel") from None

    def cancelar_antes_de_excluir(self, conn, uid):
        """Excluir a conta sem cancelar a cobrança deixaria a pessoa pagando por nada."""
        for a in conn.execute("SELECT * FROM assinaturas WHERE usuario_id = ? AND status IN ('ativa', 'pausada', 'pendente')", (uid,)).fetchall():
            self._cancelar_no_provedor(a)

    # ------------------------------------------------------------ provedor simulado e webhook

    def decidir_simulado(self, sessao, referencia, aprovado):
        if getattr(self.provedor, "nome", None) != "simulado":
            raise ErroDeNegocio("Não encontrado.", 404)
        conn = self.p.db()
        a = conn.execute("SELECT * FROM assinaturas WHERE referencia = ? AND usuario_id = ? AND status = 'pendente'",
                         (referencia if isinstance(referencia, str) else "", sessao["usuario_id"])).fetchone()
        if not a:
            raise ErroDeNegocio("Este pagamento de teste não existe mais.", 404)
        self.provedor.decidir(a["id_externo"], bool(aprovado))
        self.sincronizar(a["id_externo"])
        return self.estado(sessao)

    def webhook_mercadopago(self, cabecalhos, parametros, corpo):
        """Aviso do Mercado Pago. Só confia na assinatura do cabeçalho; o conteúdo serve apenas
        para saber QUAL assinatura consultar. Devolve o status HTTP."""
        if getattr(self.provedor, "nome", None) != "mercadopago":
            return 404
        id_do_dado = parametros.get("data.id") or ((corpo.get("data") or {}).get("id") if isinstance(corpo, dict) else None)
        if not self.provedor.webhook_valido(cabecalhos.get("x-signature"), cabecalhos.get("x-request-id"), id_do_dado):
            return 401
        tipo = (corpo.get("type") if isinstance(corpo, dict) else None) or parametros.get("type")
        if tipo == "subscription_preapproval" and isinstance(id_do_dado, (str, int)):
            try:
                self.sincronizar(str(id_do_dado))
            except FalhaNoProvedor:
                return 503          # o Mercado Pago tenta de novo mais tarde
        return 200

    def exportar(self, conn, uid):
        return [{"provedor": a["provedor"], "status": a["status"], "valor": a["valor"], "criada_em": self.p.iso(a["criada_em"]),
                 "proxima_cobranca": self.p.iso(a["proxima_cobranca"]), "valida_ate": self.p.iso(a["valida_ate"])}
                for a in conn.execute("SELECT * FROM assinaturas WHERE usuario_id = ? ORDER BY criada_em", (uid,))]
