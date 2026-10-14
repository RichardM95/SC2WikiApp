"""Account for every baseline Fact and verify literal sources of a SCORE rebuild."""
from pathlib import Path
from collections import Counter
import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET


def hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def facts(value, path=''):
    if isinstance(value, dict):
        if {'value', 'status', 'evidence', 'unit'} <= value.keys():
            yield path, value
        else:
            for key, child in value.items():
                yield from facts(child, path + '/' + key)
    elif isinstance(value, list):
        counts = Counter()
        for index, child in enumerate(value):
            key = str(index)
            if isinstance(child, dict) and 'status' not in child:
                key = child.get('id') or '|'.join(str(child.get(k, '')) for k in ('producer', 'ability', 'command'))
                if not key.strip('|'):
                    key = str(index)
                counts[key] += 1
                key += '#' + str(counts[key])
            yield from facts(child, path + '[' + key + ']')


def replacements(path, fresh):
    if path in fresh:
        return [(path, fresh[path])]
    # v1 did not identify production commands. Match all actual command branches,
    # preserving multiple routes instead of selecting a convenient first branch.
    if path.startswith('/production['):
        match = re.match(r'(/production\[)([^\]]+)#\d+(\].*)', path)
        if match:
            identity = match[2].split('|')
            if len(identity) == 3 and not identity[2]:
                return [(p, f) for p, f in fresh.items()
                    if p.startswith(match[1] + '|'.join(identity[:2]) + '|')
                    and re.sub(r'^/production\[[^\]]+\]', '', p) == match[3][1:]]
    return []


def next_step(path, fact, sources):
    actual = [sources[s]['object'] + '/' + sources[s]['field'] for s in fact['evidence']]
    fields = ('Cost/Cooldown.TimeUse 与 InfoArray.*.Cooldown；充能恢复单独核对' if 'cooldown' in path
        else 'Cost/Charge.CountMax、CountStart、CountUse、TimeUse、TimeStart、Link 与各命令 InfoArray.Charge' if 'charges' in path
        else 'InfoArray.Unit、IgnoreUnitCost、Resource、Food、KillOnFinish，以及目标 Unit.CostResource/Food 的完整结算' if path.startswith('/production') and '/cost/' in path
        else '武器 Effect 执行图中的 ValidatorArray、行为执行/过期条件和每次 Persistent 时序；不得用 DisplayEffect 代替' if path.startswith('/weapons')
        else 'Name/Tooltip/Description 明确引用及 zhCN/enUS 文本键；无原文时只能提交有机制证据的草稿' if path in ('/name','/description') or path.endswith('/name') or path.endswith('/effect')
        else 'Actor.unitName/UnitBirth 绑定、PortraitModel/PortraitActor 和 Model.Model/Image 的完整路径' if 'portrait' in path or path == '/icon'
        else '该事实所列有效字段、父链、类型默认和活动组件覆盖')
    return dict(literal_locations=actual, fields_to_verify=fields,
        procedure='先复查上述字段及本条 reason；未提供的动态条件在 Editor 中查看有效值与需求树，并在游戏中按满足/不满足条件分别验证；保留未知直至取得实际证据')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-dir', required=True, type=Path)
    parser.add_argument('--batch-suffix', required=True)
    parser.add_argument('--toolbox-root', required=True, type=Path)
    parser.add_argument('--output-root', required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.toolbox_root))
    from .check_sources import LiteralChecker
    checker = LiteralChecker(args.toolbox_root)
    project = args.project_dir.resolve()
    output = args.output_root.resolve()
    if not output.is_relative_to(project / 'runtime') or output.exists():
        raise ValueError('A fresh project runtime source_audit directory is required')
    entries, additions = [], []
    unresolved = []
    source_issues = []
    statuses, decisions, original_unknowns = Counter(), Counter(), Counter()
    file_cache, hash_cache = {}, {}
    originals = new_total = 0
    for oldfile in sorted((project / 'wiki-data').glob('*-v1/data.json')):
        faction = oldfile.parent.name[:-3]
        newfile = project / 'wiki-data' / (faction + '-' + args.batch_suffix) / 'data.json'
        old = json.loads(oldfile.read_text(encoding='utf-8-sig'))
        new = json.loads(newfile.read_text(encoding='utf-8-sig'))
        newmap = {e['id']: e for e in new['entries']}
        roots = [Path(p) for p in new['project']['component_roots']]
        sources = {s['id']: s for s in new['sources']}
        oldsources = {s['id']: s for s in old['sources']}
        for source in new['sources']:
            result = checker.check(source, roots)
            if result['status'] != 'passed':
                source_issues.append(dict(faction=faction, **result))
        for entry in old['entries']:
            updated = newmap.get(entry['id'])
            fresh = dict(facts(updated)) if updated is not None else {}
            consumed = set()
            for path, fact in facts(entry):
                originals += 1
                candidates = replacements(path, fresh)
                consumed.update(p for p, f in candidates)
                replacement = candidates[0][1] if len(candidates) == 1 else None
                decision = ('split_into_branches' if len(candidates) > 1 else 'removed_with_reason') if replacement is None else ('retained' if replacement['value'] == fact['value'] and replacement['status'] == fact['status'] else 'corrected')
                decisions[decision] += 1
                if fact['status'] == 'unresolved':
                    original_unknowns['still_unresolved' if any(f['status'] == 'unresolved' for p, f in candidates) else 'resolved_or_replaced' if candidates else 'removed_relationship'] += 1
                evidence = list(dict.fromkeys(s for p, f in candidates for s in f.get('evidence', [])))
                entries.append(dict(faction=faction, object=entry['id'], path=path, original=fact, revised=replacement,
                    original_sources=[oldsources[s] for s in fact.get('evidence', [])],
                    revised_candidates=[dict(path=p, fact=f) for p, f in candidates] if len(candidates) > 1 else [],
                    decision=decision, reason=(replacement.get('basis') or replacement.get('reason') or 'Recomputed from ordered active literal sources') if replacement else 'Expanded into the listed actual production command branches' if candidates else 'No corresponding effective relationship remains; the complete replacement relations and review notes are preserved in this entry',
                    sources=[sources[s] for s in evidence], replacement_relationships={k:[{a:r.get(a) for a in ('id','producer','ability','command') if a in r} for r in (updated or {}).get(k, [])] for k in ('production','weapons','abilities','modifiers')} if not candidates else {}))
            for path, fact in fresh.items():
                if path not in consumed:
                    additions.append(dict(faction=faction, object=entry['id'], path=path, fact=fact))
        for entry in new['entries']:
            for path, fact in facts(entry):
                new_total += 1
                statuses[fact['status']] += 1
                if fact['status'] == 'unresolved':
                    unresolved.append(dict(faction=faction, object=entry['id'], path=path, reason=fact['reason'], evidence=fact['evidence'],
                        next_step=next_step(path, fact, sources)))
    if originals != 25708 or len(entries) != originals:
        raise ValueError('Baseline accounting must cover exactly all 25,708 original Facts')
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'fact-ledger.jsonl').open('x', encoding='utf-8') as stream:
        for row in entries:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
    with (output / 'new-facts.jsonl').open('x', encoding='utf-8') as stream:
        for row in additions:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
    for name, data in [('remaining.json', unresolved), ('source-issues.json', source_issues)]:
        with (output / name).open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
    summary = dict(original_facts=originals, ledger_rows=len(entries), revised_facts=new_total,
        decisions=dict(decisions), original_unresolved=dict(original_unknowns), added_facts=len(additions), revised_statuses=dict(statuses), source_issues=len(source_issues),
        verification='Source file SHA-256 and provider object/default ordinal checked; no Editor/runtime acceptance claimed')
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))
    if source_issues:
        raise SystemExit('Source verification failed; inspect source-issues.json')


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    main()
