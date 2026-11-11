/* Painel ao vivo do MeterWatt.
 * Regras deste arquivo:
 *  - Só mostra o que o backend devolveu. Nada é inventado nem "completado".
 *  - Todo texto vai para a tela com textContent (ids de medidores vêm da rede).
 *  - Leitura antiga, estimada ou simulada sempre aparece com aviso.
 */
(() => {
  'use strict';

  const $ = id => document.getElementById(id);
  const SVG = 'http://www.w3.org/2000/svg';
  const semMovimento = matchMedia('(prefers-reduced-motion: reduce)').matches;

  const num = (v, casas = 0) => v == null ? '—'
    : v.toLocaleString('pt-BR', { minimumFractionDigits: casas, maximumFractionDigits: casas });
  const brl = v => v == null ? '—' : v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
  const hora = t => new Date(t * 1000).toLocaleTimeString('pt-BR');

  function duracao(s) {
    if (s == null) return '—';
    s = Math.round(s);
    if (s < 60) return s + ' s';
    if (s < 3600) return Math.floor(s / 60) + ' min';
    if (s < 86400) return Math.floor(s / 3600) + ' h ' + Math.floor(s % 3600 / 60) + ' min';
    return Math.floor(s / 86400) + ' d ' + Math.floor(s % 86400 / 3600) + ' h';
  }

  const ERROS_SENSOR = {
    sem_resposta: 'o módulo não respondeu',
    resposta_incompleta: 'a resposta chegou cortada',
    crc_invalido: 'a resposta chegou com erro (ruído ou mau contato na serial)',
    endereco_errado: 'respondeu um módulo com outro endereço',
    funcao_errada: 'o módulo respondeu outra coisa',
    excecao_modbus: 'o módulo recusou o pedido',
    fora_da_faixa: 'os números vieram fora da faixa que o módulo mede',
  };

  let lista = [];
  let escolhido = null;
  let minutos = 30;
  let historico = { pontos: [], unidade: 'W', agora: 0 };
  let falhasSeguidas = 0;
  let jaCarregou = false;

  try { escolhido = localStorage.getItem('meterwatt.medidor'); } catch (e) { /* sem armazenamento */ }

  // ------------------------------------------------------------ rede
  async function buscar(caminho) {
    const controle = new AbortController();
    const relogio = setTimeout(() => controle.abort(), 4000);
    try {
      const r = await fetch(caminho, { cache: 'no-store', signal: controle.signal });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return await r.json();
    } finally {
      clearTimeout(relogio);
    }
  }

  async function atualizar() {
    if (document.hidden) return;
    try {
      const dados = await buscar('/api/dispositivos');
      lista = Array.isArray(dados.dispositivos) ? dados.dispositivos : [];
      falhasSeguidas = 0;
      jaCarregou = true;
    } catch (erro) {
      falhasSeguidas++;
    }
    desenhar();
  }

  async function atualizarHistorico() {
    const d = atual();
    if (document.hidden || !d || falhasSeguidas) return;
    try {
      const h = await buscar('/api/historico/' + encodeURIComponent(d.dispositivo) + '?minutos=' + minutos);
      if (h.dispositivo === (atual() || {}).dispositivo) {
        historico = { pontos: Array.isArray(h.pontos) ? h.pontos : [], unidade: h.unidade || 'W', agora: h.agora };
        desenharGrafico();
        desenharRegua(atual());
      }
    } catch (erro) { /* a próxima rodada tenta de novo */ }
  }

  function atual() {
    if (!lista.length) return null;
    return lista.find(d => d.dispositivo === escolhido) || lista.find(d => d.online) || lista[0];
  }

  // ------------------------------------------------------------ tela
  function estado(tipo, texto) {
    $('estado').dataset.estado = tipo;
    $('estado-txt').textContent = texto;
  }

  function selo(id, texto, tom) {
    const el = $(id);
    el.textContent = texto || '';
    if (tom) el.dataset.tom = tom; else delete el.dataset.tom;
  }

  // Número grande com transição curta, para a mudança não "piscar".
  function animar(el, para, casas) {
    const de = el._valor;
    el._valor = para;
    cancelAnimationFrame(el._quadro);
    if (para == null || de == null || semMovimento || de === para) { el.textContent = num(para, casas); return; }
    const inicio = performance.now();
    const passo = t => {
      const k = Math.min((t - inicio) / 450, 1), e = 1 - Math.pow(1 - k, 3);
      el.textContent = num(de + (para - de) * e, casas);
      if (k < 1) el._quadro = requestAnimationFrame(passo);
    };
    el._quadro = requestAnimationFrame(passo);
  }

  // Faixas de tensão "adequada" publicadas pela ANEEL para cada tensão nominal.
  function avaliarTensao(v) {
    if (v == null) return ['', null];
    if (v >= 100 && v <= 150) {
      if (v >= 117 && v <= 133) return ['Dentro da faixa adequada (117 a 133 V)', 'bom'];
      return ['Fora da faixa adequada para 127 V (117 a 133 V)', 'atencao'];
    }
    if (v >= 180 && v <= 250) {
      if (v >= 202 && v <= 231) return ['Dentro da faixa adequada (202 a 231 V)', 'bom'];
      return ['Fora da faixa adequada para 220 V (202 a 231 V)', 'atencao'];
    }
    return ['Tensão fora do esperado para rede de 127 ou 220 V', 'ruim'];
  }

  function sinalWifi(rssi) {
    if (rssi == null) return '—';
    const nivel = rssi >= -60 ? 'forte' : rssi >= -70 ? 'bom' : rssi >= -80 ? 'fraco' : 'muito fraco';
    return num(rssi) + ' dBm (' + nivel + ')';
  }

  function avisos(itens) {
    const caixa = $('avisos');
    const assinatura = itens.map(i => i[0] + i[1]).join('|');
    if (caixa.dataset.assinatura === assinatura) return;   // não reescreve o que não mudou
    caixa.dataset.assinatura = assinatura;
    caixa.replaceChildren(...itens.map(([texto, tom]) => {
      const p = document.createElement('p');
      p.className = 'aviso';
      if (tom) p.dataset.tom = tom;
      p.textContent = texto;
      return p;
    }));
  }

  function preencherSeletor() {
    const sel = $('seletor');
    const ids = lista.map(d => d.dispositivo);
    $('seletor-caixa').hidden = ids.length < 2;
    if (ids.join() === [...sel.options].map(o => o.value).join()) return;
    sel.replaceChildren(...ids.map(id => {
      const o = document.createElement('option');
      o.value = id; o.textContent = id;
      return o;
    }));
    const d = atual();
    if (d) sel.value = d.dispositivo;
  }

  function desenhar() {
    const semBackend = falhasSeguidas >= 2 || (!jaCarregou && falhasSeguidas >= 1);
    if (!jaCarregou) {
      $('sem-backend').hidden = !semBackend;
      $('vazio').hidden = true; $('painel').hidden = true;
      estado(semBackend ? 'offline' : 'conectando', semBackend ? 'Backend fora do ar' : 'Conectando…');
      return;
    }
    const d = atual();
    $('sem-backend').hidden = true;
    $('vazio').hidden = !!d;
    $('painel').hidden = !d;
    if (!d) { estado(semBackend ? 'offline' : 'alerta', semBackend ? 'Backend fora do ar' : 'Aguardando o primeiro medidor'); return; }

    preencherSeletor();
    const pzem = d.tipo === 'pzem';
    const temMedida = d.sensor_ok && d.tipo != null;
    const idade = d.segundos_desde_leitura;
    const antigo = semBackend || !d.online;
    document.body.classList.toggle('antigo', antigo);

    // ----- estado geral e avisos
    const lst = [];
    if (semBackend) {
      estado('offline', 'Backend fora do ar');
      lst.push(['O painel perdeu a conexão com o backend. Os números abaixo são os últimos recebidos e podem estar desatualizados.', 'ruim']);
    } else if (!d.online) {
      estado('parado', idade == null ? 'Medidor sem sinal' : 'Sem sinal há ' + duracao(idade));
      lst.push([idade == null
        ? 'Este medidor ainda não enviou leituras desde que o backend ligou. O consumo de hoje veio do arquivo de leituras.'
        : 'O medidor parou de enviar. Os números abaixo são da última leitura recebida, de ' + duracao(idade) + ' atrás.', 'ruim']);
    } else if (!d.sensor_ok) {
      estado('alerta', 'Sensor sem leitura');
    } else if (d.simulado) {
      estado('simulado', 'Dados do simulador');
    } else {
      estado('vivo', 'Ao vivo');
    }
    if (d.online && !d.sensor_ok) {
      lst.push(['O ESP32 está conectado, mas não conseguiu ler o sensor: ' + (ERROS_SENSOR[d.erro_sensor] || d.erro_sensor || 'motivo desconhecido') + '. Confira a ligação da serial e se o módulo está energizado.', 'ruim']);
    }
    if (d.simulado) lst.push(['Estes números vêm do simulador, não de um medidor real.']);
    if (d.tipo === 'corrente') lst.push(['Este medidor só mede corrente. A potência é aparente (VA) com tensão fixa, e o consumo e o custo são estimativas.']);
    if (d.alarme) lst.push(['O módulo sinalizou potência acima do limite de alarme configurado nele.']);
    avisos(lst);

    $('quando').textContent = idade == null ? '' : 'Última leitura há ' + duracao(idade);

    // ----- potência
    const potencia = !temMedida ? null : (pzem ? d.potencia_w : d.potencia_aparente_va);
    $('rot-potencia').textContent = pzem || d.tipo == null ? 'Potência ativa agora' : 'Potência aparente agora (estimada)';
    $('potencia-un').textContent = pzem || d.tipo == null ? 'W' : 'VA';
    animar($('potencia'), potencia, potencia != null && potencia < 100 ? 1 : 0);
    $('ritmo').textContent = d.custo_por_hora != null
      ? 'Nesse ritmo, cerca de ' + brl(d.custo_por_hora) + ' por hora' + (pzem ? '' : ' (estimado)')
      : (d.tarifa_kwh == null && potencia != null ? 'Defina TARIFA_KWH no backend para ver o custo' : '');
    desenharRegua(d);

    // ----- medidas
    $('tensao').textContent = num(temMedida ? d.tensao_v : null, 1);
    const [txtTensao, tomTensao] = temMedida && pzem ? avaliarTensao(d.tensao_v) : [pzem || !temMedida ? '' : 'Não medida por este sensor', null];
    selo('tensao-selo', txtTensao, tomTensao);

    $('corrente').textContent = num(temMedida ? d.corrente_a : null, 3);
    selo('corrente-selo', '');

    $('fp').textContent = num(temMedida ? d.fp : null, 2);
    selo('fp-selo', !temMedida ? '' : !pzem ? 'Não medido por este sensor'
      : d.fp == null ? '' : d.fp >= 0.92 ? 'Quase toda a corrente vira trabalho' : 'Parte da corrente não vira trabalho (carga reativa)');

    $('frequencia').textContent = num(temMedida ? d.frequencia_hz : null, 1);
    selo('frequencia-selo', temMedida && !pzem ? 'Não medida por este sensor' : '');

    $('aparente').textContent = num(temMedida ? d.potencia_aparente_va : null, potencia != null && d.potencia_aparente_va < 100 ? 1 : 0);
    selo('aparente-selo', temMedida && pzem ? 'Tensão × corrente' : temMedida ? 'Corrente × tensão nominal fixa' : '');

    // ----- consumo
    const estimado = d.energia_fonte === 'estimada';
    $('kwh-hoje').textContent = num(d.energia_fonte ? d.kwh_hoje : null, 3);
    selo('kwh-hoje-selo', !d.energia_fonte ? '' : estimado ? 'Estimado a partir da corrente' : 'Pelo contador do módulo, desde 0h');
    $('custo-hoje').textContent = d.energia_fonte ? brl(d.custo_hoje) : '—';
    selo('custo-selo', d.tarifa_kwh == null ? 'Tarifa não configurada no backend'
      : 'Tarifa de ' + brl(d.tarifa_kwh) + ' por kWh' + (estimado ? ' (estimado)' : '') + ', sem impostos e bandeira');
    $('contador').textContent = num(d.energia_contador_kwh, 3);
    selo('contador-selo', d.energia_contador_kwh == null ? 'Este sensor não tem contador' : 'Total guardado no PZEM desde que foi zerado');

    // ----- saúde
    $('s-rssi').textContent = sinalWifi(d.rssi);
    $('s-uptime').textContent = duracao(d.uptime_s);
    $('s-ultima').textContent = idade == null ? '—' : 'há ' + duracao(idade);
    $('s-falhas').textContent = d.falhas_leitura == null ? '—' : num(d.falhas_leitura) + ' desde que ligou';
    $('s-sensor').textContent = d.sensor || (d.tipo === 'corrente' ? 'só corrente' : '—');
    $('s-fw').textContent = d.fw || '—';
    $('s-id').textContent = d.dispositivo;
  }

  // ------------------------------------------------------------ régua e gráfico
  function teto(v) {
    if (!(v > 0)) return 100;
    const base = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 2, 2.5, 5, 10]) if (v <= m * base) return m * base;
    return 10 * base;
  }

  function desenharRegua(d) {
    if (!d) return;
    const agora = d.sensor_ok ? (d.tipo === 'pzem' ? d.potencia_w : d.potencia_aparente_va) : null;
    const maior = Math.max(agora || 0, ...historico.pontos.map(p => p[1]));
    const max = teto(maior);
    $('regua-barra').style.width = agora == null ? '0%' : Math.min(100, agora / max * 100) + '%';
    $('regua-meio').textContent = num(max / 2);
    $('regua-max').textContent = num(max) + ' ' + historico.unidade;
    $('regua-nota').textContent = 'A escala acompanha o maior valor do período do gráfico.';
  }

  // No celular o gráfico ganha mais altura e letras maiores (o SVG encolhe junto com a tela).
  const G = { W: 800, H: 250, e: 58, d: 14, t: 14, b: 30 };
  let pontosTela = [];
  function ajustarProporcao() {
    const estreito = $('grafico-area').clientWidth < 520;
    Object.assign(G, estreito ? { H: 430, e: 118, d: 18, t: 22, b: 48 } : { H: 250, e: 58, d: 14, t: 14, b: 30 });
    $('grafico').setAttribute('viewBox', '0 0 ' + G.W + ' ' + G.H);
    $('grafico').classList.toggle('estreito', estreito);
  }

  function no(tag, attrs, texto) {
    const el = document.createElementNS(SVG, tag);
    for (const k in attrs) el.setAttribute(k, attrs[k]);
    if (texto != null) el.textContent = texto;
    return el;
  }

  function desenharGrafico() {
    const svg = $('grafico');
    ajustarProporcao();
    const { pontos, unidade, agora } = historico;
    const filhos = [];
    const defs = no('defs');
    const grad = no('linearGradient', { id: 'degrade', x1: 0, y1: 0, x2: 0, y2: 1 });
    grad.append(no('stop', { offset: 0, 'stop-color': '#ffb020', 'stop-opacity': .38 }),
                no('stop', { offset: 1, 'stop-color': '#ffb020', 'stop-opacity': 0 }));
    defs.append(grad);
    filhos.push(defs);

    const t1 = agora || Date.now() / 1000, t0 = t1 - minutos * 60;
    const max = teto(Math.max(0, ...pontos.map(p => p[1])));
    const x = t => G.e + (t - t0) / (t1 - t0) * (G.W - G.e - G.d);
    const y = v => G.H - G.b - v / max * (G.H - G.t - G.b);

    for (const v of [0, max / 2, max]) {
      filhos.push(no('line', { class: 'grade', x1: G.e, x2: G.W - G.d, y1: y(v), y2: y(v) }));
      filhos.push(no('text', { x: G.e - 10, y: y(v), 'text-anchor': 'end', 'dominant-baseline': 'middle' }, num(v)));
    }
    filhos.push(no('text', { x: G.e, y: G.H - 10 }, minutos >= 60 ? 'há ' + minutos / 60 + ' h' : 'há ' + minutos + ' min'));
    filhos.push(no('text', { x: G.W - G.d, y: G.H - 10, 'text-anchor': 'end' }, 'agora'));

    pontosTela = pontos.filter(p => p[0] >= t0).map(p => ({ t: p[0], v: p[1], x: x(p[0]), y: y(p[1]) }));
    if (!pontosTela.length) {
      filhos.push(no('text', { class: 'vazio-txt', x: (G.W + G.e) / 2, y: G.H / 2, 'text-anchor': 'middle' }, 'Ainda sem leituras neste período'));
    } else {
      // Um buraco grande entre leituras quebra a linha: não se liga o que não foi medido.
      const trechos = [[pontosTela[0]]];
      for (let i = 1; i < pontosTela.length; i++) {
        if (pontosTela[i].t - pontosTela[i - 1].t > 30) trechos.push([]);
        trechos[trechos.length - 1].push(pontosTela[i]);
      }
      for (const tr of trechos) {
        const caminho = tr.map((p, i) => (i ? 'L' : 'M') + p.x.toFixed(1) + ' ' + p.y.toFixed(1)).join(' ');
        if (tr.length > 1) {
          filhos.push(no('path', { class: 'area', d: caminho + ' L' + tr[tr.length - 1].x.toFixed(1) + ' ' + y(0) + ' L' + tr[0].x.toFixed(1) + ' ' + y(0) + ' Z' }));
          filhos.push(no('path', { class: 'linha', d: caminho }));
        } else {
          filhos.push(no('circle', { class: 'ponto', cx: tr[0].x, cy: tr[0].y, r: 3 }));
        }
      }
      const ult = pontosTela[pontosTela.length - 1];
      filhos.push(no('circle', { class: 'ponto', cx: ult.x, cy: ult.y, r: 5 }));
    }
    filhos.push(no('line', { class: 'cursor', id: 'cursor', x1: 0, x2: 0, y1: G.t, y2: G.H - G.b, visibility: 'hidden' }));
    svg.replaceChildren(...filhos);

    const valores = pontosTela.map(p => p.v);
    $('rot-grafico').textContent = (unidade === 'W' ? 'Potência ativa' : 'Potência aparente (estimada)') + ' ao longo do tempo';
    $('grafico-selo').textContent = valores.length
      ? 'Mínimo ' + num(Math.min(...valores)) + ' ' + unidade + ' · máximo ' + num(Math.max(...valores)) + ' ' + unidade + ' · ' + valores.length + ' leituras. O histórico do gráfico fica só na memória do backend (até 2 horas).'
      : '';
  }

  function mostrarDica(evento) {
    const dica = $('dica'), cursor = $('cursor');
    if (!pontosTela.length || !cursor) return;
    const caixa = $('grafico').getBoundingClientRect();
    const xSvg = (evento.clientX - caixa.left) / caixa.width * G.W;
    let perto = pontosTela[0];
    for (const p of pontosTela) if (Math.abs(p.x - xSvg) < Math.abs(perto.x - xSvg)) perto = p;
    cursor.setAttribute('x1', perto.x); cursor.setAttribute('x2', perto.x);
    cursor.setAttribute('visibility', 'visible');
    dica.textContent = num(perto.v, perto.v < 100 ? 1 : 0) + ' ' + historico.unidade + ' às ' + hora(perto.t);
    dica.hidden = false;
    const px = perto.x / G.W * caixa.width;
    const largura = dica.offsetWidth;
    dica.style.left = Math.max(0, Math.min(caixa.width - largura, px - largura / 2)) + 'px';
    dica.style.top = Math.max(0, perto.y / G.H * caixa.height - 40) + 'px';
  }
  function esconderDica() {
    $('dica').hidden = true;
    const cursor = $('cursor');
    if (cursor) cursor.setAttribute('visibility', 'hidden');
  }

  // ------------------------------------------------------------ eventos
  $('seletor').addEventListener('change', e => {
    escolhido = e.target.value;
    try { localStorage.setItem('meterwatt.medidor', escolhido); } catch (x) { /* sem armazenamento */ }
    historico = { pontos: [], unidade: 'W', agora: 0 };
    $('potencia')._valor = undefined;
    desenhar(); desenharGrafico(); atualizarHistorico();
  });
  document.querySelectorAll('.faixas button').forEach(b => b.addEventListener('click', () => {
    minutos = +b.dataset.min;
    document.querySelectorAll('.faixas button').forEach(o => o.setAttribute('aria-pressed', String(o === b)));
    desenharGrafico(); atualizarHistorico();
  }));
  $('grafico-area').addEventListener('pointermove', mostrarDica);
  $('grafico-area').addEventListener('pointerdown', mostrarDica);
  $('grafico-area').addEventListener('pointerleave', esconderDica);
  let larguraAnterior = innerWidth;
  addEventListener('resize', () => { if (innerWidth !== larguraAnterior) { larguraAnterior = innerWidth; desenharGrafico(); } });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { atualizar().then(atualizarHistorico); } });

  desenharGrafico();
  atualizar().then(atualizarHistorico);
  setInterval(atualizar, 2000);
  setInterval(atualizarHistorico, 5000);
})();
