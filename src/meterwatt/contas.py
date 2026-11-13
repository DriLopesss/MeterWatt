"""Contas: cadastro, confirmação de e-mail, entrada em duas etapas, sessões e senhas.

Regras que valem para todo o arquivo:
- Ninguém descobre se um e-mail tem conta: cadastro, "esqueci a senha" e reenvio de
  confirmação respondem igual, e a entrada gasta o mesmo tempo com ou sem conta.
- Toda troca de senha ou de verificação em duas etapas encerra as outras sessões e
  avisa por e-mail.
- Sessões ganham um token novo ao concluir a entrada (evita fixação de sessão).
"""
import re
import sqlite3
import unicodedata

from .erros import ErroDeNegocio, NaoEncontrado
from .privacidade import FINALIDADES
from .seguranca import (confere_senha, confere_totp, gastar_tempo_de_senha, hash_senha, hash_token,
                        normalizar_codigo_backup, novo_segredo_totp, novo_token, novos_codigos_backup,
                        problema_na_senha, qr_svg_data_uri, resumir_ip, resumir_navegador,
                        token_com_formato_valido, uri_totp)

_EMAIL = re.compile(
    r"^[^@\s<>\"'(),;:\\]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
ERRO_LOGIN = "E-mail ou senha incorretos."
VALIDADE_CONFIRMACAO_S = 24 * 3600
VALIDADE_REDEFINICAO_S = 30 * 60
VALIDADE_ETAPA_2FA_S = 5 * 60


def normalizar_email(email):
    if not isinstance(email, str):
        return None
    email = email.strip().lower()
    if len(email) > 254 or not _EMAIL.match(email):
        return None
    return email


def limpar_nome(nome):
    if not isinstance(nome, str):
        return None
    nome = " ".join(unicodedata.normalize("NFC", nome).split())
    if not 1 <= len(nome) <= 80 or any(unicodedata.category(c).startswith("C") for c in nome):
        return None
    return nome


def _invalido(mensagem="Pedido inválido."):
    return ErroDeNegocio(mensagem, 400)


class Contas:
    def __init__(self, plataforma):
        self.p = plataforma

    # ------------------------------------------------------------ apoio

    def _usuario(self, conn, uid):
        return conn.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()

    def _por_email(self, conn, email):
        return conn.execute("SELECT * FROM usuarios WHERE email = ?", (email,)).fetchone()

    def _novo_token_email(self, conn, uid, finalidade, validade_s):
        agora = self.p.agora()
        token = novo_token()
        conn.execute("UPDATE tokens_email SET usado_em = ? WHERE usuario_id = ? AND finalidade = ? AND usado_em IS NULL",
                     (agora, uid, finalidade))
        conn.execute("INSERT INTO tokens_email (usuario_id, finalidade, token_hash, criado_em, expira_em) VALUES (?, ?, ?, ?, ?)",
                     (uid, finalidade, hash_token(token), agora, agora + validade_s))
        return token

    def _token_valido(self, conn, token, finalidade):
        if not token_com_formato_valido(token):
            return None
        linha = conn.execute("SELECT * FROM tokens_email WHERE token_hash = ? AND finalidade = ?",
                             (hash_token(token), finalidade)).fetchone()
        if not linha or linha["usado_em"] is not None or linha["expira_em"] < self.p.agora():
            return None
        return linha

    def _link(self, rota, token=None):
        return f"{self.p.cfg.url_publica}/#{rota}" + (f"/{token}" if token else "")

    def _criar_sessao(self, conn, uid, etapa, ip, agente):
        agora = self.p.agora()
        token, csrf = novo_token(), novo_token()
        validade = VALIDADE_ETAPA_2FA_S if etapa == "aguardando_2fa" else self.p.cfg.sessao_maxima_dias * 86400
        conn.execute(
            "INSERT INTO sessoes (usuario_id, token_hash, csrf, etapa, criada_em, ultimo_uso, expira_em, ip_resumido, navegador) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, hash_token(token), csrf, etapa, agora, agora, agora + validade, resumir_ip(ip), resumir_navegador(agente)))
        return token, csrf

    def _avisar(self, u, assunto, corpo):
        self.p.enviar_email(u["email"], assunto, f"Olá, {u['nome']}.\n\n{corpo}\n\nEquipe MeterWatt\n")

    # ------------------------------------------------------------ cadastro e confirmação

    def cadastrar(self, nome, email, senha, aceites, ip=None):
        nome, email = limpar_nome(nome), normalizar_email(email)
        if not nome:
            raise _invalido("Informe o seu nome, com até 80 caracteres.")
        if not email:
            raise _invalido("Informe um e-mail válido.")
        problema = problema_na_senha(senha, email)
        if problema:
            raise _invalido(problema)
        aceites = aceites if isinstance(aceites, dict) else {}
        if not all(aceites.get(f) is True for f, info in FINALIDADES.items() if info["obrigatorio"]):
            raise _invalido("Para criar a conta, é preciso aceitar os termos de uso, a política de privacidade "
                            "e o tratamento dos dados de consumo.")
        conn = self.p.db()
        senha_hash = hash_senha(senha)   # calculado sempre: o tempo de resposta não revela se o e-mail existe
        existente = self._por_email(conn, email)
        if existente:
            self._cadastro_repetido(conn, existente, ip)
            return
        agora = self.p.agora()
        try:
            with conn:
                uid = conn.execute(
                    "INSERT INTO usuarios (email, nome, senha_hash, criado_em, senha_trocada_em) VALUES (?, ?, ?, ?, ?)",
                    (email, nome, senha_hash, agora, agora)).lastrowid
                for finalidade in FINALIDADES:
                    self.p.privacidade.registrar(conn, uid, finalidade, aceites.get(finalidade) is True, "cadastro")
                token = self._novo_token_email(conn, uid, "confirmar", VALIDADE_CONFIRMACAO_S)
                self.p.auditar(conn, uid, "cadastro", None, ip)
        except sqlite3.IntegrityError:   # o mesmo e-mail cadastrado duas vezes ao mesmo tempo
            existente = self._por_email(conn, email)
            if existente:
                self._cadastro_repetido(conn, existente, ip)
            return
        self.p.enviar_email(email, "Confirme o seu e-mail no MeterWatt", (
            f"Olá, {nome}.\n\nPara ativar a sua conta no MeterWatt, abra este link em até 24 horas:\n\n"
            f"{self._link('confirmar', token)}\n\nSe você não criou esta conta, ignore este e-mail: "
            "sem a confirmação, ela é apagada sozinha em alguns dias.\n\nEquipe MeterWatt\n"))

    def _cadastro_repetido(self, conn, u, ip):
        ultimo = conn.execute("SELECT MAX(instante) FROM auditoria WHERE usuario_id = ? AND evento = 'cadastro_repetido'",
                              (u["id"],)).fetchone()[0]
        self.p.auditar(conn, u["id"], "cadastro_repetido", None, ip)
        if ultimo and self.p.agora() - ultimo < 3600:
            return   # não enche a caixa de ninguém
        self._avisar(u, "Alguém tentou criar uma conta com o seu e-mail", (
            "Alguém tentou criar uma conta no MeterWatt com este e-mail. Você já tem conta.\n\n"
            f"Se foi você e esqueceu a senha, redefina aqui: {self._link('esqueci')}\n\n"
            "Se não foi você, pode ignorar este aviso. Nada mudou na sua conta."))

    def confirmar_email(self, token, ip=None):
        conn = self.p.db()
        linha = self._token_valido(conn, token, "confirmar")
        if not linha:
            raise ErroDeNegocio("Este link de confirmação não vale mais. Tente entrar: se a conta ainda não "
                                "estiver confirmada, você poderá pedir outro link.", 400, "link_invalido")
        agora = self.p.agora()
        with conn:
            conn.execute("UPDATE tokens_email SET usado_em = ? WHERE id = ?", (agora, linha["id"]))
            conn.execute("UPDATE usuarios SET email_confirmado_em = COALESCE(email_confirmado_em, ?) WHERE id = ?",
                         (agora, linha["usuario_id"]))
            self.p.auditar(conn, linha["usuario_id"], "email_confirmado", None, ip)

    def reenviar_confirmacao(self, email, ip=None):
        conn = self.p.db()
        email = normalizar_email(email)
        u = self._por_email(conn, email) if email else None
        if not u or u["email_confirmado_em"]:
            return
        ultimo = conn.execute("SELECT MAX(criado_em) FROM tokens_email WHERE usuario_id = ? AND finalidade = 'confirmar'",
                              (u["id"],)).fetchone()[0]
        if ultimo and self.p.agora() - ultimo < 60:
            return
        with conn:
            token = self._novo_token_email(conn, u["id"], "confirmar", VALIDADE_CONFIRMACAO_S)
        self._avisar(u, "Confirme o seu e-mail no MeterWatt",
                     f"Aqui está um link novo para confirmar o seu e-mail (vale por 24 horas):\n\n{self._link('confirmar', token)}")

    # ------------------------------------------------------------ entrada e sessões

    def entrar(self, email, senha, ip=None, agente=None):
        conn = self.p.db()
        agora = self.p.agora()
        email = normalizar_email(email) or ""
        chave_email, chave_ip = email or "-", ip or "-"
        espera = max(self.p.limite_login_email.esgotado(chave_email, agora), self.p.limite_login_ip.esgotado(chave_ip, agora))
        if espera:
            raise ErroDeNegocio(f"Muitas tentativas. Tente de novo em {max(1, round(espera / 60))} minuto(s) ou redefina a senha.",
                                429, "muitas_tentativas")
        if not isinstance(senha, str) or len(senha) > 128:
            senha = ""
        u = self._por_email(conn, email) if email else None

        def falhou():
            self.p.limite_login_email.registrar(chave_email, agora)
            self.p.limite_login_ip.registrar(chave_ip, agora)
            raise ErroDeNegocio(ERRO_LOGIN, 401, "login_invalido")

        if u is None:
            gastar_tempo_de_senha(senha)
            falhou()
        ok, precisa_atualizar = confere_senha(u["senha_hash"], senha)
        if not ok:
            self.p.auditar(conn, u["id"], "login_falhou", None, ip)
            falhou()
        if not u["email_confirmado_em"]:
            raise ErroDeNegocio("Confirme o seu e-mail antes de entrar. Se o link expirou, peça outro abaixo.",
                                403, "email_nao_confirmado")
        self.p.limite_login_email.zerar(chave_email)
        etapa = "aguardando_2fa" if u["totp_ativado_em"] else "completa"
        with conn:
            if precisa_atualizar:
                conn.execute("UPDATE usuarios SET senha_hash = ? WHERE id = ?", (hash_senha(senha), u["id"]))
            token, csrf = self._criar_sessao(conn, u["id"], etapa, ip, agente)
            if etapa == "completa":
                self.p.auditar(conn, u["id"], "login", None, ip)
        return {"token": token, "csrf": csrf, "etapa": etapa}

    def sessao_atual(self, token):
        if not token_com_formato_valido(token):
            return None
        conn = self.p.db()
        linha = conn.execute(
            "SELECT s.id AS sessao_id, s.usuario_id, s.csrf, s.etapa, s.tentativas_2fa, s.criada_em, s.ultimo_uso, "
            "s.expira_em, u.email, u.nome, u.totp_ativado_em, u.tarifa_kwh, u.meta_mensal_reais "
            "FROM sessoes s JOIN usuarios u ON u.id = s.usuario_id WHERE s.token_hash = ?", (hash_token(token),)).fetchone()
        if not linha:
            return None
        agora = self.p.agora()
        ociosa = self.p.cfg.sessao_ociosa_horas * 3600
        if linha["expira_em"] < agora or (linha["etapa"] == "completa" and linha["ultimo_uso"] + ociosa < agora):
            with conn:
                conn.execute("DELETE FROM sessoes WHERE id = ?", (linha["sessao_id"],))
            return None
        if agora - linha["ultimo_uso"] > 60:
            with conn:
                conn.execute("UPDATE sessoes SET ultimo_uso = ? WHERE id = ?", (agora, linha["sessao_id"]))
        return dict(linha)

    def verificar_2fa(self, sessao, codigo, ip=None, agente=None):
        conn = self.p.db()
        uid = sessao["usuario_id"]
        u = self._usuario(conn, uid)
        agora = self.p.agora()
        passo = confere_totp(self.p.cofre.decifrar(u["totp_cifrado"]), codigo, agora, u["totp_ultimo_passo"])
        backup = None
        if passo is None:
            normal = normalizar_codigo_backup(codigo)
            if normal:
                backup = conn.execute("SELECT id FROM codigos_backup WHERE usuario_id = ? AND codigo_hash = ? AND usado_em IS NULL",
                                      (uid, self.p.cofre.hmac(normal))).fetchone()
        if passo is None and backup is None:
            tentativas = sessao["tentativas_2fa"] + 1
            with conn:
                if tentativas >= 5:
                    conn.execute("DELETE FROM sessoes WHERE id = ?", (sessao["sessao_id"],))
                else:
                    conn.execute("UPDATE sessoes SET tentativas_2fa = ? WHERE id = ?", (tentativas, sessao["sessao_id"]))
                self.p.auditar(conn, uid, "login_2fa_falhou", None, ip)
            if tentativas >= 5:
                raise ErroDeNegocio("Muitos códigos errados. Entre de novo com o e-mail e a senha.", 401, "sem_sessao")
            raise ErroDeNegocio("Código incorreto. Confira o código no aplicativo autenticador.", 400, "codigo_incorreto")
        with conn:
            if passo is not None:
                conn.execute("UPDATE usuarios SET totp_ultimo_passo = ? WHERE id = ?", (passo, uid))
            else:
                conn.execute("UPDATE codigos_backup SET usado_em = ? WHERE id = ?", (agora, backup["id"]))
            conn.execute("DELETE FROM sessoes WHERE id = ?", (sessao["sessao_id"],))
            token, csrf = self._criar_sessao(conn, uid, "completa", ip, agente)
            self.p.auditar(conn, uid, "login" if backup is None else "login_codigo_backup", None, ip)
        if backup is not None:
            restantes = conn.execute("SELECT COUNT(*) FROM codigos_backup WHERE usuario_id = ? AND usado_em IS NULL",
                                     (uid,)).fetchone()[0]
            self._avisar(u, "Um código de backup foi usado na sua conta", (
                f"Alguém entrou na sua conta do MeterWatt usando um código de backup. Restam {restantes} código(s).\n\n"
                "Se foi você, tudo certo. Se não foi, troque a senha e gere códigos novos em Segurança."))
        return {"token": token, "csrf": csrf, "etapa": "completa"}

    def sair(self, sessao, ip=None):
        conn = self.p.db()
        with conn:
            conn.execute("DELETE FROM sessoes WHERE id = ?", (sessao["sessao_id"],))
            if sessao["etapa"] == "completa":
                self.p.auditar(conn, sessao["usuario_id"], "sair", None, ip)

    def listar_sessoes(self, sessao):
        conn = self.p.db()
        linhas = conn.execute("SELECT id, criada_em, ultimo_uso, ip_resumido, navegador FROM sessoes "
                              "WHERE usuario_id = ? AND etapa = 'completa' ORDER BY ultimo_uso DESC", (sessao["usuario_id"],))
        return [{"id": l["id"], "criada_em": self.p.iso(l["criada_em"]), "ultimo_uso": self.p.iso(l["ultimo_uso"]),
                 "rede": l["ip_resumido"], "navegador": l["navegador"], "atual": l["id"] == sessao["sessao_id"]} for l in linhas]

    def encerrar_sessao(self, sessao, sessao_id, ip=None):
        if not isinstance(sessao_id, int) or sessao_id == sessao["sessao_id"]:
            raise _invalido("Para encerrar esta sessão, use Sair.")
        conn = self.p.db()
        with conn:
            apagadas = conn.execute("DELETE FROM sessoes WHERE id = ? AND usuario_id = ?", (sessao_id, sessao["usuario_id"])).rowcount
            if not apagadas:
                raise NaoEncontrado("Sessão não encontrada.")
            self.p.auditar(conn, sessao["usuario_id"], "sessao_encerrada", None, ip)

    def encerrar_outras(self, sessao, ip=None):
        conn = self.p.db()
        with conn:
            n = conn.execute("DELETE FROM sessoes WHERE usuario_id = ? AND id != ?", (sessao["usuario_id"], sessao["sessao_id"])).rowcount
            if n:
                self.p.auditar(conn, sessao["usuario_id"], "sessao_encerrada", f"{n} sessão(ões)", ip)
        return n

    # ------------------------------------------------------------ senha

    def pedir_redefinicao(self, email, ip=None):
        conn = self.p.db()
        email = normalizar_email(email)
        u = self._por_email(conn, email) if email else None
        if not u:
            return
        ultimo = conn.execute("SELECT MAX(criado_em) FROM tokens_email WHERE usuario_id = ? AND finalidade = 'redefinir'",
                              (u["id"],)).fetchone()[0]
        if ultimo and self.p.agora() - ultimo < 60:
            return
        with conn:
            token = self._novo_token_email(conn, u["id"], "redefinir", VALIDADE_REDEFINICAO_S)
        self._avisar(u, "Redefinir a senha do MeterWatt", (
            "Para criar uma senha nova, abra este link em até 30 minutos:\n\n"
            f"{self._link('redefinir', token)}\n\nSe você não pediu, ignore este e-mail: a sua senha continua a mesma."))

    def redefinir_senha(self, token, nova, ip=None):
        conn = self.p.db()
        linha = self._token_valido(conn, token, "redefinir")
        if not linha:
            raise ErroDeNegocio("Este link para redefinir a senha não vale mais. Peça outro.", 400, "link_invalido")
        u = self._usuario(conn, linha["usuario_id"])
        problema = problema_na_senha(nova, u["email"])
        if problema:
            raise _invalido(problema)
        agora = self.p.agora()
        nova_hash = hash_senha(nova)
        with conn:
            conn.execute("UPDATE usuarios SET senha_hash = ?, senha_trocada_em = ?, "
                         "email_confirmado_em = COALESCE(email_confirmado_em, ?) WHERE id = ?", (nova_hash, agora, agora, u["id"]))
            conn.execute("UPDATE tokens_email SET usado_em = ? WHERE usuario_id = ? AND finalidade = 'redefinir' AND usado_em IS NULL",
                         (agora, u["id"]))
            conn.execute("DELETE FROM sessoes WHERE usuario_id = ?", (u["id"],))
            self.p.auditar(conn, u["id"], "senha_redefinida", None, ip)
        self.p.limite_login_email.zerar(u["email"])
        self._avisar(u, "A sua senha do MeterWatt foi trocada", (
            "A senha da sua conta foi redefinida pelo link enviado a este e-mail, e todas as sessões foram encerradas.\n\n"
            "Se não foi você, redefina a senha de novo agora e responda este e-mail."))

    def trocar_senha(self, sessao, atual, nova, ip=None):
        conn = self.p.db()
        u = self._usuario(conn, sessao["usuario_id"])
        agora = self.p.agora()
        if self.p.limite_confirmacao.esgotado(u["id"], agora):
            raise ErroDeNegocio("Muitas tentativas. Tente de novo em alguns minutos.", 429)
        if not confere_senha(u["senha_hash"], atual if isinstance(atual, str) else "")[0]:
            self.p.limite_confirmacao.registrar(u["id"], agora)
            raise ErroDeNegocio("A senha atual está incorreta.", 403, "senha_incorreta")
        problema = problema_na_senha(nova, u["email"])
        if problema:
            raise _invalido(problema)
        if confere_senha(u["senha_hash"], nova)[0]:
            raise _invalido("A senha nova precisa ser diferente da atual.")
        nova_hash = hash_senha(nova)
        with conn:
            conn.execute("UPDATE usuarios SET senha_hash = ?, senha_trocada_em = ? WHERE id = ?", (nova_hash, agora, u["id"]))
            conn.execute("DELETE FROM sessoes WHERE usuario_id = ? AND id != ?", (u["id"], sessao["sessao_id"]))
            self.p.auditar(conn, u["id"], "senha_trocada", None, ip)
        self._avisar(u, "A sua senha do MeterWatt foi trocada", (
            "A senha da sua conta foi trocada e as outras sessões foram encerradas.\n\n"
            "Se não foi você, redefina a senha agora pela tela de entrada e responda este e-mail."))

    # ------------------------------------------------------------ verificação em duas etapas

    def iniciar_2fa(self, sessao):
        conn = self.p.db()
        u = self._usuario(conn, sessao["usuario_id"])
        if u["totp_ativado_em"]:
            raise _invalido("A verificação em duas etapas já está ativada.")
        segredo = novo_segredo_totp()
        with conn:
            conn.execute("UPDATE usuarios SET totp_pendente_cifrado = ? WHERE id = ?", (self.p.cofre.cifrar(segredo), u["id"]))
        uri = uri_totp(segredo, u["email"])
        return {"segredo": " ".join(segredo[i:i + 4] for i in range(0, len(segredo), 4)), "uri": uri, "qr": qr_svg_data_uri(uri)}

    def ativar_2fa(self, sessao, codigo, ip=None):
        conn = self.p.db()
        u = self._usuario(conn, sessao["usuario_id"])
        if u["totp_ativado_em"]:
            raise _invalido("A verificação em duas etapas já está ativada.")
        if not u["totp_pendente_cifrado"]:
            raise _invalido("Comece a ativação de novo.")
        segredo = self.p.cofre.decifrar(u["totp_pendente_cifrado"])
        passo = confere_totp(segredo, codigo, self.p.agora())
        if passo is None:
            raise _invalido("Código incorreto. Confira se o relógio do celular está no horário automático.")
        codigos = novos_codigos_backup()
        with conn:
            conn.execute("UPDATE usuarios SET totp_cifrado = totp_pendente_cifrado, totp_pendente_cifrado = NULL, "
                         "totp_ativado_em = ?, totp_ultimo_passo = ? WHERE id = ?", (self.p.agora(), passo, u["id"]))
            self._gravar_codigos_backup(conn, u["id"], codigos)
            conn.execute("DELETE FROM sessoes WHERE usuario_id = ? AND id != ?", (u["id"], sessao["sessao_id"]))
            self.p.auditar(conn, u["id"], "2fa_ativado", None, ip)
        self._avisar(u, "Verificação em duas etapas ativada", (
            "A verificação em duas etapas foi ativada na sua conta. A partir de agora, além da senha, "
            "o MeterWatt vai pedir o código do aplicativo autenticador.\n\nSe não foi você, responda este e-mail."))
        return {"codigos_backup": codigos}

    def _gravar_codigos_backup(self, conn, uid, codigos):
        conn.execute("DELETE FROM codigos_backup WHERE usuario_id = ?", (uid,))
        conn.executemany("INSERT INTO codigos_backup (usuario_id, codigo_hash) VALUES (?, ?)",
                         [(uid, self.p.cofre.hmac(normalizar_codigo_backup(c))) for c in codigos])

    def desativar_2fa(self, sessao, senha, codigo, ip=None):
        if self.p.cfg.exigir_2fa:
            raise ErroDeNegocio("Este servidor exige a verificação em duas etapas em todas as contas.", 403)
        conn = self.p.db()
        with conn:
            u = self.p.privacidade._confirmar_identidade(conn, sessao["usuario_id"], senha, codigo)
            if not u["totp_ativado_em"]:
                raise _invalido("A verificação em duas etapas não está ativada.")
            conn.execute("UPDATE usuarios SET totp_cifrado = NULL, totp_pendente_cifrado = NULL, totp_ativado_em = NULL "
                         "WHERE id = ?", (u["id"],))
            conn.execute("DELETE FROM codigos_backup WHERE usuario_id = ?", (u["id"],))
            self.p.auditar(conn, u["id"], "2fa_desativado", None, ip)
        self._avisar(u, "Verificação em duas etapas desativada", (
            "A verificação em duas etapas foi desativada na sua conta. Agora basta a senha para entrar.\n\n"
            "Se não foi você, troque a senha agora e responda este e-mail."))

    def novos_codigos_backup(self, sessao, senha, codigo, ip=None):
        conn = self.p.db()
        with conn:
            u = self.p.privacidade._confirmar_identidade(conn, sessao["usuario_id"], senha, codigo)
            if not u["totp_ativado_em"]:
                raise _invalido("Ative a verificação em duas etapas primeiro.")
            codigos = novos_codigos_backup()
            self._gravar_codigos_backup(conn, u["id"], codigos)
            self.p.auditar(conn, u["id"], "codigos_backup_gerados", None, ip)
        return {"codigos_backup": codigos}

    # ------------------------------------------------------------ perfil

    def eu(self, sessao):
        conn = self.p.db()
        restantes = conn.execute("SELECT COUNT(*) FROM codigos_backup WHERE usuario_id = ? AND usado_em IS NULL",
                                 (sessao["usuario_id"],)).fetchone()[0]
        return {
            "nome": sessao["nome"], "email": sessao["email"], "etapa": sessao["etapa"], "csrf": sessao["csrf"],
            "dois_fatores": bool(sessao["totp_ativado_em"]), "codigos_backup_restantes": restantes,
            "exigir_2fa": self.p.cfg.exigir_2fa,
            "tarifa_kwh": sessao["tarifa_kwh"], "tarifa_padrao": self.p.cfg.tarifa_padrao,
            "meta_mensal_reais": sessao["meta_mensal_reais"],
            "consentimento_consumo": self.p.consentimento(conn, sessao["usuario_id"], "consumo"),
        }

    def preferencias(self, sessao, dados, ip=None):
        def valor(chave, minimo, maximo, descricao):
            v = dados.get(chave)
            if v is None:
                return None
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not minimo < v <= maximo:
                raise _invalido(f"{descricao} precisa ser um número maior que {minimo} e até {maximo}.")
            return float(v)
        if not isinstance(dados, dict):
            raise _invalido()
        tarifa = valor("tarifa_kwh", 0, 10, "A tarifa (R$ por kWh)")
        meta = valor("meta_mensal_reais", 0, 100000, "A meta do mês (R$)")
        conn = self.p.db()
        with conn:
            conn.execute("UPDATE usuarios SET tarifa_kwh = ?, meta_mensal_reais = ? WHERE id = ?",
                         (tarifa, meta, sessao["usuario_id"]))
            self.p.auditar(conn, sessao["usuario_id"], "preferencias", None, ip)
