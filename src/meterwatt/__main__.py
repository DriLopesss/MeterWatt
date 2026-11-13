"""Inicia o MeterWatt (aplicativo com contas).

    cd src
    python -m meterwatt                 # desenvolvimento, em http://localhost:8000
    python -m meterwatt --sem-mqtt      # só o site, sem ouvir os medidores
    python -m meterwatt --gerar-chave   # gera a MW_CHAVE_CRIPTO para produção
"""
import argparse
import logging
import sys
import threading

from . import VERSAO, config


def _retencao(plataforma, parar):
    log = logging.getLogger("meterwatt.retencao")
    while not parar.wait(15 * 60):
        try:
            apagados = plataforma.privacidade.reter()
            if any(apagados.values()):
                log.info("Retenção: %s", {k: v for k, v in apagados.items() if v})
        except Exception:
            log.exception("Falha na limpeza de retenção")
        finally:
            plataforma.fechar_conexao_da_thread()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="meterwatt", description=f"MeterWatt {VERSAO}")
    ap.add_argument("--gerar-chave", action="store_true", help="mostra uma chave nova para MW_CHAVE_CRIPTO e sai")
    ap.add_argument("--sem-mqtt", action="store_true", help="não conecta ao broker MQTT")
    args = ap.parse_args(argv)
    if args.gerar_chave:
        print(config.gerar_chave())
        return 0

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        cfg = config.carregar()
    except config.ConfiguracaoInvalida as erro:
        print(f"Configuração inválida: {erro}", file=sys.stderr)
        return 2

    from .plataforma import Plataforma
    from .web import criar_app
    plataforma = Plataforma(cfg)
    plataforma.privacidade.reter()
    if not args.sem_mqtt:
        from . import mqtt
        mqtt.iniciar(plataforma)
    parar = threading.Event()
    threading.Thread(target=_retencao, args=(plataforma, parar), daemon=True, name="retencao").start()

    app = criar_app(plataforma)
    print(f"MeterWatt {VERSAO} ({cfg.modo}) em {cfg.url_publica}")
    if cfg.email_modo == "arquivo":
        print(f"E-mails de teste vão para {cfg.pasta_instancia}/caixa-de-saida (nada sai do computador).")
    if cfg.producao:
        from waitress import serve
        serve(app, host=cfg.http_host, port=cfg.http_porta, threads=8)
    else:
        app.run(host=cfg.http_host, port=cfg.http_porta, threaded=True, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
