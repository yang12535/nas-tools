#!/usr/bin/env python3
"""
小米摄像头视频处理 - 稳定版 v2.1
修复：状态持久化、信号处理、资源清理
"""

import os
import sys
import json
import signal
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Tuple, Optional
from dataclasses import dataclass


# ============ 配置 ============

@dataclass
class Config:
    merge_dir: Path = Path("/app/video")
    input_dir: Path = Path("/input")
    output_dir: Path = Path("/output")
    delete_after_merge: bool = False
    max_merge: int = 0
    crf: int = 32
    preset: str = "medium"
    threads: int = 8
    resolution: str = "1920x1080"
    delete_after_compress: bool = True
    state_file: Path = Path("/app/state.db")  # 改为SQLite
    save_interval: int = 5  # 每N个文件保存一次状态


# ============ 日志（修复：持久化句柄） ============

import logging

class Logger:
    """修复：持久化文件句柄"""
    
    def __init__(self, log_dir: Path = Path("/logs")):
        log_dir.mkdir(parents=True, exist_ok=True)
        
        self.logger = logging.getLogger("video_processor")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers = []
        
        formatter = logging.Formatter(
            '[%(asctime)s] [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # 控制台
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        self.logger.addHandler(console)
        
        # 文件（持久化句柄）
        log_file = log_dir / f"process-{datetime.now():%Y%m%d_%H%M%S}.log"
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)
    
    def info(self, msg: str): self.logger.info(msg)
    def warning(self, msg: str): self.logger.warning(msg)
    def error(self, msg: str): self.logger.error(msg)


# ============ 状态管理（修复：SQLite + 定期保存） ============

class StateManager:
    """修复：SQLite存储，避免内存无限增长"""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_tables()
        self.pending_changes = 0
    
    def _init_tables(self):
        """初始化表结构"""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS merged_files (
                path TEXT PRIMARY KEY,
                size INTEGER,
                time TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS compressed_files (
                path TEXT PRIMARY KEY,
                input_size INTEGER,
                output_size INTEGER,
                time TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS failed_files (
                path TEXT,
                stage TEXT,
                error TEXT,
                time TEXT,
                PRIMARY KEY (path, stage)
            )
        """)
        self.conn.commit()
    
    def is_merged(self, path: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM merged_files WHERE path=?", (path,))
        return cur.fetchone() is not None
    
    def is_compressed(self, path: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM compressed_files WHERE path=?", (path,))
        return cur.fetchone() is not None
    
    def mark_merged(self, path: str, size: int, commit: bool = True):
        self.conn.execute(
            "INSERT OR REPLACE INTO merged_files VALUES (?, ?, ?)",
            (path, size, datetime.now().isoformat())
        )
        if commit:
            self.conn.commit()
    
    def mark_compressed(self, path: str, input_size: int, output_size: int, commit: bool = True):
        self.conn.execute(
            "INSERT OR REPLACE INTO compressed_files VALUES (?, ?, ?, ?)",
            (path, input_size, output_size, datetime.now().isoformat())
        )
        if commit:
            self.conn.commit()
    
    def mark_failed(self, path: str, stage: str, error: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO failed_files VALUES (?, ?, ?, ?)",
            (path, stage, str(error)[:500], datetime.now().isoformat())
        )
        self.conn.commit()
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        cur = self.conn.execute("SELECT COUNT(*) FROM merged_files")
        merged = cur.fetchone()[0]
        cur = self.conn.execute("SELECT COUNT(*) FROM compressed_files")
        compressed = cur.fetchone()[0]
        cur = self.conn.execute("SELECT COUNT(*) FROM failed_files")
        failed = cur.fetchone()[0]
        return {"merged": merged, "compressed": compressed, "failed": failed}
    
    def cleanup_old(self, days: int = 90):
        """清理N天前的记录"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        self.conn.execute("DELETE FROM merged_files WHERE time < ?", (cutoff,))
        self.conn.execute("DELETE FROM compressed_files WHERE time < ?", (cutoff,))
        self.conn.commit()
    
    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# ============ 视频处理 ============

class VideoProcessor:
    def __init__(self, config: Config, logger: Logger, state: StateManager):
        self.cfg = config
        self.log = logger
        self.state = state
        self.width, self.height = map(int, config.resolution.split('x'))
        self.running = True
        self.processed_since_save = 0
        
        # 清理临时文件
        self._cleanup_temp_files()
    
    def stop(self):
        """优雅停止"""
        self.running = False
        self.log.info("收到停止信号，当前任务完成后退出...")
    
    def _cleanup_temp_files(self):
        """修复：启动时清理残留的临时文件"""
        cleaned = 0
        for tmp in self.cfg.output_dir.rglob("*.tmp.mkv"):
            try:
                tmp.unlink()
                cleaned += 1
            except:
                pass
        if cleaned > 0:
            self.log.info(f"清理了 {cleaned} 个临时文件")
    
    def run_ffmpeg(self, cmd: List[str], timeout: int = 7200) -> Tuple[bool, str]:
        """运行ffmpeg命令"""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            if result.returncode == 0:
                return True, ""
            return False, result.stderr[:500]  # 限制错误信息长度
        except subprocess.TimeoutExpired:
            return False, "timeout"
        except Exception as e:
            return False, str(e)[:500]
    
    def get_video_files(self, directory: Path, pattern: str = "*.mp4") -> List[Path]:
        if not directory.exists():
            return []
        return sorted(directory.glob(pattern))
    
    def _maybe_commit(self, force: bool = False):
        """修复：定期保存状态"""
        self.processed_since_save += 1
        if force or self.processed_since_save >= self.cfg.save_interval:
            self.state.conn.commit()
            self.processed_since_save = 0
    
    def merge_hourly_videos(self, hour_dir: Path) -> Optional[Path]:
        """合并一个小时的视频文件"""
        if not self.running:
            return None
        
        videos = self.get_video_files(hour_dir, "*.mp4")
        if not videos:
            return None
        
        dirname = hour_dir.name
        if len(dirname) != 10 or not dirname.isdigit():
            self.log.warning(f"跳过非标准目录: {hour_dir}")
            return None
        
        year, month, day, hour = dirname[:4], dirname[4:6], dirname[6:8], dirname[8:10]
        
        output_dir = self.cfg.input_dir / year / month / day
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{hour}.mov"
        
        # 检查是否已处理
        if self.state.is_merged(str(output_file)):
            self.log.info(f"已合并，跳过: {output_file}")
            return output_file
        
        # 创建concat列表
        concat_file = hour_dir / ".concat.txt"
        try:
            with open(concat_file, 'w') as f:
                for v in videos:
                    escaped = str(v).replace("'", "'\\''")
                    f.write(f"file '{escaped}'\n")
            
            self.log.info(f"合并 {len(videos)} 个文件到 {output_file}")
            
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-v", "error",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_file),
                "-c", "copy",
                "-movflags", "+faststart",
                str(output_file)
            ]
            
            success, error = self.run_ffmpeg(cmd, timeout=3600)
            
            if success and output_file.exists() and output_file.stat().st_size > 1024:
                output_size = output_file.stat().st_size
                self.state.mark_merged(str(output_file), output_size, commit=False)
                self._maybe_commit()
                
                # 删除源文件
                if self.cfg.delete_after_merge:
                    for v in videos:
                        v.unlink(missing_ok=True)
                    hour_dir.rmdir()  # 目录应该空了
                
                return output_file
            else:
                self.log.error(f"合并失败: {error}")
                self.state.mark_failed(str(output_file), "merge", error)
                if output_file.exists():
                    output_file.unlink()
                return None
                
        finally:
            # 确保清理concat文件
            concat_file.unlink(missing_ok=True)
    
    def compress_video(self, mov_file: Path) -> Optional[Path]:
        """压缩 MOV 到 MKV"""
        if not self.running:
            return None
        
        rel_path = mov_file.relative_to(self.cfg.input_dir)
        output_file = self.cfg.output_dir / rel_path.with_suffix(".mkv")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 检查是否已处理
        if self.state.is_compressed(str(output_file)):
            self.log.info(f"已压缩，跳过: {output_file}")
            return output_file
        
        # 检查输出是否已存在且有效
        if output_file.exists() and output_file.stat().st_size > 1048576:
            self.log.info(f"输出已存在，跳过: {output_file}")
            self.state.mark_compressed(str(output_file), mov_file.stat().st_size, output_file.stat().st_size)
            return output_file
        
        input_size = mov_file.stat().st_size
        self.log.info(f"压缩 {mov_file.name} ({input_size/1024/1024:.1f}MB)")
        
        temp_file = output_file.with_suffix(".tmp.mkv")
        
        try:
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-v", "error",
                "-i", str(mov_file),
                "-vf", f"scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2",
                "-c:v", "libx265",
                "-preset", self.cfg.preset,
                "-crf", str(self.cfg.crf),
                "-threads", str(self.cfg.threads),
                "-c:a", "copy",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                str(temp_file)
            ]
            
            success, error = self.run_ffmpeg(cmd, timeout=7200)
            
            if success and temp_file.exists():
                output_size = temp_file.stat().st_size
                if output_size > 1048576:
                    temp_file.rename(output_file)
                    self.state.mark_compressed(str(output_file), input_size, output_size, commit=False)
                    self._maybe_commit()
                    
                    ratio = (1 - output_size / input_size) * 100
                    self.log.info(f"压缩完成: {output_file.name} (节省{ratio:.1f}%)")
                    
                    if self.cfg.delete_after_compress:
                        mov_file.unlink()
                    
                    return output_file
                else:
                    self.log.error(f"输出文件太小: {output_size} bytes")
                    temp_file.unlink(missing_ok=True)
                    self.state.mark_failed(str(mov_file), "compress", "output too small")
            else:
                self.log.error(f"压缩失败: {error}")
                self.state.mark_failed(str(mov_file), "compress", error)
                temp_file.unlink(missing_ok=True)
                
        except Exception as e:
            self.log.error(f"压缩异常: {e}")
            self.state.mark_failed(str(mov_file), "compress", str(e))
            temp_file.unlink(missing_ok=True)
        
        return None
    
    def run(self) -> bool:
        """主流程"""
        self.log.info("=" * 50)
        self.log.info("小米摄像头视频处理工具 v2.1")
        self.log.info(f"配置: CRF={self.cfg.crf}, 分辨率={self.cfg.resolution}")
        self.log.info("=" * 50)
        
        # 确保目录存在
        for d in [self.cfg.merge_dir, self.cfg.input_dir, self.cfg.output_dir]:
            d.mkdir(parents=True, exist_ok=True)
        
        stats = {"merged": 0, "compressed": 0, "failed": 0}
        
        # 合并
        self.log.info("\n【步骤1】合并视频...")
        hour_dirs = [d for d in self.cfg.merge_dir.iterdir() 
                     if d.is_dir() and len(d.name) == 10 and d.name.isdigit()]
        
        self.log.info(f"发现 {len(hour_dirs)} 个待合并目录")
        
        for hour_dir in hour_dirs:
            if not self.running:
                break
            if self.cfg.max_merge > 0 and stats["merged"] >= self.cfg.max_merge:
                self.log.info(f"达到最大合并数限制: {self.cfg.max_merge}")
                break
            
            result = self.merge_hourly_videos(hour_dir)
            if result:
                stats["merged"] += 1
        
        # 强制提交状态
        self._maybe_commit(force=True)
        
        # 压缩
        self.log.info("\n【步骤2】压缩视频...")
        mov_files = list(self.cfg.input_dir.rglob("*.mov"))
        self.log.info(f"发现 {len(mov_files)} 个待压缩文件")
        
        for mov_file in mov_files:
            if not self.running:
                break
            
            result = self.compress_video(mov_file)
            if result:
                stats["compressed"] += 1
            else:
                stats["failed"] += 1
        
        # 强制提交状态
        self._maybe_commit(force=True)
        
        # 统计
        db_stats = self.state.get_stats()
        self.log.info("\n" + "=" * 50)
        self.log.info("处理完成!")
        self.log.info(f"本轮 - 合并: {stats['merged']}, 压缩: {stats['compressed']}, 失败: {stats['failed']}")
        self.log.info(f"累计 - 合并: {db_stats['merged']}, 压缩: {db_stats['compressed']}, 失败: {db_stats['failed']}")
        self.log.info("=" * 50)
        
        return stats["failed"] == 0


def main():
    cfg = Config(
        merge_dir=Path(os.getenv("MERGE_DIR", "/app/video")),
        input_dir=Path(os.getenv("INPUT_DIR", "/input")),
        output_dir=Path(os.getenv("OUTPUT_DIR", "/output")),
        delete_after_merge=os.getenv("DELETE_AFTER_MERGE", "false").lower() == "true",
        max_merge=int(os.getenv("MAX_MERGE", "0")),
        crf=int(os.getenv("COMPRESS_CRF", "32")),
        preset=os.getenv("COMPRESS_PRESET", "medium"),
        threads=int(os.getenv("COMPRESS_THREADS", "8")),
        resolution=os.getenv("COMPRESS_RESOLUTION", "1920x1080"),
        delete_after_compress=os.getenv("DELETE_AFTER_COMPRESS", "true").lower() == "true",
        state_file=Path(os.getenv("STATE_FILE", "/app/state.db")),
        save_interval=int(os.getenv("SAVE_INTERVAL", "5")),
    )
    
    logger = Logger()
    
    with StateManager(cfg.state_file) as state:
        processor = VideoProcessor(cfg, logger, state)
        
        # 信号处理
        def on_signal(signum, frame):
            processor.stop()
        
        signal.signal(signal.SIGINT, on_signal)
        signal.signal(signal.SIGTERM, on_signal)
        
        try:
            success = processor.run()
            sys.exit(0 if success else 1)
        except KeyboardInterrupt:
            logger.info("用户中断")
            sys.exit(130)
        except Exception as e:
            logger.error(f"程序异常: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
