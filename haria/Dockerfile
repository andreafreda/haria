ARG BUILD_FROM
FROM $BUILD_FROM

ENV LANG C.UTF-8
ENV PYTHONUNBUFFERED=1

# Install Python deps
RUN apk add --no-cache \
    python3 \
    py3-pip \
    python3-dev \
    build-base \
    libffi-dev \
    openssl-dev \
    sqlite

WORKDIR /app

COPY app/requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app/ ./

# Data directory for SQLite
RUN mkdir -p /data

COPY run.sh /
RUN chmod +x /run.sh

CMD ["/run.sh"]
