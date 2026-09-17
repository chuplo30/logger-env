# Sử dụng image Debian hoặc Ubuntu ổn định
FROM ubuntu:22.04

# Cài đặt các gói cần thiết
RUN apt-get update && apt-get install -y \
    curl \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Tải và cài đặt Lune binary (bản dành cho Linux x86_64)
RUN curl -fsSL https://github.com/lune-org/lune/releases/latest/download/lune-linux-x86_64 \
    -o /usr/local/bin/lune \
    && chmod +x /usr/local/bin/lune

# (Tùy chọn) Kiểm tra phiên bản Lune
RUN lune --version

# Đặt thư mục làm việc
WORKDIR /app

# Copy mã nguồn dự án vào container
COPY . .

# Cài đặt Python và các thư viện cần thiết cho bot
RUN apt-get update && apt-get install -y python3 python3-pip \
    && pip3 install --no-cache-dir -r requirements.txt

# Lệnh khởi chạy bot
CMD ["python3", "bot.py"]
