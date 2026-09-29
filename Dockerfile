FROM m.daocloud.io/docker.io/library/python:3.11-slim

WORKDIR /app

# Dependencies first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY docker-entrypoint.sh .

RUN chmod +x docker-entrypoint.sh \
 && sed -i 's/\r$//' docker-entrypoint.sh \
 && mkdir -p /app/data /app/uploads

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

ENTRYPOINT ["./docker-entrypoint.sh"]
