"""Integração com o Home Assistant (e com o que mais souber ler um endereço web com uma chave).

- A pessoa cria uma chave só de leitura em Conta > Home Assistant. A chave aparece uma vez; o servidor guarda
  só o resumo (hash) dela. Até 3 chaves por conta, cada uma com um nome, e qualquer uma pode ser cancelada.
- O Home Assistant busca GET /api/integracao/estado com o cabeçalho "Authorization: Bearer <chave>" (a
  integração RESTful dele faz isso a cada 30 segundos, por padrão). A resposta tem a potência de agora, o
  consumo de hoje, o custo de hoje, a tensão e um total de energia que só cresce: o painel de Energia do Home
  Assistant precisa de um total assim ("total_increasing"), e o consumo de 5 em 5 minutos não serve porque a
  retenção apaga o que é antigo.
- O total cresce a cada leitura dos medidores de entrada (os de circuito não somam de novo) e volta a zero só
  quando a pessoa apaga o consumo em Privacidade.
- A chave não muda nada na conta e não vê o Detetive, a economia, os avisos nem os dados de cadastro.
"""
import secrets

from .erros import ErroDeNegocio, NaoEncontrado
from .seguranca import hash_token

PREFIXO = "mwha_"
MAXIMO_CHAVES = 3
USO_ANOTADO_A_CADA_S = 300


def _nome(texto):
    nome = " ".join(texto.split()) if isinstance(texto, str) else ""
    if not 1 <= len(nome) <= 40:
        raise ErroDeNegocio("Dê um nome de 1 a 40 letras para a chave (por exemplo, Home Assistant da sala).", 400)
    return nome


class Integracao:
    def __init__(self, plataforma):
        self.p = plataforma

    def listar(self, sessao):
        conn = self.p.db()
        return {"chaves": [{"id": r["id"], "nome": r["nome"], "criada_em": self.p.iso(r["criada_em"]),
                            "ultimo_uso": self.p.iso(r["ultimo_uso"]) if r["ultimo_uso"] else None}
                           for r in conn.execute("SELECT * FROM chaves_integracao WHERE usuario_id = ? ORDER BY criada_em",
                                                 (sessao["usuario_id"],))],
                "maximo": MAXIMO_CHAVES}

    def criar(self, sessao, dados, ip=None):
        nome = _nome((dados or {}).get("nome"))
        conn = self.p.db()
        uid = sessao["usuario_id"]
        if not self.p.consentimento(conn, uid, "consumo"):
            raise ErroDeNegocio("Para ligar uma integração, autorize o tratamento dos dados de consumo em Privacidade.", 403, "sem_autorizacao")
        if conn.execute("SELECT COUNT(*) FROM chaves_integracao WHERE usuario_id = ?", (uid,)).fetchone()[0] >= MAXIMO_CHAVES:
            raise ErroDeNegocio(f"Já há {MAXIMO_CHAVES} chaves. Cancele uma que não usa mais para criar outra.", 400)
        chave = PREFIXO + secrets.token_urlsafe(32)
        with conn:
            nova = conn.execute("INSERT INTO chaves_integracao (usuario_id, nome, chave_hash, criada_em) VALUES (?, ?, ?, ?)",
                                (uid, nome, hash_token(chave), self.p.agora())).lastrowid
            self.p.auditar(conn, uid, "integracao_criada", nome, ip)
        return {"id": nova, "nome": nome, "chave": chave, **self.listar(sessao)}

    def cancelar(self, sessao, cid, ip=None):
        conn = self.p.db()
        uid = sessao["usuario_id"]
        r = conn.execute("SELECT nome FROM chaves_integracao WHERE id = ? AND usuario_id = ?", (cid, uid)).fetchone()
        if not r:
            raise NaoEncontrado("Chave não encontrada.")
        with conn:
            conn.execute("DELETE FROM chaves_integracao WHERE id = ?", (cid,))
            self.p.auditar(conn, uid, "integracao_cancelada", r["nome"], ip)
        return self.listar(sessao)

    def estado(self, cabecalho):
        """O que o Home Assistant lê. `cabecalho`: o valor de Authorization."""
        chave = cabecalho[7:].strip() if isinstance(cabecalho, str) and cabecalho.startswith("Bearer ") else ""
        if not (chave.startswith(PREFIXO) and 20 <= len(chave) <= 100):
            raise ErroDeNegocio("Chave de integração ausente ou inválida.", 401, "chave_invalida")
        conn = self.p.db()
        c = conn.execute("SELECT * FROM chaves_integracao WHERE chave_hash = ?", (hash_token(chave),)).fetchone()
        if not c:
            raise ErroDeNegocio("Chave de integração ausente ou inválida.", 401, "chave_invalida")
        uid = c["usuario_id"]
        if not self.p.consentimento(conn, uid, "consumo"):
            raise ErroDeNegocio("A autorização dos dados de consumo foi retirada nesta conta.", 403, "sem_autorizacao")
        agora = self.p.agora()
        if not c["ultimo_uso"] or agora - c["ultimo_uso"] > USO_ANOTADO_A_CADA_S:
            with conn:
                conn.execute("UPDATE chaves_integracao SET ultimo_uso = ? WHERE id = ?", (agora, c["id"]))
        u = conn.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()
        sessao = {"usuario_id": uid, "tarifa_kwh": u["tarifa_kwh"], "ligacao": u["ligacao"]}
        medidores = [m for m in self.p.medidores.listar(sessao)["medidores"] if m["papel"] == "casa"]
        vivos = [m for m in medidores if m["online"] and m["potencia"] is not None]
        tarifa = self.p.tarifa_de(u)
        kwh_hoje = sum(m["kwh_hoje"] for m in medidores)
        return {
            "potencia_w": round(sum(m["potencia"] for m in vivos), 1) if vivos else None,
            "kwh_hoje": round(kwh_hoje, 3),
            "custo_hoje": round(kwh_hoje * tarifa, 2) if tarifa is not None else None,
            "energia_total_kwh": round((u["energia_total_wh"] or 0) / 1000, 3),
            "tensao_v": vivos[0]["tensao_v"] if vivos else None,
            "online": bool(vivos) and len(vivos) == len(medidores),
            "medidores": len(medidores),
            "atualizado_em": self.p.iso(agora),
        }

    def exportar(self, conn, uid):
        return [{"nome": r["nome"], "criada_em": self.p.iso(r["criada_em"]),
                 "ultimo_uso": self.p.iso(r["ultimo_uso"]) if r["ultimo_uso"] else None}
                for r in conn.execute("SELECT * FROM chaves_integracao WHERE usuario_id = ?", (uid,))]
