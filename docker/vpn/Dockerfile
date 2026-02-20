ARG PYTHON_VERSION=3.12
FROM python:$PYTHON_VERSION-slim as builder

ENV container="docker"
ENV DEBIAN_FRONTEND=noninteractive

RUN apt update && apt upgrade -y && \
    apt install -y --no-install-recommends \
        git \
        make \
        libssl-dev \
        pkg-config \
        build-essential \
        python3-dev \
        libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory.
WORKDIR /app

# copy local scripts
COPY *.py ./
COPY *.pyi ./
COPY common ./common
COPY locales ./locales
COPY proto ./proto
COPY handlers ./handlers
COPY filters ./filters
COPY middlewares ./middlewares
COPY utils ./utils
COPY requirements.txt ./

RUN python3 -m pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    rm -rf /root/.cache/pip

FROM python:$PYTHON_VERSION-slim
WORKDIR /app

ENV PYTHON_LIB_PATH=/usr/local/lib/python${PYTHON_VERSION%.*}/site-packages

COPY --from=builder $PYTHON_LIB_PATH $PYTHON_LIB_PATH
COPY --from=builder /app /app

RUN mv /app/locales/vpn/*.yml /app/locales/ && rm -rf /app/locales/vpn && rm -rf /app/locales/vps

# install ca-certificates to allow curl verify ssl certificates of a remote hosts
RUN apt update && apt install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

CMD ["python3", "/app/main.py"]
