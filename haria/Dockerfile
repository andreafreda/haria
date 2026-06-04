ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.18
FROM $BUILD_FROM

ENV LANG C.UTF-8
ENV PYTHONUNBUFFERED=1

RUN apk add --no-cache build-base libffi-dev openssl-dev sqlite zbar jpeg-dev zlib-dev

WORKDIR /app

COPY app/requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app/ ./

RUN mkdir -p /data

COPY run.sh /
RUN chmod +x /run.sh

CMD ["/run.sh"]
