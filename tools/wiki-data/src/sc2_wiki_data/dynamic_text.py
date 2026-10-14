"""Expand proven static SCORE tooltip operands; preserve unproved runtime text."""
import ast
from collections import Counter
from decimal import Decimal, DecimalException, ROUND_HALF_UP, localcontext
import math
import re
import xml.etree.ElementTree as ET

from .refine_costs import hydrate_sources


TAG = re.compile(r'<d\b[^>]*>',re.I)
OPERAND = re.compile(r'\b(Unit|Abil|Behavior|Effect|Weapon|Upgrade)\s*,\s*'
    r'([A-Za-z_][A-Za-z0-9_]*)\s*,\s*'
    r'([A-Za-z_][A-Za-z0-9_]*(?:\[[A-Za-z0-9_]+\])?'
    r'(?:\.[A-Za-z_][A-Za-z0-9_]*(?:\[[A-Za-z0-9_]+\])?)*)')
TEXT_FIELDS = {'description','effect','requirements','conditions','tooltip'}
SKIP_FIELDS = {'name','producer_name','faction','icon','portrait','portrait_image',
    'path','model','original_value','raw_template','partial_text','raw_value',
    'dynamic_text','sources','dependency_context','diagnostics'}


class UnprovedText(ValueError):
    pass


def normalize_field(field):
    """Only syntax normalization; no guesses about numeric enum indices."""
    field = re.sub(r'\[([A-Za-z0-9_]+)\]',r'|\1',field.replace('.','|'))
    parts = [part.removeprefix('@') for part in field.split('|')]
    if parts and parts[-1] in ('value','Value','Link'):
        parts.pop()
    return '|'.join(parts)


def finite_decimal(raw):
    if isinstance(raw,bool) or not isinstance(raw,(str,int,float)):
        raise UnprovedText('引用不是可证明的有限数字')
    try:
        value = Decimal(str(raw).strip())
    except (DecimalException,ValueError):
        raise UnprovedText('引用是符号、表达式或非数值字段') from None
    if not value.is_finite() or (value and abs(value.adjusted())>308) or not math.isfinite(float(value)):
        raise UnprovedText('引用数值非有限或超出安全展示范围')
    return value


def safe_arithmetic(expression, variables=None):
    """No eval: only decimals, proven operands, unary signs and + - * /."""
    variables = variables or {}
    if len(expression)>2048:
        raise UnprovedText('表达式过长')
    try:
        tree = ast.parse(expression,mode='eval')
    except (SyntaxError,RecursionError,ValueError):
        raise UnprovedText('无法按受支持的静态四则表达式解析') from None
    if sum(1 for _ in ast.walk(tree))>128:
        raise UnprovedText('表达式节点超过安全上限')

    def run(node,depth=0):
        if depth>16:
            raise UnprovedText('表达式嵌套超过安全上限')
        if isinstance(node,ast.Expression):
            return run(node.body,depth+1)
        if isinstance(node,ast.Constant) and type(node.value) in (int,float):
            token = ast.get_source_segment(expression,node)
            if not token or not re.fullmatch(r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?',token):
                raise UnprovedText('仅支持明确十进制数字常量')
            return finite_decimal(token)
        if isinstance(node,ast.Name) and node.id in variables:
            return variables[node.id]
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            result=run(node.operand,depth+1)
            return result if isinstance(node.op,ast.UAdd) else -result
        if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div)):
            left,right=run(node.left,depth+1),run(node.right,depth+1)
            if isinstance(node.op,ast.Add):result=left+right
            elif isinstance(node.op,ast.Sub):result=left-right
            elif isinstance(node.op,ast.Mult):result=left*right
            else:
                if right==0:raise UnprovedText('表达式除数为零')
                result=left/right
            return finite_decimal(str(result))
        raise UnprovedText('表达式包含运行期变量、函数或不支持的运算符')
    try:
        with localcontext() as context:
            context.prec=40
            return run(tree)
    except DecimalException:
        raise UnprovedText('静态数值运算无法完成') from None


def operand(ctx,family,oid,field):
    try:
        field_map=ctx.fields(family,oid)
    except (ValueError,KeyError):
        raise UnprovedText('引用对象或有效字段无法解析：'+family+','+oid+','+field) from None
    target=normalize_field(field)
    matches=[(path,info) for path,info in field_map.items() if normalize_field(path)==target]
    if len(matches)!=1:
        reason='未找到精确有效字段（包括数组索引）' if not matches else '规范化后存在多个字段，不能选择'
        raise UnprovedText(reason+'：'+family+','+oid+','+field)
    path,info=matches[0]
    if not info.get('source'):
        raise UnprovedText('有效字段没有完整原始来源：'+family+','+oid+','+field)
    number=finite_decimal(info.get('value'))
    try:
        source_id=ctx.evidence(family,oid,path)
    except (ValueError,KeyError):
        raise UnprovedText('有效字段缺少可定位的来源：'+family+','+oid+','+field) from None
    return number,{'object':family+':'+oid,'field':path,'raw_field':info.get('raw_field',path),
        'literal_value':str(info['value']),'evidence':[source_id]}


def expand_tag(ctx,tag):
    try:
        node=ET.fromstring(tag if tag.rstrip().endswith('/>') else tag[:-1]+'/>')
    except ET.ParseError:
        raise UnprovedText('动态文字标签格式不完整') from None
    attrs=node.attrib
    unsupported=set(attrs)-{'ref','expr','time','precision'}
    if unsupported:
        raise UnprovedText('标签要求运行期或未支持的上下文：'+','.join(sorted(unsupported)))
    kinds=[key for key in ('ref','expr','time') if key in attrs]
    if len(kinds)!=1:
        raise UnprovedText('动态标签没有唯一的 ref/expr/time 输入')
    mode=kinds[0]; expression=attrs[mode].strip()
    if not expression:
        raise UnprovedText('动态表达式为空')
    variables={};operands=[]
    if re.search(r'\b_v\d+\b',OPERAND.sub('0',expression)):
        raise UnprovedText('表达式包含未取证的内部变量名')
    def replace(match):
        number,proof=operand(ctx,*match.groups())
        key='_v'+str(len(variables));variables[key]=number;operands.append(proof)
        return key
    if mode=='ref':
        expression=OPERAND.sub(replace,expression)
    elif OPERAND.search(expression):
        raise UnprovedText('expr/time 仅支持明确数字常量算术；Catalog 引用应在 ref 中提供')
    number=safe_arithmetic(expression,variables)
    precision=attrs.get('precision')
    if precision is not None:
        if not re.fullmatch(r'[0-6]',precision):
            raise UnprovedText('precision 不是受支持的明确 0–6 位小数')
        try:
            with localcontext() as context:
                context.prec=340
                text=format(number.quantize(Decimal(1).scaleb(-int(precision)),rounding=ROUND_HALF_UP),'f')
        except DecimalException:
            raise UnprovedText('无法按明确 precision 格式化') from None
    else:
        text=format(number,'f')
        if '.' in text:text=text.rstrip('0').rstrip('.')
    if number==0 and text.startswith('-'):text=text[1:]
    if len(text)>640:
        raise UnprovedText('展开数字文本过长')
    return text,{'tag':tag,'mode':mode,'operands':operands,
        'result':text,'precision':precision,
        'formatting':'静态基础字段；未模拟运行期升级或游戏速度换算'}


def resolve_texts(ctx,data):
    """Mutate text Facts, append operand sources, and return per-batch statistics."""
    hydrate_sources(ctx,data)
    stats=Counter(facts_scanned=0,facts_changed=0,facts_resolved=0,facts_unresolved=0,
        tags_found=0,tags_resolved=0,tags_unresolved=0)
    diagnostics=[]
    def visit(item,path='',field=''):
        if isinstance(item,list):
            for i,child in enumerate(item):visit(child,path+'/'+str(i),field)
            return
        if not isinstance(item,dict):return
        if {'value','status','evidence'}.issubset(item):
            value=item.get('value')
            key=item.get('key','')
            if field in SKIP_FIELDS or '/Name/' in key or not isinstance(value,str):return
            if field.lower() not in TEXT_FIELDS and not any(token in key.lower() for token in ('/tooltip/','/description/')):return
            stats['facts_scanned']+=1
            tags=TAG.findall(value)
            if not tags:return
            stats['facts_changed']+=1;stats['tags_found']+=len(tags)
            expansions=[];issues=[];new_evidence=[]
            def replace(match):
                tag=match.group(0)
                try:
                    text,proof=expand_tag(ctx,tag)
                except UnprovedText as error:
                    reason=str(error);issues.append({'tag':tag,'reason':reason})
                    stats['tags_unresolved']+=1
                    diagnostics.append({'code':'dynamic_text_unresolved','path':path,
                        'key':key or None,'expression':tag,'reason':reason})
                    return '无'
                expansions.append(proof);stats['tags_resolved']+=1
                for source in proof['operands']:new_evidence.extend(source['evidence'])
                return text
            expanded=TAG.sub(replace,value)
            item.setdefault('raw_template',value)
            item.setdefault('original_value',value)
            item['evidence']=list(dict.fromkeys(item.get('evidence',[])+new_evidence))
            item['dynamic_text']={'expansions':expansions,'unresolved':issues,
                'basis':'只使用完整来源的有效基础字段和受限十进制四则运算；不推测当前玩家、等级或运行状态'}
            if issues:
                item.update(value=None,status='unresolved',partial_text=expanded,
                    reason='动态本地化表达式未完整解析：'+'；'.join(dict.fromkeys(i['reason'] for i in issues)))
                stats['facts_unresolved']+=1
            else:
                item.update(value=expanded,status='inferred',
                    basis='依据本地化模板及所列操作数字段展开静态文本；precision 在展示层按十进制格式化，未模拟运行期变化')
                stats['facts_resolved']+=1
            return
        for key,value in item.items():
            if key not in SKIP_FIELDS:visit(value,path+'/'+key,key)
    visit(data.get('entries',[]),'/entries')
    data['sources']=ctx.sources
    data.setdefault('diagnostics',[]).extend(diagnostics)
    report=dict(stats)
    data['dynamic_text_summary']=report
    return report
