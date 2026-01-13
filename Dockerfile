FROM python:3.11.9-slim

# ===== 基础环境 =====
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=UTC

WORKDIR /app

# ===== 系统依赖（按需）=====
RUN apt-get update && apt-get install -y \
    curl \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# ===== 安装 Python 依赖（利用缓存）=====
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ===== 拷贝代码 =====
COPY app ./app

# ===== 默认暴露端口 =====
EXPOSE 8000

# ===== 启动命令 =====
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
