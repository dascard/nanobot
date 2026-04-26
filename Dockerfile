FROM python:3.10-slim-bullseye
WORKDIR /app
# 安装 sqlite3、wkhtmltopdf (用于 MD 渲染) 以及中文支持字体
RUN apt-get update && apt-get install -y \
	git \
	tzdata \
	sqlite3 \
	wkhtmltopdf \
	xvfb \
	tzdata \
	fonts-wqy-zenhei \
	curl \
	&& rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Shanghai
COPY requirements.txt .
COPY vendor/ ./vendor/
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
	pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir imgkit markdown2
COPY . .
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
