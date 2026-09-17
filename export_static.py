"""Create a deployable read-only snapshot; no MCP credentials or raw datasets."""
import argparse,json,shutil,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent
ALLOWED={'.html','.css','.js','.svg','.woff2','.png','.webp'}
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'dist/meigen-dashboard');args=parser.parse_args();dest=args.output.resolve()
 dest.mkdir(parents=True,exist_ok=True)
 for file in (ROOT/'report').iterdir():
  if file.is_file() and file.suffix in ALLOWED:shutil.copy2(file,dest/file.name)
 (dest/'config.js').write_text('window.MEIGEN_CONFIG = {mode:"snapshot",reportUrl:"./data/report.json"};\n')
 (dest/'data').mkdir(exist_ok=True);report=json.loads((ROOT/'data/report.json').read_text())
 text=json.dumps(report,ensure_ascii=False,separators=(',',':'))
 for forbidden in ['"account_id"','"visit_id"','"session_id"','X-Chat2DB-MCP-Token']:
  if forbidden in text:raise ValueError('report_contains_internal_data')
 (dest/'data/report.json').write_text(text)
 (dest/'README.txt').write_text(f"MeiGen Dashboard 静态发布包\n数据截至：{report['window']['end']}\n将本目录内容上传到静态网站托管服务。入口index.html，所有资源使用相对路径。\n本版本保留筛选和交互，不连接数据库、不自动更新。更新数据后重新运行export_static.py并重新发布即可。\n本地预览：python3 -m http.server 8877 --directory {dest}\n")
 zip_path=dest.with_suffix('.zip')
 with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as z:
  for file in sorted(dest.rglob('*')):
   if file.is_file():z.write(file,file.relative_to(dest))
 print(json.dumps({'folder':str(dest),'zip':str(zip_path),'cutoff':report['window']['end']},ensure_ascii=False))
if __name__=='__main__':main()
