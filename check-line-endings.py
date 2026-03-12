#!/usr/bin/env python3
"""
换行符检查修复工具
用法: python check-line-endings.py [--fix]
"""

import os
import sys
import argparse

def check_and_fix_line_endings(file_path, fix=False):
    """检查并修复文件换行符为Unix格式"""
    try:
        with open(file_path, 'rb') as f:
            content = f.read()
        
        # 检查换行符
        crlf_count = content.count(b'\r\n')
        lf_count = content.count(b'\n') - crlf_count
        cr_count = content.count(b'\r') - crlf_count
        
        if crlf_count > 0:
            if fix:
                new_content = content.replace(b'\r\n', b'\n')
                with open(file_path, 'wb') as f:
                    f.write(new_content)
                print(f'[FIXED CRLF->LF] {file_path}')
            else:
                print(f'[CRLF] {file_path} - 需要修复')
            return True
        elif cr_count > 0:
            if fix:
                new_content = content.replace(b'\r', b'\n')
                with open(file_path, 'wb') as f:
                    f.write(new_content)
                print(f'[FIXED CR->LF] {file_path}')
            else:
                print(f'[CR] {file_path} - 需要修复')
            return True
        else:
            print(f'[OK] {file_path}')
            return False
    except Exception as e:
        print(f'[ERROR] {file_path}: {e}')
        return False

def main():
    parser = argparse.ArgumentParser(description='检查和修复换行符')
    parser.add_argument('--fix', action='store_true', help='自动修复换行符')
    args = parser.parse_args()
    
    # 文件列表
    files = [
        'webdav-uploader/uploader.py',
        'webdav-uploader/Dockerfile',
        'webdav-uploader/docker-compose.yml',
        'webdav-uploader/config.yaml.example',
        'xiaomi-video/process.py',
        'xiaomi-video/Dockerfile',
        'xiaomi-video/docker-compose.yml',
        'Makefile',
        'README.md',
        'REFACTOR.md',
        '.gitignore',
        '.gitattributes',
        '.editorconfig',
    ]
    
    total_files = 0
    files_needing_fix = 0
    
    print("检查换行符...")
    print("-" * 50)
    
    for f in files:
        if os.path.exists(f):
            total_files += 1
            if check_and_fix_line_endings(f, fix=args.fix):
                files_needing_fix += 1
        else:
            print(f'[MISSING] {f}')
    
    print("-" * 50)
    print(f"总计: {total_files} 个文件")
    
    if files_needing_fix == 0:
        print("所有文件换行符正常 (LF)")
        return 0
    else:
        if args.fix:
            print(f"已修复 {files_needing_fix} 个文件")
            return 0
        else:
            print(f"发现 {files_needing_fix} 个文件需要修复")
            print("运行: python check-line-endings.py --fix")
            return 1

if __name__ == "__main__":
    sys.exit(main())
