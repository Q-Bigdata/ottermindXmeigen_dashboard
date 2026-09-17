"""Package current runtime and the reviewed baseline for a private repository."""
import hashlib,json,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def main():
    report=json.loads((ROOT/'data/report.json').read_text())
    current=report['generation_id']
    reviewed='20260916T071000Z-d19bc8ca'
    files=[ROOT/'data/report.json']
    for generation in dict.fromkeys([current,reviewed]):
        base=ROOT/'generations'/generation
        if not base.exists():raise ValueError('Required generation is missing: '+generation)
        files += [base/'report.json']
        for folder in ('data','accounts'):
            files += sorted((base/folder).glob('*.json'))
    files += sorted((ROOT/'snapshots/reviewed-20260916-1510').glob('*.json'))
    output=ROOT/'runtime';output.mkdir(exist_ok=True)
    entries=[];archives=[]
    groups={'seed-data.zip':[], 'reviewed-evidence.zip':[]}
    for p in files:
        groups['reviewed-evidence.zip' if '/'+reviewed+'/' in p.as_posix() or 'snapshots' in p.parts else 'seed-data.zip'].append(p)
    for archive_name,group in groups.items():
        with zipfile.ZipFile(output/archive_name,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for p in group:
                content=p.read_bytes();name=p.relative_to(ROOT).as_posix()
                archive.writestr(name,content)
                entries.append({'path':name,'archive':archive_name,'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()})
        digest=hashlib.sha256((output/archive_name).read_bytes()).hexdigest()
        archives.append({'name':archive_name,'sha256':digest,'bytes':(output/archive_name).stat().st_size})
    manifest={'schema_version':2,'visibility':'private','current_generation':current,'cutoff':report['window']['end'],
              'archives':archives,'uncompressed_bytes':sum(e['bytes'] for e in entries),'files':entries}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    (output/'SHA256SUMS').write_text(''.join(a['sha256']+'  '+a['name']+'\n' for a in archives))
    print(json.dumps({'archives':archives,'files':len(entries),'uncompressed_bytes':manifest['uncompressed_bytes'],'current':current}))
if __name__=='__main__':main()
