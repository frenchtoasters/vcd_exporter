FROM python:3.6-alpine

LABEL MAINTAINER="Tyler French <tylerfrench2@gmail.com>"
LABEL NAME=vcd_exporter

WORKDIR /opt/vcd_exporter/
COPY . /opt/vcd_exporter/

RUN set -x; buildDeps="gcc python-dev musl-dev libffi-dev openssl openssl-dev" \
 && apk add --no-cache --update $buildDeps libxml2-dev libxslt-dev \
 && pip install -r requirements.txt . \
 && apk del $buildDeps

EXPOSE 9273

ENV PYTHONUNBUFFERED=1

#ENTRYPOINT ["/usr/local/bin/vcd_exporter"]
ENTRYPOINT ["/bin/sh"]
#CMD ["/usr/local/bin/vcd_exporter","-p", "9273","-c","/opt/vcd_exporter/vcd_exporter/vcd_config.yml"]
