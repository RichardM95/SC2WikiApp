"""Editable display affiliations; source identities are never rewritten."""
import math

import json
from .paths import repository_root

def normalize_prefix(value):
    return value.strip().lstrip('-').strip()

def default_config():
    """The repository JSON is the only source of initial display affiliations."""
    path=repository_root()/'config/factions.default.json'
    return validate_config(json.loads(path.read_text(encoding='utf-8-sig')))

def validate_config(config):
    if not isinstance(config,dict) or type(config.get('version')) is not int or config['version']!=1 or not isinstance(config.get('factions'),list):
        raise ValueError('阵营配置格式无效')
    import re
    keys=set();prefixes=set()
    for row in config['factions']:
        if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]+',row['id']) or row['id'].casefold() in keys:
            raise ValueError('阵营键重复或无效')
        keys.add(row['id'].casefold())
        if not isinstance(row.get('name'),str) or not row['name'].strip() or type(row.get('defaultVisible')) is not bool:
            raise ValueError('阵营名称或显隐无效')
        order=row.get('order')
        if isinstance(order,bool) or not isinstance(order,(int,float)) or not math.isfinite(order):raise ValueError('阵营顺序无效')
        aliases=row.get('prefixes')
        if not isinstance(aliases,list) or not aliases:raise ValueError('缺少阵营前缀')
        own=set()
        for alias in aliases:
            if not isinstance(alias,str) or not normalize_prefix(alias):raise ValueError('空阵营前缀')
            alias=normalize_prefix(alias).casefold()
            if alias in prefixes:raise ValueError('阵营前缀重复')
            prefixes.add(alias);own.add(alias)
        branches=row.get('branches',{})
        if not isinstance(branches,dict) or any(not isinstance(k,str) or normalize_prefix(k).casefold() not in own or not isinstance(v,str) or not v.strip() for k,v in branches.items()):
            raise ValueError('分支名称或前缀无效')
    return config

def assignment(prefix,config):
    wanted=normalize_prefix(prefix).casefold()
    for row in config['factions']:
        if wanted in [normalize_prefix(p).casefold() for p in row['prefixes']]:return row['id']
    return 'Unassigned'
