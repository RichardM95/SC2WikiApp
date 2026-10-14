"""Inventory opaque source assets without interpreting Catalog semantics."""
import hashlib
import json
from pathlib import Path

def file_sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()

def inventory(source_root,asset_manifest=None):
    root=Path(source_root).resolve();components={}
    for component in sorted(root.iterdir()):
        if not component.is_dir() or component.suffix.casefold()!='.sc2mod':continue
        if not component.resolve().is_relative_to(root):raise ValueError('Source component escapes root')
        files={}
        for path in sorted(component.rglob('*')):
            if not path.is_file():continue
            if not path.resolve().is_relative_to(component.resolve()):raise ValueError('Source file escapes component')
            files[path.relative_to(component).as_posix()]=file_sha(path)
        canonical=json.dumps(files,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf8')
        components['project:'+component.name]={'root':str(component.resolve()),'fingerprint':hashlib.sha256(canonical).hexdigest(),'files':files}
    assets={}
    if asset_manifest:
        data=json.loads(Path(asset_manifest).read_text(encoding='utf-8-sig'))
        for row in data.get('assets',[]):
            source=row.get('source');expected=row.get('source_sha256')
            if not source or not expected:continue
            path=Path(source).resolve()
            if not path.is_file() or file_sha(path)!=expected:raise ValueError('Exported asset source changed before snapshot: '+source)
            existing=assets.setdefault(str(path),{'sha256':expected,'consumers':[]})
            if existing['sha256']!=expected:raise ValueError('Conflicting asset source hash')
            consumer=row.get('consumer_component')
            if consumer not in existing['consumers']:existing['consumers'].append(consumer)
    return {'version':1,'source_root':str(root),'components':components,'exported_assets':assets}

def compare(previous,current):
    before=previous['components'];after=current['components']
    changed={key for key in before.keys()|after.keys() if before.get(key,{}).get('fingerprint')!=after.get(key,{}).get('fingerprint')}
    changed_assets=[]
    for source,row in previous.get('exported_assets',{}).items():
        path=Path(source)
        if not path.is_file() or file_sha(path)!=row['sha256']:
            changed_assets.append(source)
            for consumer in row.get('consumers',[]):
                if consumer:changed.add('project:'+Path(consumer).name)
                else:changed.add('*')
    return sorted(changed),changed_assets
