#!/usr/bin/env python3
"""Start the self-contained dynamic dashboard from its VS Code project folder."""
import argparse
import getpass
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent


def validate_project(root=ROOT):
    required = ['live_server.py', 'refresh_pipeline.py', 'mcp_client.py',
                'report/index.html', 'report/config.js', 'data/report.json']
    absent = [name for name in required if not (root / name).is_file()]
    if absent:
        raise ValueError('缺少项目文件：' + '、'.join(absent) + '。请复制整个动态项目目录。')
    report = json.loads((root / 'data/report.json').read_text())
    generation = report.get('generation_id', '')
    if not generation or Path(generation).name != generation:
        raise ValueError('data/report.json 的数据版本无效。')
    source = root / 'generations' / generation
    files = ['data/quality.json', 'data/visits.json', 'data/events.json',
             'data/session_context.json', 'accounts/extraction_meta.json',
             'accounts/summary.json']
    absent = [name for name in files if not (source / name).is_file()]
    if absent:
        raise ValueError(f'缺少当前完整数据版本 generations/{generation}/。请将该目录一并复制，不能只复制 dist 或 report。')
    return report


def check_mcp():
    """Handshake and a single read-only query; never log or save the token."""
    endpoint = os.environ.get('CHAT2DB_MCP_URL', 'http://127.0.0.1:11924/mcp')
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('CHAT2DB_MCP_URL 必须是合法的 HTTP(S) 地址，不要把凭证放入URL。')
    if parsed.scheme == 'http' and parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('远程 MCP 请使用 HTTPS；当前默认连接同一台电脑上的 Chat2DB。')
    source = os.environ.get('CHAT2DB_DATASOURCE_ID', '202172')
    if not source.isdigit():
        raise ValueError('CHAT2DB_DATASOURCE_ID 必须是数字。')
    # Import after configuration so child refresh processes inherit identical settings.
    from mcp_client import MCPClient
    with tempfile.TemporaryDirectory(prefix='meigen-connect-') as tmp:
        with MCPClient(output_dir=tmp, timeout=12, retries=0, deadline_seconds=30, max_calls=6) as client:
            rows = client.query_rows('STARTUP-CHECK',
                "SELECT current_database() AS database_name, current_setting('TimeZone') AS timezone")
        if len(rows) != 1 or rows[0].get('database_name') != 'umami':
            raise ValueError('连接成功，但当前数据库不是 umami，请检查 Chat2DB 数据源。')
    return rows[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8876)
    parser.add_argument('--check', action='store_true', help='检查项目与MCP连接后退出')
    parser.add_argument('--cached-only', action='store_true', help='只查看已存报告，禁用数据库刷新')
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        candidates = [shutil.which(name) for name in ('python3.14','python3.13','python3.12','python3.11','python3.10')]
        candidates += [str(Path.home()/'.local/bin/python3'), '/opt/homebrew/bin/python3', '/usr/local/bin/python3']
        for candidate in candidates:
            if not candidate or not Path(candidate).exists() or Path(candidate).resolve() == Path(sys.executable).resolve():
                continue
            try:
                good = subprocess.run([candidate, '-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)'],
                                      capture_output=True, timeout=5).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                good = False
            if good:
                print('检测到系统Python较旧，切换到已安装的Python：' + candidate, flush=True)
                os.execv(candidate, [candidate, str(Path(__file__).resolve()), *sys.argv[1:]])
        parser.exit(1, '需要 Python 3.10 或更高版本；请使用已安装的新版Python运行 run.py。\n')
    if not 1 <= args.port <= 65535:
        parser.error('port必须在1–65535之间')
    try:
        if not (ROOT/'data/report.json').is_file() and (ROOT/'runtime/seed-data.zip').is_file():
            from restore_data import restore
            print('首次启动：正在校验并恢复随仓库提供的数据快照…', flush=True)
            restore(ROOT)
        report = validate_project()
        print(f"项目检查通过，当前数据截至 {report['window']['end']}", flush=True)
        if not args.check:
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1', args.port)) == 0:
                    raise ValueError(f'端口{args.port}已被占用。已有服务可直接打开 http://127.0.0.1:{args.port}/；另开服务用 python3 run.py --port {args.port+1}。')
        if args.cached_only:
            os.environ.pop('CHAT2DB_MCP_TOKEN', None)
        else:
            if not os.environ.get('CHAT2DB_MCP_TOKEN'):
                if not sys.stdin.isatty():
                    raise ValueError('请在VS Code的交互终端运行 python3 run.py，按提示输入MCP Token；或设置CHAT2DB_MCP_TOKEN环境变量。')
                token = getpass.getpass('请输入 Chat2DB MCP Token（输入不显示，回车确认）：').strip()
                if not token:
                    raise ValueError('没有输入Token。只看缓存可用 --cached-only。')
                os.environ['CHAT2DB_MCP_TOKEN'] = token
            print('正在检查 Chat2DB MCP 与 umami 数据库…', flush=True)
            info = check_mcp()
            print(f"MCP连接通过：umami.public；数据库时区 {info['timezone']}。", flush=True)
        if args.check:
            print('检查完成。可以启动动态服务。' if not args.cached_only else '缓存检查完成。')
            return
        if args.cached_only:
            print('当前仅查看已有报告；重新运行并提供Token后可刷新。', flush=True)
        else:
            print('动态服务已就绪：页面先显示最近成功数据，点击“刷新数据”才重新查库。', flush=True)
        print('请保留此终端；Ctrl+C 停止。前端修改保存后刷新浏览器，Python修改后重启服务。', flush=True)
        os.chdir(ROOT)
        os.execv(sys.executable, [sys.executable, str(ROOT / 'live_server.py'), '--port', str(args.port)])
    except KeyboardInterrupt:
        print('\n已取消启动。')
    except Exception as exc:
        if isinstance(exc, (ValueError, FileNotFoundError)):
            text = str(exc)
        else:
            text = f'{type(exc).__name__}：连接或项目检查失败。请确认Chat2DB正在运行、MCP已开启、Token和数据源ID正确。'
        parser.exit(1, text + '\n')


if __name__ == '__main__':
    main()
