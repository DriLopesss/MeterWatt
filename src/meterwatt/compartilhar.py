"""Compartilhar a casa: a dona da conta convida alguém da família para ver o painel, só para ler.

- O convite vai por e-mail, vale 7 dias e só pode ser aceito pela conta com aquele e-mail (quem recebe o link
  encaminhado não entra). O e-mail da pessoa convidada fica guardado só enquanto o convite está pendente.
- Quem foi convidado vê o painel e o relatório do mês da casa: consumo, meta, consumo em espera e gráficos.
  Não vê o Detetive de aparelhos (que revela mais da rotina), nem a economia, a conta, os avisos ou a privacidade,
  e não muda nada. Isso é garantido no servidor (web.py, ROTAS_COMPARTILHADAS), não só escondido na tela.
- A dona pode tirar o acesso a qualquer momento; quem foi convidado pode sair. Tudo vai para a atividade das duas
  contas e para a cópia dos dados.
"""
import unicodedata

from .erros import ErroDeNegocio, NaoEncontrado
from .seguranca import hash_token, novo_token, token_com_formato_valido

VALIDADE_S = 7 * 86400
MAXIMO_PESSOAS = 5
MAXIMO_CONVITES_DIA = 10


def nome_no_email(nome):
    """O nome de quem convida, do jeito que pode ir num e-mail para um endereço qualquer: só letras, espaços,
    apóstrofo e hífen, até 40 caracteres. Sem isso, um "nome" como "Banco X: regularize em http://..." virava
    um e-mail de golpe com o remetente do MeterWatt (revisão de 25/11)."""
    limpo = "".join(c if unicodedata.category(c).startswith("L") or c in " '-" else " " for c in nome or "")
    limpo = " ".join(limpo.split())[:40].strip(" '-")
    return limpo or "Uma pessoa"


def _email(texto):
    e = (texto or "").strip().lower() if isinstance(texto, str) else ""
    if not (3 <= len(e) <= 254 and e.count("@") == 1 and "." in e.split("@")[1] and " " not in e):
        raise ErroDeNegocio("Digite um e-mail válido.", 400)
    return e


class Compartilhar:
    def __init__(self, plataforma):
        self.p = plataforma

    # ------------------------------------------------------------ quem pode ver o quê

    def pode_ver(self, conn, convidado_id, dono_id):
        return conn.execute("SELECT 1 FROM acessos_casa WHERE dono_id = ? AND convidado_id = ?", (dono_id, convidado_id)).fetchone() is not None

    def sessao_da_casa(self, sessao, dono_id):
        """A sessão "vista pela casa" de quem compartilhou: os números da dona, marcada como compartilhada."""
        conn = self.p.db()
        if not isinstance(dono_id, int) or not self.pode_ver(conn, sessao["usuario_id"], dono_id):
            raise ErroDeNegocio("Você não tem acesso a essa casa.", 403, "sem_acesso")
        dono = conn.execute("SELECT id, nome, tarifa_kwh, meta_mensal_reais, ligacao FROM usuarios WHERE id = ?", (dono_id,)).fetchone()
        return {**sessao, "usuario_id": dono["id"], "tarifa_kwh": dono["tarifa_kwh"], "meta_mensal_reais": dono["meta_mensal_reais"],
                "ligacao": dono["ligacao"], "compartilhada": True, "quem_ve": sessao["usuario_id"]}

    def casas(self, conn, uid):
        return [{"dono_id": r["id"], "nome": r["nome"], "desde": self.p.iso(r["criado_em"])} for r in conn.execute(
            "SELECT u.id, u.nome, a.criado_em FROM acessos_casa a JOIN usuarios u ON u.id = a.dono_id WHERE a.convidado_id = ? "
            "ORDER BY a.criado_em", (uid,))]

    # ------------------------------------------------------------ do lado de quem compartilha

    def estado(self, sessao):
        conn = self.p.db()
        uid = sessao["usuario_id"]
        agora = self.p.agora()
        conn.execute("DELETE FROM convites_casa WHERE expira_em < ?", (agora,))
        conn.commit()
        return {
            "pessoas": [{"id": r["id"], "nome": r["nome"], "email": r["email"], "desde": self.p.iso(r["criado_em"])} for r in conn.execute(
                "SELECT u.id, u.nome, u.email, a.criado_em FROM acessos_casa a JOIN usuarios u ON u.id = a.convidado_id "
                "WHERE a.dono_id = ? ORDER BY a.criado_em", (uid,))],
            "convites": [{"id": r["id"], "email": r["email"], "expira_em": self.p.iso(r["expira_em"])} for r in conn.execute(
                "SELECT id, email, expira_em FROM convites_casa WHERE dono_id = ? ORDER BY criado_em", (uid,))],
            "casas": self.casas(conn, uid), "maximo": MAXIMO_PESSOAS,
        }

    def convidar(self, sessao, dados, ip=None):
        conn = self.p.db()
        uid = sessao["usuario_id"]
        email = _email((dados or {}).get("email"))
        agora = self.p.agora()
        if email == sessao["email"].lower():
            raise ErroDeNegocio("Esse é o seu próprio e-mail.", 400)
        ocupados = conn.execute("SELECT (SELECT COUNT(*) FROM acessos_casa WHERE dono_id = ?) + "
                                "(SELECT COUNT(*) FROM convites_casa WHERE dono_id = ? AND expira_em >= ?)", (uid, uid, agora)).fetchone()[0]
        if ocupados >= MAXIMO_PESSOAS:
            raise ErroDeNegocio(f"A casa pode ser compartilhada com até {MAXIMO_PESSOAS} pessoas, contando os convites pendentes.", 400)
        if conn.execute("SELECT COUNT(*) FROM convites_casa WHERE dono_id = ? AND criado_em >= ?", (uid, agora - 86400)).fetchone()[0] >= MAXIMO_CONVITES_DIA:
            raise ErroDeNegocio("Muitos convites hoje. Tente de novo amanhã.", 429)
        if conn.execute("SELECT 1 FROM acessos_casa a JOIN usuarios u ON u.id = a.convidado_id WHERE a.dono_id = ? AND lower(u.email) = ?",
                        (uid, email)).fetchone():
            raise ErroDeNegocio("Essa pessoa já vê a sua casa.", 400)
        token = novo_token()
        with conn:
            conn.execute("DELETE FROM convites_casa WHERE dono_id = ? AND email = ?", (uid, email))     # reenviar troca o link
            conn.execute("INSERT INTO convites_casa (dono_id, email, token_hash, criado_em, expira_em) VALUES (?, ?, ?, ?, ?)",
                         (uid, email, hash_token(token), agora, agora + VALIDADE_S))
            self.p.auditar(conn, uid, "casa_convite", None, ip)
        # O assunto é fixo: o nome vem de quem cadastrou a conta, e o e-mail vai para um endereço qualquer.
        self.p.enviar_email(email, "Convite para ver o consumo de energia de uma casa no MeterWatt", (
            f"Olá.\n\nUma pessoa que usa o MeterWatt (nome na conta: \"{nome_no_email(sessao['nome'])}\") convidou você para ver "
            "o painel de consumo de energia da casa dela: quanto a casa gasta agora, a meta do mês e o relatório. "
            "Você só vê; não muda nada.\n\n"
            f"Para aceitar, entre na sua conta do MeterWatt com este e-mail (ou crie uma) e abra:\n"
            f"{self.p.cfg.url_publica}/#compartilhar/{token}\n\nO convite vale 7 dias. Se não conhece quem mandou, "
            "é só ignorar este e-mail.\n\nEquipe MeterWatt\n"))
        return self.estado(sessao)

    def cancelar_convite(self, sessao, cid, ip=None):
        conn = self.p.db()
        with conn:
            n = conn.execute("DELETE FROM convites_casa WHERE id = ? AND dono_id = ?", (cid, sessao["usuario_id"])).rowcount
        if not n:
            raise NaoEncontrado("Convite não encontrado.")
        return self.estado(sessao)

    def tirar_acesso(self, sessao, convidado_id, ip=None):
        conn = self.p.db()
        uid = sessao["usuario_id"]
        with conn:
            n = conn.execute("DELETE FROM acessos_casa WHERE dono_id = ? AND convidado_id = ?", (uid, convidado_id)).rowcount
            if n:
                self.p.auditar(conn, uid, "casa_acesso_retirado", None, ip)
                self.p.auditar(conn, convidado_id, "casa_acesso_perdido", sessao["nome"], None)
        if not n:
            raise NaoEncontrado("Essa pessoa não vê a sua casa.")
        return self.estado(sessao)

    # ------------------------------------------------------------ do lado de quem foi convidado

    def aceitar(self, sessao, dados, ip=None):
        token = (dados or {}).get("token")
        if not token_com_formato_valido(token):
            raise ErroDeNegocio("Link de convite inválido.", 400)
        conn = self.p.db()
        uid = sessao["usuario_id"]
        c = conn.execute("SELECT * FROM convites_casa WHERE token_hash = ?", (hash_token(token),)).fetchone()
        if not c or c["expira_em"] < self.p.agora():
            raise ErroDeNegocio("Esse convite não vale mais. Peça um novo para quem convidou você.", 400, "convite_invalido")
        if c["email"] != sessao["email"].lower():
            raise ErroDeNegocio("Esse convite foi para outro e-mail. Entre com a conta do e-mail que recebeu o convite.", 403, "outro_email")
        if c["dono_id"] == uid:
            raise ErroDeNegocio("Esse convite é da sua própria casa.", 400)
        dono = conn.execute("SELECT nome FROM usuarios WHERE id = ?", (c["dono_id"],)).fetchone()
        with conn:
            conn.execute("INSERT OR IGNORE INTO acessos_casa (dono_id, convidado_id, criado_em) VALUES (?, ?, ?)",
                         (c["dono_id"], uid, self.p.agora()))
            conn.execute("DELETE FROM convites_casa WHERE id = ?", (c["id"],))
            self.p.auditar(conn, uid, "casa_aceita", dono["nome"], ip)
            self.p.auditar(conn, c["dono_id"], "casa_compartilhada", sessao["nome"], None)
        return {"dono_id": c["dono_id"], "nome": dono["nome"]}

    def sair(self, sessao, dono_id, ip=None):
        conn = self.p.db()
        uid = sessao["usuario_id"]
        with conn:
            n = conn.execute("DELETE FROM acessos_casa WHERE dono_id = ? AND convidado_id = ?", (dono_id, uid)).rowcount
            if n:
                self.p.auditar(conn, uid, "casa_saiu", None, ip)
                self.p.auditar(conn, dono_id, "casa_convidado_saiu", sessao["nome"], None)
        if not n:
            raise NaoEncontrado("Você não vê essa casa.")
        return self.estado(sessao)

    def exportar(self, conn, uid):
        return {
            "quem_ve_a_minha_casa": [{"nome": r["nome"], "email": r["email"], "desde": self.p.iso(r["criado_em"])} for r in conn.execute(
                "SELECT u.nome, u.email, a.criado_em FROM acessos_casa a JOIN usuarios u ON u.id = a.convidado_id WHERE a.dono_id = ?", (uid,))],
            "convites_pendentes": [{"email": r["email"], "expira_em": self.p.iso(r["expira_em"])} for r in conn.execute(
                "SELECT email, expira_em FROM convites_casa WHERE dono_id = ?", (uid,))],
            "casas_que_eu_vejo": [{"nome": c["nome"], "desde": c["desde"]} for c in self.casas(conn, uid)],
        }
