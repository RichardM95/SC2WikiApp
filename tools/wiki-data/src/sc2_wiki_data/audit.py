"""Account for every v2 Fact, new form, player projection and protected file."""
import argparse, collections, hashlib, json, sys
from pathlib import Path
from .catalog_generate import load_validator, walk_facts
from .source_audit import next_step
from .projection import collect_evidence

def sha(file):
    h=hashlib.sha256()
    with Path(file).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def dump(file,data):
    with file.open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,allow_nan=False,indent=2)

def verification_context(entry,path,data):
    node=entry;owner=entry['id']
    for part in path.strip('/').split('/'):
        if isinstance(node,dict):
            for key in ('id','ability'):
                candidate=node.get(key)
                if isinstance(candidate,str) and ':' in candidate:owner=candidate
            node=node.get(part)
        elif isinstance(node,list) and part.isdigit() and int(part)<len(node):node=node[int(part)]
        else:break
    stat_fields=dict(life='LifeMax',shields='ShieldsMax',energy='EnergyMax',armor='LifeArmor',speed='Speed',sight='Sight',
        radius='Radius',cargo_size='CargoSize',supply='Food',acceleration='Acceleration',life_regen='LifeRegenRate',
        shield_regen='ShieldRegenRate',energy_regen='EnergyRegenRate')
    field=stat_fields.get(path.split('/')[-1]) if path.startswith('/stats/') else None
    if field is None:
        field=('Name/Tooltip/Description及其本地化引用' if path.endswith(('/name','/description')) else
            'Icon、Actor绑定、Model.Image/Model完整资产路径' if path.endswith('/icon') or 'portrait' in path else
            'Cost.Charge与InfoArray命令充能分支' if 'charges' in path else
            'Cost.Cooldown与InfoArray命令冷却分支' if 'cooldown' in path else
            'InfoArray.Cost/Time与Unit.CostResource/Food实际结算' if '/cost/' in path or path.endswith('/time') else
            'Requirement/Validator实际AND、OR、否定与运行状态' if 'condition' in path or 'requirements' in path else
            'Effect实际执行阶段、次数、条件与目标上下文')
    family=owner.split(':',1)[0]
    candidates=[str(Path(root)/'Base.SC2Data/GameData'/(family+'Data.xml')) for root in data['project']['component_roots']]
    return dict(object_to_inspect=owner,entry_path=path,fields_to_verify=field,active_source_candidates=candidates,
        verification_steps=[f'在实际活动组件及父对象/类型默认中定位 {owner}；检查 {field}，与本条理由及列出的字段来源对照。',
            f'在编辑器查看 {owner} 的有效值与关联命令；对数组按真实命令索引核对，不把缺省字段或被删除项当成0。',
            '若依赖玩家、行为层数、概率或事件，在游戏中分别记录触发与不触发状态、时间和目标；取得证据后再更新对应事实。'])

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('project-dir','baseline-batch','batch-suffix','hub-root','site-root','output-root'):
        p.add_argument('--'+key,required=True)
    a=p.parse_args();project=Path(a.project_dir).resolve();out=Path(a.output_root).resolve();site=Path(a.site_root).resolve()
    if not out.is_relative_to(project/'runtime/reports') or out.exists():raise ValueError('Fresh report required inside selected project')
    out.mkdir(parents=True);validator=load_validator(a.hub_root)
    manifest=json.loads((site/'manifest.json').read_text(encoding='utf-8'))
    index=json.loads((site/'data/index.json').read_text(encoding='utf-8'))['entries']
    byroute={(e['faction'],e['id']):e for e in index}
    errors=[];stats=collections.Counter();status=collections.Counter();visibility=collections.Counter();classes=collections.Counter();group_sizes=collections.Counter();remaining=[];shape_rows=[];projection_checks=0
    with (out/'fact-ledger.jsonl').open('x',encoding='utf-8') as ledger,(out/'new-facts.jsonl').open('x',encoding='utf-8') as additions:
        files=sorted((project/'wiki-data').glob('*-'+a.batch_suffix+'/data.json'))
        if len(files)!=11:raise ValueError('Eleven batches required')
        for file in files:
            faction=file.parent.name[:-(len(a.batch_suffix)+1)]
            data=json.loads(file.read_text(encoding='utf-8'));validator.validate(data)
            old=json.loads((project/'wiki-data'/(faction+'-'+a.baseline_batch)/'data.json').read_text(encoding='utf-8'))
            oldentries={e['id']:e for e in old['entries']};entries={e['id']:e for e in data['entries']};sources={s['id']:s for s in data['sources']}
            if data['sources'][:len(old['sources'])]!=old['sources']:errors.append(faction+': existing sources changed')
            stats['entries']+=len(entries)
            for uid,original in oldentries.items():
                if uid not in entries:errors.append(faction+': original object removed '+uid);continue
                before=dict(walk_facts(original));after=dict(walk_facts(entries[uid]))
                for path,fact in before.items():
                    new=after.get(path)
                    decision='removed_with_reason' if new is None else 'retained' if fact==new else 'corrected'
                    stats['baseline_facts']+=1;stats[decision]+=1
                    ledger.write(json.dumps(dict(faction=faction,object=uid,path=path,original=fact,revised=new,decision=decision,
                        reason='效果链按触发事件、条件及阶段重建，旧结构路径调整；完整原事实保留于v2' if new is None else new.get('basis') or new.get('reason') or '重新核对有效字段与本次展示/本地化信息'),ensure_ascii=False,allow_nan=False)+'\n')
            for uid,entry in entries.items():
                original=oldentries.get(uid);before=dict(walk_facts(original)) if original else {}
                if original is None:shape_rows.append(dict(faction=faction,id=uid,name=entry['name']['value'],group=entry['presentation']['group']))
                for path,fact in walk_facts(entry):
                    stats['facts']+=1;status[fact['status']]+=1
                    if path not in before:
                        stats['added_facts']+=1
                        additions.write(json.dumps(dict(faction=faction,object=uid,path=path,fact=fact,new_object=original is None),ensure_ascii=False,allow_nan=False)+'\n')
                    if fact['status']=='unresolved':remaining.append(dict(faction=faction,object=uid,path=path,reason=fact['reason'],evidence=fact['evidence'],next_step=next_step(path,fact,sources),verification_context=verification_context(entry,path,data)))
                presentation=entry['presentation'];v=presentation['visibility']['value'];visibility[v]+=1;classes[presentation['classification']['value']]+=1
                if presentation['group']['primary']==uid:group_sizes[len(presentation['group']['members'])]+=1
                nav=byroute.get((faction,uid))
                if v=='excluded':
                    if nav:errors.append('Excluded entry in player index '+faction+'/'+uid)
                    if (site/'data/entries'/faction/(uid.split(':',1)[1]+'.json')).exists():errors.append('Excluded entry exported '+uid)
                    continue
                if nav is None:errors.append('Visible entry missing '+uid);continue
                player=json.loads((site/nav['path']).read_text(encoding='utf-8'))
                source_audit=json.loads((site/nav['source_audit']).read_text(encoding='utf-8'))
                if source_audit['entry']!=entry:errors.append('Evidence entry differs '+uid)
                expected=collect_evidence(entry)
                if {s['id'] for s in source_audit['sources']}!=expected:errors.append('Evidence source subset differs '+uid)
                if player['id']!=uid or player['navigation']['primary']!=presentation['group']['primary']:errors.append('Player routing differs '+uid)
                for field,fact in entry['stats'].items():
                    projection_checks+=1
                    if player['stats'][field]['value']!=fact['value'] or player['stats'][field]['status']!=fact['status']:errors.append('Player stat differs '+uid+'/'+field)
                md=file.parent/'entries'/(uid.split(':',1)[1]+'.md')
                if not md.is_file() or md.read_text(encoding='utf-8')!=validator.article(entry,sources):errors.append('Markdown differs '+uid)
                for member in nav['members']:
                    if (faction,member['id']) not in byroute:errors.append('Broken member route '+member['id'])
                for relation in player['related_entries']:
                    if (faction,relation) not in byroute:errors.append('Broken relation '+relation)
            stats['batches']+=1
    for file,expected in manifest['file_hashes'].items():
        path=(site/file).resolve()
        if not path.is_relative_to(site) or not path.is_file() or sha(path)!=expected:errors.append('Manifest file/hash mismatch '+file)
    if (site/'data/index.json').stat().st_size>2*1024*1024:errors.append('Index exceeds 2 MiB')
    dump(out/'remaining.json',remaining);dump(out/'new-shapes.json',shape_rows)
    summary=dict(validation='static validation passed' if not errors else 'failed',counts=dict(stats),statuses=dict(status),visibility=dict(visibility),classifications=dict(classes),groups_by_size=dict(group_sizes),new_shapes=len(shape_rows),projection_checks=projection_checks,index_bytes=(site/'data/index.json').stat().st_size,errors=errors)
    dump(out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False))
    if errors:raise SystemExit(1)

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');main()
