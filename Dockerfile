FROM python:3.11-slim

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 安装 Playwright 系统依赖
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
        libcups2 libdrm2 libxkbcommon0 libatspi2.0-0 \
        libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libgbm1 libpango-1.0-0 libcairo2 libasound2 \
        libwayland-client0 fonts-wqy-zenhei && \
    rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装 Python 依赖（使用阿里云 PyPI 源）
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    -r requirements.txt

# 安装 Playwright Chromium 浏览器
RUN playwright install chromium

# 复制项目文件
COPY . .

EXPOSE 8000

CMD ["uvicorn", "douyin_phaser_api:app", "--host", "0.0.0.0", "--port", "8000"]
