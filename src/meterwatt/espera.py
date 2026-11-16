"""Lista de espera da página de lançamento.

Regras:
- Só entra quem marcar a autorização e confirmar o e-mail (o link chega por e-mail).
- O formulário responde igual para e-mail novo, repetido ou já confirmado.
- Cada pessoa confirmada recebe um link de convite. Quem traz amigos (que confirmam)
  sobe na fila.
- O mesmo link do e-mail serve para sair da lista quando quiser: o registro é apagado.
- O link de cada pessoa nunca muda (é calculado com a chave do servidor). Assim, pedir
  o link de novo, ou alguém digitar o e-mail de outra pessoa, não invalida o link que
  ela já recebeu.
- Quem não confirma em 7 dias é apagado sozinho (veja privacidade.reter).
- O e-mail só é usado para avisar da abertura das vagas. [ADVOGADO: texto do aviso.]
"""
import re
import secrets

from .contas import normalizar_email
from .erros import ErroDeNegocio
from .seguranca import hash_token, token_com_formato_valido

VERSAO = "2026-11-16"
ALFABETO = "abcdefghjkmnpqrstuvwxyz23456789"
CODIGO = re.compile(r"^[a-z0-9]{8}$")
ORIGEM = re.compile(r"^[a-z0-9_-]{1,24}$")
REENVIO_S = 120
DIAS_SEM_CONFIRMAR = 7


class ListaDeEspera:
    def __init__(self, plataforma):
        self.p = plataforma

    def _enviar_link(self, email, token, novo):
        base = self.p.cfg.url_publica
        self.p.enviar_email(email, "Confirme o seu lugar na lista do MeterWatt", (
            ("Falta um passo para entrar na lista de espera do MeterWatt.\n\n" if novo else
             "Você pediu de novo o link da lista de espera do MeterWatt.\n\n")
            + f"Abra este link para confirmar e ver a sua posição:\n\n{base}/#fila/{token}\n\n"
            "Se não foi você, ignore: sem a confirmação, este e-mail é apagado da lista em 7 dias.\n"
            f"Para sair da lista a qualquer momento: {base}/#sair-da-fila/{token}\n\nEquipe MeterWatt\n"))

    def entrar(self, email, aceite, convite=None, origem=None):
        email = normalizar_email(email)
        if not email:
            raise ErroDeNegocio("Informe um e-mail válido.", 400)
        if aceite is not True:
            raise ErroDeNegocio("Marque a autorização para receber o aviso por e-mail.", 400)
        conn = self.p.db()
        agora = self.p.agora()
        existente = conn.execute("SELECT * FROM lista_espera WHERE email = ?", (email,)).fetchone()
        if existente:
            if agora - existente["ultimo_email"] < REENVIO_S:
                return
            with conn:
                conn.execute("UPDATE lista_espera SET ultimo_email = ? WHERE id = ?", (agora, existente["id"]))
            self._enviar_link(email, self._token(email, existente["codigo"]), novo=False)
            return
        padrinho = None
        if isinstance(convite, str) and CODIGO.match(convite):
            linha = conn.execute("SELECT id FROM lista_espera WHERE codigo = ? AND confirmado_em IS NOT NULL", (convite,)).fetchone()
            padrinho = linha["id"] if linha else None
        origem = origem if isinstance(origem, str) and ORIGEM.match(origem) else None
        codigo = "".join(secrets.choice(ALFABETO) for _ in range(8))
        token = self._token(email, codigo)
        with conn:
            conn.execute("INSERT INTO lista_espera (email, codigo, token_hash, indicado_por, origem, versao, criado_em, ultimo_email) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (email, codigo, hash_token(token), padrinho, origem, VERSAO, agora, agora))
        self._enviar_link(email, token, novo=True)

    def _token(self, email, codigo):
        return self.p.cofre.hmac(f"lista-de-espera:{codigo}:{email}")

    def _por_token(self, conn, token):
        if not token_com_formato_valido(token):
            return None
        return conn.execute("SELECT * FROM lista_espera WHERE token_hash = ?", (hash_token(token),)).fetchone()

    def _posicao(self, conn, linha):
        """Ordem da fila: quem indicou mais gente confirmada vem primeiro; no empate, quem confirmou antes."""
        ordem = conn.execute(
            "SELECT l.id FROM lista_espera l WHERE l.confirmado_em IS NOT NULL ORDER BY "
            "(SELECT COUNT(*) FROM lista_espera f WHERE f.indicado_por = l.id AND f.confirmado_em IS NOT NULL) DESC, "
            "l.confirmado_em ASC, l.id ASC").fetchall()
        ids = [r["id"] for r in ordem]
        return ids.index(linha["id"]) + 1, len(ids)

    def confirmar(self, token):
        conn = self.p.db()
        linha = self._por_token(conn, token)
        if not linha:
            raise ErroDeNegocio("Este link não vale mais. Peça outro na página inicial, com o mesmo e-mail.", 400, "link_invalido")
        if linha["confirmado_em"] is None:
            with conn:
                conn.execute("UPDATE lista_espera SET confirmado_em = ? WHERE id = ?", (self.p.agora(), linha["id"]))
            linha = self._por_token(conn, token)
        posicao, total = self._posicao(conn, linha)
        indicados = conn.execute("SELECT COUNT(*) FROM lista_espera WHERE indicado_por = ? AND confirmado_em IS NOT NULL",
                                 (linha["id"],)).fetchone()[0]
        return {"posicao": posicao, "total": total, "indicados": indicados,
                "link_de_convite": f"{self.p.cfg.url_publica}/#convite/{linha['codigo']}"}

    def sair(self, token):
        conn = self.p.db()
        linha = self._por_token(conn, token)
        if not linha:
            raise ErroDeNegocio("Este link não vale mais. Talvez você já tenha saído da lista.", 400, "link_invalido")
        with conn:
            conn.execute("DELETE FROM lista_espera WHERE id = ?", (linha["id"],))

    def total(self):
        return self.p.db().execute("SELECT COUNT(*) FROM lista_espera WHERE confirmado_em IS NOT NULL").fetchone()[0]

    def reter(self, conn, agora):
        return conn.execute("DELETE FROM lista_espera WHERE confirmado_em IS NULL AND criado_em < ?",
                            (agora - DIAS_SEM_CONFIRMAR * 86400,)).rowcount

    def exportar_confirmados(self):
        """Para os fundadores chamarem a fila na ordem (python -m meterwatt --lista-espera)."""
        conn = self.p.db()
        linhas = conn.execute(
            "SELECT l.*, (SELECT COUNT(*) FROM lista_espera f WHERE f.indicado_por = l.id AND f.confirmado_em IS NOT NULL) AS indicados "
            "FROM lista_espera l WHERE l.confirmado_em IS NOT NULL ORDER BY indicados DESC, l.confirmado_em ASC, l.id ASC").fetchall()
        return [{"posicao": i + 1, "email": l["email"], "indicados": l["indicados"], "origem": l["origem"] or "",
                 "confirmado_em": self.p.iso(l["confirmado_em"])} for i, l in enumerate(linhas)]
