"""Verify and restore the bundled private runtime snapshot without overwriting data."""
import argparse,hashlib,json,os,shutil,stat,tempfile,zipfile
from pathlib import Path,PurePosixPath
ROOT=Path(__file__).resolve().parent

def restore(root=ROOT):
    root=Path(root).resolve();bundle=root/'runtime'
    metadata=json.loads((bundle/'manifest.json').read_text())
    archives=metadata.get('archives') or [{'name':'seed-data.zip','sha256':metadata['archive_sha256']}]
    for info in archives:
        if Path(info['name']).name!=info['name']:raise ValueError('Unsafe archive name')
        if hashlib.sha256((bundle/info['name']).read_bytes()).hexdigest()!=info['sha256']:
            raise ValueError('Runtime archive checksum mismatch')
    allowed={entry['path']:entry for entry in metadata['files']}
    for name in allowed:
        path=PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or path.parts[0] not in {'data','generations','snapshots'}:
            raise ValueError('Unsafe archive entry')
    restored=0;retained=0
    with tempfile.TemporaryDirectory(prefix='.seed-',dir=root) as staging:
        staging=Path(staging)
        for info in archives:
            expected={name for name,entry in allowed.items() if entry.get('archive','seed-data.zip')==info['name']}
            with zipfile.ZipFile(bundle/info['name']) as z:
                names=z.namelist()
                if len(names)!=len(set(names)) or set(names)!=expected:raise ValueError('Archive manifest differs')
                for item in z.infolist():
                    if stat.S_ISLNK(item.external_attr>>16):raise ValueError('Symlink entry rejected')
                    entry=allowed[item.filename]
                    if item.file_size!=entry['bytes']:raise ValueError('Archive size differs')
                    content=z.read(item)
                    if hashlib.sha256(content).hexdigest()!=entry['sha256']:raise ValueError('File checksum mismatch')
                    target=staging/item.filename;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
        for name in sorted(allowed,key=lambda name:name=='data/report.json'):
            target=root/name
            if target.exists():retained+=1;continue
            if not target.resolve().is_relative_to(root):raise ValueError('Destination escapes project')
            target.parent.mkdir(parents=True,exist_ok=True)
            os.replace(staging/name,target);restored+=1
    return {'restored_files':restored,'retained_files':retained,'generation':metadata['current_generation']}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=ROOT);a=p.parse_args()
    print(json.dumps(restore(a.root),ensure_ascii=False))
if __name__=='__main__':main()
