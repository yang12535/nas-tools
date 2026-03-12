# ========================================
# NAS 工具集 - Makefile
# ========================================

.PHONY: help build-webdav build-video start-webdav start-video logs-webdav logs-video clean

# 默认目标
help:
	@echo "NAS 工具集管理命令"
	@echo ""
	@echo "构建:"
	@echo "  make build-webdav    - 构建 WebDAV 上传器镜像"
	@echo "  make build-video     - 构建视频处理器镜像"
	@echo ""
	@echo "运行:"
	@echo "  make start-webdav    - 启动 WebDAV 上传器"
	@echo "  make start-video     - 启动视频处理器"
	@echo ""
	@echo "日志:"
	@echo "  make logs-webdav     - 查看 WebDAV 上传器日志"
	@echo "  make logs-video      - 查看视频处理器日志"
	@echo ""
	@echo "维护:"
	@echo "  make clean           - 清理所有容器和镜像"

# WebDAV 上传器
build-webdav:
	cd webdav-uploader && docker-compose build

start-webdav:
	cd webdav-uploader && docker-compose up -d

logs-webdav:
	cd webdav-uploader && docker-compose logs -f

stop-webdav:
	cd webdav-uploader && docker-compose down

# 视频处理器
build-video:
	cd xiaomi-video && docker-compose build

start-video:
	cd xiaomi-video && docker-compose up

logs-video:
	cd xiaomi-video && docker-compose logs

stop-video:
	cd xiaomi-video && docker-compose down

# 检查换行符
check-lf:
	@python check-line-endings.py

fix-lf:
	@python check-line-endings.py --fix

# 清理
clean:
	cd webdav-uploader && docker-compose down -v 2>/dev/null || true
	cd xiaomi-video && docker-compose down -v 2>/dev/null || true
	docker system prune -f
