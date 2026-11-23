/* Contas do "E se eu…?" (simulador de economia).
 *
 * O navegador usa este arquivo para responder na hora, enquanto a pessoa arrasta os
 * controles. O servidor faz as mesmas contas em src/meterwatt/economia.py, que é quem vale
 * para o plano salvo. Os dois são conferidos pelos mesmos casos de teste
 * (tests/economia_vetores.json): mudou uma fórmula aqui, mude lá também e rode os testes.
 *
 * Tudo em kWh por mês. Um "mês" é a média de um ano: 365 dias divididos por 12.
 */
(function (raiz) {
  "use strict";

  const DIAS_POR_MES = 365 / 12;
  const SEMANAS_POR_MES = 365 / 7 / 12;

  // Campos de cada ação e os valores aceitos [mínimo, máximo]. `null` em `opcional` = pode faltar.
  const CAMPOS = {
    banho_curto: { minutos_a_menos: [0, 30], potencia_w: [1000, 9000], banhos_semana: [1, 100], minutos_banho: [1, 60] },
    chuveiro_verao: { porcentagem: [0, 100], potencia_w: [1000, 9000], potencia_verao_w: [300, 8900], banhos_semana: [1, 100], minutos_banho: [1, 60] },
    fantasma: { watts_cortados: [0, 3000], horas_dia: [0, 24] },
    geladeira: { kwh_mes_atual: [5, 400], kwh_mes_nova: [5, 400], preco: [0, 50000] },
    usar_menos: { potencia_w: [1, 15000], minutos_dia: [0, 1440] },
  };
  const OPCIONAIS = { geladeira: ["preco"] };
  const NOMES = {
    minutos_a_menos: "os minutos a menos", potencia_w: "a potência", banhos_semana: "os banhos por semana",
    minutos_banho: "a duração do banho", porcentagem: "a porcentagem", potencia_verao_w: "a potência na posição verão",
    watts_cortados: "os watts cortados", horas_dia: "as horas por dia", kwh_mes_atual: "o consumo da geladeira atual",
    kwh_mes_nova: "o consumo da geladeira nova", preco: "o preço", minutos_dia: "os minutos por dia",
  };

  function numero(v) {
    return typeof v === "number" && Number.isFinite(v);
  }

  /* Confere os parâmetros. Devolve {ok: true, p} (só com os campos conhecidos) ou
   * {ok: false, campo, erro}. */
  function validar(tipo, parametros) {
    const campos = CAMPOS[tipo];
    if (!campos) return { ok: false, campo: null, erro: "Ação desconhecida." };
    const entrada = parametros && typeof parametros === "object" ? parametros : {};
    const p = {};
    for (const [campo, [minimo, maximo]] of Object.entries(campos)) {
      const v = entrada[campo];
      if ((v === null || v === undefined) && (OPCIONAIS[tipo] || []).includes(campo)) { p[campo] = null; continue; }
      if (!numero(v) || v < minimo || v > maximo) {
        return { ok: false, campo, erro: `Confira ${NOMES[campo]}: use um número de ${minimo} a ${maximo}.` };
      }
      p[campo] = v;
    }
    if (tipo === "banho_curto" && p.minutos_a_menos >= p.minutos_banho) {
      return { ok: false, campo: "minutos_a_menos", erro: "O banho não pode ficar mais curto que zero: diminua os minutos a menos." };
    }
    if (tipo === "chuveiro_verao" && p.potencia_verao_w >= p.potencia_w) {
      return { ok: false, campo: "potencia_verao_w", erro: "Na posição verão o chuveiro gasta menos que na inverno: confira as potências." };
    }
    return { ok: true, p };
  }

  /* Quanto a ação economiza por mês, em kWh. Supõe parâmetros já validados. */
  function kwhMes(tipo, p) {
    switch (tipo) {
      case "banho_curto":
        return p.potencia_w / 1000 * p.minutos_a_menos / 60 * p.banhos_semana * SEMANAS_POR_MES;
      case "chuveiro_verao":
        return (p.potencia_w - p.potencia_verao_w) / 1000 * p.minutos_banho / 60 * p.banhos_semana * SEMANAS_POR_MES * p.porcentagem / 100;
      case "fantasma":
        return p.watts_cortados * p.horas_dia * DIAS_POR_MES / 1000;
      case "geladeira":
        return Math.max(0, p.kwh_mes_atual - p.kwh_mes_nova);
      case "usar_menos":
        return p.potencia_w / 1000 * p.minutos_dia / 60 * DIAS_POR_MES;
      default:
        return 0;
    }
  }

  /* Total de várias ações [{tipo, parametros}], já validadas.
   * Banho mais curto e chuveiro no verão não somam simplesmente: um banho mais curto na
   * posição verão economiza menos que as duas ideias separadas. A diferença é descontada. */
  function totalKwhMes(acoes) {
    let total = 0;
    let banho = null, verao = null;
    for (const a of acoes) {
      total += kwhMes(a.tipo, a.parametros);
      if (a.tipo === "banho_curto") banho = a.parametros;
      if (a.tipo === "chuveiro_verao") verao = a.parametros;
    }
    if (banho && verao) {
      const sobreposicao = (verao.potencia_w - verao.potencia_verao_w) / 1000 * verao.porcentagem / 100
        * banho.minutos_a_menos / 60 * verao.banhos_semana * SEMANAS_POR_MES;
      total -= Math.min(sobreposicao, kwhMes("banho_curto", banho), kwhMes("chuveiro_verao", verao));
    }
    return Math.max(0, total);
  }

  /* Em quantos meses a troca se paga (geladeira nova). null quando não dá para saber. */
  function mesesParaPagar(preco, kwhPorMes, tarifa) {
    if (!numero(preco) || preco <= 0 || !numero(tarifa) || tarifa <= 0 || !(kwhPorMes > 0)) return null;
    return preco / (kwhPorMes * tarifa);
  }

  const API = { DIAS_POR_MES, SEMANAS_POR_MES, CAMPOS, validar, kwhMes, totalKwhMes, mesesParaPagar };
  if (typeof module === "object" && module.exports) module.exports = API;
  else raiz.MWEconomia = API;
})(typeof globalThis !== "undefined" ? globalThis : this);
