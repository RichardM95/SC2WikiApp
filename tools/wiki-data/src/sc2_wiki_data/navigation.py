"""Evidence-backed player navigation; never modifies SCORE's component files."""
from collections import defaultdict
import copy
import re

from .mechanics import children, evidence, fact, fields, refs, research, value
from .refine_costs import hydrate_sources


from .factions import default_config, normalize_prefix

# Component identities stay stable; display text comes from the shared configuration.
SOURCE_ORIGINS=('Cerberus','CovertOps','Golden','HTXL','Ihanrii','Moebius','Primal','Purifier','Taldarim','Umojan','Webby')
_config=default_config()
DISPLAY_NAMES={row['id']:row['name'] for row in _config['factions'] if row['id'] in SOURCE_ORIGINS}
for _origin in SOURCE_ORIGINS:
    DISPLAY_NAMES.setdefault(_origin,_origin)
SUBFACTION_NAMES={}
for _row in _config['factions']:
    for _alias in _row['prefixes']:
        _branch=_row.get('branches',{}).get(_alias)
        _separator=' ' if _branch=='F' else '·'
        SUBFACTION_NAMES[normalize_prefix(_alias)]=_row['name']+(_separator+_branch if _branch else '')
TECH_PREFIX = re.compile(r'^(?:!|(?:weapon|wrapon)\s*-)', re.I)
CJK = re.compile(r'[\u3400-\u9fff]')


def display_name(entry):
    """ID fallback belongs only to display output, never authoritative Name."""
    return entry.get('name', {}).get('value') or entry['id'].split(':', 1)[-1]


def text_field(ctx, family, oid, field, fallback=None):
    node = ctx.node(family, oid)
    anchor, path = value(node, field)
    anchors = evidence(ctx, family, oid, [path]) if anchor else []
    keys = list(dict.fromkeys(k for k in (anchor, fallback) if k))
    for key in keys:
        for locale in ('zhCN', 'enUS'):
            hit = ctx._texts.get((locale, key))
            if not hit:
                continue
            text, record = hit
            result = dict(value=text, unit='', status='confirmed',
                evidence=(anchors if key == anchor else []) + [ctx._source(family, oid, key, record, locale=locale)],
                key=key, source_locale=locale, source_language=locale)
            return result
    return ctx.missing('活动组件中没有可解析的 '+field+' 本地化引用或惯例键', anchors)


def normalized_prefix(prefix):
    return re.sub(r'^\s*-\s*', '', str(prefix or '')).strip()


def subfaction_label(key):
    if not key:
        return {'key':'', 'name':'未指定'}
    if key in SUBFACTION_NAMES:
        result = {'key':key, 'name':SUBFACTION_NAMES[key]}
        if key == 'Deluxe':
            result.update(draft=True, original=key,
                basis='SCORE 未登记适用项目译名；词典 Deluxe 无命中，按子派系专名制作中性音译草稿，未写入确认库')
        return result
    if CJK.search(key):
        return {'key':key, 'name':key}
    # Never silently turn an unseen English prefix into a confirmed translation.
    return {'key':key, 'name':'未译子派系', 'draft':True, 'original':key,
        'basis':'该前缀不在已审校映射中；需本地化审校后补充中文名称'}


def classify(ctx, entry, node):
    uid = entry['id'].split(':', 1)[1]
    p = entry.setdefault('presentation', {})
    prior = p.get('classification', {'value':entry.get('kind','unit'),
        'reason':'沿用此前词条类型', 'evidence':[]})
    categories, cp = value(node, 'EditorCategories')
    if 'ObjectType:Hero' in {part.strip() for part in (categories or '').split(',')}:
        p['classification'] = {'value':'hero', 'reason':'有效 EditorCategories 明确声明 ObjectType:Hero',
            'evidence':evidence(ctx, 'Unit', uid, [cp])}
    elif prior.get('value') == 'unclassified':
        p['classification'] = {'value':'other',
            'reason':'按用户展示要求将待分类对象归入其他；原始分类依据：'+prior.get('reason','未解析'),
            'evidence':list(prior.get('evidence',[])), 'previous_value':'unclassified'}
    prefix = p['editor_prefix'].get('value') or ''
    technical_paths = []
    if TECH_PREFIX.search(prefix.strip()):
        ev = list(p['editor_prefix'].get('evidence',[]))
        reason = '有效 EditorPrefix 为飞行物／弹药技术标记：'+prefix
    else:
        category_parts = {part.strip().lower() for part in (categories or '').split(',')}
        if category_parts & {'objecttype:projectile','objecttype:missile','objecttype:ammunition'}:
            technical_paths.append(cp)
        for child, path in children(node):
            if child.tag == 'FlagArray' and child.get('index') in ('Missile','Projectile') and child.get('value') == '1':
                technical_paths.append(path+'|@value')
        if node is not None and (node.get('parent') or '').upper() in ('MISSILE','PROJECTILE'):
            technical_paths.append('@parent')
        ev = evidence(ctx, 'Unit', uid, technical_paths)
        reason = '有效分类或 Missile／Projectile 标志明确指定技术对象'
    if ev:
        p['visibility'] = {'value':'excluded', 'reason':reason, 'evidence':ev}
    elif p.get('classification',prior).get('value') == 'auxiliary':
        p['visibility'] = {'value':'auxiliary', 'reason':prior.get('reason','辅助对象默认隐藏'),
            'evidence':list(prior.get('evidence',[]))}
    else:
        current = p.get('classification', prior)
        p['visibility'] = {'value':'public', 'reason':'玩家条目；'+current.get('reason','沿用已有分类'),
            'evidence':list(current.get('evidence',[]))}


def exclude_ammunition(ctx, data):
    """LaunchMissile.AmmoUnit is a literal technical role, not a name guess."""
    byid = {e['id']:e for e in data['entries']}
    def visit(obj):
        if isinstance(obj,dict):
            if obj.get('type') == 'CEffectLaunchMissile' and obj.get('id','').startswith('Effect:'):
                oid = obj['id'].split(':',1)[1]
                for path,uid in obj.get('fields',{}).items():
                    if path.split('|')[0] != 'AmmoUnit' or not isinstance(uid,str):
                        continue
                    target = byid.get('Unit:'+uid)
                    ev = evidence(ctx,'Effect',oid,[path])
                    if target and ev:
                        target['presentation']['visibility'] = {'value':'excluded',
                            'reason':'有效 CEffectLaunchMissile.AmmoUnit 明确绑定为弹药单位', 'evidence':ev}
            for value in obj.values():
                visit(value)
        elif isinstance(obj,list):
            for value in obj:
                visit(value)
    for entry in data['entries']:
        for weapon in entry.get('weapons',[]):
            visit(weapon.get('effect',{}))
        for ability in entry.get('abilities',[]):
            visit(ability.get('effect',{}))


def group_entries(ctx, entries):
    """Group only evidenced mode/alias pairs or a literal ReviveType chain."""
    byid = {e['id'].split(':',1)[1]:e for e in entries}
    position = {e['id'].split(':',1)[1]:i for i,e in enumerate(entries)}
    nodes = {uid:ctx.node('Unit',uid) for uid in byid}
    parents = {uid:uid for uid in byid}
    relations = []
    rank = defaultdict(int)
    morph_edges=defaultdict(dict)
    for uid,node in nodes.items():
        for aid,ap in refs(node,'AbilArray'):
            ability=ctx.node('Abil',aid)
            if ability is None or ability.tag!='CAbilMorph':continue
            for path,target in fields(ability):
                if target not in byid or not ('Unit' in path.split('|') or path.endswith('|@Unit')):continue
                ev=evidence(ctx,'Unit',uid,[ap])+evidence(ctx,'Abil',aid,[path])
                morph_edges[uid].setdefault(target,[]).extend(ev)

    def root(uid):
        while parents[uid] != uid:
            parents[uid] = parents[parents[uid]]
            uid = parents[uid]
        return uid

    def join(a, b, kind, ev):
        if a == b or a not in byid or b not in byid or not ev:
            return
        if any(byid[u]['presentation']['visibility']['value']=='excluded' for u in (a,b)):
            return
        # Different active sub-factions may contain similarly named units.
        if byid[a]['presentation']['subfaction_key'] != byid[b]['presentation']['subfaction_key']:
            return
        parents[root(b)] = root(a)
        relations.append((a,b,kind,list(dict.fromkeys(ev))))

    revive = {}
    alias = {}
    for uid, node in nodes.items():
        target, path = value(node,'ReviveType')
        if target in byid and target != uid:
            revive[uid] = target
            rank[target] += 100
            rank[uid] -= 100
            join(uid,target,'revive',evidence(ctx,'Unit',uid,[path]))
        leader, lp = value(node,'LeaderAlias')
        if leader in byid and leader != uid:
            alias[uid] = leader
            # Alias alone is not enough for unrelated units with shared selection.
            if display_name(byid[uid]) == display_name(byid[leader]):
                join(uid,leader,'mode',evidence(ctx,'Unit',uid,[lp])+
                    byid[uid]['name'].get('evidence',[])+byid[leader]['name'].get('evidence',[]))
                rank[leader] += 20
                rank[uid] -= 20

    for uid, node in nodes.items():
        for aid, ap in refs(node,'AbilArray'):
            ability = ctx.node('Abil',aid)
            if ability is None or ability.tag not in ('CAbilMorph','CAbilMorphPlacement','CAbilMerge',
                'CAbilBurrow','CAbilBurrowUp','CAbilBurrowDown'):
                continue
            targets = [(v,p) for p,v in fields(ability) if
                (('Unit' in p.split('|') or 'MorphUnit' in p.split('|') or p.endswith('|@Unit')) and v in byid)]
            revival = uid in revive and any(target == revive[uid] for target,path in targets)
            for target,path in targets:
                same_name = display_name(byid[uid]) == display_name(byid[target])
                explicit_alias = alias.get(uid)==target or alias.get(target)==uid
                subgroup_sources=[]
                # A reversible mode pair may use distinct player names (Hellion/
                # Hellbat). Require both literal morph edges AND the selection alias.
                reversible=target in morph_edges[uid] and uid in morph_edges[target]
                if reversible:
                    for unit_id,other_id in ((uid,target),(target,uid)):
                        subgroup,subgroup_path=value(nodes[unit_id],'SubgroupAlias')
                        if subgroup==other_id:
                            subgroup_sources+=evidence(ctx,'Unit',unit_id,[subgroup_path])
                            rank[other_id]+=30
                mode_pair=bool(reversible and subgroup_sources)
                if not (revival or same_name or explicit_alias or mode_pair or revive.get(target)==uid):
                    continue
                ev = evidence(ctx,'Unit',uid,[ap]) + evidence(ctx,'Abil',aid,[path])
                if same_name:
                    ev += byid[uid]['name'].get('evidence',[])+byid[target]['name'].get('evidence',[])
                if mode_pair:
                    ev+=morph_edges[uid][target]+morph_edges[target][uid]+subgroup_sources
                if revival:
                    ev += evidence(ctx,'Unit',uid,[value(node,'ReviveType')[1]])
                    if target != revive[uid]:
                        rank[target] -= 60
                join(uid,target,'revive' if revival or revive.get(target)==uid else 'morph',ev)

    groups = defaultdict(list)
    for uid in byid:
        groups[root(uid)].append(uid)
    for members in groups.values():
        primary = max(members,key=lambda u:(rank[u],byid[u]['presentation']['visibility']['value']=='public',-position[u]))
        attached = defaultdict(list)
        kinds = {}
        for a,b,kind,ev in relations:
            if a in members and b in members:
                attached[a].extend(ev); attached[b].extend(ev)
                kinds[a]=kind; kinds[b]=kind
        group = {'primary':'Unit:'+primary, 'members':[
            {'id':'Unit:'+uid, 'kind':'primary' if uid==primary else kinds.get(uid,'mode'),
             'evidence':list(dict.fromkeys(attached[uid]))} for uid in [primary]+[u for u in members if u!=primary]]}
        hero = next((byid[u]['presentation']['classification'] for u in members
                     if byid[u]['presentation']['classification']['value']=='hero'),None)
        for uid in members:
            byid[uid]['presentation']['group'] = copy.deepcopy(group)
            if hero and byid[uid]['presentation']['visibility']['value'] != 'excluded':
                byid[uid]['presentation']['classification'] = dict(hero,
                    reason='明确同一英雄形态组；'+hero['reason'])


def discover_shapes(ctx, data):
    """Return new local Unit IDs and proof, without creating authoritative entries.

    Result rows: {id: bare Unit ID, from: Unit:existing, kind, evidence}.
    Only actual morph/burrow, paired aliases, or explicit resurrection chains count.
    """
    hydrate_sources(ctx,data)
    existing = {e['id'].split(':',1)[1]:e for e in data['entries']}
    local = {oid for (family,oid),definitions in ctx.catalog.objects.items() if family=='Unit' and
        any(d.get('source_id')==ctx.primary_sid for d in definitions)}
    selected = set(existing)
    queue = list(existing)
    discovered = {}
    name_cache = {uid:e['name'] for uid,e in existing.items()}
    prefix_cache = {}
    rejected = {}
    ammunition = set()
    for (family,oid),definitions in ctx.catalog.objects.items():
        if family != 'Effect' or not any(d.get('source_id')==ctx.primary_sid for d in definitions):
            continue
        effect = ctx.node('Effect',oid)
        if effect is not None and effect.tag=='CEffectLaunchMissile':
            ammo,path = value(effect,'AmmoUnit')
            if ammo:
                ammunition.add(ammo)

    def name(uid):
        if uid not in name_cache:
            name_cache[uid] = text_field(ctx,'Unit',uid,'Name','Unit/Name/'+uid)
        return name_cache[uid]

    def prefix(uid):
        if uid not in prefix_cache:
            prefix_cache[uid] = text_field(ctx,'Unit',uid,'EditorPrefix','Unit/EditorPrefix/'+uid)
        return prefix_cache[uid]

    def eligible(uid):
        if uid not in local:
            rejected.setdefault(uid,'目标不在当前活动主组件的明确 Unit 定义中')
            return False
        node = ctx.node('Unit',uid)
        if node is None:
            rejected.setdefault(uid,'目标 Unit 对象无法解析')
            return False
        raw = prefix(uid).get('value') or ''
        category = (value(node,'EditorCategories')[0] or '').lower()
        missile = any(c.tag=='FlagArray' and c.get('index') in ('Missile','Projectile') and c.get('value')=='1' for c in node)
        if uid in ammunition or TECH_PREFIX.search(raw.strip()) or missile or (node.get('parent') or '').upper() in ('MISSILE','PROJECTILE') or any(
            token in category.split(',') for token in ('objecttype:projectile','objecttype:missile','objecttype:ammunition')):
            rejected.setdefault(uid,'目标为明确技术飞行物或弹药，不生成玩家形态')
            return False
        if uid in existing and existing[uid].get('presentation',{}).get('visibility',{}).get('value')=='excluded':
            rejected.setdefault(uid,'目标已明确排除于玩家百科')
            return False
        return True

    def same_family(a,b):
        na,nb = name(a).get('value'),name(b).get('value')
        leader_a = value(ctx.node('Unit',a),'LeaderAlias')[0]
        leader_b = value(ctx.node('Unit',b),'LeaderAlias')[0]
        same = bool(na and nb and na==nb)
        aliases = leader_a==b or leader_b==a
        pa,pb = normalized_prefix(prefix(a).get('value')),normalized_prefix(prefix(b).get('value'))
        return (same or aliases) and (not pa or not pb or pa==pb)

    def add(uid,source,kind,ev):
        if uid in selected or uid==source or not ev or not eligible(uid):
            return
        selected.add(uid)
        discovered[uid] = {'id':uid,'from':'Unit:'+source,'kind':kind,'evidence':list(dict.fromkeys(ev))}
        queue.append(uid)

    # Reverse aliases are discovered only through equal effective names; shared selection
    # aliases with distinct unit names do not invent a form relationship.
    reverse_aliases = defaultdict(list)
    for uid in sorted(local):
        leader, path = value(ctx.node('Unit',uid),'LeaderAlias')
        if leader:
            reverse_aliases[leader].append((uid,path))

    cursor = 0
    while cursor < len(queue):
        uid = queue[cursor]; cursor += 1
        if not eligible(uid):
            continue
        node = ctx.node('Unit',uid)
        revive, rp = value(node,'ReviveType')
        if revive and revive in local:
            add(revive,uid,'revive',evidence(ctx,'Unit',uid,[rp]))
        leader, lp = value(node,'LeaderAlias')
        if leader and leader in local and same_family(uid,leader):
            add(leader,uid,'mode',evidence(ctx,'Unit',uid,[lp])+name(uid).get('evidence',[])+name(leader).get('evidence',[]))
        for variant,path in reverse_aliases.get(uid,[]):
            if name(uid).get('value') and name(uid).get('value')==name(variant).get('value'):
                add(variant,uid,'mode',evidence(ctx,'Unit',variant,[path])+name(uid).get('evidence',[])+name(variant).get('evidence',[]))
        for aid, ap in refs(node,'AbilArray'):
            ability = ctx.node('Abil',aid)
            if ability is None or ability.tag not in ('CAbilMorph','CAbilMorphPlacement','CAbilBurrow','CAbilBurrowUp','CAbilBurrowDown'):
                continue
            targets = [(v,p) for p,v in fields(ability) if isinstance(v,str) and
                ('Unit' in p.split('|') or 'MorphUnit' in p.split('|') or p.endswith('|@Unit'))]
            resurrection = bool(revive and any(target==revive for target,path in targets))
            for target,path in targets:
                if not target or target==uid:
                    continue
                target_revive = value(ctx.node('Unit',target),'ReviveType')[0] if target in local else None
                related = resurrection or target_revive==uid or (target in local and same_family(uid,target))
                if not related:
                    rejected.setdefault(target,'实际变形关系通向不同兵种或共享生产载体，未证明同一单位形态')
                    continue
                ev = evidence(ctx,'Unit',uid,[ap])+evidence(ctx,'Abil',aid,[path])
                if resurrection:
                    ev += evidence(ctx,'Unit',uid,[rp])
                elif target_revive==uid:
                    ev += evidence(ctx,'Unit',target,[value(ctx.node('Unit',target),'ReviveType')[1]])
                else:
                    ev += name(uid).get('evidence',[])+name(target).get('evidence',[])
                    for object_id in (uid,target):
                        alias_value,alias_path = value(ctx.node('Unit',object_id),'LeaderAlias')
                        if alias_value in (uid,target):
                            ev += evidence(ctx,'Unit',object_id,[alias_path])
                add(target,uid,'revive' if resurrection or target_revive==uid else 'morph',ev)
    if rejected:
        data.setdefault('diagnostics',[]).append({'code':'shape_discovery_excluded',
            'reason':'发现范围限定同主组件、明确同兵种关系；以下目标未新增为玩家形态',
            'objects':[{'id':'Unit:'+uid,'reason':reason} for uid,reason in sorted(rejected.items()) if uid not in selected]})
    data['sources'] = ctx.sources
    return list(discovered.values())


def research_rows(ctx, entries):
    result = []
    for producer in entries:
        if producer['presentation']['visibility']['value']=='excluded':
            continue
        uid = producer['id'].split(':',1)[1]
        rows = producer['presentation'].get('research') or research(ctx,uid,ctx.node('Unit',uid))
        for source in rows:
            row = copy.deepcopy(source)
            aid = row.get('ability','').removeprefix('Abil:')
            an = ctx.node('Abil',aid)
            matches = [(n,p) for n,p in children(an) if n.tag=='InfoArray' and n.get('index')==row.get('command')]
            if matches:
                info, path = matches[0]
                button = info.find('Button')
                face = button.get('DefaultButtonFace') if button is not None else None
                if face:
                    name = text_field(ctx,'Button',face,'Name','Button/Name/'+face)
                    if name.get('value'):
                        row['name'] = name
                    description = text_field(ctx,'Button',face,'Tooltip','Button/Tooltip/'+face)
                    if description.get('value'):
                        row['description'] = description
                    row['face'] = face
            row['producer'] = producer['id']
            row['producer_name'] = copy.deepcopy(producer['name'])
            result.append(row)
    return result


def attach_condition_targets(data):
    """Reverse existing CountUpgrade paths; association is not sole unlock proof."""
    sources={s['id']:s for s in data['sources']}
    relations=defaultdict(list)
    def upgrade_refs(item,inherited_evidence=()):
        found={}
        if isinstance(item,dict):
            local_evidence=item.get('evidence',inherited_evidence)
            if item.get('type')=='CRequirementCountUpgrade':
                for field,upgrade in item.get('fields',{}).items():
                    if not (field.startswith('Count|') and field.endswith('@Link') and isinstance(upgrade,str)):
                        continue
                    evidence_ids=[sid for sid in local_evidence if sid in sources and
                        sources[sid].get('object')==item.get('id') and
                        (sources[sid].get('field')=='Count' or sources[sid].get('field','').startswith('Count|'))]
                    found.setdefault('Upgrade:'+upgrade,[]).extend(evidence_ids or local_evidence)
            for value in item.values():
                for upgrade,ev in upgrade_refs(value,local_evidence).items():
                    found.setdefault(upgrade,[]).extend(ev)
        elif isinstance(item,list):
            for value in item:
                for upgrade,ev in upgrade_refs(value,inherited_evidence).items():
                    found.setdefault(upgrade,[]).extend(ev)
        return found
    production_map=defaultdict(list)
    for entry in data['entries']:
        for row in entry.get('production',[]):
            production_map[(row.get('producer'),row.get('ability'),row.get('command'))].append(entry['id'])
            for condition in row.get('requirements',[]):
                for upgrade,ev in upgrade_refs(condition).items():
                    relations[upgrade].append({'target':entry['id'],'producer':row.get('producer'),
                        'ability':row.get('ability'),'command':row.get('command'),
                        'relation':'production_requirement','condition':copy.deepcopy(condition),
                        'evidence':list(dict.fromkeys(ev+condition.get('evidence',[])+row.get('evidence',[])))})
    for entry in data['entries']:
        for card in entry['presentation'].get('command_cards',[]):
            for button in card.get('buttons',[]):
                condition=button.get('conditions',{})
                for upgrade,ev in upgrade_refs(condition).items():
                    targets=production_map.get((entry['id'],button.get('ability'),button.get('command')),[]) or [entry['id']]
                    for target in targets:
                        relations[upgrade].append({'target':target,'producer':entry['id'],
                            'ability':button.get('ability'),'command':button.get('command'),
                            'relation':'command_condition','condition':copy.deepcopy(condition),
                            'evidence':list(dict.fromkeys(ev+condition.get('evidence',[])+button.get('evidence',[])))})
    row_count=target_count=0
    for entry in data['entries']:
        for row in entry['presentation'].get('research',[])+entry['presentation'].get('available_upgrades',[]):
            targets=[];seen=set()
            for target in relations.get(row['id'],[]):
                key=(target['target'],target['producer'],target['ability'],target['command'],
                    target['relation'],tuple(target['condition'].get('evidence',[])))
                if key in seen:continue
                seen.add(key);targets.append(copy.deepcopy(target))
            row['condition_targets']=targets
            row_count+=1;target_count+=len(targets)
    return {'research_rows':row_count,'association_records':target_count,
        'distinct_state_upgrades':len(relations),
        'basis':'精确引用已有 RequirementCountUpgrade 的条件关系；保留完整条件，不声明单项升级独自解锁'}


def enrich(ctx, data):
    """Enrich a generated or hydrated v2 batch in place, appending source IDs."""
    hydrate_sources(ctx,data)
    entries = data['entries']
    for entry in entries:
        uid = entry['id'].split(':',1)[1]
        node = ctx.node('Unit',uid)
        p = entry.setdefault('presentation',{})
        p['editor_prefix'] = text_field(ctx,'Unit',uid,'EditorPrefix','Unit/EditorPrefix/'+uid)
        raw = p['editor_prefix'].get('value') or ''
        p['subfaction_key'] = '' if TECH_PREFIX.search(raw.strip()) else normalized_prefix(raw)
        classify(ctx,entry,node)
        for weapon in entry.get('weapons',[]):
            wid = weapon['id'].split(':',1)[1]
            weapon['name'] = text_field(ctx,'Weapon',wid,'Name','Weapon/Name/'+wid)
            icon,path = value(ctx.node('Weapon',wid),'Icon')
            weapon['icon'] = fact(ctx,icon,'Weapon',wid,[path])
        for modifier in entry.get('modifiers',[]):
            family, mid = modifier['id'].split(':',1)
            modifier['name'] = text_field(ctx,family,mid,'Name',family+'/Name/'+mid)
        for ability in entry.get('abilities',[]):
            aid = ability['id'].split(':',1)[1]
            ability_node = ctx.node('Abil',aid)
            ability['catalog_type'] = ability_node.tag if ability_node is not None else None
            buttons = [button for card in p.get('command_cards',[]) for button in card.get('buttons',[])
                if button.get('ability')==ability['id'] and button.get('command') not in ('Cancel','CancelLast','CancelSlot')]
            if buttons:
                button = buttons[0]
                ability['icon'] = copy.deepcopy(button['icon'])
                ability['hotkey'] = copy.deepcopy(button['hotkey'])
                for key in ('name','effect'):
                    candidate = button.get('name' if key=='name' else 'description',{})
                    current = ability.get(key,{})
                    if candidate.get('value') and (not current.get('value') or
                        (key=='name' and not CJK.search(str(current['value'])) and CJK.search(str(candidate['value'])))):
                        ability[key] = copy.deepcopy(candidate)
                        ability[key]['evidence'] = list(dict.fromkeys(ability[key].get('evidence',[])+button.get('evidence',[])))
    exclude_ammunition(ctx,data)
    group_entries(ctx,entries)
    research_index = defaultdict(list)
    for row in research_rows(ctx,entries):
        research_index[row['id']].append(row)
    missing = set()
    for entry in entries:
        p = entry['presentation']
        p['available_upgrades'] = []
        seen = set()
        for modifier in entry.get('modifiers',[]):
            if modifier.get('kind')!='upgrade':
                continue
            for row in research_index.get(modifier['id'],[]):
                key = (row['id'],row['producer'],row['ability'],row['command'])
                if key in seen:
                    continue
                seen.add(key)
                copyrow = copy.deepcopy(row)
                copyrow['effect'] = copy.deepcopy(modifier['effect'])
                copyrow['association_evidence'] = list(modifier.get('evidence',[]))
                p['available_upgrades'].append(copyrow)
            if not research_index.get(modifier['id']):
                missing.add(modifier['id'])
    if missing:
        diagnostic = {'code':'available_upgrade_research_missing',
            'reason':'升级影响已解析，但本批可见单位未声明对应有效 CAbilResearch 研发入口；不以父对象或命名推测入口。Specialize/变形技能仍保留在技能及生产记录中。',
            'upgrades':sorted(missing)}
        data.setdefault('diagnostics',[]).append(diagnostic)
    attach_condition_targets(data)
    data['sources'] = ctx.sources
    return data
