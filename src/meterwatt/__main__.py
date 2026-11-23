"""Inicia o MeterWatt (aplicativo com contas).

    cd src
    python -m meterwatt                    # desenvolvimento, em http://localhost:8000
    python -m meterwatt --sem-mqtt         # só o site, sem ouvir os medidores
    python -m meterwatt --gerar-chave      # gera a MW_CHAVE_CRIPTO para produção
    python -m meterwatt --gerar-vapid      # gera a MW_VAPID_CHAVE_PRIVADA (notificações no celular)
    python -m meterwatt --backup           # tira uma cópia de segurança agora e sai
    python -m meterwatt --restaurar ARQ    # abre uma cópia num arquivo novo (não mexe no banco em uso)
    python -m meterwatt --lista-espera     # mostra a lista de espera confirmada, em CSV
"""
import argparse
import csv
import logging
import sys
import threading

from . import VERSAO, config, copia


def _tarefas(plataforma, parar):
    """A cada 5 minutos: avisos. A cada 15: limpeza de retenção. Uma vez por dia: cópia de segurança."""
    log = logging.getLogger("meterwatt.tarefas")
    volta = 0
    while not parar.wait(5 * 60):
        volta += 1
        try:
            novos = plataforma.alertas.verificar()
            if novos:
                log.info("Avisos novos: %s", novos)
            if volta % 3 == 0:
                apagados = plataforma.privacidade.reter()
                if any(apagados.values()):
                    log.info("Retenção: %s", {k: v for k, v in apagados.items() if v})
            if plataforma.cfg.backup_automatico and copia.precisa(plataforma.cfg):
                log.info("Cópia de segurança gravada em %s", copia.fazer(plataforma.cfg))
        except Exception:
            log.exception("Falha nas tarefas periódicas")
        finally:
            plataforma.fechar_conexao_da_thread()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="meterwatt", description=f"MeterWatt {VERSAO}")
    ap.add_argument("--gerar-chave", action="store_true", help="mostra uma chave nova para MW_CHAVE_CRIPTO e sai")
    ap.add_argument("--gerar-vapid", action="store_true", help="mostra uma chave nova para MW_VAPID_CHAVE_PRIVADA e sai")
    ap.add_argument("--sem-mqtt", action="store_true", help="não conecta ao broker MQTT")
    ap.add_argument("--backup", action="store_true", help="tira uma cópia de segurança cifrada e sai")
    ap.add_argument("--restaurar", metavar="ARQUIVO", help="decifra uma cópia de segurança num arquivo novo e sai")
    ap.add_argument("--lista-espera", action="store_true", help="imprime a lista de espera confirmada (CSV) e sai")
    args = ap.parse_args(argv)
    if args.gerar_chave:
        print(config.gerar_chave())
        return 0
    if args.gerar_vapid:
        from .notificacoes import Vapid
        chave = config.gerar_chave_vapid()
        print(f"MW_VAPID_CHAVE_PRIVADA={chave}")
        print(f"# chave pública (o app pega sozinho em /api/notificacoes): {Vapid(chave, '', lambda: 0).chave_publica}")
        print("# Guarde fora do repositório. Trocar esta chave desliga as notificações de todos os aparelhos.")
        return 0

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        cfg = config.carregar()
    except config.ConfiguracaoInvalida as erro:
        print(f"Configuração inválida: {erro}", file=sys.stderr)
        return 2

    if args.restaurar:
        try:
            r = copia.restaurar(cfg, args.restaurar, cfg.arquivo_banco + ".restaurado")
        except (OSError, ValueError) as erro:
            print(f"Não deu para restaurar: {erro}", file=sys.stderr)
            return 1
        print(f"Cópia aberta em {r['arquivo']} ({r['contas']} conta(s)). Para usar: pare o servidor, guarde o banco atual "
              f"com outro nome e renomeie esse arquivo para {cfg.arquivo_banco}.")
        return 0

    from .plataforma import Plataforma
    plataforma = Plataforma(cfg)
    if args.backup:
        print(f"Cópia de segurança gravada em {copia.fazer(cfg)}")
        return 0
    if args.lista_espera:
        linhas = plataforma.espera.exportar_confirmados()
        escritor = csv.DictWriter(sys.stdout, fieldnames=["posicao", "email", "indicados", "origem", "confirmado_em"])
        escritor.writeheader()
        escritor.writerows(linhas)
        return 0

    from .web import criar_app
    plataforma.privacidade.reter()
    if not args.sem_mqtt:
        from . import mqtt
        mqtt.iniciar(plataforma)
    parar = threading.Event()
    threading.Thread(target=_tarefas, args=(plataforma, parar), daemon=True, name="tarefas").start()

    app = criar_app(plataforma)
    print(f"MeterWatt {VERSAO} ({cfg.modo}) em {cfg.url_publica}")
    if cfg.email_modo == "arquivo":
        print(f"E-mails de teste vão para {cfg.pasta_instancia}/caixa-de-saida (nada sai do computador).")
    if cfg.pagamento_provedor == "simulado":
        print("Pagamento em modo de teste: nenhuma cobrança é feita de verdade.")
    if not cfg.vapid_chave_privada:
        print("Notificações no celular desligadas: defina MW_VAPID_CHAVE_PRIVADA (gere com --gerar-vapid).")
    if cfg.producao:
        from waitress import serve
        serve(app, host=cfg.http_host, port=cfg.http_porta, threads=8)
    else:
        app.run(host=cfg.http_host, port=cfg.http_porta, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
