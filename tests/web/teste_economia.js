// Confere as contas do "E se eu…?" no JavaScript contra os mesmos casos do Python.
// Rodar na raiz do repositório:  node tests/web/teste_economia.js
"use strict";
const E = require("../../src/meterwatt/estatico/economia-calculo.js");
const vetores = require("../economia_vetores.json");

let falhas = 0;
const confere = (ok, nome, detalhe) => {
  if (!ok) { falhas++; console.log(`FALHOU: ${nome}${detalhe ? ` (${detalhe})` : ""}`); }
};
const perto = (a, b) => Math.abs(a - b) < 1e-6;

for (const c of vetores.acoes) {
  const v = E.validar(c.tipo, c.parametros);
  if ("erro_campo" in c) {
    confere(!v.ok && v.campo === c.erro_campo, c.nome, `esperava erro em ${c.erro_campo}, veio ${JSON.stringify(v)}`);
  } else {
    confere(v.ok, c.nome, v.erro);
    if (v.ok) {
      const kwh = E.kwhMes(c.tipo, v.p);
      confere(perto(kwh, c.kwh_mes), c.nome, `esperava ${c.kwh_mes}, veio ${kwh}`);
      confere(!("qualquer" in v.p), c.nome, "campo desconhecido não foi descartado");
    }
  }
}
for (const c of vetores.totais) {
  const acoes = c.acoes.map((a) => ({ tipo: a.tipo, parametros: E.validar(a.tipo, a.parametros).p }));
  const kwh = E.totalKwhMes(acoes);
  confere(perto(kwh, c.kwh_mes), c.nome, `esperava ${c.kwh_mes}, veio ${kwh}`);
}
for (const c of vetores.meses_para_pagar) {
  const m = E.mesesParaPagar(c.preco, c.kwh_mes, c.tarifa);
  confere(c.meses === null ? m === null : perto(m, c.meses), c.nome, `esperava ${c.meses}, veio ${m}`);
}
const total = vetores.acoes.length + vetores.totais.length + vetores.meses_para_pagar.length;
console.log(falhas ? `teste_economia: ${falhas} falha(s)` : `teste_economia: ok (${total} casos)`);
process.exit(falhas ? 1 : 0);
