/* Servidor de mentira para a demonstração em um arquivo só (tools/gerar_demo.py).
 *
 * Responde às mesmas chamadas /api/... do servidor de verdade, com uma casa inventada.
 * Nada sai do navegador e nada é guardado: recarregar a página volta tudo ao começo.
 * Os números são SIMULADOS. Não use esta página como prova de medição.
 */
"use strict";
(() => {
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
  const cacheDia = new Map();
  function whEntre(a, b) {                                     // soma minuto a minuto
    let wh = 0;
    for (let t = a; t < b; t += 60) wh += watts(t, false) / 60;
    return wh;
  }
  function kwhDoDia(inicio) {
    if (!cacheDia.has(inicio)) cacheDia.set(inicio, whEntre(inicio, inicio + DIA) / 1000 * (0.9 + ruido(inicio / DIA) * 0.25));
    return cacheDia.get(inicio);
  }
  const kwhHoje = () => whEntre(inicioDoDia(agora()), agora()) / 1000;

  /* ---------------- o que a pessoa mexe na demonstração ---------------- */
  const E = {
    dentro: true, tarifa: 0.92, meta: 180, plano: "gratis", assinatura: null, avisosLidos: false,
    medidores: [{ id: "mw-DEMO0001", apelido: "Casa", pareado: agora() - 9 * DIA }],
    aceites: { termos: true, privacidade: true, consumo: true, historico_longo: false, aparelhos: false, novidades: false },
    porEmail: { medidor_offline: true, meta: true, consumo_incomum: true, resumo_semanal: true },
    pareando: null, ocultos: new Set(),
    atividade: [["Entrada na conta", 0.02], ["Tarifa ou meta alteradas", 2.1], ["Medidor pareado (mw-DEMO0001)", 9], ["E-mail confirmado", 9.01], ["Conta criada", 9.02]],
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
    atividade_da_conta_dias: 180, conta_nao_confirmada_dias: 7 };
  const NAO_NA_DEMO = [400, { erro: "Isto não funciona na demonstração: precisa do servidor de verdade.", codigo: "demonstracao" }];
  const reais = (v) => v.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  const num = (v, c = 1) => v.toLocaleString("pt-BR", { minimumFractionDigits: c, maximumFractionDigits: c });

  /* ---------------- respostas ---------------- */
  function listaDeMedidores() {
    const t = agora();
    if (E.pareando && t > E.pareando) {                       // o "medidor novo" aparece alguns segundos depois do código
      E.medidores.push({ id: "mw-DEMO0002", apelido: "Medidor 0002", pareado: t, parado: true });
      E.pareando = null;
    }
    const principal = E.aceites.consumo;
    return { tarifa_kwh: E.tarifa, limite_offline_s: 20, servidor_em: iso(t), medidores: E.medidores.map((m) => {
      const vivo = principal && !m.parado;
      const w = vivo ? Math.round(watts(t) * 10) / 10 : null;
      const v = 127 + Math.sin(t / 900) * 1.5;
      const kwh = m.parado ? 0 : kwhHoje();
      return { id: m.id, apelido: m.apelido, tipo: m.parado ? null : "pzem", fw: "3.3.0", pareado_em: iso(m.pareado), online: vivo,
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
    if (id === "mw-DEMO0001") {
      for (let x = t - janela; x <= t; x += passo) {
        let w = watts(x, periodo === "2h");
        if (passo > 60) { w = 0; for (let k = 0; k < passo; k += 60) w += watts(x + k, false); w /= passo / 60; }   // média do intervalo
        pontos.push([Math.round(x + (passo > 60 ? passo : 0)), Math.round(w * 10) / 10]);
      }
    }
    return { id, periodo, unidade: "W", agora: t, pontos };
  }
  function dias(n) {
    const hoje = inicioDoDia(agora()), saida = [];
    for (let i = n - 1; i >= 0; i--) {
      const inicio = inicioDoDia(hoje - i * DIA + 3600);
      const kwh = i === 0 ? kwhHoje() : (i <= 8 ? kwhDoDia(inicio) : 0);       // o medidor "chegou" há 9 dias
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
    const restam = (fimDoMes - t) / DIA, projecao = gasto + media * restam;
    const status = !E.meta ? null : projecao > E.meta ? "acima" : projecao > 0.9 * E.meta ? "atencao" : "ok";
    return { meta: E.meta, gasto_ate_agora: Math.round(gasto * 100) / 100, media_diaria: Math.round(media * 100) / 100, projecao: Math.round(projecao * 100) / 100,
      dias_restantes: Math.round(restam * 10) / 10, percentual: E.meta ? projecao / E.meta : null, status,
      folga_diaria: E.meta && restam >= 1 ? Math.round((E.meta - gasto) / restam * 100) / 100 : null, base_de_dias: 7, tarifa_kwh: E.tarifa, sem_tarifa: false };
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
    return { avisos: lista, config: Object.entries(TIPOS_DE_AVISO).map(([tipo, [nome, descricao]]) => ({ tipo, nome, descricao, por_email: E.porEmail[tipo] })) };
  }
  const PALPITES = { 1: "chuveiro elétrico ou torneira elétrica", 2: "micro-ondas, air fryer ou ferro de passar", 3: "geladeira, freezer ou ventilador forte", 4: "geladeira, freezer ou ventilador forte" };
  function aparelhos() {
    const t = agora();
    if (!E.aceites.aparelhos) return { autorizado: false, aparelhos: [], em_estudo: 0, tem_medidor_compativel: true, ciclos_para_aparecer: 3 };
    const lista = APARELHOS.filter((a) => !E.ocultos.has(a.id)).map((a) => {
      const minutosPorDia = a.ciclo ? 1440 * a.ciclo[0] / (a.ciclo[0] + a.ciclo[1]) : a.janelas.reduce((s, [x, y]) => s + y - x, 0);
      const vezes = a.ciclo ? Math.round(7 * 1440 / (a.ciclo[0] + a.ciclo[1])) : a.janelas.length * 7;
      const kwhMes = a.potencia * minutosPorDia / 60 / 1000 * 30;
      let ultima = t;
      if (!ligado(a, t)) for (let x = t; x > t - DIA; x -= 60) if (ligado(a, x)) { ultima = x; break; }
      return { id: a.id, nome: a.nome, palpite: PALPITES[a.id], medidor: "Casa", potencia_w: a.potencia, ciclos_total: vezes, vezes_na_semana: vezes,
        duracao_media_min: Math.round(minutosPorDia / (vezes / 7) * 10) / 10, kwh_mes: Math.round(kwhMes * 10) / 10,
        custo_mes: E.tarifa == null ? null : Math.round(kwhMes * E.tarifa * 100) / 100, ultima_vez: iso(ultima), ligado_agora: ligado(a, t) };
    });
    return { autorizado: true, aparelhos: lista, em_estudo: 1, tem_medidor_compativel: true, ciclos_para_aparecer: 3 };
  }
  function plano() {
    return { disponivel: true, modo_teste: true, plano: E.plano, preco_mensal: 14.9, limite_gratis: 1, limite_pago: 5, medidores: E.medidores.length,
      assinatura: E.assinatura };
  }
  function privacidade() {
    const desde = iso(agora() - 9 * DIA);
    return { finalidades: Object.entries(FINALIDADES).map(([id, [nome, descricao, obrigatorio, revogavel]]) => ({ id, nome, descricao, obrigatorio, revogavel,
      aceito: E.aceites[id], versao_aceita: "2026-11-16", versao_vigente: "2026-11-16", desde })), historico: [], retencao: RETENCAO,
      controlador: null, email_privacidade: null, encarregado: null };
  }
  function eu() {
    return { nome: "Visitante", email: "visitante@demonstracao.exemplo", etapa: "completa", csrf: "demonstracao", dois_fatores: false, codigos_backup_restantes: 0,
      exigir_2fa: false, tarifa_kwh: E.tarifa, tarifa_padrao: null, meta_mensal_reais: E.meta, consentimento_consumo: E.aceites.consumo,
      avisos_nao_lidos: E.avisosLidos ? 0 : 3, plano: E.plano };
  }

  function responder(metodo, caminho, corpo) {
    const [rota, consulta = ""] = caminho.split("?");
    const p = new URLSearchParams(consulta);
    const partes = rota.split("/").filter(Boolean).slice(1);          // sem o "api"
    const chave = `${metodo} /${partes.join("/")}`;
    if (chave === "GET /publico/info") {
      return [200, { versao: "demonstração", modo: "demonstracao", exigir_2fa: false, termos_versao: "2026-11-16", privacidade_versao: "2026-11-16",
        finalidades: {}, retencao: RETENCAO, contato: { controlador: null, email_privacidade: null, encarregado: null }, email_de_teste: false,
        plano: { disponivel: true, preco_mensal: 14.9, limite_gratis: 1, limite_pago: 5 }, na_lista_de_espera: 0 }];
    }
    if (chave === "POST /conta/entrar") { E.dentro = true; return [200, { etapa: "completa", csrf: "demonstracao" }]; }
    if (chave === "POST /conta/sair") { E.dentro = false; return [200, { ok: true }]; }
    if (chave === "POST /espera") return [202, { mensagem: "Na demonstração nenhum e-mail é guardado nem enviado. No site de verdade, você receberia um link para confirmar o lugar na fila." }];
    if (partes[0] === "espera" || ["cadastro", "confirmar", "reenviar", "esqueci", "redefinir"].includes(partes[1])) return NAO_NA_DEMO;
    if (!E.dentro) return [401, { erro: "Entre na sua conta para continuar.", codigo: "sem_sessao" }];

    if (chave === "GET /conta/eu") return [200, eu()];
    if (chave === "PUT /conta/preferencias") { E.tarifa = corpo.tarifa_kwh ?? null; E.meta = corpo.meta_mensal_reais ?? null; return [200, { ok: true }]; }
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
      if (metodo === "PATCH") { m.apelido = String(corpo.apelido || "").trim().slice(0, 40) || m.apelido; return [200, { ok: true }]; }
      if (metodo === "DELETE") { E.medidores = E.medidores.filter((x) => x !== m); return [200, { ok: true }]; }
    }
    if (chave === "GET /consumo/dias") return [200, dias(Number(p.get("dias")) || 14)];
    if (chave === "GET /fantasma") return [200, E.medidores.some((m) => !m.parado) ? fantasma() : { disponivel: false, motivo: "Precisamos de pelo menos 2 noites medidas (entre 0h e 5h) para calcular.", medidores: [] }];
    if (chave === "GET /meta") return [200, meta()];

    if (chave === "GET /avisos") return [200, avisos()];
    if (chave === "POST /avisos/lidos") { E.avisosLidos = true; return [200, { ok: true }]; }
    if (partes[0] === "avisos" && partes[1] === "config") { E.porEmail[partes[2]] = corpo.por_email === true; return [200, avisos()]; }

    if (chave === "GET /aparelhos") return [200, aparelhos()];
    if (partes[0] === "aparelhos" && metodo === "PATCH") {
      const a = APARELHOS.find((x) => x.id === Number(partes[1]));
      if (a && typeof corpo.nome === "string" && corpo.nome.trim()) a.nome = corpo.nome.trim().slice(0, 40);
      if (a && corpo.oculto === true) E.ocultos.add(a.id);
      return [200, aparelhos()];
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
      return [200, { atividade: E.atividade.map(([descricao, diasAtras]) => ({ quando: iso(agora() - diasAtras * DIA), evento: "demo", descricao, detalhe: null, rede: null })) }];
    }
    if (chave === "POST /privacidade/exportar") {
      return [200, { observacao: "Arquivo de demonstração: dados simulados.", conta: { nome: "Visitante" }, autorizacoes: E.aceites, consumo_por_dia: dias(14).dias }, "meterwatt-demonstracao.json"];
    }
    if (partes[0] === "privacidade" && metodo === "PUT" && partes[1] in FINALIDADES) {
      if (!FINALIDADES[partes[1]][3]) return [400, { erro: "Esta autorização faz parte do uso do serviço. Para deixar de usá-lo, exclua a conta.", codigo: null }];
      E.aceites[partes[1]] = corpo.aceito === true;
      E.atividade.unshift([`Autorização alterada (${FINALIDADES[partes[1]][0]}: ${corpo.aceito ? "autorizado" : "revogado"})`, 0]);
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
