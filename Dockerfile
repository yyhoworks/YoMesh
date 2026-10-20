FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml compose.yaml ./
COPY src ./src
COPY scripts ./scripts
COPY web ./web
COPY examples ./examples
COPY tests ./tests
COPY tools/linux-amd64 ./tools/cores
COPY tools/cores.linux-amd64.lock.json ./tools/cores.linux-amd64.lock.json
COPY tools/localdns.lock.json ./tools/localdns.lock.json
RUN chmod 755 tools/cores/mihomo/mihomo tools/cores/sing-box/sing-box tools/cores/mosdns/mosdns \
    && if [ -f tools/cores/localdns/localdns ]; then chmod 755 tools/cores/localdns/localdns; fi \
    && python -m unittest discover -s tests -v
USER 10001:10001
EXPOSE 8767
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8767/api/health',timeout=3).read()"
CMD ["python", "scripts/yomesh.py", "serve", "--host", "0.0.0.0", "--port", "8767"]
