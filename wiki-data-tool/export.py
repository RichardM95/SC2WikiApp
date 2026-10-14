"""Create an immutable data-only  content packet and verified update ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile
import zipfile

import projection
from factions import normalize_prefix,default_config,validate_config
from navigation import DISPLAY_NAMES
from other import parse_other

PROJECT_ID='SCORE-fa82e2d014'

def priority(entry):return entry.get('presentation',{}).get('glossary_priority',{}).get('value')

def group_priority(entry,catalog,origin):
    group=entry.get('presentation',{}).get('group',{})
    primary=catalog.get((origin,group.get('primary',entry['id'])),entry)
    number=priority(primary)
    if number is not None:return {'value':number,'basis':'正常形态的有效 GlossaryPriority','inferred':False}
    numbers=[priority(catalog[(origin,m['id'])]) for m in group.get('members',[{'id':entry['id']}]) if (origin,m['id']) in catalog and priority(catalog[(origin,m['id'])]) is not None]
    return {'value':min(numbers) if numbers else None,'basis':'正常形态缺少优先级，采用形态成员最小有效值' if numbers else '全部形态缺少优先级','inferred':bool(numbers)}

def qualify_projected(entry,data,assets,catalog,names):
    projected=projection.player_entry(entry,data,assets,catalog,names);origin=data['_faction']
    grouped=group_priority(entry,catalog,origin)
    nav=projected['navigation'];nav.update(origin=origin,editorPrefix=normalize_prefix(entry['presentation'].get('editor_prefix',{}).get('value') or ''),glossaryPriority=priority(entry),groupPriority=grouped['value'],groupPriorityBasis=grouped['basis'],groupPriorityInferred=grouped['inferred'])
    for member in nav['members']:
        target=catalog[(origin,member['id'])]
        member.update(origin=origin,editorPrefix=normalize_prefix(target['presentation'].get('editor_prefix',{}).get('value') or ''),glossaryPriority=priority(target))
    return projected

def safe_packet_path(name):
    return isinstance(name,str) and not '\\' in name and bool(re.fullmatch(r'(?:data/[A-Za-z0-9_./-]+\.json|assets/manifest\.json|assets/images/[A-Za-z0-9_./-]+\.(?:png|jpg|jpeg|webp|gif)|assets/fonts/[A-Za-z0-9_./-]+\.(?:woff2?|ttf|otf))',name)) and all(part not in ('','..','.') for part in name.split('/'))

def finalize_packet(output,batch,factions,counts):
    output=Path(output);hashes={p.relative_to(output).as_posix():projection.digest(p) for p in sorted(output.rglob('*')) if p.is_file() and p.relative_to(output).as_posix()!='manifest.json'}
    if any(not safe_packet_path(name) for name in hashes):raise ValueError('Unsafe data-only packet file')
    canonical=json.dumps(hashes,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8')
    pack_id=batch+'-'+hashlib.sha256(canonical).hexdigest()[:12]
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',pack_id):raise ValueError('Unsafe packet identity')
    manifest={'format_version':2,'content_format_version':2,'min_client_version':2,'project_id':PROJECT_ID,'pack_id':pack_id,'batch':batch,'factions':factions,'files':{'index':'data/index.json','other':'data/other.json'},'file_hashes':hashes,'sizes':{'index_bytes':(output/'data/index.json').stat().st_size,'total_bytes':sum((output/p).stat().st_size for p in hashes)},**counts}
    projection.write_json(output/'manifest.json',manifest);return manifest

def build_packet(datasets,output,source_root,toolbox_root,game_root,legacy_site):
    output=Path(output)
    if output.exists():raise FileExistsError('拒绝覆盖现有内容目录：'+str(output))
    output.mkdir(parents=True)
    catalog={(key,e['id']):e for key,data in datasets for e in data['entries']}
    # Reuse the proven asset resolution/player projection, but never package the
    # legacy site's executable HTML/CSS/JS.
    with tempfile.TemporaryDirectory(prefix='score-packet-',dir=output.parent) as temp:
        generated=Path(temp)/'content'
        old_manifest=projection.build(datasets,generated,source_root,toolbox_root,game_root,legacy_site,projection=qualify_projected,include_other=False)
        import shutil
        for folder in ('data','assets'):shutil.copytree(generated/folder,output/folder)
    index=json.loads((output/'data/index.json').read_text(encoding='utf8'))
    for row in index['entries']:
        entry=catalog[(row['faction'],row['id'])];origin=row['faction']
        row.update(origin=origin,editorPrefix=normalize_prefix(entry['presentation'].get('editor_prefix',{}).get('value') or ''),glossaryPriority=priority(entry),groupPriority=group_priority(entry,catalog,origin)['value'])
        for member in row['members']:
            target=catalog[(origin,member['id'])];member.update(origin=origin,editorPrefix=normalize_prefix(target['presentation'].get('editor_prefix',{}).get('value') or ''),glossaryPriority=priority(target))
    projection.write_json(output/'data/index.json',index)
    if (output/'data/index.json').stat().st_size>2*1024*1024:raise ValueError('Index exceeds 2 MiB')
    projection.write_json(output/'data/other.json',parse_other(legacy_site,output))
    return finalize_packet(output,old_manifest['batch'],old_manifest['factions'],{k:old_manifest[k] for k in ('entry_count','public_count','excluded_count')})

def update_zip(packet,destination):
    packet=Path(packet).resolve();destination=Path(destination)
    if destination.exists():raise FileExistsError(str(destination))
    if destination.resolve().is_relative_to(packet):raise ValueError('Update archive cannot mutate an immutable packet')
    manifest=json.loads((packet/'manifest.json').read_text(encoding='utf8'));listed=manifest['file_hashes']
    if manifest.get('project_id')!=PROJECT_ID or manifest.get('format_version')!=2 or manifest.get('content_format_version')!=2 or manifest.get('min_client_version')!=2:raise ValueError('Unsupported packet')
    if len(listed)+1>50000 or sum((packet/name).stat().st_size for name in listed)>2*1024**3:raise ValueError('Update limits exceeded')
    for name,sha in listed.items():
        path=packet/name
        if not safe_packet_path(name) or path.is_symlink() or not path.resolve().is_relative_to(packet) or projection.digest(path)!=sha:raise ValueError('Invalid packet file: '+name)
    if len({name.casefold() for name in listed})!=len(listed):raise ValueError('Windows path collision')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(destination,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name in ['manifest.json',*sorted(listed)]:archive.write(packet/name,name)
    if destination.stat().st_size>512*1024**2:destination.unlink();raise ValueError('Compressed update exceeds 512 MiB')
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip():raise ValueError('ZIP CRC check failed')
    return {'path':str(destination),'sha256':projection.digest(destination),'bytes':destination.stat().st_size}

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    config=sub.add_parser('config');config.add_argument('--output',required=True)
    build=sub.add_parser('build')
    for name in ('project-dir','batch','output','source-root','toolbox-root','game-root','legacy-site'):build.add_argument('--'+name,required=True)
    update=sub.add_parser('zip');update.add_argument('--packet',required=True);update.add_argument('--output',required=True)
    args=p.parse_args()
    if args.command=='config':
        out=Path(args.output)
        if out.exists():raise FileExistsError(str(out))
        projection.write_json(out,validate_config(default_config()));result={'path':str(out)}
    elif args.command=='zip':result=update_zip(args.packet,args.output)
    else:
        project=Path(args.project_dir);datasets=[(key,json.loads((project/'wiki-data'/(key+'-'+args.batch)/'data.json').read_text(encoding='utf8'))) for key in DISPLAY_NAMES]
        result=build_packet(datasets,args.output,args.source_root,args.toolbox_root,args.game_root,Path(args.legacy_site))
    print(json.dumps({k:v for k,v in result.items() if k!='file_hashes'},ensure_ascii=False))

if __name__=='__main__':main()
