"""Read-only SCORE catalogs with the toolbox resolver and literal provenance."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

CJK = re.compile(r"[\u3400-\u9fff]")
_CATALOG_TYPES = {}


def scoped_catalog_type(base):
    """The toolbox global constant map loses const.path; keep that scope here."""
    if base in _CATALOG_TYPES:
        return _CATALOG_TYPES[base]
    from utils.data.catalog import root_node, elements, flatten, default_types, _cache_put, indexed_overlay, merge_node, tokens_from, node_operations, TYPE_BASES

    class ScopedCatalog(base):
        _score_const_scope = True

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._scoped_constants = {}
            self._field_aliases = {}
            self._provider_field_maps = {}
            for record in self.files:
                root = root_node(record)
                if root is None:
                    continue
                for child in elements(root):
                    attrs = child.get('attributes', {})
                    if child['@tag'] == 'const' and all(k in attrs for k in ('id', 'value', 'path')):
                        self._scoped_constants[(attrs['path'], attrs['id'])] = attrs['value']

        def _literal_provenance(self, definition, applied):
            prepared, literal = flatten(applied), flatten(definition['node'])
            if len(prepared) != len(literal):
                raise ValueError('Overlay preparation changed literal field cardinality')
            result = {}
            for logical, raw in zip(prepared, literal):
                provider = {k: definition[k] for k in ('source_id', 'file', 'ordinal')}
                provider['raw_field'] = raw
                result[logical] = provider
                key = (provider['source_id'], provider['file'], provider['ordinal'], logical)
                self._provider_field_maps.setdefault(key, set()).add(raw)
            return result

        def resolve(self, fam, object_id, stack=()):
            # Identical merge rules to the toolbox, with literal overlay locations retained.
            key = fam, object_id
            if key in self._resolved:
                return self._resolved[key]
            if key in stack:
                self.diagnostics.append(dict(code='inheritance_cycle', family=fam, id=object_id))
                return None
            definitions = self.objects.get(key)
            if not definitions:
                if stack:
                    self.diagnostics.append(dict(code='missing_parent', family=fam, id=object_id))
                return None
            parent = next((d['parent'] for d in reversed(definitions) if d['parent'] is not None), None)
            subtype = definitions[-1]['tag']
            if subtype not in TYPE_BASES and subtype != 'C' + fam and 'C' + fam in self.defaults:
                self.diagnostics.append(dict(code='unverified_type_defaults', family=fam, id=object_id, type=subtype))
            result = {'@tag': subtype, 'attributes': {}, 'children': []}
            provenance, tokens = {}, {}
            for tag in default_types(subtype):
                for definition in self.defaults.get(tag, []):
                    applied = indexed_overlay(result, definition['node'])
                    provenance.update(self._literal_provenance(definition, applied))
                    result = merge_node(result, applied, _owned=True, _indexed=True)
                    tokens.update(tokens_from(definition['node']))
            if parent:
                ancestor = self.resolve(fam, parent, stack + (key,))
                if ancestor:
                    result = merge_node(result, ancestor, _owned=True)
                    tokens.update(ancestor.get('_tokens', {}))
                    provenance.update(self._provenance.get((fam, parent), {}))
            for definition in definitions:
                applied = indexed_overlay(result, definition['node'])
                literal = self._literal_provenance(definition, applied)
                result = merge_node(result, applied, _owned=True, _indexed=True)
                tokens.update(tokens_from(definition['node']))
                fields, removed = node_operations(applied)
                for path in removed:
                    provenance = {p: v for p, v in provenance.items() if p != path and not p.startswith(path + '|')}
                provenance.update({p: literal[p] for p in fields if not p.endswith('|@removed')})
            result['@tag'] = subtype
            result['attributes'].pop('default', None)
            result['attributes'].pop('removed', None)
            result['attributes']['id'] = object_id
            if parent:
                result['attributes']['parent'] = parent
            result['_tokens'] = tokens
            _cache_put(self._resolved, key, result)
            _cache_put(self._provenance, key, provenance)
            return result

        def _effective_node(self, fam, object_id):
            key = fam, object_id
            if key in self._effective:
                self._effective.move_to_end(key)
                return self._effective[key]
            original = self.resolve(fam, object_id)
            if original is None:
                return None
            node = copy.deepcopy(original)
            tokens = {**node.pop('_tokens', {}), 'id': object_id}
            classes = list(reversed(default_types(node['@tag'])))

            def expand(current, tags):
                for attr, raw in list(current.get('attributes', {}).items()):
                    expanded = raw
                    for _ in range(len(tokens) + 1):
                        new = re.sub(r'##(\w+)##', lambda m: tokens.get(m[1], m[0]), expanded)
                        if new == expanded:
                            break
                        expanded = new
                    seen = set()
                    while expanded not in seen:
                        seen.add(expanded)
                        matches = [self._scoped_constants[(cls + '.' + '.'.join(tags + [attr]), expanded)]
                            for cls in classes if (cls + '.' + '.'.join(tags + [attr]), expanded) in self._scoped_constants]
                        if not matches:
                            break
                        expanded = matches[0]
                    current['attributes'][attr] = expanded
                    if '##' in expanded:
                        self.diagnostics.append(dict(code='unresolved_token', family=fam, id=object_id, value=expanded))
                for child in elements(current):
                    expand(child, tags + [child['@tag']])
            expand(node, [])
            raw_fields, fields = flatten(original), flatten(node)
            # Expansion preserves leaf order. Refuse an index collision instead of inventing provenance.
            if len(raw_fields) != len(fields):
                raise ValueError(f'Scoped constant expansion collapsed distinct field indices: {fam}:{object_id}')
            self._field_aliases[key] = dict(zip(fields, raw_fields))
            for path, raw in fields.items():
                if path.split('|')[-1] in {'Amount', 'LifeMax', 'LifeStart', 'ShieldsMax', 'ShieldsStart',
                    'EnergyMax', 'EnergyStart', 'Range', 'Period', 'ArmorReduction', 'Speed'} and numeric(raw) is None:
                    self.diagnostics.append(dict(code='unresolved_expression', family=fam, id=object_id, field=path, value=raw))
            _cache_put(self._effective, key, node)
            return node

        def explain_field(self, fam, object_id, path):
            return self.explain_fields(fam, object_id).get(path, {'value': None, 'source': None, 'status': self.status})

        def explain_fields(self, fam, object_id):
            node = self._effective_node(fam, object_id)
            if node is None:
                return {}
            # resolve can be evicted independently of the effective-node cache.
            self.resolve(fam, object_id)
            aliases = self._field_aliases[(fam, object_id)]
            provenance = self._provenance.get((fam, object_id), {})
            result = {}
            for path, raw in flatten(node).items():
                provider = copy.deepcopy(provenance.get(aliases[path]))
                result[path] = dict(value=raw, source=provider,
                    raw_field=provider.get('raw_field', aliases[path]) if provider else aliases[path])
            return result

    _CATALOG_TYPES[base] = ScopedCatalog
    return ScopedCatalog


def toolbox_imports(toolbox_root):
    root = str(Path(toolbox_root).resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    from utils.data.catalog import Catalog, read_component, locate, digest
    from utils.data.builtin_dependencies import engine_registry, supplement
    from utils.data.component_dependencies import dependency_metadata, dependency_context
    return scoped_catalog_type(Catalog), read_component, locate, digest, engine_registry, supplement, dependency_metadata, dependency_context


def numeric(value):
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def value(node, field):
    if node is None:
        return None
    child = node.find(field)
    return child.get('value', child.get('Link')) if child is not None else None


class ComponentPool:
    """A single run shares immutable source documents across faction contexts."""
    def __init__(self, source_root, toolbox_root, game_root, gamedata_root):
        self.source_root = Path(source_root).resolve()
        self.gamedata_root = Path(gamedata_root).resolve()
        self.api = toolbox_imports(toolbox_root)
        self.registry = self.api[4](str(game_root))
        if self.registry is None:
            raise ValueError('Cannot verify the active engine dependency table')
        self.components = {}

    def component(self, sid, path):
        if sid in self.components:
            return self.components[sid]
        _, read_component, locate, digest, _, supplement, dep_state, _ = self.api
        source, files = read_component(path, source_id=sid, role='dependency')
        source['metadata'] = supplement(source['metadata'], sid, self.registry)
        source['dependency_metadata'] = dep_state(source['metadata'])
        source['dependencies'] = [e['target'] for e in source['dependency_metadata']['edges'] if e['target']]
        # read_component reports the unsupplemented state; replace only that stale diagnostic.
        source['diagnostics'] = [d for d in source['diagnostics'] if not d['code'].startswith('dependency_metadata_')]
        if source['dependency_metadata']['status'] != 'complete' and sid != 'mods/core.sc2mod':
            raise ValueError(f'Incomplete dependency metadata: {sid}: {source["dependency_metadata"]}')
        source['origin'] = 'directory'
        source['root_path'] = str(Path(path).resolve())
        source['component_ref'] = sid
        # Official unpacked locales also use <locale>.SC2Data/*.txt.
        present = {(f['locale'], Path(f['path']).name.casefold()) for f in files if f['kind'] == 'text'}
        for locale in ('enUS', 'zhCN'):
            for name in ('GameStrings.txt', 'ObjectStrings.txt', 'GameHotkeys.txt'):
                if (locale, name.casefold()) in present:
                    continue
                p = locate(path, f'{locale}.SC2Data/{name}')
                if p.is_file():
                    blob = p.read_bytes()
                    files.append(dict(source_id=sid, path=p.relative_to(path).as_posix(), kind='text',
                        locale=locale, encoding='utf-8', bom='efbbbf' if blob.startswith(b'\xef\xbb\xbf') else '',
                        text=blob.decode('utf-8-sig'), sha256=digest(blob)))
        source['fingerprint'] = digest({'files': [(f['path'], f['sha256']) for f in files], 'metadata': source['metadata']})
        self.components[sid] = source, files
        return source, files

    def locate_dependency(self, ref):
        ref = ref.replace('\\', '/')
        if ref.casefold() in self.registry['dependencies'] or ref.casefold() == 'mods/core.sc2mod':
            sid = ref.casefold()
            path = self.api[2](self.gamedata_root, sid)
        else:
            # A declaration such as Mods/SCORE/<component> resolves within this explicit source root.
            name = Path(ref).name
            candidates = [p for p in self.source_root.iterdir() if p.is_dir() and p.name.casefold() == name.casefold()]
            if len(candidates) != 1:
                raise ValueError(f'Missing or ambiguous declared dependency: {ref}')
            path = candidates[0]
            sid = 'project:' + path.name
        if not path.is_dir():
            raise ValueError(f'Missing active source directory: {path}')
        return sid, path

    def context(self, component):
        sources, files, visited, active = [], [], set(), set()
        def visit(sid, path):
            if sid in active:
                raise ValueError(f'Dependency cycle: {sid}')
            if sid in visited:
                return
            active.add(sid)
            source, records = self.component(sid, path)
            for ref in source['dependencies']:
                visit(*self.locate_dependency(ref))
            active.remove(sid)
            visited.add(sid)
            sources.append(copy.deepcopy(source))
            files.extend(records)
        # Core is the engine base, distinct from explicit dependency edges.
        visit('mods/core.sc2mod', self.api[2](self.gamedata_root, 'mods/core.sc2mod'))
        main = self.source_root / component
        visit('project:' + component, main)
        sources[-1]['role'] = 'primary'
        catalog = self.api[0](sources, files, locale='zhCN')
        return Context(catalog, sources[-1]['id'], self.registry)


class Context:
    def __init__(self, catalog, primary_sid=None, registry=None, drafts=None):
        if not getattr(catalog, '_score_const_scope', False):
            catalog = scoped_catalog_type(type(catalog))(catalog.sources, catalog.files,
                locale=catalog.locale, diagnostics=catalog.diagnostics)
        self.catalog = catalog
        self.primary_sid = primary_sid or catalog.sources[-1]['id']
        self.registry = registry
        self.sources = []
        self._source_ids = {}
        self._nodes = {}
        self._fields = {}
        self._definitions = {(d['source_id'], d['file'], d['ordinal']): d for d in catalog.definitions}
        self._files = {(r['source_id'], r['path']): r for r in catalog.files}
        self._components = {s['id']: s for s in catalog.sources}
        self._texts = {}
        self.drafts = drafts or {}
        self.review_items = []
        self._actor_map = None
        for record in catalog.files:
            if record['kind'] != 'text':
                continue
            locale = record['locale']
            for line in record.get('text', '').splitlines():
                line = line.lstrip('\ufeff')
                if not line or line.startswith('//') or '=' not in line:
                    continue
                key, raw = line.split('=', 1)
                text = raw.split('///', 1)[0].strip()
                if text:
                    self._texts[(locale, key.strip())] = (text, record)

    def node(self, family, object_id):
        key = family, object_id
        if key not in self._nodes:
            self._nodes[key] = self.catalog.to_etree(family, object_id)
        return self._nodes[key]

    def find_nodes(self, family):
        return {oid: self.node(family, oid) for fam, oid in self.catalog.objects if fam == family}

    def fields(self, family, object_id):
        key = family, object_id
        if key not in self._fields:
            self._fields[key] = self.catalog.explain_fields(family, object_id)
        return self._fields[key]

    def _source(self, family, object_id, field, provider, definition=None, locale=None):
        sid, path = provider['source_id'], provider.get('file', provider.get('path'))
        ordinal = provider.get('ordinal')
        definition = definition or self._definitions.get((sid, path, ordinal))
        actual_object = (family + ':' + definition['id']) if definition and definition.get('id') else (
            family + ':__default_' + definition['tag'] if definition else family + ':' + object_id)
        key = sid, path, actual_object, field, ordinal, locale
        if key in self._source_ids:
            return self._source_ids[key]
        record = self._files.get((sid, path))
        if record is None:
            raise ValueError(f'Missing literal source file: {sid}/{path}')
        result = dict(id='s%05d' % (len(self.sources) + 1), component=self._components[sid]['name'],
            path=path, object=actual_object, field=field, sha256=record['sha256'],
            source_class='local_mod' if sid == self.primary_sid else 'active_component_dependency',
            source_id=sid)
        if ordinal is not None:
            result['ordinal'] = ordinal
        if definition and not definition.get('id'):
            result.update(default=True, provider_type=definition['tag'])
        if locale:
            result['locale'] = locale
        self.sources.append(result)
        self._source_ids[key] = result['id']
        return result['id']

    def evidence(self, family, object_id, field):
        fields = self.fields(family, object_id)
        canonical = field
        for suffix in ('|@Link', '|@value', '|@Value'):
            if canonical.endswith(suffix):
                canonical = canonical[:-len(suffix)]
                break
        if canonical in fields and fields[canonical]['source'] is not None:
            return self._source(family, object_id, fields[canonical].get('raw_field', canonical), fields[canonical]['source'])
        raw_matches = [info for info in fields.values() if info.get('raw_field') == canonical and info['source'] is not None]
        if len(raw_matches) == 1:
            return self._source(family, object_id, canonical, raw_matches[0]['source'])
        # Object attributes are genuine source locations, separate from child field paths.
        attr = field.removeprefix('@')
        if field in ('__object__', 'C' + family):
            attr = 'id'
        if field.startswith('@') or attr == 'id':
            for d in reversed(self.catalog.objects.get((family, object_id), [])):
                if attr in d['node'].get('attributes', {}):
                    return self._source(family, object_id, '@' + attr, d, d)
            parent = self.node(family, object_id)
            if parent is not None and parent.get('parent'):
                return self.evidence(family, parent.get('parent'), field)
        raise ValueError(f'No literal field provenance: {family}:{object_id}/{field}')

    def fact(self, value, family, object_id, field, unit='', status='confirmed', basis=None):
        return self.fact_paths(value, family, object_id, [field], unit, status, basis)

    def fact_paths(self, value, family, object_id, fields, unit='', status='confirmed', basis=None):
        evidence = list(dict.fromkeys(self.evidence(family, object_id, f) for f in fields))
        if value is None or not evidence:
            raise ValueError('A resolved Fact requires a value and literal evidence')
        result = dict(value=value, unit=unit, status=status, evidence=evidence)
        if status == 'inferred':
            if not basis:
                raise ValueError('An inferred Fact requires basis')
            result['basis'] = basis
        return result

    @staticmethod
    def missing(reason, evidence=None):
        return dict(value=None, unit='', status='unresolved', evidence=list(evidence or []), reason=reason)

    @staticmethod
    def na(reason, evidence=None):
        return dict(value=None, unit='', status='not_applicable', evidence=list(evidence or []), reason=reason)

    def text(self, family, object_id, keys):
        if isinstance(keys, str):
            keys = [keys]
        node = self.node(family, object_id)
        is_name = bool(keys and ('/Name/' in keys[0] or keys[0] == 'Name'))
        anchors = ('Name',) if is_name else ('Description', 'Tooltip')
        ordered = []
        anchor_evidence = {}
        for field in anchors:
            key = value(node, field)
            if key:
                ordered.append(key)
                anchor_evidence[key] = self.evidence(family, object_id, field)
        ordered.extend(k for k in keys if '/' in k and k not in ordered)
        candidates = [(locale, key, self._texts[(locale, key)]) for key in ordered for locale in ('zhCN', 'enUS') if (locale, key) in self._texts]
        # Respect an actual Name/Description anchor; locale fallback only applies within that key.
        selected = None
        for key in ordered:
            at_key = [c for c in candidates if c[1] == key]
            if at_key:
                selected = next((c for c in at_key if c[0] == 'zhCN' and (CJK.search(c[2][0]) or c[2][0] in ('EMP', 'SCV'))), at_key[0])
                break
        if selected is None:
            return self.missing('活动组件及依赖未提供可解析的本地化文字：' + '、'.join(ordered), list(anchor_evidence.values()))
        locale, key, (text, record) = selected
        sid = self._source(family, object_id, key, record, locale=locale)
        evidence = ([anchor_evidence[key]] if key in anchor_evidence else []) + [sid]
        if CJK.search(text) or text in ('EMP', 'SCV'):
            return dict(value=text, unit='', status='confirmed', evidence=evidence, key=key, source_language=locale, source_locale=locale)
        draft = self.drafts.get((family, object_id, key)) or self.drafts.get(key)
        if draft:
            return dict(value=draft, unit='', status='inferred', evidence=evidence, key=key,
                source_language=locale, original_value=text, translation_draft=True,
                basis='根据活动来源原文制作的中文草稿，尚待本地化审校')
        return dict(value=text, unit='', status='confirmed', evidence=evidence, key=key,
            source_language='enUS', source_locale=locale, original_value=text)

    def actor_bindings(self, uid):
        if self._actor_map is None:
            self._actor_map = {}
            order = {s['id']: i for i, s in enumerate(self.catalog.sources)}
            for (family, aid), defs in self.catalog.objects.items():
                if family != 'Actor' or defs[-1]['tag'] not in ('CActorUnit', 'CActorMissile'):
                    continue
                node = self.node('Actor', aid)
                if node is None:
                    continue
                bound = []
                target = node.get('unitName')
                if target and '##' not in target:
                    bound.append((target, '@unitName', 0))
                for field, info in self.fields('Actor', aid).items():
                    if field.endswith('|@Terms'):
                        for target in re.findall(r'(?:UnitBirth|UnitCreation|UnitRevive|UnitConstruction\.Finish)\.([A-Za-z0-9_]+)', str(info['value'])):
                            bound.append((target, field, 1))
                for target, evidence, rank in bound:
                    self._actor_map.setdefault(target, []).append((rank, -order[defs[-1]['source_id']], aid, evidence))
            for target in self._actor_map:
                self._actor_map[target] = sorted(set(self._actor_map[target]))
        output = []
        for rank, layer, aid, field in self._actor_map.get(uid, []):
            try:
                output.append((rank, layer, aid, self.evidence('Actor', aid, field)))
            except ValueError:
                self.review_items.append(f'Actor:{aid} 的 {field} 绑定尚无可追溯来源')
        return output

    def hotkey(self, button_id, reference, literal):
        if not isinstance(reference, str) or not reference.startswith('Button/Hotkey/'):
            return literal
        selected = next(((locale, self._texts[(locale, reference)]) for locale in ('zhCN','enUS')
                         if (locale, reference) in self._texts), None)
        if selected is None:
            result = self.missing('活动 GameHotkeys.txt 未提供快捷键引用：' + reference, literal['evidence'])
            result['original_reference'] = reference
            return result
        locale, (key, record) = selected
        sid = self._source('Button', button_id, reference, record, locale=locale)
        return dict(value=key, unit='', status='confirmed', key=reference, source_locale=locale,
                    original_reference=reference, evidence=list(dict.fromkeys(literal['evidence'] + [sid])))

    def dependency_context(self):
        from utils.data.component_dependencies import dependency_context
        result = dependency_context(self.catalog)
        if self.registry:
            result['engine'] = {k: self.registry[k] for k in ('version', 'engine_path', 'engine_sha256', 'table_offset', 'stacking_offset')}
        return result
