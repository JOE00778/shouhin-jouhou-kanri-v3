FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py /app/app.py

ENV PYTHONUNBUFFERED=1 \
    PORT=8789 \
    CMS_DB_PATH=/data/warehouse.db \
    CMS_OUTPUTS_DIR=/data/outputs

EXPOSE 8789

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8789"]
