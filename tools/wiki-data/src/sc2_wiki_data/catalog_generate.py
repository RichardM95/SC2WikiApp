"""Generate isolated SCORE v2 wiki batches; --dry-run performs no writes."""


from __future__ import annotations


import argparse


import hashlib


import importlib.util


import json


import re


import sys


from pathlib import Path


from .catalog_adapter import ComponentPool, numeric, value


def base_stat(ctx, uid, field, unit):
    raw = value(ctx.node('Unit', uid), field)
    number = numeric(raw)
    if number is None:
        evidence = []
        if raw is not None:
            evidence = [ctx.evidence('Unit', uid, field)]
        return ctx.missing('有效对象、父级和活动来源没有可证实的数值：' + field, evidence)
    if number == 0 and field in ('ShieldsMax', 'EnergyMax'):
        return ctx.na('有效来源明确声明该容量为 0', [ctx.evidence('Unit', uid, field)])
    return ctx.fact(number, 'Unit', uid, field, unit=unit)


def classify(ctx, uid):
    node = ctx.node('Unit', uid)
    fields = ctx.fields('Unit', uid)
    for path in ('FlagArray|Structure', 'Attributes|Structure'):
        if numeric(fields.get(path, {}).get('value')) == 1:
            return dict(value='building', reason='活动有效字段明确标记建筑', evidence=[ctx.evidence('Unit', uid, path)])
    for path in ('FlagArray|Missile',):
        if numeric(fields.get(path, {}).get('value')) == 1:
            return dict(value='auxiliary', reason='活动有效字段明确标记弹体', evidence=[ctx.evidence('Unit', uid, path)])
    # An actual class template is evidence; spelling or an English name is not classification evidence.
    parent = node.get('parent') if node is not None else None
    visited = set()
    while parent and parent not in visited:
        visited.add(parent)
        if parent in ('MISSILE', 'MISSILE_INVULNERABLE', 'MISSILE_HALFLIFE', 'PLACEHOLDER', 'PLACEHOLDER_AIR'):
            return dict(value='auxiliary', reason='继承明确的弹体或占位模板', evidence=[ctx.evidence('Unit', uid, '@parent')])
        if parent in ('ITEM', 'POWERUP', 'DESTRUCTIBLE', 'SMSET', 'STARMAP', 'SMCHARACTER', 'SMCAMERA'):
            return dict(value='other', reason='继承明确的物件或场景模板', evidence=[ctx.evidence('Unit', uid, '@parent')])
        ancestor = ctx.node('Unit', parent)
        parent = ancestor.get('parent') if ancestor is not None else None
    for path in ('FlagArray|ArmySelect',):
        if numeric(fields.get(path, {}).get('value')) == 1:
            return dict(value='unit', reason='有效字段包含单位活动或部队选择标记', evidence=[ctx.evidence('Unit', uid, path)])
    return dict(value='unclassified', reason='现有有效来源不足以证明玩家分类', evidence=[ctx.evidence('Unit', uid, '@id')])


def visuals(ctx, uid):
    bindings = ctx.actor_bindings(uid)
    if not bindings:
        missing = ctx.missing('活动 Actor 没有可证实的 unitName 或单位创建事件绑定')
        return missing.copy(), missing.copy(), missing.copy(), []
    # Later local overrides win; same-priority distinct bindings remain visible for review.
    top = bindings[0][:2]
    candidates = [(aid, sid) for rank, layer, aid, sid in bindings if (rank, layer) == top]
    icon = portrait = image = model_image = None
    for aid, bind in candidates:
        actor = ctx.node('Actor', aid)
        if icon is None:
            for field in ('UnitIcon', 'HeroIcon', 'GroupIcon'):
                ref = value(actor, field)
                if ref and '##' not in ref:
                    icon = ctx.fact(ref, 'Actor', aid, field)
                    icon['evidence'].append(bind)
                    break
        if image is None:
            ref = value(actor, 'PortraitImage')
            if ref and '##' not in ref:
                image = ctx.fact(ref, 'Actor', aid, 'PortraitImage')
                image['evidence'].append(bind)
        if portrait is None:
            model = value(actor, 'PortraitModel')
            if model:
                mnode = ctx.node('Model', model)
                static_image = value(mnode, 'Image')
                if model_image is None and static_image and '##' not in static_image:
                    model_image = ctx.fact(static_image, 'Model', model, 'Image')
                    model_image['evidence'].extend([ctx.evidence('Actor', aid, 'PortraitModel'), bind])
                asset = value(mnode, 'Model')
                if asset and '##' not in asset:
                    portrait = ctx.fact(asset, 'Model', model, 'Model')
                    portrait['evidence'].extend([ctx.evidence('Actor', aid, 'PortraitModel'), bind])
            if portrait is None:
                pactor = value(actor, 'PortraitActor')
                if pactor:
                    pnode = ctx.node('Actor', pactor)
                    model = value(pnode, 'Model')
                    mnode = ctx.node('Model', model) if model else None
                    static_image = value(mnode, 'Image')
                    if model_image is None and static_image and '##' not in static_image:
                        model_image = ctx.fact(static_image, 'Model', model, 'Image')
                        model_image['evidence'].extend([ctx.evidence('Actor', aid, 'PortraitActor'), ctx.evidence('Actor', pactor, 'Model'), bind])
                    asset = value(ctx.node('Model', model), 'Model') if model else None
                    if asset and '##' not in asset:
                        portrait = ctx.fact(asset, 'Model', model, 'Model')
                        portrait['evidence'].extend([ctx.evidence('Actor', aid, 'PortraitActor'), ctx.evidence('Actor', pactor, 'Model'), bind])
    if len(candidates) > 1:
        ctx.review_items.append(f'Unit:{uid} 存在多个同优先级 Actor 绑定：' + '、'.join(a for a, _ in candidates))
    return (icon or ctx.missing('实际绑定 Actor 及其父级没有可解析图标', [s for _, s in candidates]),
        portrait or ctx.missing('实际绑定 Actor 的肖像模型/肖像 Actor 未解析为具体模型引用', [s for _, s in candidates]),
        model_image or image or ctx.missing('实际绑定 Actor 及其肖像 Model 未提供 PortraitImage/Image', [s for _, s in candidates]),
        [dict(id='Actor:' + a, evidence=[s]) for a, s in candidates])


def build_entry(ctx, old_entry, faction):
    uid = old_entry['id'].split(':', 1)[1]
    node = ctx.node('Unit', uid)
    if node is None:
        raise ValueError('Selected v1 unit no longer exists: ' + uid)
    classification = classify(ctx, uid)
    icon, portrait, image, actors = visuals(ctx, uid)
    race = value(node, 'Race')
    # Faction membership is the selected source component, not an ID-name heuristic.
    faction_fact = ctx.fact(faction['name'], 'Unit', uid, '@id', status='inferred',
        basis='该对象列在用户选定阵营主组件的原始 Unit 定义中；同名依赖覆盖不改变本批归属')
    stats = {key: base_stat(ctx, uid, field, unit) for key, field, unit in (
        ('life', 'LifeMax', 'life'), ('shields', 'ShieldsMax', 'shields'),
        ('energy', 'EnergyMax', 'energy'), ('armor', 'LifeArmor', 'armor'),
        ('speed', 'Speed', 'catalog_speed'), ('sight', 'Sight', 'catalog_distance'),
        ('radius', 'Radius', 'catalog_distance'))}
    entry = dict(id=old_entry['id'], kind='building' if classification['value'] == 'building' else 'unit',
        name=ctx.text('Unit', uid, ['Unit/Name/' + uid]),
        description=ctx.text('Unit', uid, ['Unit/Tooltip/' + uid, 'Button/Tooltip/' + uid]),
        faction=faction_fact, icon=icon, portrait=portrait, stats=stats,
        production=[], weapons=[], abilities=[], modifiers=[], related_entries=[],
        presentation=dict(classification=classification, portrait_image=image, actors=actors,
            command_cards=[], research=[], variants=[]))
    from . import mechanics
    mechanics.enrich_entry(ctx, entry, uid)
    return entry


def build_faction(pool, faction, baseline, suffix, refine=False):
    ctx = pool.context(faction['component'])
    entries = [build_entry(ctx, old, faction) for old in baseline['entries']]
    ids = {e['id'] for e in entries}
    for entry in entries:
        related = set()
        for production in entry['production']:
            if production.get('producer') in ids and production['producer'] != entry['id']:
                related.add(production['producer'])
        for other in entries:
            if other['id'] != entry['id'] and any(p.get('producer') == entry['id'] for p in other['production']):
                related.add(other['id'])
        for variant in entry['presentation']['variants']:
            if isinstance(variant, dict) and variant.get('id') in ids and variant['id'] != entry['id']:
                related.add(variant['id'])
        entry['related_entries'] = sorted(related)
    roots = [s['root_path'] for s in reversed(ctx.catalog.sources)]
    data = dict(schema_version=1, project=dict(name='SCORE 百科 · ' + faction['name'],
        primary_mod=faction['component'], language='zhCN', component_roots=roots,
        batch=suffix, source_root=str(pool.source_root)), sources=ctx.sources,
        selected_objects=[e['id'] for e in entries], entries=entries, review_items=ctx.review_items,
        dependency_context=ctx.dependency_context(), diagnostics=ctx.catalog.diagnostics)
    if refine:
        from .refine_costs import refine_data
        refine_data(ctx, data)
        from .refine_presentation import LiteralPool, refine as refine_presentation
        refine_presentation(data, LiteralPool())
    return data


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def baseline_manifest(project, pool):
    files = set()
    for path in (project / 'wiki-data').glob('*-v1'):
        files.update(p for p in path.rglob('*') if p.is_file())
    legacy_product = project / 'products' / 'SCORE-wiki'
    if legacy_product.exists():
        files.update(p for p in legacy_product.rglob('*') if p.is_file())
    manifest = project / 'runtime/catalog/score-factions.json'
    files.add(manifest)
    for source, records in pool.components.values():
        root = Path(source['root_path'])
        files.update(root / r['path'] for r in records)
        files.update(root / p for p in source['metadata'] if isinstance(p, str) and (root / p).is_file())
    files.update(p for p in Path(__file__).parent.glob('*.py') if p.is_file())
    from .paths import repository_root
    files.add(repository_root()/'config/factions.default.json')
    return dict(version=1, files=[dict(path=str(p), sha256=sha(p), size=p.stat().st_size) for p in sorted(files)])


def walk_facts(value, path=''):
    if isinstance(value, dict):
        if 'status' in value and 'evidence' in value and 'value' in value:
            yield path, value
        else:
            for key, item in value.items():
                yield from walk_facts(item, path + '/' + key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_facts(item, path + '/' + str(index))


def load_validator(hub):
    config = json.loads((Path(hub) / 'workspace-config.json').read_text(encoding='utf-8-sig'))
    path = (Path(hub) / config['workspaces']['wiki'] / 'tools/sc2-wiki-data.py').resolve()
    if not path.is_relative_to(Path(hub).resolve()) or not path.is_file():
        raise ValueError('Configured wiki validator is missing or outside the Hub')
    spec = importlib.util.spec_from_file_location('score_wiki_validator', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


