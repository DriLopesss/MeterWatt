"""Banco de dados (SQLite) e suas versões.

Cada item de MIGRACOES leva o banco de uma versão para a próxima. Nunca edite uma
migração já publicada: acrescente uma nova no fim da lista.

O SQLite atende o protótipo e o piloto. Para muitos clientes, o mesmo esquema passa para
PostgreSQL; as consultas usam SQL comum e parâmetros, nunca texto montado à mão.
"""
import sqlite3

MIGRACOES = [
    # 1: estrutura inicial
    """
    CREATE TABLE usuarios (
        id INTEGER PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        nome TEXT NOT NULL,
        senha_hash TEXT NOT NULL,
        criado_em REAL NOT NULL,
        senha_trocada_em REAL NOT NULL,
        email_confirmado_em REAL,
        totp_cifrado BLOB,
        totp_pendente_cifrado BLOB,
        totp_ativado_em REAL,
        totp_ultimo_passo INTEGER NOT NULL DEFAULT 0,
        tarifa_kwh REAL,
        meta_mensal_reais REAL
    );

    CREATE TABLE sessoes (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        token_hash TEXT NOT NULL UNIQUE,
        csrf TEXT NOT NULL,
        etapa TEXT NOT NULL CHECK (etapa IN ('aguardando_2fa', 'completa')),
        tentativas_2fa INTEGER NOT NULL DEFAULT 0,
        criada_em REAL NOT NULL,
        ultimo_uso REAL NOT NULL,
        expira_em REAL NOT NULL,
        ip_resumido TEXT,
        navegador TEXT
    );
    CREATE INDEX sessoes_usuario ON sessoes(usuario_id);

    CREATE TABLE codigos_backup (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        codigo_hash TEXT NOT NULL,
        usado_em REAL
    );
    CREATE INDEX codigos_backup_usuario ON codigos_backup(usuario_id);

    CREATE TABLE tokens_email (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        finalidade TEXT NOT NULL CHECK (finalidade IN ('confirmar', 'redefinir')),
        token_hash TEXT NOT NULL UNIQUE,
        criado_em REAL NOT NULL,
        expira_em REAL NOT NULL,
        usado_em REAL
    );
    CREATE INDEX tokens_email_usuario ON tokens_email(usuario_id, finalidade);

    CREATE TABLE consentimentos (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        finalidade TEXT NOT NULL,
        versao TEXT NOT NULL,
        aceito INTEGER NOT NULL CHECK (aceito IN (0, 1)),
        registrado_em REAL NOT NULL,
        origem TEXT NOT NULL
    );
    CREATE INDEX consentimentos_usuario ON consentimentos(usuario_id, finalidade, registrado_em);

    CREATE TABLE medidores (
        id TEXT PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        apelido TEXT NOT NULL,
        pareado_em REAL NOT NULL,
        ultima_mensagem REAL,
        ultima_gravacao REAL,
        status_mqtt TEXT,
        tipo TEXT,
        fw TEXT,
        contador_wh REAL,
        potencia_anterior REAL
    );
    CREATE INDEX medidores_usuario ON medidores(usuario_id);

    CREATE TABLE codigos_pareamento (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        codigo_hash TEXT NOT NULL UNIQUE,
        criado_em REAL NOT NULL,
        expira_em REAL NOT NULL,
        usado_em REAL,
        medidor_id TEXT
    );

    CREATE TABLE leituras (
        medidor_id TEXT NOT NULL REFERENCES medidores(id) ON DELETE CASCADE,
        instante REAL NOT NULL,
        sensor_ok INTEGER NOT NULL,
        erro_sensor TEXT,
        tensao_v REAL,
        corrente_a REAL,
        potencia_w REAL,
        potencia_va REAL,
        fp REAL,
        frequencia_hz REAL,
        rssi INTEGER,
        simulado INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX leituras_medidor_instante ON leituras(medidor_id, instante);

    CREATE TABLE consumo_5min (
        medidor_id TEXT NOT NULL REFERENCES medidores(id) ON DELETE CASCADE,
        inicio INTEGER NOT NULL,
        n INTEGER NOT NULL,
        soma_w REAL NOT NULL,
        min_w REAL NOT NULL,
        max_w REAL NOT NULL,
        wh REAL NOT NULL,
        simulado INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (medidor_id, inicio)
    ) WITHOUT ROWID;

    CREATE TABLE auditoria (
        id INTEGER PRIMARY KEY,
        instante REAL NOT NULL,
        usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
        evento TEXT NOT NULL,
        detalhe TEXT,
        ip_resumido TEXT
    );
    CREATE INDEX auditoria_usuario ON auditoria(usuario_id, instante);
    CREATE INDEX auditoria_instante ON auditoria(instante);

    CREATE TABLE exclusoes (
        id INTEGER PRIMARY KEY,
        instante REAL NOT NULL,
        email_hmac TEXT NOT NULL
    );
    """,
    # 2: avisos (alertas e resumo semanal)
    """
    CREATE TABLE avisos (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        tipo TEXT NOT NULL,
        chave TEXT NOT NULL,
        criado_em REAL NOT NULL,
        titulo TEXT NOT NULL,
        texto TEXT NOT NULL,
        lido_em REAL,
        UNIQUE (usuario_id, chave)
    );
    CREATE INDEX avisos_usuario ON avisos(usuario_id, criado_em);

    CREATE TABLE avisos_config (
        usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        tipo TEXT NOT NULL,
        por_email INTEGER NOT NULL CHECK (por_email IN (0, 1)),
        PRIMARY KEY (usuario_id, tipo)
    ) WITHOUT ROWID;
    """,
    # 3: Detetive de aparelhos
    """
    CREATE TABLE aparelhos (
        id INTEGER PRIMARY KEY,
        medidor_id TEXT NOT NULL REFERENCES medidores(id) ON DELETE CASCADE,
        nome TEXT,
        potencia_w REAL NOT NULL,
        n_ciclos INTEGER NOT NULL DEFAULT 0,
        criado_em REAL NOT NULL,
        oculto INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX aparelhos_medidor ON aparelhos(medidor_id);

    CREATE TABLE ciclos (
        id INTEGER PRIMARY KEY,
        medidor_id TEXT NOT NULL REFERENCES medidores(id) ON DELETE CASCADE,
        aparelho_id INTEGER REFERENCES aparelhos(id) ON DELETE CASCADE,
        inicio REAL NOT NULL,
        fim REAL NOT NULL,
        potencia_w REAL NOT NULL,
        wh REAL NOT NULL
    );
    CREATE INDEX ciclos_medidor ON ciclos(medidor_id, inicio);
    CREATE INDEX ciclos_aparelho ON ciclos(aparelho_id, inicio);

    CREATE TABLE degraus_abertos (
        id INTEGER PRIMARY KEY,
        medidor_id TEXT NOT NULL REFERENCES medidores(id) ON DELETE CASCADE,
        instante REAL NOT NULL,
        delta_w REAL NOT NULL
    );
    CREATE INDEX degraus_medidor ON degraus_abertos(medidor_id, instante);
    """,
    # 4: assinaturas (planos e cobrança)
    """
    CREATE TABLE assinaturas (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        provedor TEXT NOT NULL,
        referencia TEXT NOT NULL UNIQUE,
        id_externo TEXT UNIQUE,
        status TEXT NOT NULL CHECK (status IN ('pendente', 'ativa', 'pausada', 'cancelada')),
        valor REAL NOT NULL,
        criada_em REAL NOT NULL,
        atualizada_em REAL NOT NULL,
        proxima_cobranca REAL,
        valida_ate REAL,
        link_pagamento TEXT
    );
    CREATE INDEX assinaturas_usuario ON assinaturas(usuario_id, criada_em);
    """,
    # 5: lista de espera (página de lançamento)
    """
    CREATE TABLE lista_espera (
        id INTEGER PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        codigo TEXT NOT NULL UNIQUE,
        token_hash TEXT NOT NULL UNIQUE,
        indicado_por INTEGER REFERENCES lista_espera(id) ON DELETE SET NULL,
        origem TEXT,
        versao TEXT NOT NULL,
        criado_em REAL NOT NULL,
        ultimo_email REAL NOT NULL,
        confirmado_em REAL
    );
    CREATE INDEX lista_espera_indicado ON lista_espera(indicado_por);
    """,
    # 6: senha própria de cada medidor no servidor de mensagens
    """
    ALTER TABLE medidores ADD COLUMN credencial_propria INTEGER NOT NULL DEFAULT 0;
    """,
    # 7: plano de economia ("E se eu…?")
    """
    CREATE TABLE economia_plano (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        tipo TEXT NOT NULL CHECK (tipo IN ('banho_curto', 'chuveiro_verao', 'fantasma', 'geladeira', 'usar_menos')),
        chave TEXT NOT NULL,
        parametros TEXT NOT NULL,
        aparelho_id INTEGER REFERENCES aparelhos(id) ON DELETE SET NULL,
        nome TEXT,
        kwh_mes REAL NOT NULL,
        criada_em REAL NOT NULL,
        atualizada_em REAL NOT NULL,
        UNIQUE (usuario_id, chave)
    );
    """,
    # 8: o "antes" de cada ação do plano, guardado (a retenção apaga o consumo de 5 em 5 minutos depois de 90 dias)
    """
    ALTER TABLE economia_plano ADD COLUMN linha_de_base TEXT;
    """,
    # 9: notificações no celular e no computador (Web Push). O endereço e as chaves de cada aparelho
    # ficam cifrados; o hmac do endereço evita repetir o mesmo aparelho. A inscrição sobrevive ao fim da
    # sessão (senão a pessoa pararia de receber avisos um dia depois de fechar o app), mas sai junto
    # quando a pessoa sai da conta naquele aparelho, troca a senha ou encerra as outras sessões.
    """
    CREATE TABLE push_inscricoes (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        sessao_id INTEGER REFERENCES sessoes(id) ON DELETE SET NULL,
        endpoint_hmac TEXT NOT NULL UNIQUE,
        dados_cifrados BLOB NOT NULL,
        navegador TEXT,
        criada_em REAL NOT NULL,
        atualizada_em REAL NOT NULL,
        ultimo_envio REAL,
        falhas INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX push_inscricoes_usuario ON push_inscricoes(usuario_id);
    CREATE INDEX push_inscricoes_sessao ON push_inscricoes(sessao_id);
    ALTER TABLE avisos_config ADD COLUMN por_push INTEGER NOT NULL DEFAULT 1 CHECK (por_push IN (0, 1));
    """,
    # 10: o que cada medidor mede. "casa" mede a entrada de um imóvel e soma no total da conta; "circuito"
    # mede uma parte de dentro de outro medidor (o ar-condicionado, a cozinha) e não soma de novo.
    """
    ALTER TABLE medidores ADD COLUMN papel TEXT NOT NULL DEFAULT 'casa' CHECK (papel IN ('casa', 'circuito'));
    ALTER TABLE medidores ADD COLUMN dentro_de TEXT REFERENCES medidores(id) ON DELETE SET NULL;
    """,
    # 11: a conta de luz mais perto da real. Tipo de ligação (o mínimo da conta), horário de ponta e os quatro
    # preços da Tarifa Branca da distribuidora de cada pessoa, e a bandeira de cada mês (vale para todos).
    """
    ALTER TABLE usuarios ADD COLUMN ligacao TEXT CHECK (ligacao IN ('monofasica', 'bifasica', 'trifasica'));
    ALTER TABLE usuarios ADD COLUMN ponta_inicio_min INTEGER CHECK (ponta_inicio_min BETWEEN 0 AND 1439);
    ALTER TABLE usuarios ADD COLUMN tb_convencional REAL;
    ALTER TABLE usuarios ADD COLUMN tb_fora REAL;
    ALTER TABLE usuarios ADD COLUMN tb_intermediario REAL;
    ALTER TABLE usuarios ADD COLUMN tb_ponta REAL;
    CREATE TABLE bandeiras (
        mes TEXT PRIMARY KEY CHECK (mes GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'),
        cor TEXT NOT NULL CHECK (cor IN ('verde', 'amarela', 'vermelha1', 'vermelha2')),
        registrada_em REAL NOT NULL
    );
    """,
    # 12: contas de luz que a pessoa digita para conferir com o medido. O medido e a cobertura ficam guardados
    # junto: o consumo de 5 em 5 minutos some com a retenção e a conferência não pode sumir junto.
    """
    CREATE TABLE contas_luz (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        inicio TEXT NOT NULL,
        fim TEXT NOT NULL,
        kwh REAL NOT NULL,
        total_reais REAL,
        medido_kwh REAL NOT NULL,
        cobertura REAL NOT NULL,
        estimado INTEGER NOT NULL DEFAULT 0,
        criada_em REAL NOT NULL
    );
    CREATE INDEX contas_luz_usuario ON contas_luz(usuario_id, fim);
    """,
    # 13: compartilhar a casa, só para ver. O convite guarda o e-mail da pessoa só até ser aceito ou vencer.
    """
    CREATE TABLE convites_casa (
        id INTEGER PRIMARY KEY,
        dono_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        email TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        criado_em REAL NOT NULL,
        expira_em REAL NOT NULL
    );
    CREATE INDEX convites_casa_dono ON convites_casa(dono_id);
    CREATE TABLE acessos_casa (
        dono_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        convidado_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        criado_em REAL NOT NULL,
        PRIMARY KEY (dono_id, convidado_id),
        CHECK (dono_id <> convidado_id)
    );
    CREATE INDEX acessos_casa_convidado ON acessos_casa(convidado_id);
    """,
    # 14: casa com duas ou três fases (um PZEM por fase). A última leitura de cada fase e o contador
    # de energia de cada uma: a energia é contada fase a fase, e uma fase que some e volta não perde nada.
    """
    ALTER TABLE medidores ADD COLUMN fases TEXT;
    ALTER TABLE medidores ADD COLUMN contadores_fases TEXT;
    """,
    # 15: minutos medidos sem conexão e mandados depois (firmware 3.5). Até onde já chegaram (para não
    # contar duas vezes uma mensagem repetida) e o resumo da última recuperação, para a tela.
    """
    ALTER TABLE medidores ADD COLUMN atrasadas_ate REAL;
    ALTER TABLE medidores ADD COLUMN ultima_recuperacao TEXT;
    """,
    # 16: qualidade da energia. Tensão de 10 em 10 minutos por fase (como os registros da ANEEL), faltas
    # de energia e de fase com início e fim, e o tempo ligado do medidor (para saber quando ele reiniciou).
    """
    CREATE TABLE tensao_10min (
        medidor_id TEXT NOT NULL REFERENCES medidores(id) ON DELETE CASCADE,
        fase INTEGER NOT NULL,
        inicio INTEGER NOT NULL,
        n INTEGER NOT NULL,
        soma_v REAL NOT NULL,
        min_v REAL NOT NULL,
        max_v REAL NOT NULL,
        PRIMARY KEY (medidor_id, fase, inicio)
    ) WITHOUT ROWID;
    CREATE TABLE eventos_energia (
        id INTEGER PRIMARY KEY,
        medidor_id TEXT NOT NULL REFERENCES medidores(id) ON DELETE CASCADE,
        tipo TEXT NOT NULL,
        fase INTEGER,
        inicio REAL NOT NULL,
        fim REAL NOT NULL,
        simulado INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX eventos_energia_medidor ON eventos_energia(medidor_id, inicio);
    ALTER TABLE medidores ADD COLUMN uptime_s REAL;
    """,
    # 17: integração (Home Assistant). Chaves só de leitura (guardadas como hash) e um total de energia que só
    # cresce, para o painel de Energia de lá (o consumo de 5 em 5 minutos é apagado pela retenção).
    """
    CREATE TABLE chaves_integracao (
        id INTEGER PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        nome TEXT NOT NULL,
        chave_hash TEXT NOT NULL UNIQUE,
        criada_em REAL NOT NULL,
        ultimo_uso REAL
    );
    CREATE INDEX chaves_integracao_usuario ON chaves_integracao(usuario_id);
    ALTER TABLE usuarios ADD COLUMN energia_total_wh REAL NOT NULL DEFAULT 0;
    """,
    # 18: aparelhos já vistos e a marca do segredo de cada um. Um medidor removido de uma conta só pode ser pareado
    # de novo pelo próprio aparelho (o mesmo segredo, que fica numa memória que o BOOT não apaga). Sem isso, quem
    # tem a senha de provisão pareava o id de um aparelho removido na própria conta (revisão de 25/11).
    """
    CREATE TABLE medidores_conhecidos (
        id TEXT PRIMARY KEY,
        segredo_hmac TEXT NOT NULL,
        visto_em REAL NOT NULL
    );
    """,
]


def abrir(caminho):
    conn = sqlite3.connect(caminho, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA secure_delete = ON")   # o que é apagado é sobrescrito no arquivo
    return conn


def migrar(conn):
    versao = conn.execute("PRAGMA user_version").fetchone()[0]
    if versao > len(MIGRACOES):
        raise RuntimeError(f"O banco está na versão {versao}, mais nova que este programa ({len(MIGRACOES)}).")
    for numero in range(versao, len(MIGRACOES)):
        conn.executescript("BEGIN;" + MIGRACOES[numero] + f"; PRAGMA user_version = {numero + 1}; COMMIT;")
    return len(MIGRACOES)
