"""Effective glossary priority and predecessor evidence helpers."""
import copy
import hashlib
from catalog_adapter import numeric

def add_priority(ctx,entry):
    if 'glossary_priority' in entry['presentation']:raise ValueError('Priority already exists: '+entry['id'])
    uid=entry['id'].split(':',1)[1]
    field=ctx.fields('Unit',uid).get('GlossaryPriority',{})
    raw=field.get('value');number=numeric(raw)
    import math
    if number is not None and math.isfinite(number) and field.get('source'):
        fact=ctx.fact(number,'Unit',uid,'GlossaryPriority')
    else:
        fact=ctx.missing('有效 Unit GlossaryPriority 未声明或无法解析；名词表排序置后')
    entry['presentation']['glossary_priority']=fact
    return fact['value'] is not None


def protected_equal(before,after):
    cleaned=copy.deepcopy(after)
    for entry in cleaned['entries']:entry['presentation'].pop('glossary_priority',None)
    cleaned['sources']=cleaned['sources'][:len(before['sources'])]
    cleaned['project']=copy.deepcopy(before['project'])
    if cleaned!=before:raise ValueError('Protected predecessor content changed')


def predecessor_metadata(data,batch,predecessor,raw):
    data['project'].update(batch=batch,predecessor_batch=predecessor,
        predecessor_sha256=hashlib.sha256(raw).hexdigest())


