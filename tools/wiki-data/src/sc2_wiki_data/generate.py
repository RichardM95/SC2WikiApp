"""Plan from full source fingerprints; rebuild affected authority in a fresh batch."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

from .catalog_adapter import ComponentPool
from .dynamic_text import resolve_texts
from .catalog_generate import build_faction,build_entry,load_validator
from .priority import add_priority,predecessor_metadata
from .navigation import DISPLAY_NAMES,discover_shapes,enrich
from .refine_costs import refine_data
from .refine_presentation import LiteralPool,refine
from . import mechanics
from .source_inventory import inventory,compare as compare_inventory,file_sha

def current_context(pool,ctx):
    context=ctx.dependency_context()
    records={s['id']:s for s in context['sources']}
    for source in ctx.catalog.sources:
        document=Path(source['root_path'])/'DocumentInfo'
        if document.is_file():records[source['id']]['document_info']={'physical_path':str(document.resolve()),'sha256':hashlib.sha256(document.read_bytes()).hexdigest()}
    for edge in context.get('edges',[]):
        if not edge.get('target'):continue
        sid,path=pool.locate_dependency(edge['target'])
        if sid.startswith('project:'):
            resolved=Path(path).resolve()
            if sid not in context['load_order'] or not resolved.is_relative_to(pool.source_root):raise ValueError('Dependency resolution left active source root')
            edge.update(resolved_source=sid,physical_path=str(resolved),resolution='resolved_by_explicit_source_root',resolution_reason='活动 Catalog 在显式 source_root 内解析唯一匹配组件；保留声明，不推断游戏安装挂载位置。')
    return context

def source_changes(previous,current):
    before={s['id']:s.get('fingerprint') for s in previous.get('sources',[])}
    after={s['id']:s.get('fingerprint') for s in current.get('sources',[])}
    changed=sorted(sid for sid in before.keys()|after.keys() if before.get(sid)!=after.get(sid))
    reasons=[]
    if previous.get('load_order')!=current.get('load_order'):reasons.append('dependency_order')
    if previous.get('engine')!=current.get('engine'):reasons.append('engine_metadata')
    return changed,reasons

def reconstruct(pool,faction,before,batch):
    ctx=pool.context(faction['component'])
    selection=copy.deepcopy(before)
    removed=[e['id'] for e in selection['entries'] if ctx.node('Unit',e['id'].split(':',1)[1]) is None]
    selection['entries']=[e for e in selection['entries'] if e['id'] not in removed]
    # Local additions are selected from actual primary definitions, never base
    # multiplayer units. Technical objects retain source_audit data and are excluded by
    # the existing navigation policy.
    existing={e['id'] for e in selection['entries']}
    for (family,uid),definitions in ctx.catalog.objects.items():
        if family=='Unit' and re.fullmatch(r'[A-Za-z0-9_]+',uid) and any(d['source_id']==ctx.primary_sid for d in definitions) and 'Unit:'+uid not in existing:
            selection['entries'].append({'id':'Unit:'+uid});existing.add('Unit:'+uid)
    data=build_faction(pool,faction,selection,batch,refine=True)
    ctx=pool.context(faction['component'])
    from .refine_costs import hydrate_sources
    hydrate_sources(ctx,data)
    candidates=discover_shapes(ctx,data)
    added=[]
    for candidate in candidates:
        extra=build_entry(ctx,{'id':'Unit:'+candidate['id']},faction)
        extra['related_entries']=[candidate['from']] if candidate['from']!=extra['id'] else []
        data['entries'].append(extra);data['selected_objects'].append(extra['id']);added.append(extra)
    if added:
        subset=dict(data,entries=added,sources=ctx.sources);refine_data(ctx,subset);refine(subset,LiteralPool())
    for entry in data['entries']:mechanics.refresh_effects(ctx,entry)
    enrich(ctx,data);mechanics.refresh_condition_texts(data);resolve_texts(ctx,data)
    for entry in data['entries']:add_priority(ctx,entry)
    data['sources']=ctx.sources
    data['dependency_context']=current_context(pool,ctx)
    data['project']['regeneration']={'basis':'活动组件完整文件与元数据指纹变化','removed_selected_objects':removed}
    return data

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('plan','generate','snapshot'))
    for name in ('project-dir','source-root','toolbox-root','game-root','gamedata-root','hub-root','baseline-batch'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--batch',help='Fresh output batch for generate')
    parser.add_argument('--dry-run',action='store_true',help='Exercise regeneration and validation without writing a batch')
    parser.add_argument('--force-origin',choices=list(DISPLAY_NAMES),help='Explicitly regenerate one source batch even when fingerprints match')
    parser.add_argument('--report',help='Fresh JSON report with complete source-file hashes')
    parser.add_argument('--baseline-inventory',help='Explicit release inventory; defaults to runtime report for baseline batch')
    parser.add_argument('--asset-manifest',help='Exported assets/manifest.json for snapshot source/hash verification')
    args=parser.parse_args();project=Path(args.project_dir).resolve();source=Path(args.source_root).resolve();hub=Path(args.hub_root).resolve();wiki=project/'wiki-data'
    if project.is_relative_to(source) or project.is_relative_to(hub):raise ValueError('Unsafe project destination')
    if args.report and (Path(args.report).resolve().is_relative_to(source) or Path(args.report).exists()):raise ValueError('Unsafe or existing report path')
    if args.command=='generate' and (not args.batch or not re.fullmatch(r'[A-Za-z0-9_-]+',args.batch) or args.batch==args.baseline_batch):raise ValueError('A fresh safe batch is required')
    inventory_out=project/'runtime/reports'/('score-wiki-'+str(args.batch))/'source-inventory.json'
    if args.command=='generate' and not args.dry_run and inventory_out.exists():raise FileExistsError(str(inventory_out))
    if args.command=='snapshot':
        if not args.report:raise ValueError('snapshot requires a fresh --report path')
        destination=Path(args.report).resolve()
        if destination.exists() or destination.is_relative_to(source):raise ValueError('Unsafe or existing snapshot path')
        snapshot=inventory(source,args.asset_manifest);snapshot['batch']=args.baseline_batch
        destination.parent.mkdir(parents=True,exist_ok=True)
        with destination.open('x',encoding='utf8') as stream:json.dump(snapshot,stream,ensure_ascii=False,indent=2)
        print(json.dumps({'snapshot':str(destination),'components':len(snapshot['components']),'files':sum(len(c['files']) for c in snapshot['components'].values()),'exported_asset_sources':len(snapshot['exported_assets'])},ensure_ascii=False));return
    inventory_path=Path(args.baseline_inventory) if args.baseline_inventory else project/'runtime/reports'/('score-wiki-'+args.baseline_batch)/'source-inventory.json'
    prior_inventory=None;live_inventory=inventory(source);asset_changed=[];asset_components=[]
    if inventory_path.is_file():
        prior_inventory=json.loads(inventory_path.read_text(encoding='utf8'))
        if Path(prior_inventory.get('source_root','')).resolve()!=source or prior_inventory.get('batch')!=args.baseline_batch:raise ValueError('Inventory baseline identity mismatch')
        asset_components,asset_changed=compare_inventory(prior_inventory,live_inventory)
        for path,row in prior_inventory.get('exported_assets',{}).items():
            if Path(path).is_file():live_inventory['exported_assets'][path]=dict(row,sha256=file_sha(path))
    pool=ComponentPool(source,args.toolbox_root,args.game_root,args.gamedata_root);validator=load_validator(hub)
    factions=json.loads((project/'runtime/catalog/score-factions.json').read_text(encoding='utf-8-sig'))['factions']
    report={'baseline_batch':args.baseline_batch,'source_root':str(source),'asset_inventory_available':prior_inventory is not None,'asset_inventory_path':str(inventory_path),'changed_asset_sources':asset_changed,'affected_batches':[],'reusable_batches':[],'changed_components':[],'batches':[]};pending=[];changed_all=set()
    for key in DISPLAY_NAMES:
        old=wiki/(key+'-'+args.baseline_batch)/'data.json';raw=old.read_bytes();before=json.loads(raw.decode('utf8'));validator.validate(before)
        if Path(before['project']['source_root']).resolve()!=source or before['project']['batch']!=args.baseline_batch:raise ValueError('Baseline identity mismatch')
        component=before['project']['primary_mod'];ctx=pool.context(component);current=current_context(pool,ctx);changed,reasons=source_changes(before.get('dependency_context',{}),current);changed_all.update(changed)
        loaded=set(current['load_order'])
        inventory_changes=sorted(set(asset_components)&loaded)
        if '*' in asset_components:inventory_changes.append('shared_exported_asset')
        if inventory_changes:
            reasons.append('source_file_inventory');changed=sorted(set(changed)|set(inventory_changes));changed_all.update(inventory_changes)
        if prior_inventory is None:reasons.append('asset_baseline_missing')
        if args.force_origin==key:reasons.append('explicit_regeneration')
        affected=bool(changed or reasons);report['affected_batches' if affected else 'reusable_batches'].append(key)
        row={'origin':key,'affected':affected,'changed_components':changed,'reasons':reasons,'current_fingerprints':{s['id']:s['fingerprint'] for s in current['sources']},'source_files':[{'source_id':r['source_id'],'path':r['path'],'sha256':r['sha256']} for r in ctx.catalog.files]};report['batches'].append(row)
        if args.command=='plan':continue
        target=wiki/(key+'-'+args.batch)
        if not target.resolve().is_relative_to(wiki.resolve()) or target.exists():raise FileExistsError('Unsafe or existing output: '+str(target))
        if affected:
            faction=next(f for f in factions if f['component']==component);data=reconstruct(pool,faction,before,args.batch)
        else:
            data=copy.deepcopy(before)
            if any('glossary_priority' not in e.get('presentation',{}) for e in data['entries']):
                from .refine_costs import hydrate_sources
                hydrate_sources(ctx,data)
                for e in data['entries']:
                    if 'glossary_priority' not in e['presentation']:add_priority(ctx,e)
                data['sources']=ctx.sources
            data['project']['batch']=args.batch
        predecessor_metadata(data,args.batch,args.baseline_batch,raw)
        validator.validate(data);pending.append((old,hashlib.sha256(raw).hexdigest(),target,data))
    report['changed_components']=sorted(changed_all);report['data_reuse']=not bool(report['affected_batches'])
    if args.command=='generate' and not args.dry_run:
        drift,_=compare_inventory(live_inventory,inventory(source))
        if drift:raise ValueError('Source files changed during generation: '+','.join(drift))
        for old,sha,target,data in pending:
            if hashlib.sha256(old.read_bytes()).hexdigest()!=sha:raise ValueError('Baseline changed during planning')
        sys.path.insert(0,str(hub/'tools'));from project_storage import ensure_project_space
        ensure_project_space(project)
        for old,sha,target,data in pending:
            target.mkdir(exist_ok=False)
            if (old.parent/'evidence').is_dir():shutil.copytree(old.parent/'evidence',target/'evidence')
            (target/'data.json').write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
            validator.validate_snapshots(data,target);validator.render(data,target,project)
        if inventory_out.exists():raise FileExistsError(str(inventory_out))
        live_inventory['batch']=args.batch
        inventory_out.parent.mkdir(parents=True,exist_ok=True)
        with inventory_out.open('x',encoding='utf8') as stream:json.dump(live_inventory,stream,ensure_ascii=False,indent=2)
    if args.report:
        report_path=Path(args.report).resolve()
        if report_path.is_relative_to(source) or report_path.exists():raise ValueError('Unsafe or existing report path')
        report_path.parent.mkdir(parents=True,exist_ok=True)
        with report_path.open('x',encoding='utf8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    summary={k:v for k,v in report.items() if k!='batches'}
    summary['batches']=[{k:v for k,v in row.items() if k!='source_files'} for row in report['batches']]
    summary['report']=args.report
    print(json.dumps(summary,ensure_ascii=False))

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf8');main()
