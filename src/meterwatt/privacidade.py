"""LGPD no código: finalidades, consentimentos, exportação, exclusão, retenção e atividade.

Os textos jurídicos são rascunho. Itens marcados [ADVOGADO] em docs/lgpd.md precisam de
revisão antes de qualquer cliente real.
"""
from .erros import ErroDeNegocio
from .seguranca import confere_senha, confere_totp, normalizar_codigo_backup

VERSAO_TERMOS = "2026-11-13"
VERSAO_PRIVACIDADE = "2026-11-13"

FINALIDADES = {
    "termos": {
        "nome": "Termos de uso",
        "descricao": "Aceite das regras de uso do serviço.",
        "obrigatorio": True, "revogavel": False, "versao": VERSAO_TERMOS,
    },
    "privacidade": {
        "nome": "Política de privacidade",
        "descricao": "Ciência de como os seus dados são tratados.",
        "obrigatorio": True, "revogavel": False, "versao": VERSAO_PRIVACIDADE,
    },
    "consumo": {
        "nome": "Dados de consumo de energia",
        "descricao": ("Guardar as leituras dos seus medidores para mostrar o painel, o histórico e os alertas. "
                      "Essas leituras revelam a rotina da casa. Sem esta autorização, nenhum medidor é pareado "
                      "e nenhuma leitura nova é guardada."),
        "obrigatorio": True, "revogavel": True, "versao": VERSAO_PRIVACIDADE,
    },
    "historico_longo": {
        "nome": "Histórico de até 2 anos",
        "descricao": ("Guardar o consumo de 5 em 5 minutos por até 2 anos, em vez de 90 dias, para comparar "
                      "meses e estações do ano."),
        "obrigatorio": False, "revogavel": True, "versao": VERSAO_PRIVACIDADE,
    },
    "novidades": {
        "nome": "Novidades por e-mail",
        "descricao": "Receber notícias sobre o MeterWatt. O seu e-mail nunca é vendido nem repassado.",
        "obrigatorio": False, "revogavel": True, "versao": VERSAO_PRIVACIDADE,
    },
}

EVENTOS = {
    "cadastro": "Conta criada",
    "cadastro_repetido": "Tentativa de criar outra conta com o seu e-mail",
    "email_confirmado": "E-mail confirmado",
    "login": "Entrada na conta",
    "login_falhou": "Senha errada ao entrar",
    "login_2fa_falhou": "Código de verificação errado ao entrar",
    "login_codigo_backup": "Entrada com código de backup",
    "sair": "Saída da conta",
    "sessao_encerrada": "Sessão encerrada em outro aparelho",
    "senha_redefinida": "Senha redefinida pelo e-mail",
    "senha_trocada": "Senha trocada",
    "2fa_ativado": "Verificação em duas etapas ativada",
    "2fa_desativado": "Verificação em duas etapas desativada",
    "codigos_backup_gerados": "Novos códigos de backup gerados",
    "preferencias": "Tarifa ou meta alteradas",
    "codigo_pareamento": "Código de pareamento gerado",
    "medidor_pareado": "Medidor pareado",
    "pareamento_recusado": "Pareamento recusado: o medidor já pertence a outra conta",
    "medidor_renomeado": "Medidor renomeado",
    "medidor_removido": "Medidor removido",
    "consentimento": "Autorização alterada",
    "dados_exportados": "Cópia dos dados baixada",
    "consumo_apagado": "Dados de consumo apagados",
}


class Privacidade:
    def __init__(self, plataforma):
        self.p = plataforma

    # ------------------------------------------------------------ consentimentos

    def registrar(self, conn, usuario_id, finalidade, aceito, origem):
        info = FINALIDADES[finalidade]
        conn.execute(
            "INSERT INTO consentimentos (usuario_id, finalidade, versao, aceito, registrado_em, origem) "
            "VALUES (?, ?, ?, ?, ?, ?)", (usuario_id, finalidade, info["versao"], 1 if aceito else 0, self.p.agora(), origem))

    def estado(self, sessao):
        conn = self.p.db()
        uid = sessao["usuario_id"]
        historico = conn.execute(
            "SELECT finalidade, versao, aceito, registrado_em, origem FROM consentimentos "
            "WHERE usuario_id = ? ORDER BY registrado_em DESC, id DESC", (uid,)).fetchall()
        atuais = {}
        for h in historico:
            atuais.setdefault(h["finalidade"], h)
        finalidades = []
        for chave, info in FINALIDADES.items():
            atual = atuais.get(chave)
            finalidades.append({
                "id": chave, "nome": info["nome"], "descricao": info["descricao"],
                "obrigatorio": info["obrigatorio"], "revogavel": info["revogavel"],
                "aceito": bool(atual and atual["aceito"]),
                "versao_aceita": atual["versao"] if atual else None, "versao_vigente": info["versao"],
                "desde": self.p.iso(atual["registrado_em"]) if atual else None,
            })
        return {
            "finalidades": finalidades,
            "historico": [{"finalidade": FINALIDADES.get(h["finalidade"], {}).get("nome", h["finalidade"]),
                           "aceito": bool(h["aceito"]), "versao": h["versao"],
                           "quando": self.p.iso(h["registrado_em"]), "origem": h["origem"]} for h in historico],
            "retencao": self.regras_de_retencao(),
            **self.contato(),
        }

    def alterar(self, sessao, finalidade, aceito, ip=None):
        info = FINALIDADES.get(finalidade)
        if not info or not isinstance(aceito, bool):
            raise ErroDeNegocio("Autorização desconhecida.")
        if not info["revogavel"]:
            raise ErroDeNegocio("Esta autorização faz parte do uso do serviço. Para deixar de usá-lo, exclua a conta.")
        conn = self.p.db()
        uid = sessao["usuario_id"]
        if self.p.consentimento(conn, uid, finalidade) == aceito:
            return
        with conn:
            self.registrar(conn, uid, finalidade, aceito, "painel")
            if finalidade == "consumo" and not aceito:
                conn.execute("DELETE FROM codigos_pareamento WHERE usuario_id = ? AND usado_em IS NULL", (uid,))
            self.p.auditar(conn, uid, "consentimento", f"{info['nome']}: {'autorizado' if aceito else 'revogado'}", ip)

    # ------------------------------------------------------------ direitos do titular

    def exportar(self, sessao, ip=None):
        """Cópia completa dos dados da pessoa, em formato aberto (JSON). Nada de hashes,
        segredos ou tokens: eles não são informação sobre a pessoa e ajudariam um invasor."""
        conn = self.p.db()
        uid = sessao["usuario_id"]
        agora = self.p.agora()
        if self.p.limite_exportar.esgotado(uid, agora):
            raise ErroDeNegocio("Você já baixou os seus dados várias vezes na última hora. Tente mais tarde.", 429)
        self.p.limite_exportar.registrar(uid, agora)
        u = conn.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()
        medidores = conn.execute("SELECT * FROM medidores WHERE usuario_id = ? ORDER BY pareado_em", (uid,)).fetchall()
        saida = {
            "gerado_em": self.p.iso(agora),
            "observacao": ("Cópia dos dados pessoais guardados pelo MeterWatt sobre você, conforme a LGPD "
                           "(art. 18). Leituras de segundo a segundo ficam guardadas só pelo prazo informado em "
                           "'retencao'; depois disso, só os totais de 5 em 5 minutos."),
            "conta": {
                "nome": u["nome"], "email": u["email"], "criada_em": self.p.iso(u["criado_em"]),
                "email_confirmado_em": self.p.iso(u["email_confirmado_em"]),
                "verificacao_em_duas_etapas": bool(u["totp_ativado_em"]),
                "tarifa_kwh": u["tarifa_kwh"], "meta_mensal_reais": u["meta_mensal_reais"],
            },
            "autorizacoes": self.estado(sessao)["historico"],
            "retencao": self.regras_de_retencao(),
            "medidores": [],
            "sessoes_ativas": [
                {"criada_em": self.p.iso(s["criada_em"]), "ultimo_uso": self.p.iso(s["ultimo_uso"]),
                 "rede": s["ip_resumido"], "navegador": s["navegador"]}
                for s in conn.execute("SELECT * FROM sessoes WHERE usuario_id = ? AND etapa = 'completa'", (uid,))],
            "atividade": self.atividade(sessao, limite=10000),
        }
        for m in medidores:
            agregados = conn.execute(
                "SELECT inicio, n, soma_w, min_w, max_w, wh, simulado FROM consumo_5min WHERE medidor_id = ? ORDER BY inicio",
                (m["id"],)).fetchall()
            leituras = conn.execute(
                "SELECT * FROM leituras WHERE medidor_id = ? ORDER BY instante", (m["id"],)).fetchall()
            saida["medidores"].append({
                "id": m["id"], "apelido": m["apelido"], "pareado_em": self.p.iso(m["pareado_em"]),
                "tipo_de_sensor": m["tipo"], "firmware": m["fw"],
                "consumo_5_minutos": [
                    {"inicio": self.p.iso(a["inicio"]), "leituras": a["n"],
                     "potencia_media": round(a["soma_w"] / a["n"], 1) if a["n"] else None,
                     "potencia_minima": a["min_w"], "potencia_maxima": a["max_w"],
                     "energia_wh": round(a["wh"], 3), "simulado": bool(a["simulado"])} for a in agregados],
                "leituras_recentes": [
                    {"instante": self.p.iso(l["instante"]), "sensor_ok": bool(l["sensor_ok"]), "erro_sensor": l["erro_sensor"],
                     "tensao_v": l["tensao_v"], "corrente_a": l["corrente_a"], "potencia_w": l["potencia_w"],
                     "potencia_va": l["potencia_va"], "fp": l["fp"], "frequencia_hz": l["frequencia_hz"],
                     "sinal_wifi_dbm": l["rssi"], "simulado": bool(l["simulado"])} for l in leituras],
            })
        self.p.auditar(conn, uid, "dados_exportados", None, ip)
        return saida

    def _confirmar_identidade(self, conn, uid, senha, codigo):
        """Senha sempre; e o código do autenticador (ou de backup) se a conta usa duas etapas."""
        agora = self.p.agora()
        espera = self.p.limite_confirmacao.esgotado(uid, agora)
        if espera:
            raise ErroDeNegocio(f"Muitas tentativas. Tente de novo em {max(1, espera // 60)} minuto(s).", 429)
        u = conn.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()
        ok, _ = confere_senha(u["senha_hash"], senha if isinstance(senha, str) else "")
        if not ok:
            self.p.limite_confirmacao.registrar(uid, agora)
            raise ErroDeNegocio("Senha incorreta.", 403, "senha_incorreta")
        if u["totp_ativado_em"]:
            segredo = self.p.cofre.decifrar(u["totp_cifrado"])
            passo = confere_totp(segredo, codigo or "", self.p.agora(), u["totp_ultimo_passo"])
            if passo:
                conn.execute("UPDATE usuarios SET totp_ultimo_passo = ? WHERE id = ?", (passo, uid))
                return u
            normal = normalizar_codigo_backup(codigo or "")
            if normal:
                linha = conn.execute("SELECT id FROM codigos_backup WHERE usuario_id = ? AND codigo_hash = ? AND usado_em IS NULL",
                                     (uid, self.p.cofre.hmac(normal))).fetchone()
                if linha:
                    conn.execute("UPDATE codigos_backup SET usado_em = ? WHERE id = ?", (self.p.agora(), linha["id"]))
                    return u
            self.p.limite_confirmacao.registrar(uid, agora)
            raise ErroDeNegocio("Código de verificação incorreto.", 403, "codigo_incorreto")
        self.p.limite_confirmacao.zerar(uid)
        return u

    def apagar_consumo(self, sessao, senha, codigo=None, ip=None):
        conn = self.p.db()
        uid = sessao["usuario_id"]
        with conn:
            self._confirmar_identidade(conn, uid, senha, codigo)
            ids = [r["id"] for r in conn.execute("SELECT id FROM medidores WHERE usuario_id = ?", (uid,))]
            for mid in ids:
                conn.execute("DELETE FROM leituras WHERE medidor_id = ?", (mid,))
                conn.execute("DELETE FROM consumo_5min WHERE medidor_id = ?", (mid,))
                conn.execute("UPDATE medidores SET contador_wh = NULL, potencia_anterior = NULL WHERE id = ?", (mid,))
            self.p.auditar(conn, uid, "consumo_apagado", f"{len(ids)} medidor(es)", ip)

    def excluir_conta(self, sessao, senha, codigo=None, ip=None):
        """Apaga a conta e tudo ligado a ela: medidores, leituras, sessões, autorizações e
        atividade. Fica só um registro da exclusão, com o e-mail transformado num código
        irreversível, para provar que o pedido foi atendido. [ADVOGADO: confirmar.]"""
        conn = self.p.db()
        uid = sessao["usuario_id"]
        with conn:
            u = self._confirmar_identidade(conn, uid, senha, codigo)
            conn.execute("DELETE FROM usuarios WHERE id = ?", (uid,))   # o resto sai em cascata
            conn.execute("INSERT INTO exclusoes (instante, email_hmac) VALUES (?, ?)",
                         (self.p.agora(), self.p.cofre.hmac("exclusao:" + u["email"])))
        self.p.enviar_email(u["email"], "Sua conta no MeterWatt foi excluída", (
            f"Olá, {u['nome']}.\n\nA sua conta no MeterWatt foi excluída, com os medidores, as leituras de consumo, "
            "as sessões e as autorizações ligadas a ela.\n\nSe não foi você, responda este e-mail.\n"))

    # ------------------------------------------------------------ transparência

    def atividade(self, sessao, limite=50):
        conn = self.p.db()
        linhas = conn.execute(
            "SELECT instante, evento, detalhe, ip_resumido FROM auditoria WHERE usuario_id = ? "
            "ORDER BY instante DESC, id DESC LIMIT ?", (sessao["usuario_id"], limite)).fetchall()
        return [{"quando": self.p.iso(l["instante"]), "evento": l["evento"],
                 "descricao": EVENTOS.get(l["evento"], l["evento"]), "detalhe": l["detalhe"],
                 "rede": l["ip_resumido"]} for l in linhas]

    def regras_de_retencao(self):
        c = self.p.cfg
        return {
            "leituras_detalhadas_horas": c.retencao_bruta_horas,
            "consumo_5_minutos_dias": c.retencao_agregada_dias,
            "consumo_5_minutos_dias_com_historico_longo": c.retencao_agregada_longa_dias,
            "atividade_da_conta_dias": c.retencao_auditoria_dias,
            "conta_nao_confirmada_dias": c.retencao_conta_nao_confirmada_dias,
        }

    def contato(self):
        c = self.p.cfg
        return {"controlador": c.controlador, "email_privacidade": c.email_privacidade, "encarregado": c.encarregado}

    # ------------------------------------------------------------ retenção automática

    def reter(self):
        """Apaga o que passou do prazo. Roda sozinho a cada 15 minutos (veja __main__.py)."""
        conn = self.p.db()
        c = self.p.cfg
        agora = self.p.agora()
        apagados = {}
        with conn:
            apagados["leituras"] = conn.execute(
                "DELETE FROM leituras WHERE instante < ?", (agora - c.retencao_bruta_horas * 3600,)).rowcount
            com_historico_longo = [r["id"] for r in conn.execute("SELECT id FROM usuarios")
                                   if self.p.consentimento(conn, r["id"], "historico_longo")]
            limite_curto = agora - c.retencao_agregada_dias * 86400
            if com_historico_longo:
                marcadores = ",".join("?" * len(com_historico_longo))
                apagados["consumo_5min"] = conn.execute(
                    "DELETE FROM consumo_5min WHERE inicio < ? AND medidor_id IN "
                    f"(SELECT id FROM medidores WHERE usuario_id NOT IN ({marcadores}))",
                    (limite_curto, *com_historico_longo)).rowcount
            else:
                apagados["consumo_5min"] = conn.execute(
                    "DELETE FROM consumo_5min WHERE inicio < ?", (limite_curto,)).rowcount
            apagados["consumo_5min"] += conn.execute(
                "DELETE FROM consumo_5min WHERE inicio < ?", (agora - c.retencao_agregada_longa_dias * 86400,)).rowcount
            apagados["sessoes"] = conn.execute(
                "DELETE FROM sessoes WHERE expira_em < ? OR (etapa = 'completa' AND ultimo_uso < ?)",
                (agora, agora - c.sessao_ociosa_horas * 3600)).rowcount
            apagados["tokens_email"] = conn.execute("DELETE FROM tokens_email WHERE expira_em < ?", (agora - 86400,)).rowcount
            apagados["codigos_pareamento"] = conn.execute(
                "DELETE FROM codigos_pareamento WHERE expira_em < ?", (agora - 86400,)).rowcount
            apagados["auditoria"] = conn.execute(
                "DELETE FROM auditoria WHERE instante < ?", (agora - c.retencao_auditoria_dias * 86400,)).rowcount
            apagados["contas_nao_confirmadas"] = conn.execute(
                "DELETE FROM usuarios WHERE email_confirmado_em IS NULL AND criado_em < ?",
                (agora - c.retencao_conta_nao_confirmada_dias * 86400,)).rowcount
        return apagados
