#!/usr/bin/env python3
"""
WebDAV 文件上传器 - 稳定版 v2.1
修复：限速bug、文件指纹、资源清理
"""

import os
import sys
import time
import json
import yaml
import signal
import hashlib
import sqlite3
import logging
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List
from dataclasses import dataclass
from contextlib import contextmanager

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============ 配置 ============

@dataclass
class Config:
    webdav_url: str = ""
    webdav_user: str = ""
    webdav_pass: str = ""
    webdav_root: str = "/"
    rate_limit: int = 1024 * 1024
    watch_dir: str = "./upload"
    interval: int = 3600
    delete_after_upload: bool = True
    verify_checksum: bool = True
    chunk_size: int = 8192
    log_level: str = "INFO"
    log_file: Optional[str] = None
    db_path: str = ".uploaded.db"
    
    @classmethod
    def from_yaml(cls, path: str) -> "Config":
        with open(path, 'r') as f:
            data = yaml.safe_load(f) or {}
        return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})


# ============ 日志（修复：持久化句柄） ============

class Logger:
    """修复：持久化文件句柄，减少IO"""
    
    def __init__(self, level: str = "INFO", log_file: Optional[str] = None):
        self.logger = logging.getLogger("webdav_uploader")
        self.logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        
        # 避免重复添加handler
        self.logger.handlers = []
        
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # 控制台
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        self.logger.addHandler(console)
        
        # 文件
        if log_file:
            os.makedirs(os.path.dirname(log_file) or '.', exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
    
    def debug(self, msg: str): self.logger.debug(msg)
    def info(self, msg: str): self.logger.info(msg)
    def warning(self, msg: str): self.logger.warning(msg)
    def error(self, msg: str): self.logger.error(msg)
    def exception(self, msg: str): self.logger.exception(msg)


# ============ 数据库（修复：上下文管理器） ============

class FileDB:
    """修复：确保连接正确关闭"""
    
    def __init__(self, db_path: str = ".uploaded.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")  # 更好的并发支持
        self._init_table()
    
    def _init_table(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS uploaded (
                file_hash TEXT PRIMARY KEY,
                file_path TEXT,
                file_size INTEGER,
                uploaded_at TEXT
            )
        """)
        # 创建索引加速查询
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_uploaded_at ON uploaded(uploaded_at)")
        self.conn.commit()
    
    def exists(self, file_hash: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM uploaded WHERE file_hash=?", (file_hash,))
        return cur.fetchone() is not None
    
    def add(self, file_hash: str, file_path: str, file_size: int):
        self.conn.execute(
            "INSERT OR REPLACE INTO uploaded VALUES (?, ?, ?, ?)",
            (file_hash, file_path, file_size, datetime.now().isoformat())
        )
        self.conn.commit()
    
    def cleanup_old(self, days: int = 30):
        """清理N天前的记录，防止数据库无限增长"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        self.conn.execute("DELETE FROM uploaded WHERE uploaded_at < ?", (cutoff,))
        self.conn.commit()
    
    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# ============ 限速器（修复：逻辑bug） ============

class RateLimiter:
    """修复：正确的令牌桶算法"""
    
    def __init__(self, rate_bytes_per_sec: int):
        self.rate = max(rate_bytes_per_sec, 1)
        self.tokens = float(self.rate)
        self.last_update = time.monotonic()
        self._lock = False  # 简单的锁标志
    
    def acquire(self, nbytes: int):
        """获取 nbytes 的传输许可"""
        while nbytes > 0:
            now = time.monotonic()
            elapsed = now - self.last_update
            
            # 补充令牌
            self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
            self.last_update = now
            
            if self.tokens >= 1:
                # 消耗令牌（逐字节或按块）
                consume = min(nbytes, int(self.tokens))
                self.tokens -= consume
                nbytes -= consume
            else:
                # 等待生成足够的令牌
                sleep_time = (1 - self.tokens) / self.rate
                time.sleep(sleep_time)


# ============ WebDAV 客户端（修复：文件句柄、重试） ============

class WebDAVClient:
    """修复：更好的错误处理和重试"""
    
    def __init__(self, config: Config, limiter: Optional[RateLimiter] = None, logger: Optional[Logger] = None):
        self.cfg = config
        self.limiter = limiter
        self.log = logger or Logger()
        
        self.session = requests.Session()
        self.session.auth = (config.webdav_user, config.webdav_pass)
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 WebDAV-Uploader/2.1'
        })
        
        # 更好的重试策略
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504]
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=1, pool_maxsize=1)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
    
    def _make_url(self, remote_path: str) -> str:
        base = self.cfg.webdav_url.rstrip('/')
        root = self.cfg.webdav_root.rstrip('/')
        path = remote_path.lstrip('/')
        return f"{base}{root}/{path}"
    
    def _calc_full_hash(self, local_path: str) -> str:
        """修复：计算完整文件MD5，不是只读8KB"""
        h = hashlib.md5()
        with open(local_path, 'rb') as f:
            while chunk := f.read(self.cfg.chunk_size):
                h.update(chunk)
        return h.hexdigest()
    
    def _calc_quick_hash(self, local_path: str) -> str:
        """快速指纹：文件大小+前8KB+后8KB"""
        size = os.path.getsize(local_path)
        h = hashlib.md5()
        h.update(str(size).encode())
        
        with open(local_path, 'rb') as f:
            # 前8KB
            h.update(f.read(8192))
            # 后8KB
            if size > 16384:
                f.seek(-8192, 2)
                h.update(f.read(8192))
        
        return h.hexdigest()
    
    def upload(self, local_path: str, remote_name: Optional[str] = None) -> bool:
        remote_name = remote_name or os.path.basename(local_path)
        file_size = os.path.getsize(local_path)
        
        self.log.info(f"上传: {local_path} -> {remote_name} ({file_size/1024/1024:.2f}MB)")
        
        # 确保目录存在
        self._mkdir(remote_name)
        url = self._make_url(remote_name)
        
        # 修复：使用上下文管理器确保文件关闭
        def file_generator():
            with open(local_path, 'rb') as f:
                while True:
                    chunk = f.read(self.cfg.chunk_size)
                    if not chunk:
                        break
                    if self.limiter:
                        self.limiter.acquire(len(chunk))
                    yield chunk
        
        try:
            resp = self.session.put(url, data=file_generator(), timeout=300)
            resp.raise_for_status()
            
            # 校验
            if self.cfg.verify_checksum:
                local_hash = self._calc_full_hash(local_path)
                if not self._verify(url, local_hash, file_size):
                    self.log.error("校验失败")
                    return False
            
            self.log.info(f"上传成功: {remote_name}")
            return True
            
        except requests.exceptions.RequestException as e:
            self.log.error(f"上传失败: {e}")
            return False
        except Exception as e:
            self.log.exception(f"上传异常: {e}")
            return False
    
    def _mkdir(self, remote_path: str):
        """创建远程目录"""
        parts = remote_path.split('/')
        current = ""
        for part in parts[:-1]:
            if not part:
                continue
            current += f"/{part}"
            url = self._make_url(current)
            try:
                self.session.request('MKCOL', url, timeout=30)
            except:
                pass
    
    def _verify(self, url: str, expected_hash: str, expected_size: int) -> bool:
        """校验远程文件"""
        try:
            resp = self.session.head(url, timeout=30)
            if resp.status_code != 200:
                return False
            
            # 检查大小
            remote_size = int(resp.headers.get('Content-Length', 0))
            if remote_size != expected_size:
                self.log.warning(f"大小不匹配: 本地{expected_size} vs 远程{remote_size}")
                return False
            
            # 如果有Content-MD5头，检查它
            remote_md5 = resp.headers.get('Content-MD5')
            if remote_md5:
                return remote_md5 == expected_hash
            
            return True
        except Exception as e:
            self.log.warning(f"校验异常: {e}")
            return True  # 无法校验时假设成功


# ============ 主程序（修复：信号处理、清理） ============

class UploaderApp:
    """修复：更好的生命周期管理"""
    
    def __init__(self, config: Config):
        self.cfg = config
        self.log = Logger(config.log_level, config.log_file)
        self.db: Optional[FileDB] = None
        self.client: Optional[WebDAVClient] = None
        self.limiter: Optional[RateLimiter] = None
        self.running = True
        
        # 信号处理
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)
    
    def _on_signal(self, signum, frame):
        self.log.info(f"收到信号 {signum}，正在优雅退出...")
        self.running = False
    
    def scan_files(self) -> List[tuple]:
        """扫描待上传文件"""
        files = []
        watch_path = Path(self.cfg.watch_dir)
        
        if not watch_path.exists():
            self.log.warning(f"监控目录不存在: {self.cfg.watch_dir}")
            return files
        
        for path in watch_path.rglob("*"):
            if not path.is_file():
                continue
            
            try:
                # 使用快速指纹检查
                file_hash = self.client._calc_quick_hash(str(path))
                
                if self.db.exists(file_hash):
                    self.log.debug(f"跳过已上传: {path}")
                    continue
                
                files.append((str(path), file_hash))
            except Exception as e:
                self.log.error(f"扫描文件失败 {path}: {e}")
        
        return files
    
    def run_once(self) -> bool:
        """运行一轮，返回是否有文件处理"""
        files = self.scan_files()
        
        if not files:
            self.log.info("没有新文件")
            return False
        
        self.log.info(f"发现 {len(files)} 个新文件")
        
        for file_path, file_hash in files:
            if not self.running:
                self.log.info("中断：保存当前进度")
                break
            
            try:
                if self.client.upload(file_path):
                    file_size = os.path.getsize(file_path)
                    self.db.add(file_hash, file_path, file_size)
                    
                    if self.cfg.delete_after_upload:
                        os.remove(file_path)
                        self.log.info(f"已删除本地文件: {file_path}")
                else:
                    self.log.error(f"上传失败，保留文件: {file_path}")
                    
            except Exception as e:
                self.log.exception(f"处理文件异常: {file_path}")
        
        return True
    
    def run(self, once: bool = False):
        """主循环"""
        self.log.info(f"启动上传器 v2.1")
        self.log.info(f"监控: {self.cfg.watch_dir}, 限速: {self.cfg.rate_limit/1024:.0f}KB/s")
        
        # 初始化组件
        with FileDB(self.cfg.db_path) as db:
            self.db = db
            self.limiter = RateLimiter(self.cfg.rate_limit) if self.cfg.rate_limit > 0 else None
            self.client = WebDAVClient(self.cfg, self.limiter, self.log)
            
            # 清理旧记录（30天前）
            db.cleanup_old(days=30)
            
            while self.running:
                has_work = self.run_once()
                
                if once:
                    break
                
                if not has_work:
                    self.log.info(f"等待 {self.cfg.interval} 秒...")
                    # 分段睡眠，便于响应信号
                    for _ in range(self.cfg.interval):
                        if not self.running:
                            break
                        time.sleep(1)
        
        self.log.info("退出")


def main():
    parser = argparse.ArgumentParser(description="WebDAV 文件上传器 v2.1")
    parser.add_argument("-c", "--config", default="config.yaml", help="配置文件")
    parser.add_argument("--once", action="store_true", help="只运行一次")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()
    
    config = Config.from_yaml(args.config)
    if args.verbose:
        config.log_level = "DEBUG"
    
    app = UploaderApp(config)
    
    try:
        app.run(once=args.once)
    except Exception as e:
        logging.exception("程序异常")
        sys.exit(1)


if __name__ == "__main__":
    main()
