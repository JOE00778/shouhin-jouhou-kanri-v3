FROM python:3.12-slim

WORKDIR /app

# 无外部依赖；标准库足够
COPY scripts /app/scripts
COPY webhook_server.py /app/webhook_server.py

# cookies + csv 通过 volume 挂载，不打进镜像
ENV PYTHONUNBUFFERED=1
EXPOSE 8787

CMD ["python3", "/app/webhook_server.py"]
