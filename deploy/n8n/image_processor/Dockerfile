FROM python:3.12-slim

WORKDIR /app

# rembg / Pillow 原生依赖：libGL（OpenCV）+ libglib（rembg 间接依赖）
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# rembg 首次运行会下载 u2net.onnx (~170MB) 到 ~/.u2net；
# 预热进镜像，避免容器启动后还要联网拉模型
RUN python -c "from rembg import new_session; new_session('u2net')"

COPY app.py /app/app.py
COPY assets /app/assets

ENV PYTHONUNBUFFERED=1 \
    PORT=8788 \
    WHITEBG_ROOT=/data/whitebg \
    TEMPLATE_PATH=/app/assets/template_red.png

EXPOSE 8788

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8788"]
