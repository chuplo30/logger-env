FROM ubuntu:22.04

RUN apt-get update && apt-get install -y \
    curl ca-certificates python3 python3-pip \
    && curl -fsSL https://github.com/lune-org/lune/releases/latest/download/lune-linux-x86_64 \
       -o /usr/local/bin/lune \
    && chmod +x /usr/local/bin/lune \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip3 install --no-cache-dir -r requirements.txt

CMD ["python3", "bot.py"]
