# Imagem do aplicativo MeterWatt. Construa a partir da raiz do repositório:
#   docker build -f infra/Dockerfile -t meterwatt .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --system --uid 10001 --home-dir /app meterwatt
WORKDIR /app
# Versões travadas e conferidas pelo hash (src/requirements-trava.txt, gerado do requirements.txt com
# pip-compile --generate-hashes). Um pacote trocado no caminho não instala. O Dependabot propõe as atualizações.
COPY src/requirements-trava.txt .
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements-trava.txt
COPY src/meterwatt ./meterwatt
RUN mkdir -p /dados && chown meterwatt /dados

# Roda sem privilégios. O banco, a chave e os backups ficam no volume /dados.
USER meterwatt
ENV MW_MODO=producao MW_PASTA_INSTANCIA=/dados MW_HTTP_HOST=0.0.0.0 MW_HTTP_PORTA=8000 MW_CONFIAR_PROXY=1
VOLUME /dados
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/publico/saude', timeout=3)"
CMD ["python", "-m", "meterwatt"]
