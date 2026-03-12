# NAS 工具集

> 🚧 **施工中** - 正在重构整合，代码可能不稳定，生产环境请谨慎使用。

个人 NAS 自动化工具集合，适用于飞牛/N100/8845 mini PC 等设备。

## 项目列表

### 1. WebDAV 限速上传器 v2.2

**使用场景：24h自动化、夜间低带宽上传、家庭宽带防限速**

单线程限速上传，适合 NAS 长期运行，夜间自动上传不占用白天带宽。

**引用源项目:** 自研

**功能：**
- 单线程文件上传
- 令牌桶限速（默认 1MB/s）
- MD5 校验
- SQLite 状态持久化
- 1小时轮询
- 上传后自动删除本地文件

**目录：** `webdav-uploader/`

**快速开始：**
```bash
cd webdav-uploader
cp config.yaml.example config.yaml
# 编辑 config.yaml 配置 WebDAV 信息
docker-compose up -d
```

### 2. 小米视频处理工具 v2.2

**使用场景：小米摄像头视频自动归档、节省存储空间**

将 1分钟片段合并为小时级视频，并压缩为 H.265 格式节省空间（通常节省 50-70%）。

**引用源项目：**
- 合并功能：[hslr-s/xiaomi-camera-merge](https://github.com/hslr-s/xiaomi-camera-merge) (Go)
- 压缩功能：[yang12535/xiaomi-compress](https://github.com/yang12535/xiaomi-compress) (Shell)

**功能：**
- 合并 1分钟片段 → 小时级 MOV
- MOV → MKV (H.265) 压缩
- 断点续传（SQLite 状态）
- ffprobe 视频验证
- 单容器处理完即退出
- CPU/内存资源限制

**目录：** `xiaomi-video/`

**快速开始：**
```bash
cd xiaomi-video
docker-compose up
```

**目录映射：**
- `./merge` - 小米摄像头原始视频（如 `2023102401/`）
- `./input` - 合并后的 MOV 文件（年/月/日/小时.mov）
- `./output` - 压缩后的 MKV 文件
- `./logs` - 处理日志
- `./state.db` - SQLite 状态数据库

## 全局管理

```bash
# 查看所有命令
make help

# 构建镜像
make build-webdav
make build-video

# 启动服务
make start-webdav
make start-video

# 查看日志
make logs-webdav
make logs-video
```

## 硬件建议

- **CPU**：视频压缩建议 4核+（H.265 吃 CPU）
- **内存**：8GB+（大文件处理）
- **磁盘**：SSD 可显著提升处理速度
- **网络**：上传工具建议夜间低峰期运行

## 更新日志

### v2.2
- 修复 concat 文件格式（与 Go 源项目一致）
- 添加 ffprobe 视频验证
- 修复目录删除问题（使用 shutil.rmtree）
- 添加 CPU/内存资源限制

### v2.1
- SQLite 状态持久化
- 信号处理优雅退出
- 资源清理优化

## 换行符说明

本项目使用 **Unix 换行符 (LF)**。在 Windows 上编辑后部署到 Linux/NAS 时，请确保换行符正确：

```bash
# 检查换行符
python check-line-endings.py

# 自动修复换行符
python check-line-endings.py --fix
```

或使用 Git 自动处理：
```bash
git config --global core.autocrlf input
```

项目已配置 `.gitattributes` 和 `.editorconfig` 来自动管理换行符。

## License

MIT - 自用随意，风险自负。
