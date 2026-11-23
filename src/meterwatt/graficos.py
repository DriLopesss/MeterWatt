"""Os gráficos do painel: o perfil do dia (consumo hora a hora) e "para onde vai agora".

Tudo sai do que os medidores mediram. O total da casa é a soma dos medidores de entrada; um
medidor de circuito mede uma parte do que a entrada já mede (veja Medidores.ids_da_casa).

Limites honestos:
- "Normal" é a mediana dos dias do mesmo tipo (dia útil ou fim de semana) das últimas 4 semanas.
  Feriado conta como dia útil.
- Uma hora só entra nas contas se pelo menos 10 dos 12 intervalos de 5 minutos foram medidos.
  Hora com buraco aparece vazia, em vez de mostrar um consumo menor que o real.
- Os aparelhos de "para onde vai agora" são o palpite do Detetive: degraus de potência que ainda
  não desligaram, casados com os aparelhos que ele já conhece.
"""
import statistics
from datetime import datetime

COBERTURA_HORA = 10          # intervalos de 5 minutos medidos, de 12, para a hora valer
DIAS_DO_NORMAL = 28
MAPA_MAXIMO = 31
DIAS_DA_FAIXA = 14
BASE_VALIDA_S = 10 * 60      # o "sempre ligado" muda devagar: recalcula a cada 10 minutos
FOLGA_BALANCO_W = 30         # circuitos podem passar da entrada até isto (ou 5%) sem aviso: é ruído de medição


def _n(valor, casas=3):
    return None if valor is None else round(valor, casas)


class Graficos:
    def __init__(self, plataforma):
        self.p = plataforma
        self._bases = {}         # medidor -> (válido até, watts ou None)

    # ------------------------------------------------------------ perfil do dia

    def horas(self, sessao, dias=14):
        """Consumo de hora em hora: hoje, o normal da casa, o mapa dos últimos dias e o horário mais pesado."""
        conn = self.p.db()
        uid = sessao["usuario_id"]
        agora = self.p.agora()
        zona = self.p.cfg.zona
        dias = max(1, min(int(dias), MAPA_MAXIMO))
        tarifa = self.p.tarifa_de(sessao)
        ids = self.p.medidores.ids_da_casa(conn, uid)
        estimado = bool(ids) and all(
            r["tipo"] == "corrente" for r in conn.execute(
                f"SELECT tipo FROM medidores WHERE id IN ({','.join('?' * len(ids))})", ids))
        local_agora = datetime.fromtimestamp(agora, zona)
        hoje, hora_atual = local_agora.date(), local_agora.hour

        desde = self.p.inicio_do_dia(agora, max(dias - 1, DIAS_DO_NORMAL))
        por_hora = {}              # (data, hora) -> [wh, {medidor: intervalos medidos}]
        por_dia = {}               # data -> wh (tudo, como em "Últimos 14 dias")
        por_balde = {}             # início do intervalo -> watts somando as entradas
        if ids:
            marcadores = ",".join("?" * len(ids))
            for r in conn.execute(f"SELECT medidor_id, inicio, n, soma_w, wh FROM consumo_5min "
                                  f"WHERE medidor_id IN ({marcadores}) AND inicio >= ? AND inicio <= ?",
                                  (*ids, int(desde), agora)):
                local = datetime.fromtimestamp(r["inicio"], zona)
                item = por_hora.setdefault((local.date(), local.hour), [0.0, {}])
                item[0] += r["wh"] or 0.0
                por_dia[local.date()] = por_dia.get(local.date(), 0.0) + (r["wh"] or 0.0)
                if r["n"]:
                    item[1][r["medidor_id"]] = item[1].get(r["medidor_id"], 0) + 1
                    if r["inicio"] >= agora - DIAS_DA_FAIXA * 86400:
                        por_balde[r["inicio"]] = por_balde.get(r["inicio"], 0.0) + r["soma_w"] / r["n"]

        def valor(data, hora):
            item = por_hora.get((data, hora))
            if item is None:
                return None
            if data == hoje and hora == hora_atual:
                return item[0] / 1000 if item[1] else None        # a hora que está correndo: o que já foi
            return item[0] / 1000 if max(item[1].values(), default=0) >= COBERTURA_HORA else None

        def horas_do_dia(data):
            return [_n(valor(data, h)) if not (data == hoje and h > hora_atual) else None for h in range(24)]

        datas = [datetime.fromtimestamp(self.p.inicio_do_dia(agora, i) + 3600, zona).date() for i in range(dias - 1, -1, -1)]
        mapa = [{"dia": d.isoformat(), "dia_da_semana": d.weekday(), "horas": horas_do_dia(d),
                 "kwh": _n(por_dia.get(d, 0.0) / 1000)} for d in datas]

        util = hoje.weekday() < 5
        anteriores = [datetime.fromtimestamp(self.p.inicio_do_dia(agora, i) + 3600, zona).date() for i in range(1, DIAS_DO_NORMAL + 1)]
        mesmos = [d for d in anteriores if (d.weekday() < 5) == util]
        normal_horas, dias_usados = [], set()
        for h in range(24):
            valores = []
            for d in mesmos:
                v = valor(d, h)
                if v is not None:
                    valores.append(v)
                    dias_usados.add(d)
            normal_horas.append(_n(statistics.median(valores)) if len(valores) >= 2 else None)
        normal = None
        if any(v is not None for v in normal_horas):
            normal = {"tipo": "util" if util else "fim_de_semana", "dias": len(dias_usados), "horas": normal_horas}

        pico = None
        if normal:
            hora = max((h for h in range(24) if normal_horas[h] is not None), key=lambda h: normal_horas[h])
            kwh = normal_horas[hora]
            dias_no_mes = 365 / 12 * (5 / 7 if util else 2 / 7)
            pico = {"hora": hora, "kwh": kwh, "custo": None if tarifa is None else round(kwh * tarifa, 2),
                    "custo_mes": None if tarifa is None else round(kwh * tarifa * dias_no_mes, 2)}

        comparacao = None
        if normal:
            pares = [(valor(hoje, h), normal_horas[h]) for h in range(hora_atual)]
            pares = [(a, b) for a, b in pares if a is not None and b is not None]
            if len(pares) >= 3:
                comparacao = {"horas": len(pares), "hoje_kwh": _n(sum(a for a, _ in pares)),
                              "normal_kwh": _n(sum(b for _, b in pares))}

        faixa = None
        if len(por_balde) >= 288:            # pelo menos um dia de intervalos medidos
            ordenados = sorted(por_balde.values())
            faixa = {"base_w": round(ordenados[int(0.05 * (len(ordenados) - 1))], 1),
                     "alto_w": round(ordenados[int(0.95 * (len(ordenados) - 1))], 1)}

        return {
            "tarifa_kwh": tarifa, "estimado": estimado, "hora_atual": hora_atual,
            "hoje": {"dia": hoje.isoformat(), "horas": horas_do_dia(hoje)},
            "normal": normal, "mapa": mapa, "pico": pico, "comparacao": comparacao, "faixa": faixa,
        }

    # ------------------------------------------------------------ para onde vai agora

    def _base(self, conn, mid, agora):
        """O que um medidor gasta parado (mediana das madrugadas da semana), guardado por alguns minutos."""
        guardado = self._bases.get(mid)
        if guardado and guardado[0] > agora:
            return guardado[1]
        m = self.p.medidores
        noites = [self.p.inicio_do_dia(agora, d) for d in range(0, 8)]
        noites = [d for d in noites if d + 5 * 3600 <= agora]
        minimos = [x for x in (m._minimo_noturno(conn, mid, d) for d in noites) if x is not None]
        base = statistics.median(minimos) if len(minimos) >= 2 else None
        self._bases[mid] = (agora + BASE_VALIDA_S, base)
        return base

    def partes_agora(self, sessao):
        """O consumo de agora dividido em partes: circuitos medidos, imóveis, ou aparelhos (palpite do Detetive)."""
        conn = self.p.db()
        uid = sessao["usuario_id"]
        agora = self.p.agora()
        tarifa = self.p.tarifa_de(sessao)
        lista = self.p.medidores.listar(sessao)["medidores"]
        entradas = [m for m in lista if m["papel"] == "casa"]
        circuitos = [m for m in lista if m["papel"] == "circuito"]
        vivas = [m for m in entradas if m["online"] and m["potencia"] is not None]
        if not vivas:
            return {"disponivel": False, "partes": [],
                    "motivo": "Nenhum medidor da entrada está enviando leituras agora." if entradas else "Nenhum medidor pareado."}
        total = sum(m["potencia"] for m in vivas)
        unidade = "VA" if all(m["unidade"] == "VA" for m in vivas) else "W"
        aviso = None

        def viva(m):
            return m["potencia"] if m["online"] and m["potencia"] is not None else None

        if circuitos:
            fonte = "circuitos"
            partes = [{"id": c["id"], "nome": c["apelido"], "tipo": "circuito", "watts": viva(c), "kwh_hoje": c["kwh_hoje"],
                       "dentro_de": c["dentro_de"]} for c in circuitos]
            soma = sum(p["watts"] or 0 for p in partes)
            resto = total - soma
            if resto < -max(FOLGA_BALANCO_W, 0.05 * total):
                aviso = ("Os circuitos somam mais do que a entrada. Confira se cada medidor de circuito está mesmo depois "
                         "do medidor da entrada e se os dois estão medindo certo.")
            kwh_resto = sum(e["kwh_hoje"] for e in entradas) - sum(c["kwh_hoje"] for c in circuitos)
            partes.append({"nome": "Resto da casa", "tipo": "resto", "watts": max(0.0, resto), "kwh_hoje": _n(max(0.0, kwh_resto), 4)})
        elif len(entradas) > 1:
            fonte = "imoveis"
            partes = [{"id": e["id"], "nome": e["apelido"], "tipo": "entrada", "watts": viva(e), "kwh_hoje": e["kwh_hoje"]}
                      for e in entradas]
        else:
            e = vivas[0]
            base = self._base(conn, e["id"], agora)
            detetive = e["tipo"] == "pzem" and self.p.consentimento(conn, uid, "aparelhos")
            partes = self.p.detetive.ligados_agora(conn, e["id"], total - (base or 0.0), agora) if detetive else []
            fonte = "detetive" if detetive else "base"
            usado = sum(p["watts"] for p in partes)
            if base is not None:
                b = min(base, max(0.0, total - usado))
                partes.append({"nome": "Sempre ligado", "tipo": "base", "watts": round(b, 1)})
                usado += b
            partes.append({"nome": "Outros", "tipo": "resto", "watts": round(max(0.0, total - usado), 1)})

        for p in partes:
            p["custo_por_hora"] = None if tarifa is None or p["watts"] is None else round(p["watts"] / 1000 * tarifa, 3)
        return {"disponivel": True, "fonte": fonte, "total": round(total, 1), "unidade": unidade,
                "custo_por_hora": None if tarifa is None else round(total / 1000 * tarifa, 3),
                "partes": partes, "aviso": aviso, "em": self.p.iso(agora)}
