"""Check final projection fidelity, assets, local links and lazy data budget."""
import argparse, collections, hashlib, json, re, sys
from pathlib import Path
from urllib.parse import urlsplit,unquote

def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('project-dir','site-root','batch-suffix','report'):p.add_argument('--'+n,required=True)
    a=p.parse_args();project=Path(a.project_dir).resolve();site=Path(a.site_root).resolve();report=Path(a.report).resolve()
    if report.exists() or not report.is_relative_to(project/'runtime/reports'):raise ValueError('Fresh project report required')
    manifest=json.loads((site/'manifest.json').read_text(encoding='utf-8'));index=json.loads((site/'data/index.json').read_text(encoding='utf-8'))['entries']
    errors=[];counts=collections.Counter();asset_hashes={};asset=json.loads((site/'assets/manifest.json').read_text(encoding='utf-8'))
    for item in asset['assets']:
        if item.get('output'):
            output=site/item['output'];counts['resolved_asset_records']+=1
            if not output.resolve().is_relative_to(site) or sha(output)!=item['output_sha256']:errors.append('Asset output hash differs '+item['output'])
            source=item.get('source')
            if source and Path(source).is_file() and item.get('source_sha256'):
                if source not in asset_hashes:asset_hashes[source]=sha(Path(source))
                if asset_hashes[source]!=item['source_sha256']:errors.append('Asset source hash differs '+source)
                counts['asset_source_hash_checks']+=1
        else:counts['missing_asset_records']+=1
    for file in sorted((project/'wiki-data').glob('*-'+a.batch_suffix+'/data.json')):
        faction=file.parent.name[:-(len(a.batch_suffix)+1)];data=json.loads(file.read_text(encoding='utf-8'))
        for entry in data['entries']:
            uid=entry['id'].split(':',1)[1];path=site/'data/entries'/faction/(uid+'.json')
            if not path.exists():continue
            player=json.loads(path.read_text(encoding='utf-8'));counts['visible_entries']+=1
            counts['missing_unit_icons']+=player.get('icon') is None;counts['missing_portraits']+=player.get('portrait') is None
            text=path.read_text(encoding='utf-8')
            for token in ('"raw_template":','"dynamic_text":','"raw_fields":','"execution_graph":'):
                if token in text:errors.append('Raw source_audit data exported '+str(path)+token)
            for original,projected in zip(entry.get('weapons',[]),player.get('weapons',[])):
                raw=original['damage'].get('value') or original['damage'].get('raw_value')
                value=projected['damage'].get('value') or projected['damage'].get('partial')
                if isinstance(raw,dict) and raw.get('components'):
                    counts['weapon_damage_structures']+=1
                    if not isinstance(value,dict):errors.append('Damage structure lost '+uid);continue
                    parts=value.get('components',[])
                    if len(parts)!=len(raw['components']):errors.append('Damage segment count differs '+uid);continue
                    for before,after in zip(raw['components'],parts):
                        counts['weapon_damage_segments']+=1
                        for field in ('amount','count','armor_reduction','bonus'):
                            if before.get(field)!=after.get(field):errors.append('Damage value differs '+uid+'/'+field)
            if any(x.get('catalog_type',x.get('type')) in ('CAbilMove','CAbilStop','CAbilAttack','CAbilRally','CAbilQueue') for x in player.get('abilities',[])):errors.append('Basic engine ability exported '+uid)
    for file in site.rglob('*.html'):
        content=file.read_text(encoding='utf-8')
        for value in re.findall(r'(?:src|href)=["\']([^"\']+)',content):
            split=urlsplit(value)
            if split.scheme or split.netloc:
                if split.scheme in ('http','https') and re.search(r'(?:src=|stylesheet)',content[max(0,content.find(value)-70):content.find(value)]):errors.append('Remote runtime resource '+value)
                continue
            if split.path:
                target=(site/unquote(split.path.lstrip('/'))).resolve() if value.startswith('/') else (file.parent/unquote(split.path)).resolve()
                counts['HTML_local_links']+=1
                if not target.is_relative_to(site) or not target.exists():errors.append('Broken local HTML link '+str(file)+':'+value)
    if manifest['sizes']['index_bytes']>2*1024*1024:errors.append('Index exceeds 2 MiB')
    result=dict(validation='static validation passed' if not errors else 'failed',batch=manifest['batch'],counts=dict(counts),sizes=manifest['sizes'],package_bytes=sum(f.stat().st_size for f in site.rglob('*') if f.is_file()),errors=errors)
    report.parent.mkdir(parents=True,exist_ok=True)
    with report.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False));return 1 if errors else 0

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');raise SystemExit(main())
