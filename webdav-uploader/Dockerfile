# WebDAV 文件上传器 - 稳定版 v2.1
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir requests pyyaml urllib3

COPY uploader.py /app/uploader.py

ENV WEBDAV_URL=""
ENV WEBDAV_USER=""
ENV WEBDAV_PASS=""
ENV WEBDAV_ROOT="/"
ENV RATE_LIMIT=1048576
ENV WATCH_DIR="./upload"
ENV INTERVAL=3600
ENV DELETE_AFTER_UPLOAD=true
ENV VERIFY_CHECKSUM=true

ENTRYPOINT ["python3", "/app/uploader.py"]
CMD ["-c", "config.yaml"]
