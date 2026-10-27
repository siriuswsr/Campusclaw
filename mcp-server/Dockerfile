FROM m.daocloud.io/docker.io/library/python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 只复制代码；令牌只从运行时环境变量注入，绝不写进镜像层。
COPY app.py .

EXPOSE 8090

CMD ["gunicorn", "-b", "0.0.0.0:8090", "-w", "1", "--timeout", "60", "app:app"]
