# NAS 工具集 - 重构记录

## 重构概览

**版本**: v2.1 → v2.2  
**日期**: 2024年  
**重构目标**: 修复整合版与源项目的功能差异

## 引用的源项目

1. **xiaomi-camera-merge** (合并功能)
   - 地址: https://github.com/hslr-s/xiaomi-camera-merge
   - 语言: Go
   - 状态: 生产环境稳定运行

2. **xiaomi-compress** (压缩功能)
   - 地址: https://github.com/yang12535/xiaomi-compress
   - 语言: Shell
   - 状态: 生产环境稳定运行

## 修复问题清单

### 1. concat 文件格式错误 ⚠️ 关键

**问题描述:**
- 原整合版使用: `file '/path/to/video.mp4'` (带引号，全路径)
- Go 源项目使用: `file video.mp4` (无引号，相对路径)

**影响:**
- ffmpeg concat 在某些环境下解析失败
- 路径中包含特殊字符时出错

**修复方案:**
```python
# 修复前
escaped = str(v).replace("'", "'\\''")
f.write(f"file '{escaped}'\n")

# 修复后（与Go版本一致）
f.write(f"file {v.name}\n")  # 使用相对路径，无引号
```

**验证:** 在视频目录中执行 ffmpeg，与 Go 版本行为一致

---

### 2. 缺少 ffprobe 视频验证 ⚠️ 关键

**问题描述:**
- Shell 源项目在压缩后使用 ffprobe 验证视频有效性
- 原整合版只检查文件大小

**影响:**
- 可能产生损坏的视频文件但标记为成功
- 无法检测编码过程中的潜在错误

**修复方案:**
```python
def verify_video(self, video_path: Path) -> bool:
    """使用 ffprobe 验证视频文件有效性"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", str(video_path)],
            capture_output=True, text=True, timeout=60
        )
        return result.returncode == 0
    except Exception as e:
        self.log.warning(f"ffprobe 验证失败: {e}")
        return False
```

**新增配置:**
- `VERIFY_VIDEO` - 是否启用验证（默认 true）
- `MIN_OUTPUT_SIZE` - 最小输出文件大小（默认 1MB）

---

### 3. 目录删除问题 ⚠️ 中

**问题描述:**
- 原整合版使用 `hour_dir.rmdir()` 
- 该函数只能删除空目录

**影响:**
- 如果目录中残留文件（如 .concat.txt 未被删除），会删除失败
- 导致重复合并同一目录

**修复方案:**
```python
# 修复前
hour_dir.rmdir()

# 修复后（与Go版本的 RemoveAll 一致）
import shutil
shutil.rmtree(hour_dir)
```

---

### 4. 缺少资源限制 ⚠️ 中

**问题描述:**
- Shell 源项目在 docker-compose 中设置了 CPU/内存限制
- 原整合版没有资源限制

**影响:**
- 视频压缩可能占满所有 CPU/内存
- 影响 NAS 其他服务

**修复方案:**
```yaml
deploy:
  resources:
    limits:
      cpus: "8"
      memory: 4G
```

---

### 5. 缺少配置文件示例 ⚠️ 低

**问题描述:**
- README 中提到 `cp config.yaml.example config.yaml`
- 但文件不存在

**修复:**
- 创建了 `webdav-uploader/config.yaml.example`

---

## 新增功能

### 1. 更严格的输出验证
- 文件大小检查（> 1MB）
- ffprobe 视频格式验证
- 无效文件自动删除重试

### 2. 环境变量配置
- `VERIFY_VIDEO` - 启用/禁用视频验证
- `MIN_OUTPUT_SIZE` - 自定义最小文件大小

### 3. 资源限制
- docker-compose 中添加 CPU/内存限制
- 与源项目配置保持一致

## 测试建议

### 合并功能测试
```bash
# 1. 准备测试数据
mkdir -p merge/2024031210
cp test*.mp4 merge/2024031210/

# 2. 运行合并
cd xiaomi-video && docker-compose up

# 3. 验证输出
ls -la input/2024/03/12/10.mov

# 4. 验证 concat 文件格式
cat merge/2024031210/.concat.txt
# 应该显示: file test1.mp4 (无引号)
```

### 压缩功能测试
```bash
# 1. 运行压缩

# 2. 验证 ffprobe
ffprobe -v error -show_format input/2024/03/12/10.mov

# 3. 验证输出文件
ls -la output/2024/03/12/10.mkv
```

## 回滚方案

如需回滚到 v2.1:
```bash
git checkout v2.1 -- xiaomi-video/process.py
```

## 已知限制

1. **Windows 信号处理**: SIGTERM 在 Windows 上可能不完全支持
2. **SQLite 并发**: 不建议同时运行多个视频处理器实例
3. **ffprobe 依赖**: 需要镜像中包含 ffprobe（当前镜像已包含）

## 换行符处理

### 问题
Windows (CRLF) 和 Linux (LF) 换行符不一致可能导致脚本执行失败。

### 解决方案
1. **Git 配置**: 添加 `.gitattributes` 自动处理
2. **编辑器配置**: 添加 `.editorconfig` 规范编辑器行为
3. **检查脚本**: 添加 `check-line-endings.py` 部署前验证

### 使用
```bash
# 检查换行符
python check-line-endings.py

# 自动修复
python check-line-endings.py --fix
```

## 后续优化方向

1. 添加 prometheus 指标导出
2. Web UI 查看处理状态
3. 支持更多视频格式（当前仅支持小米摄像头格式）
4. 添加通知功能（合并/压缩完成通知）
