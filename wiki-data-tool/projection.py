"""Generate a bounded player index and on-demand content/evidence packages."""
from collections import defaultdict
import copy
import hashlib
import html
import json
import math
from pathlib import Path
import re
import shutil
from urllib.parse import urlencode, urlsplit, unquote

from assets import AssetBuilder
from navigation import DISPLAY_NAMES, display_name, subfaction_label


RACES = {'Cerberus':'zerg','Primal':'zerg','Webby':'zerg','Golden':'protoss',
    'Ihanrii':'protoss','Purifier':'protoss','Taldarim':'protoss','CovertOps':'terran',
    'HTXL':'terran','Moebius':'terran','Umojan':'terran'}
FACTION_PORTRAITS = {key:'Assets/Textures/ui_commanderportrait_'+key.lower()+'.dds'
    for key in ('CovertOps','Golden','Ihanrii','Moebius','Purifier','Taldarim','Umojan','Webby')}
RAW_KEYS = {'mechanics','raw_fields','raw_value','execution','execution_graph','effect_graph',
    'raw_xml','provider','providers','diagnostics','execution_root','actual_graph','display_graph',
    'path','fields','definition','dependencies','raw_template','dynamic_text','details',
    'structure','selectors','inherited','owner','response_fields','state_fields','original_reference','member_field'}
CATALOG_LABELS = {'Unit':'关联单位','Abil':'关联技能','Weapon':'对应武器',
    'Behavior':'对应行为','Effect':'对应效果','Validator':'验证条件',
    'Requirement':'科技要求','RequirementNode':'科技要求','Upgrade':'对应升级',
    'Button':'按钮','Actor':'表现对象','Model':'模型'}
TYPE_LABELS = {'CEffectDamage':'伤害','CEffectSet':'组合效果','CEffectPersistent':'持续效果',
    'CEffectCreatePersistent':'持续效果','CEffectLaunchMissile':'导弹发射',
    'CEffectSearch':'范围检索','CEffectEnumArea':'范围检索','CEffectApplyBehavior':'施加行为',
    'CEffectRemoveBehavior':'移除行为','CEffectCreateUnit':'创建单位',
    'CEffectSwitch':'条件分支','CEffectModifyUnit':'修改单位',
    'CEffectRandomPointInCircle':'随机位置','CBehaviorBuff':'行为触发',
    'Melee':'近战','Ranged':'远程'}
BASIC_ABILITIES = {'stop','attack','move','holdposition','patrol','rally'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def collect_evidence(obj):
    found = set()
    def visit(item):
        if isinstance(item,dict):
            for key,value in item.items():
                if (key=='evidence' or key.endswith('_evidence')) and isinstance(value,list):
                    found.update(v for v in value if isinstance(v,str))
                visit(value)
        elif isinstance(item,list):
            for value in item:
                visit(value)
    visit(obj)
    return found


def friendly(text, names):
    if not isinstance(text,str):
        return text
    # Resolve proven display names; keep unknown technical identifiers in source_audit only.
    # An unprocessed dynamic tag must never leave an empty numeric sentence.
    text = re.sub(r'<d\b[^>]*>', '无', text, flags=re.I)
    text = re.sub(r'<(?:s|c)\b[^>]*>|</(?:s|c)>', '', text)
    text = text.replace('<n>','\n')
    text = re.sub(r'\b(Unit|Abil|Weapon|Behavior|Effect|Validator|RequirementNode|Requirement|Upgrade|Button|Actor|Model):[A-Za-z0-9_]+',
        lambda m:names.get(m.group(0),CATALOG_LABELS[m.group(1)]),text)
    # Mechanical summaries may contain Catalog / Entry / Field paths rather than IDs.
    bare = names.get('_bare') or {key.split(':',1)[1]:value for key,value in names.items() if ':' in key}
    text = re.sub(r'\b[A-Za-z][A-Za-z0-9_]*\b',lambda m:bare.get(m.group(0),m.group(0)),text)
    text = re.sub(r'((?:周期效果|初始效果|最终效果|结束效果|应用行为|移除行为|影响单位)[：:]\s*)[A-Za-z][A-Za-z0-9_]+',
        lambda m:m.group(1)+'对应机制',text)
    return re.sub(r'\b(Unit|Abil|Weapon|Behavior|Effect|Validator|RequirementNode|Requirement|Upgrade|Button|Actor|Model)\s*[/,]\s*[A-Za-z0-9_]+',
        lambda m:CATALOG_LABELS[m.group(1)],text)


def graph_summary(graph):
    effects = graph.get('effects',[])
    types = list(dict.fromkeys(TYPE_LABELS.get(e.get('type'), '其他效果')
        for e in effects if isinstance(e,dict)))
    result = '执行机制：'+('、'.join(types) if types else '详见机制证据')
    events=[]; random=[]; states=[]; runtime=False
    def phase_label(phase, context=None):
        context=context or {}
        if phase=='DamageResponse':
            fatal=any(path.split('|')[-1].removeprefix('@')=='Fatal' and str(v)=='1'
                for path,v in context.get('fields',{}).items())
            return '受到致死伤害时响应' if fatal else '受到符合条件的伤害时响应'
        if phase=='DeathResponse':return '死亡时响应'
        if phase in ('PeriodicEffect','PeriodicEffectArray'):return '行为活动期间周期执行'
        if phase=='ImpactEffect':return '导弹命中时'
        if phase=='LaunchEffect':return '导弹发射时'
        owner=str(context.get('owner') or '')
        family='导弹' if ('missile' in context or owner.startswith('Effect:')) else (
            '持续效果' if 'persistent' in context else '行为')
        return {'InitialEffect':family+'启动时' if family!='行为' else '行为应用时',
            'FinalEffect':family+'终止时','ExpireEffect':family+'自然到期时'}.get(phase)
    def event(context):
        nonlocal runtime
        if not isinstance(context,dict):return
        label=phase_label(context.get('kind') or context.get('phase'),context)
        if label:events.append(label)
        if context.get('metadata',{}).get('runtime_dependent'):
            runtime=True
        for state in context.get('behavior_states',[]):
            if state.get('mode')=='Disable':states.append('行为禁用条件')
            if state.get('mode')=='Remove':states.append('行为移除条件')
    for trigger in graph.get('triggers',[]):event(trigger)
    for effect in effects:
        if not isinstance(effect,dict):continue
        event(effect.get('trigger'))
        for state in effect.get('behavior_states',[]):
            if state.get('mode')=='Disable':states.append('行为禁用条件')
            if state.get('mode')=='Remove':states.append('行为移除条件')
        for path,value in effect.get('fields',{}).items():
            tokens=[t.removeprefix('@') for t in path.split('|')]
            if 'RandomPeriod' in tokens and str(value)=='1':random.append('随机周期选择')
            if 'RandomOffset' in tokens and str(value)=='1':random.append('随机位置选择')
        if effect.get('type')=='CEffectRandomPointInCircle':random.append('随机位置选择')
    for component in graph.get('components',[]):
        if not isinstance(component,dict):continue
        event(component.get('trigger'))
        for timing in component.get('schedule',[]):
            if not isinstance(timing,dict):continue
            event(timing)
            if timing.get('flags',{}).get('random_period'):random.append('随机周期选择')
            if timing.get('flags',{}).get('random_offset'):random.append('随机位置选择')
    if events:
        stages=list(dict.fromkeys(events))
        result+='；触发阶段：'+'、'.join(stages)
        if len(stages)>1:result+='（各阶段按相应条件执行，不表示同时触发）'
    if random:result+='；'+'、'.join(dict.fromkeys(random))+'，不代表固定执行顺序'
    if states:result+='；受'+'／'.join(dict.fromkeys(states))+'约束'
    if runtime:result+='；事件触发结果受运行条件影响'
    issues = graph.get('issues',[])
    if issues:
        result += '；部分条件或执行次数尚未解析'
    return result


def finite_number(value):
    if isinstance(value,bool):return None
    try:
        number=float(value)
    except (TypeError,ValueError,OverflowError):return None
    if not math.isfinite(number):return None
    return int(number) if number.is_integer() else number


def mechanism_parameters(fields):
    """Expose only known numeric mechanism fields, never the raw Catalog map."""
    aliases={'Duration':'duration','Period':'period','Amount':'amount',
        'ModifyFraction':'fraction','Fraction':'fraction','ModifyLimit':'maximum',
        'ArmorReduction':'armor_reduction','Radius':'radius','Count':'count'}
    result={}
    for path,raw in fields.items():
        leaf=path.split('|')[-1].removeprefix('@')
        if leaf in ('value','Value','Link') and len(path.split('|'))>1:
            leaf=path.split('|')[-2]
        if leaf not in aliases:continue
        number=finite_number(raw)
        if number is not None:result[aliases[leaf]]=number
    return result


def project_target(target,names):
    if target is None:return None
    if isinstance(target,str):
        if target in ('SourceUnit','Source','Caster'):return {'description':'施法者'}
        if target in ('TargetUnit','Target'):return {'description':'目标单位'}
        if target=='TargetUnitOrPoint':return {'description':'目标单位或位置'}
        if target in ('TargetPoint','TargetLocation'):return {'description':'目标位置'}
        if target in names:return {'description':friendly(names[target],names)}
        return {'description':'目标上下文待解析'}
    if not isinstance(target,dict):return {'description':'目标上下文待解析'}
    labels=[];known={}
    if isinstance(target.get('inherited'),dict):
        inherited=project_target(target['inherited'],names)
        if inherited and inherited.get('description')!='目标上下文待解析':labels.append(inherited['description'])
    for path,value in target.get('selectors',{}).items():
        translated=project_target(value,names)
        if translated and translated['description']!='目标上下文待解析':labels.append(translated['description'])
    if 'search' in target:labels.append('范围内目标')
    if 'random_location' in target:labels.append('随机位置')
    for field,label in (('Radius','radius'),('Arc','arc')):
        if field in target:
            number=finite_number(target[field])
            if number is not None:known[label]=number
    return {'description':' → '.join(dict.fromkeys(labels)) or '目标上下文待解析',**known}


def project_event(event,names):
    if not isinstance(event,dict):return None
    labels={'DamageResponse':'受到符合条件的伤害时响应','DeathResponse':'死亡时响应',
        'PeriodicEffect':'行为活动期间周期执行','PeriodicEffectArray':'行为活动期间周期执行',
        'ImpactEffect':'导弹命中时','LaunchEffect':'导弹发射时','InitialEffect':'应用或启动时',
        'FinalEffect':'终止时','ExpireEffect':'自然到期时'}
    phase=event.get('kind') or event.get('phase')
    result={'description':labels.get(phase,'触发阶段待解析')}
    if phase=='DamageResponse' and any(path.split('|')[-1].removeprefix('@')=='Fatal' and str(v)=='1'
        for path,v in event.get('fields',{}).items()):
        result['description']='受到致死伤害时响应'
    result.update(mechanism_parameters(event.get('fields',{})))
    result.update(mechanism_parameters(event.get('state_fields',{})))
    for key in ('period','duration','interval','count','initial_time','delay'):
        if key in event:result[key]=event[key] if event[key] is None else finite_number(event[key])
    return result


def project_schedule(schedule,names):
    result=[]
    for timing in schedule if isinstance(schedule,list) else []:
        if not isinstance(timing,dict):continue
        record={k:project_value(v,names) for k,v in timing.items() if k in
            ('interval','duration','period','count','initial_time','delay','occurrence')}
        if timing.get('phase'):
            record['description']=project_event(timing,names)['description']
        flags=timing.get('flags',{})
        if flags.get('random_period'):record['summary']='周期随机选择，不代表固定执行顺序'
        if flags.get('random_offset'):record['summary']=record.get('summary','')+'；位置随机选择'
        for source,destination in (('interval_candidates','interval_candidates'),('offset','offset'),('offset_candidates','offsets')):
            if source not in timing:continue
            values=timing[source]
            if source=='interval_candidates':record[destination]=[finite_number(v) for v in values]
            else:
                parsed=[]
                for value in values if isinstance(values,list) else [values]:
                    if isinstance(value,str):
                        vector=[finite_number(v) for v in value.split(',')]
                        parsed.append(vector if vector and all(v is not None for v in vector) else '位置待解析')
                    else:parsed.append(project_value(value,names))
                record[destination]=parsed
        result.append(record)
    return result


def project_graph(graph,names):
    """Summarize graph nodes while retaining proven damage/mechanism quantities."""
    result={'summary':graph_summary(graph)}
    if 'components' in graph:
        result['components']=[project_value(component,names) for component in graph['components']]
    for key in ('actual','display','bonuses','amount','count','total'):
        if key in graph:result[key]=project_value(graph[key],names)
    events=[project_event(trigger,names) for trigger in graph.get('triggers',[])]
    events=[event for event in events if event]
    for node in graph.get('effects',[]):
        if not isinstance(node,dict):continue
        params=mechanism_parameters(node.get('fields',{}))
        # Damage components already carry each amount/count/bonus; avoid listing the
        # same hit a second time as though it were an additional damage segment.
        if params and node.get('type')!='CEffectDamage':
            events.append({'name':TYPE_LABELS.get(node.get('type'),'效果机制'),**params})
    if events:result['events']=events
    return result


def project_value(value, names):
    if isinstance(value,list):
        return [project_value(v,names) for v in value]
    if isinstance(value,dict):
        if isinstance(value.get('execution'),list):
            return {'effects':[project_graph(v,names) for v in value['execution'] if isinstance(v,dict)]}
        if isinstance(value.get('effects'),list) and any(k in value for k in ('root','issues','execution_root')):
            return project_graph(value,names)
        result={}
        for key,item in value.items():
            if key in RAW_KEYS or key in ('effect','id','root','behavior','missile','persistent','search'):continue
            if key=='target':result[key]=project_target(item,names)
            elif key=='trigger':result[key]=project_event(item,names)
            elif key=='schedule':result[key]=project_schedule(item,names)
            elif key=='areas':
                result[key]=[{k.lower():finite_number(v) for k,v in area.items() if k in ('Radius','Arc','Fraction') and finite_number(v) is not None}
                    for area in item if isinstance(area,dict)] if isinstance(item,list) else []
            else:result[key]=project_value(item,names)
        return result
    return friendly(value,names)


def player_entry(entry, data, assets, catalog, names):
    """Preserve content shape; source-rich mechanisms live only in evidence JSON."""
    faction = data.get('_faction')
    def project(obj, key=None):
        if isinstance(obj,list):
            return [project(v,key) for v in obj]
        if not isinstance(obj,dict):
            return obj
        if {'value','status','evidence'}.issubset(obj):
            result = {k:copy.deepcopy(v) for k,v in obj.items() if k not in RAW_KEYS and k not in ('original_value',)}
            if key in ('icon','portrait_image'):
                result['value'] = assets.image(obj,data,'icon')
                if result['value'] is None:
                    result.update(status='unresolved',reason='未找到可转换的明确图像')
            else:
                result['value'] = project_value(obj.get('value'),names)
                if obj.get('value') is None and isinstance(obj.get('partial_text'),str):
                    result['partial_text'] = friendly(obj['partial_text'],names)
                    result['partial'] = result['partial_text']
                elif obj.get('value') is None and isinstance(obj.get('raw_value'),dict):
                    partial = project_value(obj['raw_value'],names)
                    result['partial'] = partial
                execution = obj.get('details',{}).get('execution') if isinstance(obj.get('details'),dict) else None
                if key in ('effect','execution_fact') and isinstance(execution,list):
                    summary='；'.join(graph_summary(graph) for graph in execution if isinstance(graph,dict))
                    if obj.get('details',{}).get('execution_issues'):
                        summary+='；部分执行条件或次数尚未解析'
                    if summary and isinstance(result['value'],str):
                        result['value'] += '；'+summary
                        result['status']='inferred'
                        result['basis']='展示层根据已取证执行图整理机制摘要；原始模板、完整字段及其状态保留于证据包'
                    elif summary and not result.get('partial'):
                        result['partial']=summary
            return result
        return {k:project(v,k) for k,v in obj.items() if k not in RAW_KEYS}

    result = project(entry)
    p = entry['presentation']
    result['icon'] = assets.image(entry.get('icon'),data,'icon')
    result['portrait'] = assets.image(p.get('portrait_image'),data,'portrait',entry.get('portrait'))
    members = []
    for member in p.get('group',{}).get('members',[{'id':entry['id']} ]):
        target = catalog.get((faction,member['id']))
        if not target or target['presentation']['visibility']['value']=='excluded':
            continue
        ident = member['id'].split(':',1)[1]
        members.append({'id':member['id'],'name':display_name(target),
            'icon':assets.image(target.get('icon'),data,'icon'),
            'visibility':target['presentation']['visibility']['value'],
            'kind':member.get('kind','mode'),
            'path':f'data/entries/{faction}/{ident}.json',
            'source_audit':f'data/evidence/{faction}/{ident}.json'})
    label = subfaction_label(p.get('subfaction_key',''))
    classification = p.get('classification',{}).get('value',entry['kind'])
    if classification not in ('unit','hero','building','other'):
        classification = 'other'
    result['navigation'] = {'name':display_name(entry),'faction':faction,
        'factionName':DISPLAY_NAMES.get(faction,faction),'classification':classification,
        'subfaction':label['key'],'subfactionName':label['name'],
        'members':members,'primary':p.get('group',{}).get('primary',entry['id'])}
    result['related_entries'] = [rid for rid in entry.get('related_entries',[]) if
        (faction,rid) in catalog and catalog[(faction,rid)]['presentation']['visibility']['value']!='excluded']
    result['abilities'] = [a for a in result.get('abilities',[]) if
        a['id'].split(':',1)[-1].lower() not in BASIC_ABILITIES and a.get('catalog_type',a.get('type')) not in
        ('CAbilMove','CAbilStop','CAbilAttack','CAbilRally','CAbilQueue')]
    for row in result['presentation'].get('research',[])+result['presentation'].get('available_upgrades',[]):
        associations=[]
        for association in row.get('condition_targets',[]):
            target=catalog.get((faction,association.get('target')))
            if not target or target['presentation']['visibility']['value']=='excluded':
                continue
            producer=catalog.get((faction,association.get('producer')))
            if producer and producer['presentation']['visibility']['value']=='excluded':
                association['producer']=None
            associations.append(association)
        row['condition_targets']=associations
    for weapon_index,weapon in enumerate(result.get('weapons',[]),1):
        if not weapon.get('name',{}).get('value'):
            weapon.setdefault('name',{})['display'] = '武器 '+str(weapon_index)
    for related in result.get('production',[]):
        producer = related.get('producer')
        target = catalog.get((faction,producer))
        if target and target['presentation']['visibility']['value']=='excluded':
            related['producer'] = None
        if target:
            related['producer_name'] = {'value':display_name(target),'status':target['name']['status'],
                'unit':'','evidence':target['name'].get('evidence',[])}
    return result


def write_json(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':'),allow_nan=False),encoding='utf-8')


def other_page(legacy_site, output, visible):
    legacy_site = Path(legacy_site).resolve()
    source = legacy_site/'other.html'
    if not source.is_file():
        (output/'other.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>坠毁系统</title><h1>坠毁系统</h1><p>历史专题资料未提供。</p><a href="index.html">返回百科</a></html>',encoding='utf-8')
        return
    text = source.read_text(encoding='utf-8-sig')
    original = text
    resources = {}

    def copy_resource(reference):
        parsed = urlsplit(html.unescape(reference))
        if parsed.scheme or parsed.netloc or not parsed.path:
            return None
        relative = unquote(parsed.path).replace('\\','/')
        if relative.startswith('/') or ':' in relative:
            return None
        src = (source.parent/relative).resolve()
        if not src.is_relative_to(legacy_site):
            raise ValueError('历史专题资源越出原站目录：'+reference)
        if not src.is_file():
            return None
        sha = digest(src)
        target = output/'assets'/'legacy'/(sha[:24]+src.suffix.lower())
        target.parent.mkdir(parents=True,exist_ok=True)
        if src.suffix.lower()=='.css':
            css = src.read_text(encoding='utf-8-sig')
            def css_url(match):
                ref=match.group(1).strip(' \"\'')
                if urlsplit(ref).scheme or urlsplit(ref).netloc:
                    return 'url("")'
                asset = (src.parent/unquote(urlsplit(ref).path)).resolve()
                if not asset.is_relative_to(legacy_site):
                    raise ValueError('历史样式资源越出原站目录：'+ref)
                if asset.is_file():
                    image_sha=digest(asset)
                    image_target=target.parent/(image_sha[:24]+asset.suffix.lower())
                    shutil.copyfile(asset,image_target)
                    resources[str(image_target.relative_to(output)).replace('\\','/')]=dict(
                        source=str(asset),source_sha256=image_sha,output_sha256=digest(image_target),method='verified legacy CSS resource copy')
                    return 'url("'+image_target.name+'")'
                return 'url("")'
            css = re.sub(r'url\(([^)]+)\)',css_url,css,flags=re.I)
            css = re.sub(r'@import[^;]+;','',css,flags=re.I)
            target.write_text(css,encoding='utf-8')
        else:
            shutil.copyfile(src,target)
        result = str(target.relative_to(output)).replace('\\','/')
        resources[result] = dict(source=str(src),source_sha256=sha,output_sha256=digest(target),
            method='verified legacy local resource copy')
        return result

    def stylesheet(match):
        tag=match.group(0)
        href=re.search(r'href=[\"\']([^\"\']+)[\"\']',tag,flags=re.I)
        if not href or not re.search(r'rel=[\"\']stylesheet[\"\']',tag,flags=re.I):
            return ''
        path=copy_resource(href.group(1))
        return '<link rel="stylesheet" href="'+path+'">' if path else ''
    text = re.sub(r'<link\b[^>]*>',stylesheet,text,flags=re.I)
    def image_source(match):
        path=copy_resource(match.group(1))
        return 'src="'+path+'"' if path else 'data-missing-src="true"'
    text = re.sub(r'src=[\"\']([^\"\']+)[\"\']',image_source,text,flags=re.I)
    def local_link(match):
        href = html.unescape(match.group(1))
        if href.startswith(('http:','https:','//','file:')):
            return 'href="index.html"'
        found = re.fullmatch(r'(?:\.\./)?units/([A-Za-z0-9_]+)/([A-Za-z0-9_]+)\.html',href)
        if found:
            faction, uid = found.groups()
            return 'href="index.html#'+html.escape(urlencode({'faction':faction,'unit':'Unit:'+uid}),quote=True)+'"' if (faction,'Unit:'+uid) in visible else 'href="index.html"'
        if href.startswith('assets/legacy/') and (output/href).is_file():
            return match.group(0)
        if href.startswith('#') or href in ('index.html','other.html'):
            return match.group(0)
        return 'href="index.html"'
    # Legacy content stays a separately labelled topic; only local links survive.
    text = re.sub(r'href="([^"]*)"',local_link,text)
    text = re.sub(r'<script\b[^>]*>.*?</script>','',text,flags=re.S|re.I)
    # Source tables and technical prose become an opt-in source_audit layer, not hidden text
    # in a hover title or accessible name. The original document is retained below.
    text = re.sub(r'(<h2\b[^>]*>(?:来源|防重复校验器|美术与音效)</h2>.*?)(?=<h2\b|</section>|</main>)',
        r'<div class="legacy-debug">\1</div>',text,flags=re.S)
    text = re.sub(r'<th>[^<]*(?:\bID\b|SHA-256|来源)[^<]*</th>',lambda m:m.group(0).replace('<th>','<th class="legacy-debug">'),text)
    text = re.sub(r'（(C(?:Behavior|Effect|Abil|Unit|Actor|Model)[A-Za-z0-9_]+[^）]*)）',
        r'<span class="legacy-debug">（\1）</span>',text)
    text = re.sub(r'<div class="dsc">.*?</div>',
        '<div class="dsc">SCORE 共享的空军坠毁响应专题。</div>',text,flags=re.S)
    text = re.sub(r'\s+(?:title|aria-label)=[\"\'][^\"\']*(?:已确认|未解析|来源|证据|(?:Unit|Effect|Behavior|Weapon|Abil):)[^\"\']*[\"\']','',text)
    text = text.replace('随后执行 Suicide 移除自身','随后自我移除')
    css = '<style>.source-list,.status-help,.badge,.fact-status,.catalog,.src,.legacy-debug,.fact-evidence{display:none}body.show-evidence .source-list,body.show-evidence .status-help,body.show-evidence .badge,body.show-evidence .fact-status,body.show-evidence .catalog,body.show-evidence .src,body.show-evidence .legacy-debug,body.show-evidence .fact-evidence{display:revert}</style>'
    text = text.replace('</head>',css+'</head>')
    text = re.sub(r'<body\b([^>]*)>',r'<body\1><label><input id="legacy-evidence" type="checkbox">特别设置：显示数据与证据</label>',text,count=1)
    source_audit = '<details class="legacy-debug"><summary>历史原文</summary><pre>'+html.escape(original)+'</pre></details>'
    script = '<script>(function(){const box=document.getElementById("legacy-evidence");try{box.checked=localStorage.getItem("score.showEvidence")==="true"}catch{}function apply(){document.body.classList.toggle("show-evidence",box.checked);try{localStorage.setItem("score.showEvidence",String(box.checked))}catch{}}box.onchange=apply;apply()})()</script>'
    text = text.replace('</body>',source_audit+script+'</body>')
    (output/'other.html').write_text(text,encoding='utf-8')
    write_json(output/'assets/legacy-manifest.json',{'resources':resources})


def build(datasets, output, source_root, toolbox_root, game_root, legacy_site, projection=None, include_other=True):
    output = Path(output)
    if output.exists():
        raise FileExistsError('拒绝覆盖现有输出目录：'+str(output))
    keys = [key for key,data in datasets]
    if len(keys)!=len(set(keys)) or any(not re.fullmatch(r'[A-Za-z0-9_]+',key) for key in keys):
        raise ValueError('阵营键重复或路径不安全')
    batches = {data.get('project',{}).get('batch') for key,data in datasets}
    if len(batches)!=1 or None in batches:
        raise ValueError('数据批次不一致或缺失')
    for key,data in datasets:
        ids = [entry.get('id','') for entry in data.get('entries',[])]
        if data.get('schema_version')!=1 or len(ids)!=len(set(ids)) or any(not re.fullmatch(r'Unit:[A-Za-z0-9_]+',uid) for uid in ids):
            raise ValueError('条目格式、ID或路径不正确：'+key)
        if any(entry.get('presentation',{}).get('visibility',{}).get('value') not in ('public','auxiliary','excluded') for entry in data['entries']):
            raise ValueError('需要先生成明确展示资格：'+key)
    output.mkdir(parents=True)
    assets = AssetBuilder(output,toolbox_root,game_root)
    catalog = {(key,e['id']):e for key,data in datasets for e in data['entries']}
    visible = {k for k,e in catalog.items() if e['presentation']['visibility']['value']!='excluded'}
    index = []
    factions = []
    public_count = 0
    content_sizes = []
    evidence_sizes = []
    for key,original in datasets:
        data = dict(original,_faction=key)
        if not data.get('project',{}).get('component_roots'):
            data['project'] = dict(data.get('project',{}),component_roots=[
                str(Path(source_root)/('SCORE-'+key+'.SC2Mod')),str(Path(source_root)/'SCORE-Other.SC2Mod')])
        names = {e['id']:display_name(e) for e in data['entries']}
        for e in data['entries']:
            for item in e.get('weapons',[])+e.get('abilities',[])+e.get('modifiers',[]):
                name = item.get('name',{}).get('value')
                if name:
                    names[item['id']] = name
        for e in data['entries']:
            for row in e['presentation'].get('available_upgrades',[]):
                if row.get('name',{}).get('value'):
                    names[row['id']] = row['name']['value']
        for source in data['sources']:
            oid=source.get('object','')
            family=oid.split(':',1)[0]
            if family in CATALOG_LABELS:
                names.setdefault(oid,CATALOG_LABELS[family])
        names['_bare'] = {oid.split(':',1)[1]:label for oid,label in names.items() if ':' in oid}
        faction_data = {'project':{'component_roots':[str(Path(source_root)/'SCORE-Other.SC2Mod')]}}
        portrait = assets.image(FACTION_PORTRAITS.get(key),faction_data,'faction')
        if not portrait:
            representative = next((e for e in data['entries'] if
                e['presentation']['visibility']['value']=='public' and e.get('icon',{}).get('value')),None)
            if representative:
                portrait = assets.image(representative['icon'],data,'faction')
                record = next((r for r in reversed(assets.records) if r.get('output')==portrait),None)
                if record and portrait:
                    record.update(faction=key,representative_unit=representative['id'],
                        representative_evidence=representative['icon']['evidence'],
                        presentation_role='faction_representative_icon')
        subfactions = {}
        sources = {s['id']:s for s in data['sources']}
        for entry in data['entries']:
            p = entry['presentation']
            visibility = p['visibility']['value']
            if visibility=='excluded':
                continue
            public_count += visibility=='public'
            sub = subfaction_label(p.get('subfaction_key',''))
            subfactions[sub['key']] = sub
            uid = entry['id'].split(':',1)[1]
            path = f'data/entries/{key}/{uid}.json'
            source_audit = f'data/evidence/{key}/{uid}.json'
            projected = (projection or player_entry)(entry,data,assets,catalog,names)
            write_json(output/path,projected)
            evidence_ids = collect_evidence(entry)
            absent = evidence_ids-set(sources)
            if absent:
                raise ValueError('来源引用不存在：'+','.join(sorted(absent)))
            write_json(output/source_audit,{'entry':entry,'sources':[s for s in data['sources'] if s['id'] in evidence_ids],
                'dependency_context':data.get('dependency_context',{})})
            content_sizes.append((output/path).stat().st_size)
            evidence_sizes.append((output/source_audit).stat().st_size)
            nav = projected['navigation']
            index.append({'id':entry['id'],'faction':key,'name':display_name(entry),'icon':projected['icon'],
                'classification':nav['classification'],'visibility':visibility,
                'subfaction':sub['key'],'subfactionName':sub['name'],'group':nav['primary'],
                'members':nav['members'],'path':path,'source_audit':source_audit})
        factions.append({'key':key,'name':DISPLAY_NAMES.get(key,key),'race':RACES.get(key,'terran'),
            'portrait':portrait,'subfactions':list(subfactions.values())})
    write_json(output/'data/index.json',{'entries':index})
    if (output/'data/index.json').stat().st_size>2*1024*1024:
        raise ValueError('总览索引超过 2 MiB')
    assets.save()
    if include_other:
        other_page(legacy_site,output,visible)
    manifest = {'format_version':1,'batch':next(iter(batches)),'min_client_version':1,
        'factions':factions,'entry_count':len(catalog),'public_count':public_count,
        'excluded_count':len(catalog)-len(visible),'files':{'index':'data/index.json'},
        'file_hashes':{str(p.relative_to(output)).replace('\\','/'):digest(p)
            for p in sorted(output.rglob('*')) if p.is_file()},
        'sizes':{'index_bytes':(output/'data/index.json').stat().st_size,
            'max_entry_bytes':max(content_sizes,default=0),'max_evidence_bytes':max(evidence_sizes,default=0)}}
    write_json(output/'manifest.json',manifest)
    return manifest
