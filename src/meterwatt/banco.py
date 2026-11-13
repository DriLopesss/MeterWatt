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
