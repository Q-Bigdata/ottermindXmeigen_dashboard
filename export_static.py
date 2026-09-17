"""Create a deployable read-only snapshot; no MCP credentials or raw datasets."""
import argparse,json,re,shutil,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent
ALLOWED={'.html','.css','.js','.svg','.woff2','.png','.webp'}

BUNDLE_ORDER=['refresh.js','data.js','charts.js','breakpoints.js','strategy.js','deep_panels.js','insights.js','app.js']
MODULE_EXPORTS={
 'refresh.js':['createRefreshController'],
 'data.js':['escapeHTML','formatNumber','formatRate','formatSeconds','NODE_NAMES','DEVICES','BROWSERS','SOURCES','createModel'],
 'charts.js':['escapeHtml','number','percent','shortNumber','chartBars','trendChart','compareBars','sparkline','lineOrBars'],
 'breakpoints.js':['COUNTRY_NAMES','dimensionLabel','renderBreakpointMap'],
 'strategy.js':['renderStrategy'],
 'deep_panels.js':['renderDeepDiagnosis','renderDeepRetention','renderDeepCommercial'],
 'insights.js':['renderDiagnosis','renderRetention','renderCommercial','renderOpportunities'],
}
MODULE_ALIASES={
 'breakpoints.js':{'e':'escapeHTML','n':'formatNumber','pct':'formatRate'},
 'strategy.js':{'e':'escapeHTML','n':'formatNumber','pct':'formatRate','sec':'formatSeconds'},
 'deep_panels.js':{'e':'escapeHTML','n':'formatNumber','pct':'formatRate','seconds':'formatSeconds'},
 'app.js':{'e':'escapeHTML','num':'formatNumber','pct':'formatRate','secs':'formatSeconds'},
}
def build_single_html(dest, report):
    source=ROOT/'report'
    html=(source/'index.html').read_text()
    styles='\n'.join((source/name).read_text() for name in ('styles.css','deep_panels.css'))
    scripts=[]
    for name in BUNDLE_ORDER[:-1]:
        text=(source/name).read_text()
        text=re.sub(r'^\s*import .*?;\s*$', '', text, flags=re.M)
        text=text.replace('export ', '')
        exports=', '.join(f'{x}:{x}' for x in MODULE_EXPORTS[name])
        aliases='\n'.join(f'const {alias}=globalThis.{source};' for alias,source in MODULE_ALIASES.get(name,{}).items())
        scripts.append(f'// --- {name} ---\n(function(){{\n{aliases}\n{text}\nObject.assign(globalThis,{{{exports}}});\n}})();')
    app=(source/'app.js').read_text()
    app=re.sub(r'^\s*import .*?;\s*$', '', app, flags=re.M).replace('export ', '')
    aliases='\n'.join(f'const {alias}=globalThis.{source};' for alias,source in MODULE_ALIASES['app.js'].items())
    scripts.append('// --- app.js ---\n'+aliases+'\n'+app)
    bundle='\n'.join(scripts)
    payload=json.dumps(report,ensure_ascii=False,separators=(',',':')).replace('</script','<\\/script')
    favicon='<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\'%3E%3Ccircle cx=\'12\' cy=\'12\' r=\'10\' fill=\'%233b82f6\'/%3E%3C/svg%3E">'
    html=html.replace('<link rel="icon" href="./icon.svg" type="image/svg+xml"><link rel="stylesheet" href="./styles.css"><link rel="stylesheet" href="./deep_panels.css">', f'{favicon}<style>{styles}</style>')
    html=html.replace('<script src="./config.js"></script>\n<script type="module" src="./app.js"></script>', f'<script>window.MEIGEN_CONFIG={{mode:"snapshot",reportData:{payload}}};</script>\n<script>{bundle}</script>')
    out=dest.parent/'meigen-dashboard.html'
    out.write_text(html)
    return out
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
 (dest/'README.txt').write_text(f"MeiGen Dashboard 静态发布包\n数据截至：{report['window']['end']}\n目录版入口 index.html；单文件版 ../meigen-dashboard.html。两者都固定本次数据并保留筛选与交互，不连接数据库。\n目录版预览：python3 -m http.server 8877 --directory {dest}\n")
 single=build_single_html(dest,report)
 zip_path=dest.with_suffix('.zip')
 with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as z:
  for file in sorted(dest.rglob('*')):
   if file.is_file():z.write(file,file.relative_to(dest))
 print(json.dumps({'folder':str(dest),'zip':str(zip_path),'html':str(single),'cutoff':report['window']['end']},ensure_ascii=False))
if __name__=='__main__':main()
