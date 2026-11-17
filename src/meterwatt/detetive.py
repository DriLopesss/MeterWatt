"""Detetive de aparelhos: descobre o que liga e desliga na casa olhando só a potência total.

Como funciona (versão simples, de propósito):
1. DEGRAU: quando a potência muda de patamar e fica estável no novo, isso é um aparelho
   ligando (degrau para cima) ou desligando (para baixo).
2. CICLO: um degrau para baixo é casado com o degrau para cima mais recente de tamanho
   parecido. O par vira um ciclo: começou às X, terminou às Y, gastou Z.
3. APARELHO: ciclos de potência parecida são agrupados. Depois de 3 ciclos, o grupo
   aparece na tela para a pessoa dar um nome ("Geladeira", "Chuveiro").

Limites honestos:
- Só funciona com medidor de potência ativa (PZEM) enviando a cada 1 ou 2 segundos.
- Aparelhos de potência variável (inverter, notebook, TV) não formam degraus limpos.
- Dois aparelhos de potência parecida viram um grupo só.
- Os limiares abaixo foram ajustados com o simulador. Precisam ser revistos com o PZEM real.

Privacidade: saber qual aparelho ligou e quando revela mais da rotina do que o consumo
total. Por isso o Detetive só roda com uma autorização própria ("aparelhos"), desligada
por padrão; ao revogar, tudo o que ele guardou é apagado.
"""
import statistics
import unicodedata

from .erros import ErroDeNegocio, NaoEncontrado

LIMIAR_W = 40                 # degrau mínimo
LIMIAR_RELATIVO = 0.03        # ou 3% do patamar: com o chuveiro ligado, o ruído é maior
JANELA = 4                    # leituras seguidas para considerar o patamar estável
TOLERANCIA_W = 15
TOLERANCIA_RELATIVA = 0.05
BURACO_S = 15                 # sem leitura por mais que isso: recomeça do zero
CICLO_MAXIMO_S = 12 * 3600
CASAR_W = 25                  # o desligar casa com o ligar se a diferença for até 25 W...
CASAR_RELATIVO = 0.15         # ...ou até 15%
AGRUPAR_W = 20
AGRUPAR_RELATIVO = 0.12
CICLOS_PARA_APARECER = 3
RETENCAO_CICLOS_DIAS = 90

PALPITES = [   # (potência mínima, máxima, duração média máxima em min ou None, texto)
    (3500, 9000, None, "chuveiro elétrico ou torneira elétrica"),
    (1700, 3500, 20, "secador de cabelo, chaleira ou forno elétrico"),
    (1700, 3500, None, "aquecedor, forno elétrico ou máquina de lavar aquecendo"),
    (900, 1700, 12, "micro-ondas, air fryer ou ferro de passar"),
    (900, 1700, None, "ar-condicionado, ferro de passar ou air fryer"),
    (400, 900, None, "ar-condicionado pequeno, bomba d'água ou aspirador"),
    (60, 400, None, "geladeira, freezer ou ventilador forte"),
    (0, 60, None, "lâmpadas, ventilador ou TV"),
]


class Detector:
    """Acompanha as leituras de um medidor e avisa quando há um degrau de potência."""

    def __init__(self):
        self.nivel = None
        self.janela = []          # [(instante, watts)]
        self.ultimo = None

    def observar(self, instante, watts):
        """Devolve None ou (instante_do_degrau, delta_em_watts)."""
        if self.ultimo is not None and instante - self.ultimo > BURACO_S:
            self.nivel, self.janela = None, []
        self.ultimo = instante
        self.janela.append((instante, watts))
        if len(self.janela) > JANELA:
            self.janela.pop(0)
        if len(self.janela) < JANELA:
            return None
        valores = [w for _, w in self.janela]
        meio = statistics.median(valores)
        if max(valores) - min(valores) > max(TOLERANCIA_W, TOLERANCIA_RELATIVA * meio):
            return None                                  # ainda mudando: espera estabilizar
        if self.nivel is None:
            self.nivel = meio
            return None
        delta = meio - self.nivel
        if abs(delta) >= max(LIMIAR_W, LIMIAR_RELATIVO * max(meio, self.nivel)):
            self.nivel = meio
            return (self.janela[0][0], delta)
        self.nivel += 0.2 * delta                        # acompanha variações lentas sem criar degrau
        return None


def palpite(potencia_w, duracao_media_min):
    for minimo, maximo, duracao_maxima, texto in PALPITES:
        if minimo <= potencia_w < maximo and (duracao_maxima is None or duracao_media_min <= duracao_maxima):
            return texto
    return None


def _limpar_nome(nome):
    if not isinstance(nome, str):
        return None
    nome = " ".join(unicodedata.normalize("NFC", nome).split())
    if not 1 <= len(nome) <= 40 or any(unicodedata.category(c).startswith("C") for c in nome):
        return None
    return nome


class Detetive:
    def __init__(self, plataforma):
        self.p = plataforma
        self.detectores = {}      # medidor -> Detector (só na memória)

    # ------------------------------------------------------------ entrada das leituras

    def observar(self, conn, medidor_id, usuario_id, instante, watts):
        """Chamado a cada leitura boa de um medidor de potência ativa."""
        if not self.p.consentimento(conn, usuario_id, "aparelhos"):
            self.detectores.pop(medidor_id, None)
            return None
        degrau = self.detectores.setdefault(medidor_id, Detector()).observar(instante, watts)
        if degrau is None:
            return None
        quando, delta = degrau
        with conn:
            if delta > 0:
                conn.execute("INSERT INTO degraus_abertos (medidor_id, instante, delta_w) VALUES (?, ?, ?)", (medidor_id, quando, delta))
                conn.execute("DELETE FROM degraus_abertos WHERE medidor_id = ? AND instante < ?", (medidor_id, quando - CICLO_MAXIMO_S))
                return "ligou"
            return self._fechar_ciclo(conn, medidor_id, quando, -delta)

    def _fechar_ciclo(self, conn, medidor_id, fim, queda):
        abertos = conn.execute("SELECT id, instante, delta_w FROM degraus_abertos WHERE medidor_id = ? AND instante >= ? "
                               "ORDER BY instante DESC", (medidor_id, fim - CICLO_MAXIMO_S)).fetchall()
        par = next((a for a in abertos if abs(a["delta_w"] - queda) <= max(CASAR_W, CASAR_RELATIVO * a["delta_w"])), None)
        if par is None:
            return "desligou_sem_par"
        potencia = (par["delta_w"] + queda) / 2
        duracao = fim - par["instante"]
        conn.execute("DELETE FROM degraus_abertos WHERE id = ?", (par["id"],))
        candidatos = conn.execute("SELECT id, potencia_w, n_ciclos FROM aparelhos WHERE medidor_id = ?", (medidor_id,)).fetchall()
        perto = [a for a in candidatos if abs(a["potencia_w"] - potencia) <= max(AGRUPAR_W, AGRUPAR_RELATIVO * a["potencia_w"])]
        if perto:
            ap = min(perto, key=lambda a: abs(a["potencia_w"] - potencia))
            n = ap["n_ciclos"] + 1
            peso = 1 / min(n, 20)          # a média acompanha o aparelho se ele mudar com o tempo
            conn.execute("UPDATE aparelhos SET potencia_w = ?, n_ciclos = ? WHERE id = ?",
                         (ap["potencia_w"] * (1 - peso) + potencia * peso, n, ap["id"]))
            aparelho_id = ap["id"]
        else:
            aparelho_id = conn.execute("INSERT INTO aparelhos (medidor_id, potencia_w, n_ciclos, criado_em) VALUES (?, ?, 1, ?)",
                                       (medidor_id, potencia, self.p.agora())).lastrowid
        conn.execute("INSERT INTO ciclos (medidor_id, aparelho_id, inicio, fim, potencia_w, wh) VALUES (?, ?, ?, ?, ?, ?)",
                     (medidor_id, aparelho_id, par["instante"], fim, potencia, potencia * duracao / 3600))
        return "ciclo"

    # ------------------------------------------------------------ telas

    def listar(self, sessao):
        conn = self.p.db()
        uid = sessao["usuario_id"]
        autorizado = self.p.consentimento(conn, uid, "aparelhos")
        agora = self.p.agora()
        tarifa = self.p.tarifa_de(sessao)
        saida = []
        linhas = conn.execute(
            "SELECT a.*, m.apelido AS medidor FROM aparelhos a JOIN medidores m ON m.id = a.medidor_id "
            "WHERE m.usuario_id = ? AND a.oculto = 0 AND a.n_ciclos >= ? ORDER BY a.potencia_w DESC",
            (uid, CICLOS_PARA_APARECER)).fetchall() if autorizado else []
        for a in linhas:
            semana = conn.execute("SELECT COUNT(*) AS n, SUM(wh) AS wh, AVG(fim - inicio) AS duracao, MAX(fim) AS ultimo, MIN(inicio) AS primeiro "
                                  "FROM ciclos WHERE aparelho_id = ? AND inicio >= ?", (a["id"], agora - 7 * 86400)).fetchone()
            geral = conn.execute("SELECT AVG(fim - inicio) AS duracao, MAX(fim) AS ultimo FROM ciclos WHERE aparelho_id = ?", (a["id"],)).fetchone()
            duracao_min = (semana["duracao"] or geral["duracao"] or 0) / 60
            dias = max(1.0, min(7.0, (agora - semana["primeiro"]) / 86400)) if semana["primeiro"] else None
            kwh_mes = (semana["wh"] or 0) / 1000 / dias * 365 / 12 if dias else None
            ligado = conn.execute(
                "SELECT 1 FROM degraus_abertos WHERE medidor_id = ? AND instante >= ? AND ABS(delta_w - ?) <= ?",
                (a["medidor_id"], agora - CICLO_MAXIMO_S, a["potencia_w"], max(AGRUPAR_W, AGRUPAR_RELATIVO * a["potencia_w"]))).fetchone()
            saida.append({
                "id": a["id"], "nome": a["nome"], "palpite": palpite(a["potencia_w"], duracao_min), "medidor": a["medidor"],
                "potencia_w": round(a["potencia_w"]), "ciclos_total": a["n_ciclos"], "vezes_na_semana": semana["n"],
                "duracao_media_min": round(duracao_min, 1), "kwh_mes": None if kwh_mes is None else round(kwh_mes, 1),
                "custo_mes": None if kwh_mes is None or tarifa is None else round(kwh_mes * tarifa, 2),
                "ultima_vez": self.p.iso(geral["ultimo"]), "ligado_agora": bool(ligado),
            })
        tem_pzem = conn.execute("SELECT 1 FROM medidores WHERE usuario_id = ? AND tipo = 'pzem'", (uid,)).fetchone() is not None
        em_estudo = conn.execute(
            "SELECT COUNT(*) FROM aparelhos a JOIN medidores m ON m.id = a.medidor_id WHERE m.usuario_id = ? AND a.n_ciclos < ?",
            (uid, CICLOS_PARA_APARECER)).fetchone()[0] if autorizado else 0
        return {"autorizado": autorizado, "aparelhos": saida, "em_estudo": em_estudo, "tem_medidor_compativel": tem_pzem,
                "ciclos_para_aparecer": CICLOS_PARA_APARECER}

    def _do_usuario(self, conn, uid, aparelho_id):
        a = conn.execute("SELECT a.id FROM aparelhos a JOIN medidores m ON m.id = a.medidor_id WHERE a.id = ? AND m.usuario_id = ?",
                         (aparelho_id, uid)).fetchone()
        if not a:
            raise NaoEncontrado("Aparelho não encontrado.")

    def alterar(self, sessao, aparelho_id, dados):
        conn = self.p.db()
        self._do_usuario(conn, sessao["usuario_id"], aparelho_id)
        with conn:
            if "nome" in dados:
                nome = _limpar_nome(dados["nome"])
                if not nome:
                    raise ErroDeNegocio("O nome do aparelho precisa ter de 1 a 40 caracteres.", 400)
                conn.execute("UPDATE aparelhos SET nome = ? WHERE id = ?", (nome, aparelho_id))
            if dados.get("oculto") is True:
                conn.execute("UPDATE aparelhos SET oculto = 1 WHERE id = ?", (aparelho_id,))

    # ------------------------------------------------------------ privacidade

    def apagar_tudo(self, conn, uid):
        """Chamar dentro de uma transação: apaga tudo o que o Detetive guardou dessa conta."""
        for r in conn.execute("SELECT id FROM medidores WHERE usuario_id = ?", (uid,)).fetchall():
            conn.execute("DELETE FROM aparelhos WHERE medidor_id = ?", (r["id"],))     # os ciclos saem em cascata
            conn.execute("DELETE FROM ciclos WHERE medidor_id = ?", (r["id"],))
            conn.execute("DELETE FROM degraus_abertos WHERE medidor_id = ?", (r["id"],))
            self.detectores.pop(r["id"], None)

    def exportar(self, conn, uid):
        saida = []
        for a in conn.execute("SELECT a.* FROM aparelhos a JOIN medidores m ON m.id = a.medidor_id WHERE m.usuario_id = ?", (uid,)).fetchall():
            saida.append({
                "medidor": a["medidor_id"], "nome": a["nome"], "potencia_w": round(a["potencia_w"], 1), "oculto": bool(a["oculto"]),
                "ciclos": [{"inicio": self.p.iso(c["inicio"]), "fim": self.p.iso(c["fim"]), "potencia_w": round(c["potencia_w"], 1),
                            "energia_wh": round(c["wh"], 2)}
                           for c in conn.execute("SELECT * FROM ciclos WHERE aparelho_id = ? ORDER BY inicio", (a["id"],))],
            })
        return saida

    def reter(self, conn, agora):
        n = conn.execute("DELETE FROM ciclos WHERE fim < ?", (agora - RETENCAO_CICLOS_DIAS * 86400,)).rowcount
        conn.execute("DELETE FROM degraus_abertos WHERE instante < ?", (agora - CICLO_MAXIMO_S,))
        conn.execute("DELETE FROM aparelhos WHERE id NOT IN (SELECT DISTINCT aparelho_id FROM ciclos WHERE aparelho_id IS NOT NULL)")
        return n
