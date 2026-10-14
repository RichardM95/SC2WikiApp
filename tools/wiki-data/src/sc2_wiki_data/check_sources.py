"""Check literal provenance of all SCORE batch sources without resolving gameplay.

Only main's explicitly requested report is written. Package/source files stay read-only.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def find_hub():
    for start in (Path.cwd().resolve(), Path(__file__).resolve().parent):
        for candidate in (start, *start.parents):
            if (candidate / 'workspace-config.json').is_file():
                return candidate
    raise ValueError('Run from a Hub working directory or retain the script inside the Hub')


class LiteralChecker:
    def __init__(self, toolbox_root):
        toolbox = Path(toolbox_root).resolve()
        if not (toolbox / 'utils/data/catalog.py').is_file():
            raise ValueError('Missing explicit toolbox catalog parser: ' + str(toolbox))
        sys.path.insert(0, str(toolbox))
        from utils.data.catalog import locate, flatten, family, encode_node
        from utils.data.xml_document import load_document
        self.locate, self.flatten, self.family = locate, flatten, family
        self.encode_node, self.load_document = encode_node, load_document
        self.files = {}

    def file(self, path):
        path = path.resolve()
        if path in self.files:
            return self.files[path]
        blob = path.read_bytes()
        result = dict(path=str(path), sha256=digest(blob), size=len(blob), source_count=0)
        if path.suffix.casefold() == '.xml':
            document = self.load_document(str(path))
            roots = [self.encode_node(n) for n in document.nodes if hasattr(n, 'tag')]
            catalog = next((n for n in roots if n.get('@tag') == 'Catalog'), None)
            if catalog is None:
                raise ValueError('XML source has no Catalog root: ' + str(path))
            result.update(kind='xml', nodes=catalog.get('children', []), fields={})
        elif path.suffix.casefold() == '.txt':
            # Tool source records use UTF-8; decoding errors cannot be claimed as a pass.
            rows = {}
            for line_number, line in enumerate(blob.decode('utf-8-sig').splitlines(), 1):
                if not line or line.lstrip().startswith('//') or '=' not in line:
                    continue
                key, raw = line.split('=', 1)
                rows.setdefault(key.strip(), []).append(dict(line=line_number, raw=raw,
                    value=raw.split('///', 1)[0].strip()))
            result.update(kind='text', rows=rows)
        else:
            result['kind'] = 'unsupported'
        self.files[path] = result
        return result

    def check(self, source, roots):
        base = dict(source_id=source.get('id'), object=source.get('object'), field=source.get('field'))
        def reject(code, reason, **details):
            return dict(base, status='unsupported' if code.startswith('UNSUPPORTED') else 'failed',
                code=code, reason=reason, **details)
        if source.get('kind', 'component') != 'component':
            return reject('UNSUPPORTED_SOURCE_KIND', '非组件来源由权威知识快照校验器负责，本检查器不能认证', kind=source.get('kind'))
        candidates = [p for p in roots if p.name.casefold() == str(source.get('component', '')).casefold()]
        if len(candidates) != 1:
            return reject('COMPONENT_ROOT_AMBIGUOUS', '来源组件不能在明确活动 component_roots 中唯一定位', candidates=[str(p) for p in candidates])
        relative = PurePosixPath(str(source.get('path', '')).replace('\\', '/'))
        if not relative.parts or relative.is_absolute() or '..' in relative.parts or ':' in str(relative):
            return reject('UNSAFE_SOURCE_PATH', '来源路径不是安全组件相对路径')
        root = candidates[0]
        try:
            path = self.locate(root, str(relative))
            if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
                return reject('SOURCE_FILE_MISSING_OR_ESCAPED', '来源文件缺失或越出声明组件', path=str(path))
            record = self.file(path)
        except (OSError, ValueError, UnicodeError) as exc:
            return reject('SOURCE_FILE_UNREADABLE', str(exc))
        record['source_count'] += 1
        if record['sha256'] != source.get('sha256'):
            return reject('SOURCE_HASH_MISMATCH', '来源文件实际字节哈希与 data.json 不同',
                path=str(path), expected=source.get('sha256'), actual=record['sha256'])
        field = source.get('field')
        if not isinstance(field, str) or not field:
            return reject('FIELD_MISSING', '来源未提供具体字段')
        if record['kind'] == 'text':
            rows = record['rows'].get(field, [])
            if not rows:
                return reject('LOCALIZATION_KEY_MISSING', '原始 TXT 没有该本地化键', path=str(path))
            nonempty = [r for r in rows if r['value']]
            if not nonempty:
                return reject('LOCALIZATION_KEY_EMPTY', '原始键只有空正文或行内注释', lines=[r['line'] for r in rows])
            locale_match = re.search(r'(?:^|/)([A-Za-z]{2}[A-Za-z]{2})\.SC2Data/', str(relative), re.I)
            locale = source.get('locale')
            if locale and (not locale_match or locale.casefold() != locale_match.group(1).casefold()):
                return reject('LOCALIZATION_LOCALE_MISMATCH', '来源locale与实际TXT目录不一致', locale=locale)
            return dict(base, status='passed', kind='text', path=str(path), line=nonempty[-1]['line'],
                duplicate_key_count=len(rows), sha256=record['sha256'])
        if record['kind'] != 'xml':
            return reject('UNSUPPORTED_COMPONENT_FILE', '本检查器只认证 Catalog XML 和本地化 TXT', path=str(path))
        ordinal = source.get('ordinal')
        if isinstance(ordinal, bool) or not isinstance(ordinal, int):
            return reject('UNSUPPORTED_XML_LOCATOR', 'XML来源没有无损文档ordinal，不能猜测物理节点')
        nodes = record['nodes']
        if ordinal < 0 or ordinal >= len(nodes) or '@tag' not in nodes[ordinal]:
            return reject('XML_ORDINAL_INVALID', 'ordinal没有指向实际Catalog定义节点', ordinal=ordinal)
        node = nodes[ordinal]
        attrs = node.get('attributes', {})
        object_id = source.get('object', '')
        if ':' not in object_id:
            return reject('SOURCE_OBJECT_INVALID', '来源对象缺少Catalog家族与ID')
        family, identity = object_id.split(':', 1)
        if self.family(node['@tag']) != family:
            return reject('XML_FAMILY_MISMATCH', '指定ordinal的实际类型不属于来源家族', tag=node['@tag'], ordinal=ordinal)
        if identity.startswith('__default_'):
            expected_type = identity.removeprefix('__default_')
            if (attrs.get('id') is not None or attrs.get('default') != '1' or source.get('default') is not True
                or node['@tag'] != expected_type or source.get('provider_type') != node['@tag']):
                return reject('XML_DEFAULT_MISMATCH', '无名默认来源的default/type/id标识不匹配', tag=node['@tag'], attributes=attrs)
        elif attrs.get('id') != identity:
            return reject('XML_ID_MISMATCH', '来源object ID与指定ordinal节点不同', actual_id=attrs.get('id'), ordinal=ordinal)
        elif source.get('default') is True and attrs.get('default') != '1':
            return reject('XML_DEFAULT_MISMATCH', '来源声明默认节点但实际节点未声明default=1')
        elif source.get('provider_type') and source['provider_type'] != node['@tag']:
            return reject('XML_TYPE_MISMATCH', 'provider_type与原始节点类型不同', actual_type=node['@tag'])
        if field.startswith('@') and '|' not in field:
            if field[1:] not in attrs:
                return reject('XML_ATTRIBUTE_MISSING', '实际定义没有该对象属性', tag=node['@tag'])
            return dict(base, status='passed', kind='xml', path=str(path), ordinal=ordinal,
                tag=node['@tag'], sha256=record['sha256'])
        if ordinal not in record['fields']:
            record['fields'][ordinal] = self.flatten(node)
        literal_fields = record['fields'][ordinal]
        canonical = field
        for suffix in ('|@value', '|@Value', '|@Link'):
            if canonical.endswith(suffix):
                canonical = canonical[:-len(suffix)]
                break
        if canonical not in literal_fields:
            tag = canonical.split('|', 1)[0]
            suggestions = [p for p in literal_fields if p.split('|', 1)[0] == tag]
            return reject('FIELD_NOT_LITERAL', '来源字段不能在指定原始节点按raw XML层级/索引精确定位',
                path=str(path), ordinal=ordinal, literal_field_candidates=suggestions[:12], candidate_count=len(suggestions))
        return dict(base, status='passed', kind='xml', path=str(path), ordinal=ordinal,
            tag=node['@tag'], sha256=record['sha256'])


def check_batches(project, suffix, toolbox_root, validator):
    checker = LiteralChecker(toolbox_root)
    batches = sorted((project / 'wiki-data').glob('*-' + suffix))
    if len(batches) != 11:
        raise ValueError(f'Expected 11 SCORE batches, found {len(batches)} for {suffix}')
    report = dict(version=1, project_dir=str(project), batch_suffix=suffix, status='passed',
        limitations=[
            '验证物理源文件、节点、字段和TXT键的存在，不独立证明游戏内有效值或玩法语义。',
            '非组件知识来源明确列为unsupported，需另行通过权威快照校验。',
            '无index数组的有效append偏移不等同原始局部索引；无法精确定位的来源明确失败。'], batches=[])
    totals = Counter()
    for directory in batches:
        path = directory / 'data.json'
        blob = path.read_bytes()
        data = validator.strict_json_loads(blob.decode('utf-8-sig'))
        findings, counters, proof = [], Counter(), hashlib.sha256()
        try:
            validator.validate(data)
            validation = 'passed'
        except ValueError as exc:
            validation = 'failed'
            findings.append(dict(status='failed', code='AUTHORITATIVE_VALIDATION_FAILED', reason=str(exc)))
        roots = list(dict.fromkeys(Path(p).resolve() for p in data.get('project', {}).get('component_roots', [])))
        if not roots:
            raise ValueError('No explicit component_roots in ' + str(path))
        for source in data.get('sources', []):
            result = checker.check(source, roots)
            counters[result['status']] += 1
            if result['status'] == 'passed':
                counters[result['kind']] += 1
                proof.update(json.dumps(result, sort_keys=True, ensure_ascii=False).encode('utf-8'))
            else:
                findings.append(result)
        if path.read_bytes() != blob:
            findings.append(dict(status='failed', code='PACKAGE_CHANGED_DURING_CHECK', reason='检查期间data.json被其他写入者改变'))
        status = 'passed' if not findings and validation == 'passed' else 'failed'
        report['batches'].append(dict(batch=directory.name, status=status, data_sha256=digest(blob),
            authoritative_validation=validation, sources=len(data.get('sources', [])), counts=dict(counters),
            passed_sources_proof_sha256=proof.hexdigest(), findings=findings))
        totals.update(counters)
        if status != 'passed':
            report['status'] = 'failed'
    report['totals'] = dict(totals)
    report['files'] = [{k: record[k] for k in ('path', 'sha256', 'size', 'source_count', 'kind')}
        for _, record in sorted(checker.files.items())]
    report['counts'] = dict(batches=len(batches), files=len(checker.files), sources=sum(b['sources'] for b in report['batches']),
        failed_batches=sum(b['status'] != 'passed' for b in report['batches']))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-dir', required=True)
    parser.add_argument('--batch-suffix', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--toolbox-root', help='Defaults to the existing SC2EditorTool sibling of the discovered Hub')
    parser.add_argument('--hub-root', type=Path, help='Explicit Hub root when running from another working directory')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.batch_suffix):
        parser.error('Unsafe batch suffix')
    hub = args.hub_root.resolve() if args.hub_root else find_hub()
    if not (hub / 'workspace-config.json').is_file():
        parser.error('Missing explicit Hub workspace-config.json')
    sys.path.insert(0, str(hub / 'tools'))
    from project_storage import validate_project_location, ensure_project_space
    from .catalog_generate import load_validator
    project = validate_project_location(hub, args.project_dir)
    output = Path(args.output).resolve()
    if not output.is_relative_to(project / 'runtime/reports') or output.suffix.casefold() != '.json':
        parser.error('Output must be a new JSON report inside the selected project runtime/reports')
    if output.exists():
        raise FileExistsError('Existing source report is protected: ' + str(output))
    toolbox = Path(args.toolbox_root).resolve() if args.toolbox_root else hub.parent / 'SC2EditorTool'
    report = check_batches(project, args.batch_suffix, toolbox, load_validator(hub))
    ensure_project_space(project)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(dict(status=report['status'], counts=report['counts'], totals=report['totals'], output=str(output)), ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
