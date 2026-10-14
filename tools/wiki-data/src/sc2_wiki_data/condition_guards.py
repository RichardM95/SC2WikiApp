"""Create an exclusive successor with shape groups, condition text and reverse links."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys

from .catalog_adapter import ComponentPool
from .catalog_generate import load_validator
from .navigation import DISPLAY_NAMES,attach_condition_targets,group_entries
from .refine_costs import hydrate_sources

PREDECESSOR='v3-20261013-1'
SUCCESSOR='v3-20261013-2'


def unchanged_except_group(original,updated):
    compare=copy.deepcopy(updated)
    compare['presentation']['group']=copy.deepcopy(original['presentation']['group'])
    if compare!=original:
        raise ValueError('Regroup changed a non-group field: '+original['id'])


def unchanged_except_condition_texts(original,updated):
    """Allow only existing resolved condition Fact text, never sources or values."""
    def has_count(obj):
        if isinstance(obj,list):return any(has_count(v) for v in obj)
        if not isinstance(obj,dict):return False
        if obj.get('type') in ('CRequirementCountUnit','CRequirementCountUpgrade'):return True
        return any(has_count(v) for key,v in obj.items() if key not in ('value','fields','sources','evidence'))
    def check(old,new):
        if isinstance(old,dict) and isinstance(new,dict):
            if old.keys()!=new.keys():raise ValueError('Condition refresh added or removed a field')
            is_condition= {'value','status','evidence','unit'}<=old.keys() and has_count(old.get('details'))
            for key in old:
                if key=='value' and is_condition and old.get('status') in ('confirmed','inferred') and isinstance(old[key],str):
                    if not isinstance(new[key],str):raise ValueError('Condition text ceased to be a string')
                    continue
                check(old[key],new[key])
        elif isinstance(old,list) and isinstance(new,list):
            if len(old)!=len(new):raise ValueError('Condition refresh changed array length')
            for a,b in zip(old,new):check(a,b)
        elif old!=new:raise ValueError('Condition refresh changed a protected field')
    check(original,updated)


def unchanged_except_condition_targets(original,updated):
    compare=copy.deepcopy(updated)
    for old,new in zip(original['entries'],compare['entries']):
        for key in ('research','available_upgrades'):
            for old_row,new_row in zip(old['presentation'].get(key,[]),new['presentation'].get(key,[])):
                if 'condition_targets' in old_row:new_row['condition_targets']=copy.deepcopy(old_row['condition_targets'])
                else:new_row.pop('condition_targets',None)
    if compare!=original:raise ValueError('Reverse association changed an existing field')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('project-dir','source-root','toolbox-root','game-root','gamedata-root','hub-root'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    project=Path(args.project_dir).resolve();hub=Path(args.hub_root).resolve()
    source_root=Path(args.source_root).resolve();wiki=project/'wiki-data'
    if project.is_relative_to(hub) or project.is_relative_to(source_root):
        raise ValueError('Project output must stay outside Hub and source components')
    if not wiki.resolve().is_relative_to(project):
        raise ValueError('wiki-data resolves outside the selected project')
    packages=[]
    for key in DISPLAY_NAMES:
        old=wiki/(key+'-'+PREDECESSOR)/'data.json'
        new=wiki/(key+'-'+SUCCESSOR)
        if not old.resolve().is_relative_to(project) or not new.resolve().is_relative_to(project):
            raise ValueError('Batch path resolves outside the selected project')
        if not old.is_file():raise FileNotFoundError(str(old))
        if new.exists():raise FileExistsError('Refusing to overwrite '+str(new))
        packages.append((key,old,new))
    validator=load_validator(hub)
    pool=ComponentPool(source_root,args.toolbox_root,args.game_root,args.gamedata_root)
    pending=[]
    for key,old,new in packages:
        raw=old.read_bytes();sha=hashlib.sha256(raw).hexdigest()
        data=json.loads(raw.decode('utf-8-sig'));validator.validate(data)
        if data['project']['batch']!=PREDECESSOR or Path(data['project']['source_root']).resolve()!=source_root:
            raise ValueError('Source or predecessor identity mismatch: '+key)
        before=copy.deepcopy(data['entries']);sources=copy.deepcopy(data['sources'])
        ctx=pool.context(data['project']['primary_mod']);hydrate_sources(ctx,data)
        classifications={e['id']:copy.deepcopy(e['presentation']['classification']) for e in before}
        group_entries(ctx,data['entries'])
        # Hero classification was settled in the predecessor; do not reapply its
        # narrative prefix while rebuilding groups alone.
        for entry,original in zip(data['entries'],before):
            entry['presentation']['classification']=classifications[entry['id']]
            unchanged_except_group(original,entry)
        from . import mechanics
        protected=copy.deepcopy(data)
        conditions=mechanics.refresh_condition_texts(data)
        unchanged_except_condition_texts(protected,data)
        protected=copy.deepcopy(data)
        research_links=attach_condition_targets(data)
        unchanged_except_condition_targets(protected,data)
        if data['sources'][:len(sources)]!=sources:
            raise ValueError('Existing source IDs or records changed: '+key)
        data['sources']=ctx.sources
        data['project'].update(batch=SUCCESSOR,predecessor_batch=PREDECESSOR,predecessor_sha256=sha)
        if data['project'].get('baseline_batch')!='v2-20261013':
            raise ValueError('The protected v2 baseline must remain attached')
        validator.validate(data)
        info={'faction':key,'entries':len(data['entries']),
            'changed_groups':sum(a['presentation']['group']!=b['presentation']['group'] for a,b in zip(before,data['entries'])),
            'added_sources':len(data['sources'])-len(sources),
            'groups':len({e['presentation']['group']['primary'] for e in data['entries']}),
            'condition_texts':conditions,'research_condition_associations':research_links}
        print(json.dumps(info,ensure_ascii=False),flush=True)
        pending.append((old,sha,new,data))
    if args.dry_run:return
    for old,sha,new,data in pending:
        if hashlib.sha256(old.read_bytes()).hexdigest()!=sha:
            raise ValueError('Predecessor changed during preparation')
    sys.path.insert(0,str(hub/'tools'))
    from project_storage import ensure_project_space
    ensure_project_space(project)
    created=[]
    try:
        for old,sha,new,data in pending:
            new.mkdir(exist_ok=False);created.append(new)
            # Translation snapshots, if present, keep their exact relative paths.
            if (old.parent/'evidence').is_dir():shutil.copytree(old.parent/'evidence',new/'evidence')
            (new/'data.json').write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
            validator.validate_snapshots(data,new)
            validator.render(data,new,project)
            if not (new/'index.md').is_file() or not (new/'review.md').is_file():
                raise ValueError('Authority Markdown/review output missing')
            if len(list((new/'entries').glob('*.md')))!=len(data['entries']):
                raise ValueError('Authority article count mismatch')
    except BaseException:
        for directory in reversed(created):
            resolved=directory.resolve()
            if not resolved.is_relative_to(wiki.resolve()) or not resolved.name.endswith('-'+SUCCESSOR):
                raise RuntimeError('Refusing rollback outside successor directories')
            shutil.rmtree(resolved)
        raise
    print(json.dumps({'batch':SUCCESSOR,'packages':len(pending),'status':'static validation passed'},ensure_ascii=False))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
