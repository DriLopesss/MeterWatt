"""Calibração do MeterWatt: compara as leituras do medidor com instrumentos de referência.

Uso em duas etapas, na raiz do repositório:

1. Na bancada, com o backend rodando e o eletricista presente:
       python tools/calibrar.py anotar
   O programa pergunta o nome da carga, marca sozinho a hora de início e de fim de cada
   medição e pede os valores do multímetro (tensão) e do alicate amperímetro (corrente).

2. Depois, para gerar o relatório:
       python tools/calibrar.py relatorio
   Ele lê o CSV de leituras do backend e as medidas anotadas, e escreve docs/calibracao.md.

O relatório não "aprova" o medidor: ele mostra o erro de cada carga e diz se ficou
dentro da meta escolhida. Quem decide o que fazer com o resultado são vocês.
"""
import argparse
import csv
import math
import os
import sys
from datetime import datetime, timezone
from statistics import mean, pstdev

CAMPOS_MEDIDAS = ["inicio", "fim", "carga", "resistiva", "tensao_ref_v", "corrente_ref_a",
                  "potencia_ref_w", "observacao"]
MINIMO_DE_LEITURAS = 5          # menos que isso numa janela, o relatório avisa
CORRENTE_QUASE_ZERO_A = 0.05    # abaixo disso não faz sentido falar em erro percentual


def agora():
    return datetime.now(timezone.utc)


def ler_numero(texto, obrigatorio=True):
    """Lê um número digitado, aceitando vírgula. Devolve None se vazio e não obrigatório."""
    while True:
        bruto = input(texto).strip().replace(",", ".")
        if not bruto and not obrigatorio:
            return None
        try:
            valor = float(bruto)
            if valor >= 0 and math.isfinite(valor):
                return valor
        except ValueError:
            pass
        print("  Não entendi. Digite só o número, por exemplo 127,3")


def anotar(caminho):
    novo = not os.path.exists(caminho)
    os.makedirs(os.path.dirname(caminho) or ".", exist_ok=True)
    print("Anotação de medidas de referência. Deixe o backend rodando durante todo o teste.")
    print("Dica: comece com a rede ligada e SEM carga, para conferir a tensão e o zero de corrente.\n")
    while True:
        carga = input("Nome da carga (ou Enter para terminar): ").strip()
        if not carga:
            break
        resistiva = input("  É uma carga resistiva (chaleira, ferro, chuveiro, lâmpada comum)? [S/n] ").strip().lower() != "n"
        input("  Ligue a carga, espere uns 10 segundos e aperte Enter para COMEÇAR a medição...")
        inicio = agora()
        input("  Medindo. Leia o multímetro e o alicate. Depois de pelo menos 30 s, aperte Enter para ENCERRAR...")
        fim = agora()
        duracao = (fim - inicio).total_seconds()
        if duracao < 20:
            print(f"  Aviso: a medição durou só {duracao:.0f} s. O relatório pode ter poucas leituras.")
        tensao = ler_numero("  Tensão no multímetro (V): ")
        corrente = ler_numero("  Corrente no alicate (A): ")
        potencia = ler_numero("  Potência no instrumento de referência (W), se houver (Enter para pular): ", obrigatorio=False)
        obs = input("  Observação (opcional): ").strip()
        with open(caminho, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CAMPOS_MEDIDAS)
            if novo:
                w.writeheader()
                novo = False
            w.writerow({"inicio": inicio.isoformat(timespec="seconds"), "fim": fim.isoformat(timespec="seconds"),
                        "carga": carga, "resistiva": "sim" if resistiva else "nao",
                        "tensao_ref_v": tensao, "corrente_ref_a": corrente,
                        "potencia_ref_w": "" if potencia is None else potencia, "observacao": obs})
        print(f"  Anotado em {caminho}.\n")
    print("Pronto. Para gerar o relatório: python tools/calibrar.py relatorio")


# ---------------------------------------------------------------- relatório

def _float(texto):
    try:
        valor = float(str(texto).replace(",", "."))
        return valor if math.isfinite(valor) else None
    except (TypeError, ValueError):
        return None


def carregar_leituras(caminho):
    leituras = []
    with open(caminho, newline="", encoding="utf-8") as f:
        for linha in csv.DictReader(f):
            if str(linha.get("sensor_ok", "")).lower() != "true":
                continue
            try:
                quando = datetime.fromisoformat(linha["recebido_em"])
            except (KeyError, ValueError):
                continue
            leituras.append({
                "quando": quando, "dispositivo": linha.get("dispositivo", ""),
                "tensao_v": _float(linha.get("tensao_v")), "corrente_a": _float(linha.get("corrente_a")),
                "potencia_w": _float(linha.get("potencia_w")), "fp": _float(linha.get("fp")),
                "simulado": str(linha.get("simulado", "")).lower() == "true",
            })
    return leituras


def carregar_medidas(caminho):
    medidas = []
    with open(caminho, newline="", encoding="utf-8") as f:
        for n, linha in enumerate(csv.DictReader(f), start=2):
            try:
                inicio, fim = datetime.fromisoformat(linha["inicio"]), datetime.fromisoformat(linha["fim"])
            except (KeyError, ValueError):
                raise SystemExit(f"{caminho}, linha {n}: início ou fim com data inválida.")
            tensao, corrente = _float(linha.get("tensao_ref_v")), _float(linha.get("corrente_ref_a"))
            if tensao is None or corrente is None:
                raise SystemExit(f"{caminho}, linha {n}: faltam a tensão ou a corrente de referência.")
            medidas.append({"inicio": inicio, "fim": fim, "carga": linha.get("carga", "").strip() or f"carga {n-1}",
                            "resistiva": linha.get("resistiva", "").strip().lower() != "nao",
                            "tensao_ref_v": tensao, "corrente_ref_a": corrente,
                            "potencia_ref_w": _float(linha.get("potencia_ref_w")),
                            "observacao": linha.get("observacao", "").strip()})
    return medidas


def _media(valores):
    valores = [v for v in valores if v is not None]
    return mean(valores) if valores else None


def _erro_pct(medido, referencia):
    if medido is None or not referencia:
        return None
    return (medido - referencia) / referencia * 100


def analisar(leituras, medidas, dispositivo=None, meta_pct=5.0):
    """Cruza cada medida de referência com as leituras do medidor na mesma janela de tempo."""
    presentes = sorted({l["dispositivo"] for l in leituras
                        if any(m["inicio"] <= l["quando"] <= m["fim"] for m in medidas)})
    if dispositivo is None:
        if len(presentes) > 1:
            raise SystemExit("Há leituras de mais de um medidor nas janelas: " + ", ".join(presentes) +
                             ". Escolha um com --dispositivo.")
        dispositivo = presentes[0] if presentes else None
    linhas, houve_simulado = [], False
    for m in medidas:
        janela = [l for l in leituras if l["dispositivo"] == dispositivo and m["inicio"] <= l["quando"] <= m["fim"]]
        houve_simulado = houve_simulado or any(l["simulado"] for l in janela)
        v, i = _media(l["tensao_v"] for l in janela), _media(l["corrente_a"] for l in janela)
        p, fp = _media(l["potencia_w"] for l in janela), _media(l["fp"] for l in janela)
        correntes = [l["corrente_a"] for l in janela if l["corrente_a"] is not None]
        # Potência de referência: a anotada; ou V x I, que só vale para carga resistiva (FP = 1).
        p_ref, origem_p = m["potencia_ref_w"], "instrumento"
        if p_ref is None and m["resistiva"] and m["corrente_ref_a"] > CORRENTE_QUASE_ZERO_A:
            p_ref, origem_p = m["tensao_ref_v"] * m["corrente_ref_a"], "V × I"
        sem_carga = m["corrente_ref_a"] <= CORRENTE_QUASE_ZERO_A
        erros = {"tensao": _erro_pct(v, m["tensao_ref_v"]),
                 "corrente": None if sem_carga else _erro_pct(i, m["corrente_ref_a"]),
                 "potencia": _erro_pct(p, p_ref)}
        avaliados = [abs(e) for e in erros.values() if e is not None]
        if not janela:
            situacao = "sem leituras"
        elif sem_carga and i is not None and i > CORRENTE_QUASE_ZERO_A:
            situacao = "fora da meta"          # medidor acusa corrente onde o alicate mostra zero
        elif avaliados and max(avaliados) > meta_pct:
            situacao = "fora da meta"
        elif len(janela) < MINIMO_DE_LEITURAS:
            situacao = "poucas leituras"
        else:
            situacao = "dentro da meta"
        linhas.append({"medida": m, "n": len(janela), "tensao_v": v, "corrente_a": i, "potencia_w": p, "fp": fp,
                       "ruido_a": pstdev(correntes) if len(correntes) > 1 else None,
                       "potencia_ref_w": p_ref, "origem_p": origem_p if p_ref is not None else None,
                       "sem_carga": sem_carga, "erros": erros, "situacao": situacao})
    return {"dispositivo": dispositivo, "meta_pct": meta_pct, "linhas": linhas, "simulado": houve_simulado}


def _n(valor, casas=1, sufixo=""):
    if valor is None:
        return "—"
    return f"{valor:.{casas}f}".replace(".", ",") + sufixo


def _pct(valor):
    return "—" if valor is None else f"{valor:+.1f}".replace(".", ",") + "%"


def montar_relatorio(analise, quando=None):
    quando = quando or datetime.now()
    linhas, meta = analise["linhas"], analise["meta_pct"]
    t = ["# Calibração do MeterWatt", "",
         f"Gerado em {quando.strftime('%d/%m/%Y às %H:%M')} por `tools/calibrar.py`. "
         f"Medidor: `{analise['dispositivo'] or 'nenhum encontrado'}`. Meta de erro: {_n(meta, 1)}%.", ""]
    if analise["simulado"]:
        t += ["> **ATENÇÃO: as leituras usadas aqui vieram do simulador.** Este relatório serve só para "
              "testar a ferramenta. Ele não diz nada sobre a precisão do medidor.", ""]
    if not linhas:
        t += ["Nenhuma medida de referência foi anotada.", ""]
        return "\n".join(t)

    com_dados = [l for l in linhas if l["n"]]
    fora = [l for l in linhas if l["situacao"] == "fora da meta"]
    def maior(chave):
        valores = [abs(l["erros"][chave]) for l in com_dados if l["erros"][chave] is not None]
        return max(valores) if valores else None
    t += ["## Resultado", ""]
    if not com_dados:
        t += ["Nenhuma leitura do medidor caiu dentro das janelas anotadas. Confira se o backend estava "
              "rodando durante o teste e se o arquivo de leituras é o certo.", ""]
    else:
        t += [f"- Cargas medidas: {len(linhas)} ({len(com_dados)} com leituras do medidor).",
              f"- Maior erro de tensão: {_n(maior('tensao'), 1, '%')}.",
              f"- Maior erro de corrente: {_n(maior('corrente'), 1, '%')}.",
              f"- Maior erro de potência: {_n(maior('potencia'), 1, '%')}.",
              (f"- **{len(fora)} carga(s) fora da meta de {_n(meta, 1)}%:** " + ", ".join(l["medida"]["carga"] for l in fora) + "."
               if fora else f"- Nenhuma carga ficou fora da meta de {_n(meta, 1)}%."), ""]
        poucas = [l["medida"]["carga"] for l in linhas if 0 < l["n"] < MINIMO_DE_LEITURAS]
        if poucas:
            t += [f"Poucas leituras (menos de {MINIMO_DE_LEITURAS}) em: " + ", ".join(poucas) +
                  ". Repita essas medições por mais tempo antes de confiar no número.", ""]

    t += ["## Medidas", "",
          "| Carga | Leituras | Tensão ref. | Tensão medida | Erro | Corrente ref. | Corrente medida | Erro | Potência ref. | Potência medida | Erro | FP | Situação |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for l in linhas:
        m = l["medida"]
        erro_i = "sem carga" if l["sem_carga"] else _pct(l["erros"]["corrente"])
        p_ref = "—" if l["potencia_ref_w"] is None else f"{_n(l['potencia_ref_w'], 0, ' W')} ({l['origem_p']})"
        t.append(f"| {m['carga']} | {l['n']} | {_n(m['tensao_ref_v'], 1, ' V')} | {_n(l['tensao_v'], 1, ' V')} | {_pct(l['erros']['tensao'])} "
                 f"| {_n(m['corrente_ref_a'], 3, ' A')} | {_n(l['corrente_a'], 3, ' A')} | {erro_i} "
                 f"| {p_ref} | {_n(l['potencia_w'], 0, ' W')} | {_pct(l['erros']['potencia'])} | {_n(l['fp'], 2)} | {l['situacao']} |")
    t += ["", "## Detalhes de cada medição", ""]
    for l in linhas:
        m = l["medida"]
        local = m["inicio"].astimezone()
        duracao = (m["fim"] - m["inicio"]).total_seconds()
        t.append(f"- **{m['carga']}**: {local.strftime('%d/%m/%Y %H:%M:%S')}, {duracao:.0f} s, "
                 f"{'resistiva' if m['resistiva'] else 'não resistiva'}, "
                 f"oscilação da corrente medida: {_n(l['ruido_a'], 3, ' A')}."
                 + (f" Observação: {m['observacao']}" if m["observacao"] else ""))
    t += ["", "## Como ler este relatório", "",
          "- O erro é (medido − referência) ÷ referência. Positivo quer dizer que o MeterWatt mostrou mais que o instrumento.",
          "- A referência também erra. Um alicate amperímetro comum tem erro de 1% a 3%, então diferenças dessa ordem "
          "não permitem dizer quem está certo.",
          "- Quando a potência de referência aparece como \"V × I\", ela foi calculada da tensão e da corrente anotadas. "
          "Isso só vale para carga resistiva, em que o fator de potência é 1.",
          "- Sem carga, o erro percentual de corrente não faz sentido. O relatório só confere se o medidor mostra perto de zero.",
          "- \"Dentro da meta\" vale para estas cargas, neste dia. Não é certificação nem garante a precisão em outras condições.", ""]
    return "\n".join(t)


def relatorio(args):
    for caminho, nome in ((args.leituras, "leituras do backend"), (args.medidas, "medidas de referência")):
        if not os.path.exists(caminho):
            raise SystemExit(f"Não encontrei o arquivo de {nome}: {caminho}")
    analise = analisar(carregar_leituras(args.leituras), carregar_medidas(args.medidas), args.dispositivo, args.meta)
    texto = montar_relatorio(analise)
    os.makedirs(os.path.dirname(args.saida) or ".", exist_ok=True)
    with open(args.saida, "w", encoding="utf-8") as f:
        f.write(texto)
    fora = sum(1 for l in analise["linhas"] if l["situacao"] == "fora da meta")
    print(f"Relatório gravado em {args.saida}: {len(analise['linhas'])} carga(s), {fora} fora da meta.")
    if analise["simulado"]:
        print("Atenção: as leituras são do simulador. O relatório não vale como calibração.")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Calibração do MeterWatt contra instrumentos de referência.")
    sub = ap.add_subparsers(dest="comando", required=True)
    a = sub.add_parser("anotar", help="anota as medidas do multímetro e do alicate durante o teste")
    a.add_argument("--medidas", default="docs/calibracao-medidas.csv")
    r = sub.add_parser("relatorio", help="gera docs/calibracao.md a partir das leituras e das medidas anotadas")
    r.add_argument("--leituras", default="src/leituras.csv", help="CSV gravado pelo backend")
    r.add_argument("--medidas", default="docs/calibracao-medidas.csv")
    r.add_argument("--saida", default="docs/calibracao.md")
    r.add_argument("--meta", type=float, default=5.0, help="erro máximo aceito, em %% (padrão: 5)")
    r.add_argument("--dispositivo", default=None, help="identificador do medidor, se houver mais de um")
    args = ap.parse_args(argv)
    if args.comando == "anotar":
        anotar(args.medidas)
    else:
        relatorio(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nInterrompido. O que já foi anotado está salvo.")
