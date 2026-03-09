# 小米摄像头视频处理 - 稳定版 v2.1
FROM jrottenberg/ffmpeg:6.1-ubuntu2204

RUN apt-get update && apt-get install -y python3 python3-pip && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY process.py /app/process.py

ENV MERGE_DIR=/app/video
ENV INPUT_DIR=/input
ENV OUTPUT_DIR=/output
ENV DELETE_AFTER_MERGE=false
ENV MAX_MERGE=0
ENV COMPRESS_CRF=32
ENV COMPRESS_PRESET=medium
ENV COMPRESS_THREADS=8
ENV COMPRESS_RESOLUTION=1920x1080
ENV DELETE_AFTER_COMPRESS=true
ENV STATE_FILE=/app/state.db
ENV SAVE_INTERVAL=5

ENTRYPOINT ["python3", "/app/process.py"]
