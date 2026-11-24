/* Demonstração: um servidor de mentira, dentro do navegador, com uma casa inventada.
 *
 * Serve a dois usos:
 * - o botão "Ver o app funcionando" do site e do app (app.js carrega este arquivo só quando a
 *   pessoa entra na demonstração). É o que quem não tem o medidor usa para conhecer o app,
 *   inclusive quem revisa e quem testa o app na Play Store;
 * - a demonstração em um arquivo só (tools/gerar_demo.py).
 *
 * Responde às mesmas chamadas /api/... do servidor de verdade. Nada sai do navegador e nada é
 * guardado: recarregar a página volta tudo ao começo. Os números são SIMULADOS. Não use esta
 * página como prova de medição.
 *
 * A história da casa: o medidor chegou há 21 dias. Há 7 dias a família pôs no plano de economia
 * banhos 2 minutos mais curtos e o corte de parte do consumo em espera; antes disso, os banhos
 * duravam 11 minutos e a casa deixava 96 W ligados de madrugada. É isso que o "Deu certo?" mostra.
 * As contas do plano usam economia-calculo.js, o mesmo arquivo do aplicativo.
 *
 * O medidor que a pessoa parear aqui (depois de assinar o plano de teste) mede o circuito da
 * cozinha: geladeira e micro-ondas. Serve para ver a casa com circuitos e o "para onde vai agora".
 */
"use strict";
(() => {
  window.MW_DEMONSTRACAO = true;          // app.js mostra a faixa e não mexe nas notificações de verdade
  const DIA = 86400;
  const agora = () => Date.now() / 1000;
  const iso = (t) => new Date(t * 1000).toISOString();
  const inicioDoDia = (t) => { const d = new Date(t * 1000); d.setHours(0, 0, 0, 0); return d.getTime() / 1000; };
  const diaLocal = (t) => { const d = new Date(t * 1000); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
  const ruido = (n) => { const x = Math.sin(n * 12.9898) * 43758.5453; return x - Math.floor(x); };   // "aleatório" que se repete

  /* ---------------- a casa inventada ---------------- */
  const APARELHOS = [
    { id: 1, nome: null, potencia: 5400, janelas: [[7 * 60, 7 * 60 + 8], [19 * 60 + 30, 19 * 60 + 40]] },                // chuveiro
    { id: 2, nome: null, potencia: 1300, janelas: [[12 * 60 + 15, 12 * 60 + 18], [20 * 60 + 5, 20 * 60 + 8]] },          // micro-ondas
    { id: 3, nome: null, potencia: 140, ciclo: [12, 18] },                                                                // geladeira: 12 min ligada, 18 desligada
    { id: 4, nome: null, potencia: 110, janelas: [[19 * 60, 23 * 60]] },                                                  // TV
  ];
  const FANTASMA_W = 74;
  const MEDIDOR_DESDE = agora() - 21 * DIA;
  const PLANO_DESDE = agora() - 7 * DIA;
  // Antes do plano: banhos de 11 minutos (2 a mais, 2 por dia, 5.400 W) e 22 W a mais em espera.
  const ANTES_DO_PLANO_KWH = (5400 * 2 * 2 / 60 + 22 * 24) / 1000;
  function ligado(ap, t) {
    const minuto = (t - inicioDoDia(t)) / 60;
    if (ap.ciclo) return (t / 60) % (ap.ciclo[0] + ap.ciclo[1]) < ap.ciclo[0];
    return ap.janelas.some(([a, b]) => minuto >= a && minuto < b);
  }
  function watts(t, comRuido = true) {
    const hora = (t - inicioDoDia(t)) / 3600;
    let w = FANTASMA_W;
    for (const ap of APARELHOS) if (ligado(ap, t)) w += ap.potencia;
    if (hora >= 18 && hora < 23.5) w += 95;                    // luzes
    if (hora >= 8 && hora < 18) w += 35;                       // coisas pequenas do dia
    return comRuido ? w * (1 + (ruido(Math.floor(t)) - 0.5) * 0.02) : w;
  }
  function wattsCozinha(t) {                                   // o circuito da cozinha: geladeira, micro-ondas e o relógio dele
    let w = 9;
    for (const ap of APARELHOS) if ((ap.id === 2 || ap.id === 3) && ligado(ap, t)) w += ap.potencia;
    return w;
  }
  const cacheDia = new Map();
  function whEntre(a, b, f = (t) => watts(t, false)) {       // soma minuto a minuto
    let wh = 0;
    for (let t = a; t < b; t += 60) wh += f(t) / 60;
    return wh;
  }
  function kwhDoDia(inicio) {
    if (!cacheDia.has(inicio)) {
      const extra = inicio + DIA <= inicioDoDia(PLANO_DESDE) ? ANTES_DO_PLANO_KWH : 0;
      cacheDia.set(inicio, whEntre(inicio, inicio + DIA) / 1000 * (0.9 + ruido(inicio / DIA) * 0.25) + extra);
    }
    return cacheDia.get(inicio);
  }
  // Dia medido por inteiro? O dia do pareamento (pela metade) e o de hoje não contam.
  const kwhMedido = (inicio) => (inicio > inicioDoDia(MEDIDOR_DESDE) && inicio < inicioDoDia(agora()) ? kwhDoDia(inicio) : 0);
  const kwhHoje = () => whEntre(inicioDoDia(agora()), agora()) / 1000;

  /* ---------------- o que a pessoa mexe na demonstração ---------------- */
  const E = {
    dentro: true, tarifa: 0.92, meta: 180, plano: "gratis", assinatura: null, avisosLidos: false,
    medidores: [{ id: "mw-DEMO0001", apelido: "Casa", pareado: MEDIDOR_DESDE }],
    aceites: { termos: true, privacidade: true, consumo: true, historico_longo: false, aparelhos: true, novidades: false },
    porEmail: { medidor_offline: true, meta: true, consumo_incomum: true, resumo_semanal: true },
    porPush: { medidor_offline: true, meta: true, consumo_incomum: true, resumo_semanal: true },
    pareando: null, ocultos: new Set(),
    atividade: [["Entrada na conta", 0.02], ["Tarifa ou meta alteradas", 2.1],
      ["Plano de economia alterado", 7, "Cortar o consumo em espera: no plano"], ["Plano de economia alterado", 7.001, "Banho mais curto: no plano"],
      ["Autorização alterada", 20.5, "Detetive de aparelhos: autorizado"], ["Medidor pareado (mw-DEMO0001)", 21], ["E-mail confirmado", 21.01], ["Conta criada", 21.02]],
    // Plano de economia: começa com o que a família pôs há 7 dias.
    economia: [
      { id: 1, tipo: "banho_curto", chave: "banho_curto", nome: null, aparelho_id: null, criada: PLANO_DESDE,
        parametros: { minutos_a_menos: 2, potencia_w: 5400, banhos_semana: 14, minutos_banho: 11 } },
      { id: 2, tipo: "fantasma", chave: "fantasma", nome: null, aparelho_id: null, criada: PLANO_DESDE + 60,
        parametros: { watts_cortados: 30, horas_dia: 20 } },
    ],
    ecoId: 2,
    ligacao: "bifasica",
    // Tarifa Branca: ponta das 18h às 21h e preços de EXEMPLO (na casa inventada, tudo é simulado).
    tb: { inicio: 18 * 60, precos: { convencional: 0.90, fora: 0.75, intermediario: 1.10, ponta: 1.73 } },
  };
  const FINALIDADES = {
    termos: ["Termos de uso", "Aceite das regras de uso do serviço.", true, false],
    privacidade: ["Política de privacidade", "Ciência de como os seus dados são tratados.", true, false],
    consumo: ["Dados de consumo de energia", "Guardar as leituras dos seus medidores para mostrar o painel, o histórico e os alertas. Essas leituras revelam a rotina da casa. Sem esta autorização, nenhum medidor é pareado e nenhuma leitura nova é guardada.", true, true],
    historico_longo: ["Histórico de até 2 anos", "Guardar o consumo de 5 em 5 minutos por até 2 anos, em vez de 90 dias, para comparar meses e estações do ano.", false, true],
    aparelhos: ["Detetive de aparelhos", "Analisar as leituras para descobrir quais aparelhos ligam e desligam, quando e quanto gastam. Isso revela mais da rotina da casa do que o consumo total. Ao desligar, tudo o que o Detetive guardou é apagado.", false, true],
    novidades: ["Novidades por e-mail", "Receber notícias sobre o MeterWatt. O seu e-mail nunca é vendido nem repassado.", false, true],
  };
  const TIPOS_DE_AVISO = {
    medidor_offline: ["Medidor sem sinal", "Quando um medidor fica mais de 30 minutos sem enviar leituras."],
    meta: ["Meta do mês", "Quando a previsão da conta passa de 90% da meta e quando passa da meta."],
    consumo_incomum: ["Consumo fora do normal", "Quando uma hora gasta mais que o dobro do normal da casa para aquele horário."],
    resumo_semanal: ["Resumo da semana", "Toda segunda-feira: quanto a casa gastou, a comparação com a semana anterior e o consumo fantasma."],
  };
  const RETENCAO = { leituras_detalhadas_horas: 48, consumo_5_minutos_dias: 90, consumo_5_minutos_dias_com_historico_longo: 730,
    atividade_da_conta_dias: 180, conta_nao_confirmada_dias: 7, backup_copias: 14 };
  const NAO_NA_DEMO = [400, { erro: "Isto não funciona na demonstração: precisa do servidor de verdade.", codigo: "demonstracao" }];
  const reais = (v) => v.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  const num = (v, c = 1) => v.toLocaleString("pt-BR", { minimumFractionDigits: c, maximumFractionDigits: c });

  /* ---------------- respostas ---------------- */
  function listaDeMedidores() {
    const t = agora();
    if (E.pareando && t > E.pareando) {                       // o "medidor novo" aparece alguns segundos depois do código
      E.medidores.push({ id: "mw-DEMO0002", apelido: "Medidor 0002", pareado: t, cozinha: true, papel: "casa", dentro: null });
      E.pareando = null;
    }
    const principal = E.aceites.consumo;
    return { tarifa_kwh: E.tarifa, limite_offline_s: 20, servidor_em: iso(t), medidores: E.medidores.map((m) => {
      const vivo = principal && !m.parado;
      const w = vivo ? Math.round((m.cozinha ? wattsCozinha(t) : watts(t)) * 10) / 10 : null;
      const v = 127 + Math.sin(t / 900) * 1.5;
      const kwh = m.parado ? 0 : m.cozinha ? whEntre(Math.max(m.pareado, inicioDoDia(t)), t, wattsCozinha) / 1000 : kwhHoje();
      return { id: m.id, apelido: m.apelido, tipo: m.parado ? null : "pzem", fw: "3.3.0", pareado_em: iso(m.pareado), online: vivo,
        papel: m.papel || "casa", dentro_de: m.dentro || null,
        segundos_desde_mensagem: vivo ? 0.6 : (m.parado ? null : 120), sensor_ok: vivo ? true : null, erro_sensor: null, simulado: true,
        unidade: "W", energia_estimada: false, tensao_v: vivo ? Math.round(v * 10) / 10 : null,
        corrente_a: vivo ? Math.round(w / v / 0.94 * 1000) / 1000 : null, potencia: w, fp: vivo ? 0.94 : null, frequencia_hz: vivo ? 60 : null,
        rssi: -58, kwh_hoje: Math.round(kwh * 10000) / 10000, custo_hoje: E.tarifa == null ? null : Math.round(kwh * E.tarifa * 100) / 100,
        custo_por_hora: vivo && E.tarifa != null ? Math.round(w / 1000 * E.tarifa * 100) / 100 : null };
    }) };
  }
  function historico(id, periodo) {
    const t = agora(), passo = { "2h": 10, "24h": 300, "7d": 1800 }[periodo] || 10, janela = { "2h": 7200, "24h": DIA, "7d": 7 * DIA }[periodo] || 7200;
    const pontos = [];
    const m = E.medidores.find((x) => x.id === id);
    if (m && !m.parado) {
      const f = m.cozinha ? wattsCozinha : (x, ruidoso) => watts(x, ruidoso);
      for (let x = Math.max(t - janela, m.pareado); x <= t; x += passo) {
        let w = f(x, periodo === "2h");
        if (passo > 60) { w = 0; for (let k = 0; k < passo; k += 60) w += f(x + k, false); w /= passo / 60; }   // média do intervalo
        pontos.push([Math.round(x + (passo > 60 ? passo : 0)), Math.round(w * 10) / 10]);
      }
    }
    return { id, periodo, unidade: "W", agora: t, pontos };
  }
  function dias(n) {
    const hoje = inicioDoDia(agora()), saida = [];
    for (let i = n - 1; i >= 0; i--) {
      const inicio = inicioDoDia(hoje - i * DIA + 3600);
      const kwh = i === 0 ? kwhHoje() : kwhMedido(inicio);
      saida.push({ dia: diaLocal(inicio), kwh: Math.round(kwh * 1000) / 1000, custo: E.tarifa == null ? null : Math.round(kwh * E.tarifa * 100) / 100 });
    }
    return { dias: saida, tarifa_kwh: E.tarifa };
  }
  function fantasma() {
    const kwhMes = FANTASMA_W * 24 * 365 / 12 / 1000;
    const semana = dias(8).dias.slice(0, 7).reduce((s, d) => s + d.kwh, 0);
    return { disponivel: true, watts: FANTASMA_W, kwh_mes: Math.round(kwhMes * 10) / 10, custo_mes: E.tarifa == null ? null : Math.round(kwhMes * E.tarifa * 100) / 100,
      custo_ano: E.tarifa == null ? null : Math.round(kwhMes * 12 * E.tarifa * 100) / 100,
      fatia_do_consumo: Math.round(Math.min(1, FANTASMA_W * 24 * 7 / 1000 / semana) * 1000) / 1000, noites: 7, estimado: false, medidores: [] };
  }
  function meta() {
    if (E.tarifa == null) return { meta: E.meta, sem_tarifa: true, status: null };
    const t = agora(), d = new Date(t * 1000), lista = dias(14).dias;
    const mes = diaLocal(t).slice(0, 7);
    const gasto = lista.filter((x) => x.dia.startsWith(mes)).reduce((s, x) => s + x.kwh, 0) * E.tarifa;
    const media = lista.slice(-8, -1).reduce((s, x) => s + x.kwh, 0) / 7 * E.tarifa;
    const fimDoMes = new Date(d.getFullYear(), d.getMonth() + 1, 1).getTime() / 1000;
    const restam = (fimDoMes - t) / DIA;
    const conta = contaDoMes((gasto + media * restam) / E.tarifa);
    const projecao = conta.cobrado * E.tarifa + conta.adicional;
    const status = !E.meta ? null : projecao > E.meta ? "acima" : projecao > 0.9 * E.meta ? "atencao" : "ok";
    return { meta: E.meta, gasto_ate_agora: Math.round(gasto * 100) / 100, media_diaria: Math.round(media * 100) / 100, projecao: Math.round(projecao * 100) / 100,
      dias_restantes: Math.round(restam * 10) / 10, percentual: E.meta ? projecao / E.meta : null, status,
      folga_diaria: E.meta && restam >= 1 ? Math.round((E.meta - gasto) / restam * 100) / 100 : null, base_de_dias: 7, tarifa_kwh: E.tarifa, sem_tarifa: false,
      ...conta.detalhes };
  }
  /* ---------------- a conta de verdade: bandeira, mínimo e Tarifa Branca ---------------- */
  // Bandeiras de 2026 anunciadas pela ANEEL (amarela de maio a setembro, verde em outubro e novembro).
  // Os outros meses aparecem como "não informada", como no servidor antes de alguém registrar.
  const BANDEIRAS_2026 = { "2026-05": "amarela", "2026-06": "amarela", "2026-07": "amarela", "2026-08": "amarela", "2026-09": "amarela",
    "2026-10": "verde", "2026-11": "verde" };
  const VALOR_BANDEIRA = { verde: 0, amarela: 1.885, vermelha1: 4.463, vermelha2: 7.877 };
  const NOME_BANDEIRA = { verde: "verde", amarela: "amarela", vermelha1: "vermelha patamar 1", vermelha2: "vermelha patamar 2" };
  const MINIMO = { monofasica: 30, bifasica: 50, trifasica: 100 };
  function contaDoMes(kwhMes) {
    const mes = diaLocal(agora()).slice(0, 7), cor = BANDEIRAS_2026[mes] || null;
    const minimo = MINIMO[E.ligacao] || null;
    const cobrado = minimo ? Math.max(kwhMes, minimo) : kwhMes;
    const adicional = cobrado * (VALOR_BANDEIRA[cor] || 0) / 100;
    return { cobrado, adicional, detalhes: { kwh_previsto: r1(kwhMes),
      bandeira: { mes, cor, nome: NOME_BANDEIRA[cor] || null, informada: !!cor, reais_por_100_kwh: VALOR_BANDEIRA[cor] || 0, adicional: Math.round(adicional * 100) / 100 },
      minimo: minimo ? { kwh: minimo, aplicado: kwhMes < minimo } : null } };
  }

  // Feriados sem ponta (REN ANEEL 1.000/2021): os mesmos de tarifas.py.
  function pascoa(ano) {
    const a = ano % 19, b = Math.floor(ano / 100), c = ano % 100, d = Math.floor(b / 4), e = b % 4, f = Math.floor((b + 8) / 25);
    const g = Math.floor((b - f + 1) / 3), h = (19 * a + b - d - g + 15) % 30, i = Math.floor(c / 4), k = c % 4;
    const l = (32 + 2 * e + 2 * i - h - k) % 7, m = Math.floor((a + 11 * h + 22 * l) / 451);
    const mes = Math.floor((h + l - 7 * m + 114) / 31), dia = ((h + l - 7 * m + 114) % 31) + 1;
    return new Date(ano, mes - 1, dia);
  }
  function temPonta(d0) {
    const d = new Date(d0 * 1000), ano = d.getFullYear();
    if (d.getDay() === 0 || d.getDay() === 6) return false;
    const md = `${d.getMonth() + 1}-${d.getDate()}`;
    if (["1-1", "4-21", "5-1", "9-7", "10-12", "11-2", "11-15", "12-25"].includes(md)) return false;
    const p = pascoa(ano).getTime();
    return ![-47, -2, 60].some((n) => Math.abs(new Date(p + n * DIA * 1000).setHours(0, 0, 0, 0) - d.getTime()) < 3 * 3600 * 1000);
  }
  function posto(minuto, inicio) {
    if (minuto >= inicio && minuto < inicio + 180) return "ponta";
    if ((minuto >= inicio - 60 && minuto < inicio) || (minuto >= inicio + 180 && minuto < inicio + 240)) return "intermediario";
    return "fora";
  }
  const hhmm = (m) => `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  const cacheMeia = new Map();
  function kwhDaMeiaHora(d0, i) {                       // com o mesmo ajuste do dia de kwhDaHora
    const chave = `${d0}:${i}`;
    if (!cacheMeia.has(chave)) {
      const inicio = d0 + i * 1800;
      let kwh = whEntre(inicio, inicio + 1800) / 1000 * (0.9 + ruido(d0 / DIA) * 0.25);
      if (d0 + DIA <= inicioDoDia(PLANO_DESDE)) kwh += 0.011 + (i === 14 || i === 39 ? 5400 * 2 / 60 / 1000 : 0);
      cacheMeia.set(chave, kwh);
    }
    return cacheMeia.get(chave);
  }
  function tarifaBranca() {
    const inicio = E.tb.inicio ?? 18 * 60, exemplo = E.tb.inicio == null;
    const postos = Array.from({ length: 48 }, (_, i) => posto(i * 30, inicio));
    const cab = { ponta: { inicio: hhmm(inicio), fim: hhmm(inicio + 180), exemplo },
      intermediario: [{ inicio: hhmm(inicio - 60), fim: hhmm(inicio) }, { inicio: hhmm(inicio + 180), fim: hhmm(inicio + 240) }],
      postos, precos: E.tb.precos, tarifa_kwh: E.tarifa };
    if (!E.aceites.consumo) return { ...cab, disponivel: false, motivo: "Nenhum medidor pareado.", dias_medidos: 0 };
    const hoje0 = inicioDoDia(agora()), uteis = [], outros = [];
    for (let n = 1; n <= 28; n++) {
      const d0 = inicioDoDia(hoje0 - n * DIA + 3600);
      if (d0 <= inicioDoDia(MEDIDOR_DESDE)) continue;          // o dia do pareamento (pela metade) não conta
      (temPonta(d0) ? uteis : outros).push(d0);
    }
    const media = (lista, i) => lista.reduce((s, d0) => s + kwhDaMeiaHora(d0, i), 0) / lista.length;
    const perfilUtil = Array.from({ length: 48 }, (_, i) => media(uteis, i));
    const perfilOutros = outros.length ? Array.from({ length: 48 }, (_, i) => media(outros, i)) : perfilUtil;
    // Dias com e sem ponta num mês médio dos próximos 12 meses.
    let n = 0;
    for (let k = 0; k < 365; k++) if (temPonta(inicioDoDia(hoje0 + k * DIA + 3600))) n++;
    const diasUteis = n / 12, outrosDias = (365 - n) / 12;
    const kwh = { fora: 0, intermediario: 0, ponta: 0 };
    perfilUtil.forEach((v, i) => { kwh[postos[i]] += v * diasUteis; });
    kwh.fora += perfilOutros.reduce((s, v) => s + v, 0) * outrosDias;
    const total = kwh.fora + kwh.intermediario + kwh.ponta;
    const r = { ...cab, disponivel: true, dias_medidos: uteis.length + outros.length, dias: { uteis: uteis.length, outros: outros.length },
      perfil: { dia_util: perfilUtil.map((v) => r2(v, 4)), fim_de_semana: outros.length ? perfilOutros.map((v) => r2(v, 4)) : null },
      mes: { dias_uteis: r1(diasUteis), outros_dias: r1(outrosDias), kwh: { fora: r1(kwh.fora), intermediario: r1(kwh.intermediario), ponta: r1(kwh.ponta), total: r1(total) },
        percentual: Object.fromEntries(Object.entries(kwh).map(([k, v]) => [k, Math.round(v / total * 1000) / 1000])) },
      custo: null, empate: null, fator: null, minimo: MINIMO[E.ligacao] ? { kwh: MINIMO[E.ligacao], abaixo: total < MINIMO[E.ligacao] } : null,
      aparelhos_na_ponta: [] };
    if (E.aceites.aparelhos) {
      r.aparelhos_na_ponta = APARELHOS.filter((a) => a.janelas && !E.ocultos.has(a.id)).map((a) => {
        const minutos = a.janelas.reduce((s, [x, y]) => s + Math.max(0, Math.min(y, inicio + 180) - Math.max(x, inicio)), 0);
        return { id: a.id, nome: nomeAparelho(a), palpite: a.nome ? null : PALPITES[a.id], kwh_mes: r1(a.potencia * minutos / 60 / 1000 * diasUteis) };
      }).filter((a) => a.kwh_mes >= 1).sort((a, b) => b.kwh_mes - a.kwh_mes).slice(0, 3);
    }
    const p = E.tb.precos;
    if (p) {
      const fator = E.tarifa ? E.tarifa / p.convencional : 1;
      const postosReais = Object.fromEntries(Object.entries(kwh).map(([k, v]) => [k, v * p[k] * fator]));
      const convencional = total * p.convencional * fator, branca = postosReais.fora + postosReais.intermediario + postosReais.ponta;
      r.fator = Math.round(fator * 1000) / 1000;
      r.custo = { convencional: r2(convencional), branca: r2(branca), diferenca: r2(branca - convencional),
        postos: Object.fromEntries(Object.entries(postosReais).map(([k, v]) => [k, r2(v)])) };
      if (branca > convencional) {
        const passar = (branca - convencional) / (fator * (p.ponta - p.fora));
        r.empate = { kwh_mes: r1(passar), kwh_por_dia_util: r2(passar / diasUteis), cabe_na_ponta: passar <= kwh.ponta };
      }
    }
    return r;
  }
  function salvarTarifaBranca(corpo) {
    const erro = (texto) => [400, { erro: texto, codigo: null }];
    let inicio = corpo.ponta_inicio ?? null;
    if (inicio != null) {
      const m = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(inicio);
      inicio = m ? Number(m[1]) * 60 + Number(m[2]) : null;
      if (inicio == null || inicio % 30 || inicio < 15 * 60 || inicio > 20 * 60) return erro("O começo da ponta vai de 15:00 a 20:00, de meia em meia hora.");
    }
    const nomes = { convencional: "convencional", fora: "fora de ponta", intermediario: "intermediário", ponta: "ponta" };
    const precos = {};
    for (const [k, nome] of Object.entries(nomes)) {
      const v = corpo[k];
      if (v == null) continue;
      if (typeof v !== "number" || !(v > 0 && v <= 10)) return erro(`O preço ${nome} precisa ser um número maior que 0 e até 10 (R$ por kWh).`);
      precos[k] = v;
    }
    const n = Object.keys(precos).length;
    if (n && n < 4) return erro("Preencha os quatro preços da mesma tabela, ou deixe os quatro em branco.");
    if (n && precos.ponta <= precos.fora) return erro("Na Tarifa Branca, a ponta é mais cara que fora de ponta. Confira se os preços não trocaram de lugar.");
    E.tb = { inicio, precos: n ? precos : null };
    E.atividade.unshift(["Horário e preços da Tarifa Branca alterados", 0]);
    return [200, tarifaBranca()];
  }

  /* ---------------- perfil do dia e para onde vai agora ---------------- */
  const r3 = (v) => (v == null ? null : Math.round(v * 1000) / 1000);
  const r1 = (v) => Math.round(v * 10) / 10;
  const cacheHora = new Map();
  // Uma hora de um dia já fechado, com o mesmo ajuste do dia: a soma das horas bate com "Últimos 14 dias".
  // Antes do plano de economia, os banhos eram 2 minutos mais longos (7h e 19h) e a casa deixava 22 W a mais.
  function kwhDaHora(d0, h) {
    const chave = `${d0}:${h}`;
    if (!cacheHora.has(chave)) {
      const inicio = d0 + h * 3600;
      let kwh = whEntre(inicio, inicio + 3600) / 1000 * (0.9 + ruido(d0 / DIA) * 0.25);
      if (d0 + DIA <= inicioDoDia(PLANO_DESDE)) kwh += 0.022 + (h === 7 || h === 19 ? 5400 * 2 / 60 / 1000 : 0);
      cacheHora.set(chave, kwh);
    }
    return cacheHora.get(chave);
  }
  const mediana = (v) => { const o = [...v].sort((a, b) => a - b), m = Math.floor(o.length / 2); return o.length % 2 ? o[m] : (o[m - 1] + o[m]) / 2; };
  const diaDaSemana = (d0) => (new Date(d0 * 1000).getDay() + 6) % 7;       // 0 = segunda, como no servidor
  let faixaGuardada = null;
  function horas(n) {
    const t = agora(), hoje0 = inicioDoDia(t), horaAtual = new Date(t * 1000).getHours();
    const medido = (d0) => d0 > inicioDoDia(MEDIDOR_DESDE);
    if (!E.aceites.consumo) n = Math.max(1, n);
    const mapa = [];
    for (let i = n - 1; i >= 0; i--) {
      const d0 = inicioDoDia(hoje0 - i * DIA + 3600);
      const lista = [];
      for (let h = 0; h < 24; h++) {
        if (i === 0) lista.push(h > horaAtual ? null : r3(whEntre(d0 + h * 3600, Math.min(t, d0 + (h + 1) * 3600)) / 1000));
        else lista.push(medido(d0) ? r3(kwhDaHora(d0, h)) : null);
      }
      mapa.push({ dia: diaLocal(d0), dia_da_semana: diaDaSemana(d0), horas: lista, kwh: r3(i === 0 ? kwhHoje() : kwhMedido(d0)) });
    }
    const util = diaDaSemana(hoje0) < 5;
    const dias = [];
    for (let i = 1; i <= 28; i++) { const d0 = inicioDoDia(hoje0 - i * DIA + 3600); if (medido(d0) && (diaDaSemana(d0) < 5) === util) dias.push(d0); }
    const normalHoras = Array.from({ length: 24 }, (_, h) => (dias.length >= 2 ? r3(mediana(dias.map((d0) => kwhDaHora(d0, h)))) : null));
    const normal = dias.length >= 2 ? { tipo: util ? "util" : "fim_de_semana", dias: dias.length, horas: normalHoras } : null;
    let pico = null, comparacao = null;
    if (normal) {
      const h = normalHoras.indexOf(Math.max(...normalHoras));
      const porMes = 365 / 12 * (util ? 5 / 7 : 2 / 7);
      pico = { hora: h, kwh: normalHoras[h], custo: E.tarifa == null ? null : Math.round(normalHoras[h] * E.tarifa * 100) / 100,
        custo_mes: E.tarifa == null ? null : Math.round(normalHoras[h] * E.tarifa * porMes * 100) / 100 };
      if (horaAtual >= 3) {
        const hoje = mapa[mapa.length - 1].horas.slice(0, horaAtual);
        comparacao = { horas: horaAtual, hoje_kwh: r3(hoje.reduce((a, b) => a + b, 0)), normal_kwh: r3(normalHoras.slice(0, horaAtual).reduce((a, b) => a + b, 0)) };
      }
    }
    if (!faixaGuardada) {
      const amostras = [];
      for (let x = t - 14 * DIA; x < t; x += 300) amostras.push(watts(x, false));
      amostras.sort((a, b) => a - b);
      faixaGuardada = { base_w: r1(amostras[Math.floor(0.05 * (amostras.length - 1))]), alto_w: r1(amostras[Math.floor(0.95 * (amostras.length - 1))]) };
    }
    return { tarifa_kwh: E.tarifa, estimado: false, hora_atual: horaAtual, hoje: { dia: diaLocal(hoje0), horas: mapa[mapa.length - 1].horas },
      normal, mapa, pico, comparacao, faixa: faixaGuardada };
  }
  function partesAgora() {
    const t = agora();
    const vivos = E.aceites.consumo ? E.medidores.filter((m) => !m.parado) : [];
    const entradas = vivos.filter((m) => (m.papel || "casa") === "casa"), circuitos = vivos.filter((m) => m.papel === "circuito");
    if (!entradas.length) {
      return { disponivel: false, partes: [], motivo: E.medidores.length ? "Nenhum medidor da entrada está enviando leituras agora." : "Nenhum medidor pareado." };
    }
    const wDe = (m) => (m.cozinha ? wattsCozinha(t) : watts(t));
    const kwhDe = (m) => (m.cozinha ? whEntre(Math.max(m.pareado, inicioDoDia(t)), t, wattsCozinha) / 1000 : kwhHoje());
    const total = entradas.reduce((x, m) => x + wDe(m), 0);
    let partes, fonte;
    if (circuitos.length) {
      fonte = "circuitos";
      partes = circuitos.map((c) => ({ id: c.id, nome: c.apelido, tipo: "circuito", watts: r1(wDe(c)), kwh_hoje: r3(kwhDe(c)), dentro_de: c.dentro }));
      const soma = partes.reduce((x, p) => x + p.watts, 0);
      partes.push({ nome: "Resto da casa", tipo: "resto", watts: r1(Math.max(0, total - soma)),
        kwh_hoje: r3(Math.max(0, entradas.reduce((x, m) => x + kwhDe(m), 0) - partes.reduce((x, p) => x + p.kwh_hoje, 0))) });
    } else if (entradas.length > 1) {
      fonte = "imoveis";
      partes = entradas.map((m) => ({ id: m.id, nome: m.apelido, tipo: "entrada", watts: r1(wDe(m)), kwh_hoje: r3(kwhDe(m)) }));
    } else {
      fonte = E.aceites.aparelhos ? "detetive" : "base";
      partes = !E.aceites.aparelhos ? [] : APARELHOS.filter((a) => !E.ocultos.has(a.id) && ligado(a, t)).map((a) => {
        let desde = t;
        while (desde > t - DIA && ligado(a, desde - 60)) desde -= 60;
        return { id: a.id, tipo: "aparelho", nome: a.nome || `Aparelho de ${a.potencia.toLocaleString("pt-BR")} W`, palpite: a.nome ? null : PALPITES[a.id],
          watts: a.potencia, desde: iso(desde) };
      }).sort((a, b) => b.watts - a.watts);
      const usado = partes.reduce((x, p) => x + p.watts, 0);
      const base = Math.min(FANTASMA_W, Math.max(0, total - usado));
      partes.push({ nome: "Sempre ligado", tipo: "base", watts: r1(base) }, { nome: "Outros", tipo: "resto", watts: r1(Math.max(0, total - usado - base)) });
    }
    const custo = (w) => (E.tarifa == null || w == null ? null : r3(w / 1000 * E.tarifa));
    partes.forEach((p) => { p.custo_por_hora = custo(p.watts); });
    return { disponivel: true, fonte, total: r1(total), unidade: "W", custo_por_hora: custo(total), partes, aviso: null, em: iso(t) };
  }

  function avisos() {
    const t = agora(), m = meta(), f = fantasma(), semana = dias(8).dias.slice(0, 7).reduce((s, d) => s + d.kwh, 0);
    const lista = [
      { id: 3, tipo: "consumo_incomum", quando: iso(t - 0.8 * DIA), titulo: "Consumo fora do normal",
        texto: "Entre 19h e 20h a casa gastou 1,42 kWh. O normal para esse horário, nos últimos dias, é perto de 0,58 kWh. Vale conferir se algum aparelho ficou ligado sem querer." },
      { id: 2, tipo: "resumo_semanal", quando: iso(t - 1.6 * DIA), titulo: "Resumo da semana",
        texto: `Na última semana a casa gastou ${num(semana)} kWh, cerca de ${reais(semana * (E.tarifa || 0))}. Consumo fantasma: ${FANTASMA_W} W sempre ligados (${reais(f.custo_mes || 0)} por mês).`
          + (m.meta && !m.sem_tarifa ? ` Previsão da conta do mês: ${reais(m.projecao)}, para uma meta de ${reais(m.meta)}.` : "") },
      { id: 1, tipo: "medidor_offline", quando: iso(t - 4.2 * DIA), titulo: "Casa está sem sinal",
        texto: "O medidor Casa não envia leituras há mais de 30 minutos. Confira se ele está ligado e se o Wi-Fi da casa está funcionando. Enquanto isso, o consumo não está sendo registrado." },
    ].map((a) => ({ ...a, lido: E.avisosLidos }));
    return { avisos: lista, config: Object.entries(TIPOS_DE_AVISO).map(([tipo, [nome, descricao]]) => ({ tipo, nome, descricao, por_email: E.porEmail[tipo], por_push: E.porPush[tipo] })) };
  }
  const PALPITES = { 1: "chuveiro elétrico ou torneira elétrica", 2: "micro-ondas, air fryer ou ferro de passar", 3: "geladeira, freezer ou ventilador forte", 4: "geladeira, freezer ou ventilador forte" };
  function aparelhos() {
    const t = agora();
    if (!E.aceites.aparelhos) return { autorizado: false, aparelhos: [], em_estudo: 0, tem_medidor_compativel: true, ciclos_para_aparecer: 3 };
    const lista = APARELHOS.filter((a) => !E.ocultos.has(a.id)).map((a) => {
      const minutosPorDia = a.ciclo ? 1440 * a.ciclo[0] / (a.ciclo[0] + a.ciclo[1]) : a.janelas.reduce((s, [x, y]) => s + y - x, 0);
      const vezes = a.ciclo ? Math.round(7 * 1440 / (a.ciclo[0] + a.ciclo[1])) : a.janelas.length * 7;
      const kwhMes = a.potencia * minutosPorDia / 60 / 1000 * 365 / 12;
      let ultima = t;
      if (!ligado(a, t)) for (let x = t; x > t - DIA; x -= 60) if (ligado(a, x)) { ultima = x; break; }
      return { id: a.id, nome: a.nome, palpite: PALPITES[a.id], medidor: "Casa", potencia_w: a.potencia, ciclos_total: vezes, vezes_na_semana: vezes,
        duracao_media_min: Math.round(minutosPorDia / (vezes / 7) * 10) / 10, kwh_mes: Math.round(kwhMes * 10) / 10,
        custo_mes: E.tarifa == null ? null : Math.round(kwhMes * E.tarifa * 100) / 100, ultima_vez: iso(ultima), ligado_agora: ligado(a, t) };
    });
    return { autorizado: true, aparelhos: lista, em_estudo: 1, tem_medidor_compativel: true, ciclos_para_aparecer: 3 };
  }

  /* ---------------- E se eu…? ---------------- */
  const TITULOS_ECO = { banho_curto: "Banho mais curto", chuveiro_verao: "Chuveiro na posição verão", fantasma: "Cortar o consumo em espera",
    geladeira: "Trocar a geladeira", usar_menos: "Usar menos" };
  const r2 = (v, c = 2) => (v == null ? null : Math.round(v * 10 ** c) / 10 ** c);
  const nomeAparelho = (a) => a.nome || `Aparelho de ${a.potencia.toLocaleString("pt-BR")} W`;
  const minutosPorDia = (a) => (a.ciclo ? 1440 * a.ciclo[0] / (a.ciclo[0] + a.ciclo[1]) : a.janelas.reduce((s, [x, y]) => s + y - x, 0));

  function baseEconomia() {
    const saida = { tarifa_kwh: E.tarifa, consumo_kwh_dia: null, detetive_ligado: false, medidores: E.medidores.length, consumo_autorizado: E.aceites.consumo,
      chuveiro: { potencia_w: 5500, banhos_semana: 14, minutos_banho: 10, fonte: "tipico", aparelho_id: null },
      fantasma: { watts: 40, fonte: "tipico" }, geladeira: { kwh_mes: 50, potencia_w: null, fonte: "tipico", aparelho_id: null }, aparelhos: [] };
    if (!E.aceites.consumo) return saida;
    const semana = dias(8).dias.slice(0, 7).filter((d) => d.kwh > 0);
    if (semana.length) saida.consumo_kwh_dia = r2(semana.reduce((s, d) => s + d.kwh, 0) / semana.length, 3);
    saida.fantasma = { watts: FANTASMA_W, fonte: "medido" };
    saida.detetive_ligado = E.aceites.aparelhos;
    if (!E.aceites.aparelhos) return saida;
    const visiveis = APARELHOS.filter((a) => !E.ocultos.has(a.id));
    const chuveiro = visiveis.find((a) => /chuveiro/i.test(a.nome || "")) || visiveis.find((a) => a.potencia >= 2500);
    if (chuveiro) saida.chuveiro = { potencia_w: chuveiro.potencia, banhos_semana: chuveiro.janelas.length * 7,
      minutos_banho: r2(minutosPorDia(chuveiro) / chuveiro.janelas.length, 1), fonte: "detetive", aparelho_id: chuveiro.id };
    const geladeira = visiveis.find((a) => /geladeira|freezer/i.test(a.nome || "")) || visiveis.find((a) => a.ciclo && a.potencia >= 60 && a.potencia < 400);
    if (geladeira) saida.geladeira = { kwh_mes: r2(geladeira.potencia * minutosPorDia(geladeira) / 60 / 1000 * 365 / 12, 1), potencia_w: geladeira.potencia,
      fonte: "detetive", aparelho_id: geladeira.id };
    saida.aparelhos = visiveis.filter((a) => a !== chuveiro && a !== geladeira && a.potencia < 2500).map((a) => ({ id: a.id, nome: a.nome,
      palpite: PALPITES[a.id], potencia_w: a.potencia, rotulo: nomeAparelho(a), minutos_dia: r2(minutosPorDia(a), 1) }));
    return saida;
  }

  function planoEconomia() {
    return E.economia.map((a) => ({ id: a.id, tipo: a.tipo, chave: a.chave, titulo: TITULOS_ECO[a.tipo], nome: a.nome, aparelho_id: a.aparelho_id,
      parametros: a.parametros, kwh_mes: r2(MWEconomia.kwhMes(a.tipo, a.parametros), 3), desde: iso(a.criada) }));
  }

  function totaisEconomia() {
    const kwh = MWEconomia.totalKwhMes(E.economia);
    return { kwh_mes: r2(kwh), reais_mes: E.tarifa == null ? null : r2(kwh * E.tarifa), reais_ano: E.tarifa == null ? null : r2(kwh * 12 * E.tarifa) };
  }

  // Dias medidos por inteiro antes (14 dias) e depois do dia em que a ação entrou no plano.
  function janelas(inicio) {
    const dia0 = inicioDoDia(inicio), antes = [], depois = [];
    for (let d = inicioDoDia(dia0 - 14 * DIA + 3600); d < dia0; d = inicioDoDia(d + DIA + 3600)) if (kwhMedido(d) > 0) antes.push(kwhMedido(d));
    for (let d = inicioDoDia(dia0 + DIA + 3600); d < inicioDoDia(agora()); d = inicioDoDia(d + DIA + 3600)) if (kwhMedido(d) > 0) depois.push(kwhMedido(d));
    return [antes, depois];
  }

  function verificacaoEconomia() {
    if (!E.economia.length || !E.aceites.consumo) return null;
    const inicio = Math.min(...E.economia.map((a) => a.criada));
    const [antes, depois] = janelas(inicio);
    const pronto = antes.length >= 5 && depois.length >= 5;
    const saida = { desde: iso(inicio), pronto, dias_antes: antes.length, dias_depois: depois.length, minimo: 5, total: null, itens: [] };
    if (!pronto) return saida;
    const media = (l) => l.reduce((s, x) => s + x, 0) / l.length;
    const a = media(antes), d = media(depois);
    saida.total = { antes_kwh_dia: r2(a), depois_kwh_dia: r2(d), variacao: r2((d - a) / a, 3),
      reais_mes: E.tarifa == null ? null : r2((a - d) * 365 / 12 * E.tarifa) };
    const pronta = (acoes) => acoes.length && janelas(Math.min(...acoes.map((x) => x.criada))).every((l) => l.length >= 5);
    // Só as ações da história da casa têm medição antes e depois; as postas hoje ainda não.
    if (E.aceites.aparelhos && pronta(E.economia.filter((x) => x.tipo === "banho_curto" || x.tipo === "chuveiro_verao"))) {
      saida.itens.push({ rotulo: "Chuveiro (verão e inverno)", unidade: "kWh por dia", antes: r2(5400 * 22 / 60 / 1000), depois: r2(5400 * 18 / 60 / 1000) });
    }
    if (pronta(E.economia.filter((x) => x.tipo === "fantasma"))) saida.itens.push({ rotulo: "Consumo em espera", unidade: "W", antes: FANTASMA_W + 22, depois: FANTASMA_W });
    return saida;
  }

  function estadoEconomia() {
    return { ...baseEconomia(), plano: planoEconomia(), plano_total: totaisEconomia(), verificacao: verificacaoEconomia(), maximo_no_plano: 12 };
  }

  function salvarEconomia(corpo) {
    const tipo = corpo.tipo;
    const v = MWEconomia.validar(tipo, corpo.parametros);
    if (!v.ok) return [400, { erro: v.erro, codigo: "parametro_invalido" }];
    if (!(MWEconomia.kwhMes(tipo, v.p) > 0)) return [400, { erro: "Com esses valores não há economia. Ajuste antes de pôr no plano.", codigo: "sem_economia" }];
    let chave = tipo, nome = null, aparelho = null;
    if ((tipo === "usar_menos" || tipo === "geladeira") && corpo.aparelho_id != null) {
      aparelho = APARELHOS.find((a) => a.id === corpo.aparelho_id);
      if (!aparelho) return [404, { erro: "Aparelho não encontrado.", codigo: null }];
    }
    if (tipo === "usar_menos") {
      if (aparelho) { chave = `usar_menos:${aparelho.id}`; nome = nomeAparelho(aparelho); }
      else {
        nome = String(corpo.nome || "").normalize("NFC").split(/\s+/).filter(Boolean).join(" ");
        if (!nome || nome.length > 40) return [400, { erro: "Dê um nome ao aparelho, com até 40 caracteres.", codigo: "parametro_invalido" }];
        chave = `usar_menos:outro:${nome.toLowerCase()}`;
      }
    }
    const existente = E.economia.find((a) => a.chave === chave);
    if (!existente && E.economia.length >= 12) return [400, { erro: "O plano já tem 12 ações. Tire alguma antes de pôr outra.", codigo: null }];
    if (existente) Object.assign(existente, { parametros: v.p, nome, aparelho_id: aparelho ? aparelho.id : null });
    else E.economia.push({ id: ++E.ecoId, tipo, chave, nome, aparelho_id: aparelho ? aparelho.id : null, criada: agora(), parametros: v.p });
    E.atividade.unshift(["Plano de economia alterado", 0, `${TITULOS_ECO[tipo]}${nome ? ` (${nome})` : ""}: no plano`]);
    return [200, estadoEconomia()];
  }

  function plano() {
    return { disponivel: true, modo_teste: true, plano: E.plano, preco_mensal: 14.9, limite_gratis: 1, limite_pago: 5, medidores: E.medidores.length,
      assinatura: E.assinatura };
  }
  function privacidade() {
    const desde = iso(MEDIDOR_DESDE);
    return { finalidades: Object.entries(FINALIDADES).map(([id, [nome, descricao, obrigatorio, revogavel]]) => ({ id, nome, descricao, obrigatorio, revogavel,
      aceito: E.aceites[id], versao_aceita: "2026-11-23", versao_vigente: "2026-11-23", desde })), historico: [], retencao: RETENCAO,
      controlador: null, email_privacidade: null, encarregado: null };
  }
  function eu() {
    return { nome: "Visitante", email: "visitante@demonstracao.exemplo", etapa: "completa", csrf: "demonstracao", dois_fatores: false, codigos_backup_restantes: 0,
      exigir_2fa: false, tarifa_kwh: E.tarifa, tarifa_padrao: null, meta_mensal_reais: E.meta, consentimento_consumo: E.aceites.consumo,
      avisos_nao_lidos: E.avisosLidos ? 0 : 3, plano: E.plano, ligacao: E.ligacao };
  }

  function alterarMedidor(m, corpo) {
    if ("apelido" in corpo) {
      const nome = String(corpo.apelido || "").trim().slice(0, 40);
      if (!nome) return [400, { erro: "O nome do medidor precisa ter de 1 a 40 caracteres.", codigo: null }];
      m.apelido = nome;
    }
    if ("papel" in corpo) {
      if (corpo.papel === "casa") { m.papel = "casa"; m.dentro = null; }
      else if (corpo.papel === "circuito") {
        const entradas = E.medidores.filter((x) => x !== m && (x.papel || "casa") === "casa");
        if (!entradas.length) return [400, { erro: "Um circuito fica dentro de um medidor de entrada, e a conta precisa ter pelo menos um.", codigo: "sem_entrada" }];
        if (E.medidores.some((x) => x.dentro === m.id && x.papel === "circuito")) return [400, { erro: "Há circuitos dentro deste medidor. Mude esses circuitos antes.", codigo: "tem_circuitos" }];
        const dentro = corpo.dentro_de || (entradas.length === 1 ? entradas[0].id : null);
        if (!entradas.some((x) => x.id === dentro)) return [400, { erro: "Escolha dentro de qual medidor de entrada este circuito fica.", codigo: "dentro_de" }];
        m.papel = "circuito";
        m.dentro = dentro;
      } else return [400, { erro: "Diga se o medidor mede a entrada (casa) ou um circuito.", codigo: null }];
      E.atividade.unshift(["O que o medidor mede mudou", 0, `${m.id}: ${m.papel === "casa" ? "entrada" : "circuito"}`]);
    }
    return [200, { ok: true }];
  }

  function responder(metodo, caminho, corpo) {
    const [rota, consulta = ""] = caminho.split("?");
    const p = new URLSearchParams(consulta);
    const partes = rota.split("/").filter(Boolean).slice(1);          // sem o "api"
    const chave = `${metodo} /${partes.join("/")}`;
    if (chave === "GET /publico/info") {
      return [200, { versao: "demonstração", modo: "demonstracao", exigir_2fa: false, termos_versao: "2026-11-16", privacidade_versao: "2026-11-23",
        finalidades: {}, retencao: RETENCAO, contato: { controlador: null, email_privacidade: null, encarregado: null }, email_de_teste: false,
        plano: { disponivel: true, preco_mensal: 14.9, limite_gratis: 1, limite_pago: 5 }, na_lista_de_espera: 0 }];
    }
    if (chave === "POST /conta/entrar") { E.dentro = true; return [200, { etapa: "completa", csrf: "demonstracao" }]; }
    if (chave === "POST /conta/sair") { E.dentro = false; return [200, { ok: true }]; }
    if (chave === "POST /espera") return [202, { mensagem: "Na demonstração nenhum e-mail é guardado nem enviado. No site de verdade, você receberia um link para confirmar o lugar na fila." }];
    if (partes[0] === "espera" || ["cadastro", "confirmar", "reenviar", "esqueci", "redefinir"].includes(partes[1])) return NAO_NA_DEMO;
    if (!E.dentro) return [401, { erro: "Entre na sua conta para continuar.", codigo: "sem_sessao" }];

    if (chave === "GET /conta/eu") return [200, eu()];
    if (chave === "PUT /conta/preferencias") {
      if (corpo.ligacao !== undefined && ![null, "monofasica", "bifasica", "trifasica"].includes(corpo.ligacao)) {
        return [400, { erro: "O tipo de ligação é monofásica, bifásica ou trifásica.", codigo: null }];
      }
      E.tarifa = corpo.tarifa_kwh ?? null; E.meta = corpo.meta_mensal_reais ?? null;
      if (corpo.ligacao !== undefined) E.ligacao = corpo.ligacao;
      return [200, { ok: true }];
    }
    if (chave === "PUT /conta/tarifa-branca") return salvarTarifaBranca(corpo || {});
    if (chave === "GET /conta/sessoes") return [200, { sessoes: [{ id: 1, criada_em: iso(agora() - 1200), ultimo_uso: iso(agora()), rede: "demonstração", navegador: "Este navegador", atual: true }] }];
    if (partes[0] === "conta") return NAO_NA_DEMO;

    if (chave === "GET /medidores") return [200, listaDeMedidores()];
    if (chave === "POST /medidores/codigo") {
      if (E.medidores.length >= (E.plano === "casa" ? 5 : 1)) {
        return [403, { erro: "O plano grátis permite 1 medidor. Para parear mais, assine o plano Casa.", codigo: "limite_plano" }];
      }
      E.pareando = agora() + 6;
      return [201, { codigo: "DEMO-2XPA", expira_em: iso(agora() + 900), validade_s: 900 }];
    }
    if (partes[0] === "medidores" && partes[2] === "historico") return [200, historico(decodeURIComponent(partes[1]), p.get("periodo") || "2h")];
    if (partes[0] === "medidores" && partes.length === 2) {
      const m = E.medidores.find((x) => x.id === decodeURIComponent(partes[1]));
      if (!m) return [404, { erro: "Medidor não encontrado.", codigo: null }];
      if (metodo === "PATCH") return alterarMedidor(m, corpo);
      if (metodo === "DELETE") {
        E.medidores = E.medidores.filter((x) => x !== m);
        if (!E.medidores.some((x) => (x.papel || "casa") === "casa")) E.medidores.forEach((x) => { x.papel = "casa"; x.dentro = null; });
        E.medidores.forEach((x) => { if (x.dentro === m.id) x.dentro = null; });
        return [200, { ok: true }];
      }
    }
    if (chave === "GET /consumo/dias") return [200, dias(Number(p.get("dias")) || 14)];
    if (chave === "GET /consumo/horas") return [200, horas(Math.max(1, Math.min(31, Number(p.get("dias")) || 14)))];
    if (chave === "GET /agora/partes") return [200, partesAgora()];
    if (chave === "GET /fantasma") return [200, E.medidores.some((m) => !m.parado) ? fantasma() : { disponivel: false, motivo: "Precisamos de pelo menos 2 noites medidas (entre 0h e 5h) para calcular.", medidores: [] }];
    if (chave === "GET /meta") return [200, meta()];

    if (chave === "GET /avisos") return [200, avisos()];
    if (chave === "POST /avisos/lidos") { E.avisosLidos = true; return [200, { ok: true }]; }
    if (partes[0] === "avisos" && partes[1] === "config") {
      if ("por_email" in corpo) E.porEmail[partes[2]] = corpo.por_email === true;
      if ("por_push" in corpo) E.porPush[partes[2]] = corpo.por_push === true;
      return [200, avisos()];
    }
    // Na demonstração nada sai do navegador: as notificações ficam desligadas.
    if (chave === "GET /notificacoes") return [200, { disponivel: false, chave: null, aparelhos: [], demonstracao: true }];

    if (chave === "GET /aparelhos") return [200, aparelhos()];
    if (partes[0] === "aparelhos" && metodo === "PATCH") {
      const a = APARELHOS.find((x) => x.id === Number(partes[1]));
      if (a && typeof corpo.nome === "string" && corpo.nome.trim()) a.nome = corpo.nome.trim().slice(0, 40);
      if (a && corpo.oculto === true) E.ocultos.add(a.id);
      return [200, aparelhos()];
    }

    if (chave === "GET /economia") return [200, estadoEconomia()];
    if (chave === "GET /economia/tarifa-branca") return [200, tarifaBranca()];
    if (chave === "GET /economia/resumo") {
      return [200, { acoes: E.economia.length, ...totaisEconomia(), verificacao: E.economia.length ? verificacaoEconomia() : null }];
    }
    if (chave === "POST /economia/plano") return salvarEconomia(corpo);
    if (metodo === "DELETE" && partes[0] === "economia" && partes[1] === "plano") {
      const acao = E.economia.find((a) => a.id === Number(partes[2]));
      if (!acao) return [404, { erro: "Ação não encontrada.", codigo: null }];
      E.economia = E.economia.filter((a) => a !== acao);
      E.atividade.unshift(["Plano de economia alterado", 0, `${TITULOS_ECO[acao.tipo]}${acao.nome ? ` (${acao.nome})` : ""}: fora do plano`]);
      return [200, estadoEconomia()];
    }

    if (chave === "GET /plano" || chave === "POST /plano/atualizar") return [200, plano()];
    if (chave === "POST /plano/assinar") {
      E.assinatura = { status: "pendente", valor: 14.9, desde: iso(agora()), proxima_cobranca: null, valida_ate: null, link_pagamento: "#pagamento-teste/demonstracao" };
      return [200, { link: location.href.split("#")[0] + "#pagamento-teste/demonstracao" }];
    }
    if (chave === "POST /plano/pagamento-teste") {
      if (corpo.aprovado) { E.plano = "casa"; E.assinatura = { ...E.assinatura, status: "ativa", link_pagamento: null, proxima_cobranca: iso(agora() + 30 * DIA) }; } else E.assinatura = null;
      return [200, plano()];
    }
    if (chave === "POST /plano/cancelar") { E.assinatura = { ...E.assinatura, status: "cancelada", valida_ate: E.assinatura.proxima_cobranca }; return [200, plano()]; }

    if (chave === "GET /privacidade") return [200, privacidade()];
    if (chave === "GET /privacidade/atividade") {
      return [200, { atividade: E.atividade.map(([descricao, diasAtras, detalhe = null]) => ({ quando: iso(agora() - diasAtras * DIA), evento: "demo", descricao, detalhe, rede: null })) }];
    }
    if (chave === "POST /privacidade/exportar") {
      return [200, { observacao: "Arquivo de demonstração: dados simulados.", conta: { nome: "Visitante" }, autorizacoes: E.aceites, consumo_por_dia: dias(14).dias }, "meterwatt-demonstracao.json"];
    }
    if (partes[0] === "privacidade" && metodo === "PUT" && partes[1] in FINALIDADES) {
      if (!FINALIDADES[partes[1]][3]) return [400, { erro: "Esta autorização faz parte do uso do serviço. Para deixar de usá-lo, exclua a conta.", codigo: null }];
      E.aceites[partes[1]] = corpo.aceito === true;
      E.atividade.unshift(["Autorização alterada", 0, `${FINALIDADES[partes[1]][0]}: ${corpo.aceito ? "autorizado" : "revogado"}`]);
      return [200, privacidade()];
    }
    return NAO_NA_DEMO;
  }

  const fetchDeVerdade = window.fetch ? window.fetch.bind(window) : null;
  window.fetch = async (caminho, opcoes = {}) => {
    if (typeof caminho !== "string" || !caminho.startsWith("/api/")) {
      if (fetchDeVerdade) return fetchDeVerdade(caminho, opcoes);
      throw new Error("sem rede");
    }
    let corpo = {};
    try { corpo = opcoes.body ? JSON.parse(opcoes.body) : {}; } catch { corpo = {}; }
    await new Promise((r) => setTimeout(r, 60 + Math.random() * 120));          // um pouco de espera, como num servidor
    const [status, dados, arquivo] = responder((opcoes.method || "GET").toUpperCase(), caminho, corpo);
    const texto = JSON.stringify(dados, null, arquivo ? 2 : 0);
    return { ok: status >= 200 && status < 300, status,
      headers: { get: (nome) => (arquivo && nome.toLowerCase() === "content-disposition" ? `attachment; filename="${arquivo}"` : null) },
      json: async () => JSON.parse(texto), text: async () => texto };
  };
})();
