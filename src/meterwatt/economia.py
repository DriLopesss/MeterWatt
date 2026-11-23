"""E se eu…? Simulador de economia, plano de economia e a conferência "deu certo?".

As contas (kWh por mês de cada ação) são as mesmas de estatico/economia-calculo.js, que o
navegador usa para responder na hora. As duas versões são conferidas pelos mesmos casos
(tests/economia_vetores.json). Mudou uma fórmula aqui, mude lá também.

O que vem da casa (quando a pessoa autorizou o consumo e há medição):
- chuveiro: potência, banhos por semana e duração, do Detetive de aparelhos;
- geladeira: consumo por mês, do Detetive;
- consumo em espera: a base das madrugadas (o mesmo número do "consumo fantasma");
- consumo médio por dia: dos últimos 7 dias medidos por inteiro.
Onde não há medição, entram valores típicos, marcados como "tipico" para a tela avisar.
"""
import json
import statistics
import unicodedata

from .erros import ErroDeNegocio, NaoEncontrado

DIAS_POR_MES = 365 / 12
SEMANAS_POR_MES = 365 / 7 / 12

CAMPOS = {
    "banho_curto": {"minutos_a_menos": (0, 30), "potencia_w": (1000, 9000), "banhos_semana": (1, 100), "minutos_banho": (1, 60)},
    "chuveiro_verao": {"porcentagem": (0, 100), "potencia_w": (1000, 9000), "potencia_verao_w": (300, 8900),
                       "banhos_semana": (1, 100), "minutos_banho": (1, 60)},
    "fantasma": {"watts_cortados": (0, 3000), "horas_dia": (0, 24)},
    "geladeira": {"kwh_mes_atual": (5, 400), "kwh_mes_nova": (5, 400), "preco": (0, 50000)},
    "usar_menos": {"potencia_w": (1, 15000), "minutos_dia": (0, 1440)},
}
OPCIONAIS = {"geladeira": {"preco"}}
NOMES = {
    "minutos_a_menos": "os minutos a menos", "potencia_w": "a potência", "banhos_semana": "os banhos por semana",
    "minutos_banho": "a duração do banho", "porcentagem": "a porcentagem", "potencia_verao_w": "a potência na posição verão",
    "watts_cortados": "os watts cortados", "horas_dia": "as horas por dia", "kwh_mes_atual": "o consumo da geladeira atual",
    "kwh_mes_nova": "o consumo da geladeira nova", "preco": "o preço", "minutos_dia": "os minutos por dia",
}
TITULOS = {
    "banho_curto": "Banho mais curto", "chuveiro_verao": "Chuveiro na posição verão", "fantasma": "Cortar o consumo em espera",
    "geladeira": "Trocar a geladeira", "usar_menos": "Usar menos",
}

TIPICOS = {"chuveiro_w": 5500, "banhos_semana": 14, "minutos_banho": 10, "fantasma_w": 40, "geladeira_kwh_mes": 50}
CHUVEIRO_MINIMO_W = 2500          # acima disso, para o Detetive, é chuveiro (inclui a posição verão)
GELADEIRA_W = (60, 400)
GELADEIRA_CICLOS_SEMANA = 35      # geladeira liga e desliga várias vezes por dia
MAXIMO_NO_PLANO = 12
DIAS_PARA_COMPARAR = 5            # dias medidos antes e depois de começar
JANELA_ANTES_DIAS = 14
COBERTURA_DIA = 0.8


class ParametroInvalido(ValueError):
    def __init__(self, campo, erro):
        super().__init__(erro)
        self.campo, self.erro = campo, erro


def _numero(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (float("inf"), float("-inf"))


def validar(tipo, parametros):
    """Devolve os parâmetros conferidos (só os campos conhecidos) ou levanta ParametroInvalido."""
    campos = CAMPOS.get(tipo)
    if campos is None:
        raise ParametroInvalido(None, "Ação desconhecida.")
    entrada = parametros if isinstance(parametros, dict) else {}
    p = {}
    for campo, (minimo, maximo) in campos.items():
        v = entrada.get(campo)
        if v is None and campo in OPCIONAIS.get(tipo, ()):
            p[campo] = None
            continue
        if not _numero(v) or not minimo <= v <= maximo:
            raise ParametroInvalido(campo, f"Confira {NOMES[campo]}: use um número de {minimo} a {maximo}.")
        p[campo] = v
    if tipo == "banho_curto" and p["minutos_a_menos"] >= p["minutos_banho"]:
        raise ParametroInvalido("minutos_a_menos", "O banho não pode ficar mais curto que zero: diminua os minutos a menos.")
    if tipo == "chuveiro_verao" and p["potencia_verao_w"] >= p["potencia_w"]:
        raise ParametroInvalido("potencia_verao_w", "Na posição verão o chuveiro gasta menos que na inverno: confira as potências.")
    return p


def kwh_mes(tipo, p):
    if tipo == "banho_curto":
        return p["potencia_w"] / 1000 * p["minutos_a_menos"] / 60 * p["banhos_semana"] * SEMANAS_POR_MES
    if tipo == "chuveiro_verao":
        return ((p["potencia_w"] - p["potencia_verao_w"]) / 1000 * p["minutos_banho"] / 60 * p["banhos_semana"]
                * SEMANAS_POR_MES * p["porcentagem"] / 100)
    if tipo == "fantasma":
        return p["watts_cortados"] * p["horas_dia"] * DIAS_POR_MES / 1000
    if tipo == "geladeira":
        return max(0.0, p["kwh_mes_atual"] - p["kwh_mes_nova"])
    if tipo == "usar_menos":
        return p["potencia_w"] / 1000 * p["minutos_dia"] / 60 * DIAS_POR_MES
    return 0.0


def total_kwh_mes(acoes):
    """Soma de [{tipo, parametros}], descontando a sobreposição de banho mais curto com posição verão."""
    total, banho, verao = 0.0, None, None
    for a in acoes:
        total += kwh_mes(a["tipo"], a["parametros"])
        if a["tipo"] == "banho_curto":
            banho = a["parametros"]
        elif a["tipo"] == "chuveiro_verao":
            verao = a["parametros"]
    if banho and verao:
        sobreposicao = ((verao["potencia_w"] - verao["potencia_verao_w"]) / 1000 * verao["porcentagem"] / 100
                        * banho["minutos_a_menos"] / 60 * verao["banhos_semana"] * SEMANAS_POR_MES)
        total -= min(sobreposicao, kwh_mes("banho_curto", banho), kwh_mes("chuveiro_verao", verao))
    return max(0.0, total)


def meses_para_pagar(preco, kwh_por_mes, tarifa):
    if not _numero(preco) or preco <= 0 or not _numero(tarifa) or tarifa <= 0 or not (kwh_por_mes or 0) > 0:
        return None
    return preco / (kwh_por_mes * tarifa)


def _limpar_nome(nome):
    if not isinstance(nome, str):
        return None
    nome = " ".join(unicodedata.normalize("NFC", nome).split())
    if not 1 <= len(nome) <= 40 or any(unicodedata.category(c).startswith("C") for c in nome):
        return None
    return nome


def _r(v, casas):
    return None if v is None else round(v, casas)


def _rotulo(nome, potencia_w):
    """Nome do aparelho, ou "Aparelho de 1.300 W" como na tela do Detetive."""
    return nome or f"Aparelho de {round(potencia_w):,} W".replace(",", ".")


class Economia:
    def __init__(self, plataforma):
        self.p = plataforma

    # ------------------------------------------------------------ dias medidos

    def _proximo_dia(self, inicio_do_dia):
        return self.p.inicio_do_dia(inicio_do_dia + 86400 + 3 * 3600)

    def _dias(self, inicio, fim):
        """Inícios de cada dia local em [inicio, fim)."""
        dias, d = [], self.p.inicio_do_dia(inicio)
        while d < fim:
            if d >= inicio:
                dias.append(d)
            d = self._proximo_dia(d)
        return dias

    def _dias_medidos(self, conn, ids, inicio, fim):
        """{início do dia: kWh} só dos dias medidos quase por inteiro (80% dos intervalos de 5 minutos)."""
        saida = {}
        if not ids:
            return saida
        marcadores = ",".join("?" * len(ids))
        for d in self._dias(inicio, fim):
            prox = self._proximo_dia(d)
            linhas = conn.execute(f"SELECT COUNT(*) AS n, SUM(wh) AS wh FROM consumo_5min WHERE medidor_id IN ({marcadores}) "
                                  "AND inicio >= ? AND inicio < ? GROUP BY medidor_id", (*ids, int(d), int(prox))).fetchall()
            if linhas and max(l["n"] for l in linhas) >= COBERTURA_DIA * (prox - d) / 300:
                saida[d] = sum(l["wh"] or 0 for l in linhas) / 1000
        return saida

    def _ids(self, conn, uid):
        return self.p.medidores.ids_da_casa(conn, uid)       # o consumo da casa: circuito não soma de novo

    # ------------------------------------------------------------ o que vem da casa

    def base(self, sessao):
        conn = self.p.db()
        uid = sessao["usuario_id"]
        u = conn.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()
        tarifa = self.p.tarifa_de(u)
        chuveiro = {"potencia_w": TIPICOS["chuveiro_w"], "banhos_semana": TIPICOS["banhos_semana"],
                    "minutos_banho": TIPICOS["minutos_banho"], "fonte": "tipico", "aparelho_id": None}
        fantasma = {"watts": TIPICOS["fantasma_w"], "fonte": "tipico"}
        geladeira = {"kwh_mes": TIPICOS["geladeira_kwh_mes"], "potencia_w": None, "fonte": "tipico", "aparelho_id": None}
        ids = self._ids(conn, uid)
        saida = {"tarifa_kwh": tarifa, "consumo_kwh_dia": None, "detetive_ligado": False, "medidores": len(ids),
                 "consumo_autorizado": self.p.consentimento(conn, uid, "consumo"),
                 "chuveiro": chuveiro, "fantasma": fantasma, "geladeira": geladeira, "aparelhos": []}
        if not saida["consumo_autorizado"]:
            return saida                                  # sem autorização: nada medido entra na conta
        agora = self.p.agora()
        dias = self._dias_medidos(conn, ids, self.p.inicio_do_dia(agora, 7), self.p.inicio_do_dia(agora))
        if dias:
            saida["consumo_kwh_dia"] = round(statistics.mean(dias.values()), 3)
        f = self.p.medidores.fantasma_de(uid)
        if f.get("disponivel") and not f.get("estimado"):
            fantasma.update(watts=f["watts"], fonte="medido")

        detetive = self.p.detetive.listar(sessao)
        saida["detetive_ligado"] = detetive["autorizado"]
        aparelhos = [a for a in detetive["aparelhos"] if a["kwh_mes"] is not None]

        def nomeado(a, *palavras):
            return any(p in (a["nome"] or "").lower() for p in palavras)

        def por_semana(a):
            """Vezes por semana. Com o Detetive ligado há poucos dias, a contagem da semana é menor
            que o hábito: 4 banhos em 2 dias são 14 por semana, não 4."""
            dias = a.get("dias_observados")
            return max(1, round(a["vezes_na_semana"] * 7 / dias)) if dias else a["vezes_na_semana"]

        chuveiros = [a for a in aparelhos if nomeado(a, "chuveiro") or a["potencia_w"] >= CHUVEIRO_MINIMO_W]
        if chuveiros:
            ch = max(chuveiros, key=lambda a: (nomeado(a, "chuveiro"), a["kwh_mes"]))
            chuveiro.update(potencia_w=ch["potencia_w"], fonte="detetive", aparelho_id=ch["id"])
            if ch["vezes_na_semana"] > 0:
                chuveiro["banhos_semana"] = por_semana(ch)
            if ch["duracao_media_min"] >= 1:
                chuveiro["minutos_banho"] = round(ch["duracao_media_min"], 1)
        geladeiras = [a for a in aparelhos if nomeado(a, "geladeira", "freezer")
                      or (GELADEIRA_W[0] <= a["potencia_w"] < GELADEIRA_W[1] and por_semana(a) >= GELADEIRA_CICLOS_SEMANA)]
        if geladeiras:
            gel = max(geladeiras, key=lambda a: (nomeado(a, "geladeira", "freezer"), a["kwh_mes"]))
            geladeira.update(kwh_mes=gel["kwh_mes"], potencia_w=gel["potencia_w"], fonte="detetive", aparelho_id=gel["id"])
        usados = {chuveiro["aparelho_id"], geladeira["aparelho_id"]}
        for a in aparelhos:
            if a["id"] in usados or a["potencia_w"] >= CHUVEIRO_MINIMO_W:
                continue
            saida["aparelhos"].append({
                "id": a["id"], "nome": a["nome"], "palpite": a["palpite"], "potencia_w": a["potencia_w"],
                "rotulo": _rotulo(a["nome"], a["potencia_w"]),
                "minutos_dia": round(a["duracao_media_min"] * por_semana(a) / 7, 1),
            })
        return saida

    # ------------------------------------------------------------ plano

    def _gravar(self, conn, sql, valores):
        """Grava dentro da transação aberta, se houver; senão, numa só para isso."""
        if conn.in_transaction:
            conn.execute(sql, valores)
        else:
            with conn:
                conn.execute(sql, valores)

    def _adotar_orfas(self, conn, uid):
        """Ação "usar menos" cujo aparelho sumiu (Detetive desligado, medidor removido): o banco esvazia
        aparelho_id, mas a chave continua "usar_menos:<id>". A ação passa a ser como as de "Outro
        aparelho", com o nome que tinha, para dar para atualizar e tirar do plano sem duplicar."""
        orfas = conn.execute("SELECT id, nome FROM economia_plano WHERE usuario_id = ? AND tipo = 'usar_menos' "
                             "AND aparelho_id IS NULL AND chave NOT LIKE 'usar_menos:outro:%'", (uid,)).fetchall()
        for o in orfas:
            base = _limpar_nome(o["nome"]) or "Aparelho"
            nome, n = base, 1
            while conn.execute("SELECT 1 FROM economia_plano WHERE usuario_id = ? AND chave = ?",
                               (uid, "usar_menos:outro:" + nome.lower())).fetchone():
                n += 1
                nome = f"{base} ({n})"
            self._gravar(conn, "UPDATE economia_plano SET chave = ?, nome = ? WHERE id = ?", ("usar_menos:outro:" + nome.lower(), nome, o["id"]))

    def _plano(self, conn, uid):
        self._adotar_orfas(conn, uid)
        linhas = conn.execute("SELECT * FROM economia_plano WHERE usuario_id = ? ORDER BY criada_em, id", (uid,)).fetchall()
        return [{"id": l["id"], "tipo": l["tipo"], "chave": l["chave"], "titulo": TITULOS[l["tipo"]], "nome": l["nome"],
                 "aparelho_id": l["aparelho_id"], "parametros": json.loads(l["parametros"]), "kwh_mes": round(l["kwh_mes"], 3),
                 "desde": self.p.iso(l["criada_em"]), "_criada_em": l["criada_em"],
                 "_base": json.loads(l["linha_de_base"]) if l["linha_de_base"] else None} for l in linhas]

    def _totais(self, plano, tarifa):
        kwh = total_kwh_mes(plano)
        return {"kwh_mes": round(kwh, 2), "reais_mes": None if tarifa is None else round(kwh * tarifa, 2),
                "reais_ano": None if tarifa is None else round(kwh * 12 * tarifa, 2)}

    def estado(self, sessao):
        conn = self.p.db()
        uid = sessao["usuario_id"]
        base = self.base(sessao)
        plano = self._plano(conn, uid)
        saida = dict(base)
        saida["plano"] = [{k: v for k, v in a.items() if not k.startswith("_")} for a in plano]
        saida["plano_total"] = self._totais(plano, base["tarifa_kwh"])
        saida["verificacao"] = self.verificacao_de(uid, plano)
        saida["maximo_no_plano"] = MAXIMO_NO_PLANO
        return saida

    def _aparelho_do_usuario(self, conn, uid, aparelho_id):
        if aparelho_id is None:
            return None
        if not isinstance(aparelho_id, int) or isinstance(aparelho_id, bool):
            raise NaoEncontrado("Aparelho não encontrado.")
        a = conn.execute("SELECT a.* FROM aparelhos a JOIN medidores m ON m.id = a.medidor_id WHERE a.id = ? AND m.usuario_id = ?",
                         (aparelho_id, uid)).fetchone()
        if not a:
            raise NaoEncontrado("Aparelho não encontrado.")
        return a

    def salvar(self, sessao, dados, ip=None):
        if not isinstance(dados, dict):
            raise ErroDeNegocio("Pedido inválido.", 400)
        tipo = dados.get("tipo")
        try:
            p = validar(tipo, dados.get("parametros"))
        except ParametroInvalido as e:
            raise ErroDeNegocio(e.erro, 400, "parametro_invalido") from None
        kwh = kwh_mes(tipo, p)
        if kwh <= 0:
            raise ErroDeNegocio("Com esses valores não há economia. Ajuste antes de pôr no plano.", 400, "sem_economia")
        conn = self.p.db()
        uid = sessao["usuario_id"]
        self._adotar_orfas(conn, uid)          # uma ação sem aparelho nunca vira duas
        aparelho = None
        nome = None
        if tipo in ("usar_menos", "geladeira"):
            aparelho = self._aparelho_do_usuario(conn, uid, dados.get("aparelho_id"))
        if tipo == "usar_menos":
            if aparelho is not None:
                chave = f"usar_menos:{aparelho['id']}"
                nome = _rotulo(aparelho["nome"], aparelho["potencia_w"])
            else:
                nome = _limpar_nome(dados.get("nome"))
                if not nome:
                    raise ErroDeNegocio("Dê um nome ao aparelho, com até 40 caracteres.", 400, "parametro_invalido")
                chave = "usar_menos:outro:" + nome.lower()
        else:
            chave = tipo
        agora = self.p.agora()
        with conn:
            existentes = conn.execute("SELECT COUNT(*) FROM economia_plano WHERE usuario_id = ? AND chave != ?", (uid, chave)).fetchone()[0]
            if existentes >= MAXIMO_NO_PLANO:
                raise ErroDeNegocio(f"O plano já tem {MAXIMO_NO_PLANO} ações. Tire alguma antes de pôr outra.", 400)
            conn.execute(
                "INSERT INTO economia_plano (usuario_id, tipo, chave, parametros, aparelho_id, nome, kwh_mes, criada_em, atualizada_em) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (usuario_id, chave) DO UPDATE SET parametros = excluded.parametros, "
                "aparelho_id = excluded.aparelho_id, nome = excluded.nome, kwh_mes = excluded.kwh_mes, atualizada_em = excluded.atualizada_em",
                (uid, tipo, chave, json.dumps(p), aparelho["id"] if aparelho is not None else None, nome, kwh, agora, agora))
            sufixo = f" ({nome})" if nome else ""
            self.p.auditar(conn, uid, "plano_economia", f"{TITULOS[tipo]}{sufixo}: no plano", ip)
        return self.estado(sessao)

    def remover(self, sessao, acao_id, ip=None):
        conn = self.p.db()
        uid = sessao["usuario_id"]
        with conn:
            linha = conn.execute("SELECT tipo, nome FROM economia_plano WHERE id = ? AND usuario_id = ?", (acao_id, uid)).fetchone()
            if not linha:
                raise NaoEncontrado("Ação não encontrada.")
            conn.execute("DELETE FROM economia_plano WHERE id = ?", (acao_id,))
            nome = f" ({linha['nome']})" if linha["nome"] else ""
            self.p.auditar(conn, uid, "plano_economia", f"{TITULOS[linha['tipo']]}{nome}: fora do plano", ip)
        return self.estado(sessao)

    # ------------------------------------------------------------ deu certo?

    def _media_aparelhos(self, conn, aparelho_ids, dias):
        """kWh por dia dos aparelhos, só nos dias medidos."""
        if not aparelho_ids or not dias:
            return None
        marcadores = ",".join("?" * len(aparelho_ids))
        wh = 0.0
        for d in dias:
            wh += conn.execute(f"SELECT COALESCE(SUM(wh), 0) FROM ciclos WHERE aparelho_id IN ({marcadores}) AND inicio >= ? AND inicio < ?",
                               (*aparelho_ids, d, self._proximo_dia(d))).fetchone()[0]
        return wh / 1000 / len(dias)

    def _depois(self, conn, ids, inicio):
        """Dias medidos depois do dia em que a ação entrou no plano (esse dia e o de hoje ficam de fora)."""
        return self._dias_medidos(conn, ids, self._proximo_dia(self.p.inicio_do_dia(inicio)), self.p.inicio_do_dia(self.p.agora()))

    def _detetive_desde(self, conn, uid):
        """Desde quando o Detetive olha a casa sem parar (a autorização em vigor); None se está desligado."""
        linha = conn.execute("SELECT aceito, registrado_em FROM consentimentos WHERE usuario_id = ? AND finalidade = 'aparelhos' "
                             "ORDER BY registrado_em DESC, id DESC LIMIT 1", (uid,)).fetchone()
        return linha["registrado_em"] if linha and linha["aceito"] else None

    @staticmethod
    def _com_detetive(dias, desde):
        """Só os dias que começaram com o Detetive já ligado: antes disso, os aparelhos não têm ciclos."""
        return [d for d in dias if desde is not None and d >= desde]

    def _chuveiros(self, conn, ids):
        """As duas posições do chuveiro podem virar dois grupos no Detetive: entram juntas."""
        if not ids:
            return []
        marcadores = ",".join("?" * len(ids))
        todos = conn.execute(f"SELECT id, nome, potencia_w FROM aparelhos WHERE medidor_id IN ({marcadores})", ids).fetchall()
        return [x["id"] for x in todos if x["potencia_w"] >= CHUVEIRO_MINIMO_W or "chuveiro" in (x["nome"] or "").lower()]

    def _grupo(self, conn, ids, acao):
        if acao["tipo"] in ("banho_curto", "chuveiro_verao"):
            return self._chuveiros(conn, ids)
        if acao["tipo"] in ("geladeira", "usar_menos") and acao["aparelho_id"]:
            return [acao["aparelho_id"]]
        return []

    def _linha_de_base(self, conn, uid, ids, acao):
        """O "antes" da ação: consumo por dia nas 2 semanas anteriores ao dia em que ela entrou no plano,
        o dos aparelhos dela (só dias com o Detetive ligado) e, no consumo em espera, a base das madrugadas.

        É calculado uma vez, logo que a ação entra no plano, e guardado com ela: aqueles dias já passaram,
        e a retenção apaga o consumo de 5 em 5 minutos depois de 90 dias. Sem isso, a conferência de um
        plano antigo sumiria."""
        if acao.get("_base") is not None:
            return acao["_base"]
        dia0 = self.p.inicio_do_dia(acao["_criada_em"])
        dias = self._dias_medidos(conn, ids, self.p.inicio_do_dia(dia0, JANELA_ANTES_DIAS), dia0)
        base = {"dias": len(dias), "kwh_dia": round(statistics.mean(dias.values()), 4) if dias else None}
        grupo = self._grupo(conn, ids, acao)
        com_detetive = self._com_detetive(list(dias), self._detetive_desde(conn, uid))
        if grupo and len(com_detetive) >= DIAS_PARA_COMPARAR:
            base["aparelhos"] = {"dias": len(com_detetive), "kwh_dia": round(self._media_aparelhos(conn, grupo, com_detetive), 4)}
        if acao["tipo"] == "fantasma" and len(dias) >= DIAS_PARA_COMPARAR:
            w = self.p.medidores.base_noturna(conn, ids, list(dias))
            if w is not None:
                base["espera_w"] = round(w, 1)
        self._gravar(conn, "UPDATE economia_plano SET linha_de_base = ? WHERE id = ?", (json.dumps(base), acao["id"]))
        acao["_base"] = base
        return base

    def verificacao_de(self, uid, plano=None):
        conn = self.p.db()
        plano = self._plano(conn, uid) if plano is None else plano
        if not plano or not self.p.consentimento(conn, uid, "consumo"):
            return None
        ids = self._ids(conn, uid)
        primeira = min(plano, key=lambda a: (a["_criada_em"], a["id"]))
        base = self._linha_de_base(conn, uid, ids, primeira)
        depois = self._depois(conn, ids, primeira["_criada_em"])
        pronto = base["dias"] >= DIAS_PARA_COMPARAR and len(depois) >= DIAS_PARA_COMPARAR
        saida = {"desde": self.p.iso(primeira["_criada_em"]), "pronto": pronto, "dias_antes": base["dias"], "dias_depois": len(depois),
                 "minimo": DIAS_PARA_COMPARAR, "total": None, "itens": []}
        if not pronto:
            return saida
        u = conn.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()
        tarifa = self.p.tarifa_de(u)
        a, d = base["kwh_dia"], statistics.mean(depois.values())
        saida["total"] = {"antes_kwh_dia": round(a, 2), "depois_kwh_dia": round(d, 2),
                          "variacao": round((d - a) / a, 3) if a > 0 else None,
                          "reais_mes": None if tarifa is None else round((a - d) * DIAS_POR_MES * tarifa, 2)}
        desde_detetive = self._detetive_desde(conn, uid)

        def comparar(rotulo, acao, grupo):
            """Antes (guardado com a ação) e depois de um grupo de aparelhos, só em dias com o Detetive ligado."""
            antes = self._linha_de_base(conn, uid, ids, acao).get("aparelhos")
            dep = self._com_detetive(list(self._depois(conn, ids, acao["_criada_em"])), desde_detetive)
            if not antes or not grupo or len(dep) < DIAS_PARA_COMPARAR:
                return
            saida["itens"].append({"rotulo": rotulo, "unidade": "kWh por dia", "antes": round(antes["kwh_dia"], 2),
                                   "depois": _r(self._media_aparelhos(conn, grupo, dep), 2)})

        if desde_detetive is not None and ids:
            do_chuveiro = [x for x in plano if x["tipo"] in ("banho_curto", "chuveiro_verao")]
            if do_chuveiro:
                comparar("Chuveiro (verão e inverno)", min(do_chuveiro, key=lambda x: (x["_criada_em"], x["id"])), self._chuveiros(conn, ids))
            for acao in plano:
                if acao["tipo"] in ("geladeira", "usar_menos") and acao["aparelho_id"]:
                    comparar("Geladeira" if acao["tipo"] == "geladeira" else acao["nome"], acao, [acao["aparelho_id"]])
        fant = [x for x in plano if x["tipo"] == "fantasma"]
        if fant and ids:
            acao = fant[0]
            antes_w = self._linha_de_base(conn, uid, ids, acao).get("espera_w")
            dep = self._depois(conn, ids, acao["_criada_em"])
            depois_w = self.p.medidores.base_noturna(conn, ids, list(dep)) if len(dep) >= DIAS_PARA_COMPARAR else None
            if antes_w is not None and depois_w is not None:
                saida["itens"].append({"rotulo": "Consumo em espera", "unidade": "W", "antes": round(antes_w), "depois": round(depois_w)})
        return saida

    def resumo(self, sessao):
        return self.resumo_de(sessao["usuario_id"])

    def resumo_de(self, uid):
        conn = self.p.db()
        plano = self._plano(conn, uid)
        u = conn.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()
        saida = {"acoes": len(plano), **self._totais(plano, self.p.tarifa_de(u))}
        saida["verificacao"] = self.verificacao_de(uid, plano) if plano else None
        return saida

    # ------------------------------------------------------------ privacidade

    def exportar(self, conn, uid):
        return [{"acao": TITULOS[a["tipo"]], "aparelho": a["nome"], "parametros": a["parametros"],
                 "economia_estimada_kwh_mes": a["kwh_mes"], "desde": a["desde"],
                 "consumo_antes_kwh_dia": (a["_base"] or {}).get("kwh_dia")} for a in self._plano(conn, uid)]

    def apagar(self, conn, uid):
        conn.execute("DELETE FROM economia_plano WHERE usuario_id = ?", (uid,))
