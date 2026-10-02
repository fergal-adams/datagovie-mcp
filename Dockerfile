FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 DATAGOVIE_CACHE=/tmp/datagovie-cache
COPY requirements-hosted.txt .
RUN pip install --no-cache-dir -r requirements-hosted.txt
COPY server.py http_server.py ./
RUN useradd --create-home app
USER app
EXPOSE 8000
CMD ["python", "http_server.py"]
