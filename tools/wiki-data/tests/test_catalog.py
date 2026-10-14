import hashlib
import json
import sys
import unittest
import os
from pathlib import Path

from sc2_wiki_data.catalog_adapter import ComponentPool, Context, toolbox_imports
from sc2_wiki_data.catalog_generate import base_stat, visuals, build_entry, load_validator

TOOLBOX = Path(os.environ.get('SCORE_TOOLBOX_ROOT',str(Path(__file__).resolve().parents[3] / 'SC2EditorTool')))
HUB = Path(os.environ.get('SCORE_HUB_ROOT',str(Path(__file__).resolve().parents[2])))
toolbox_imports(TOOLBOX)
from utils.data.catalog import Catalog, encode_node, parse_document


def synthetic(definitions, texts=()):
    sources, files = [], []
    for sid, xml in definitions:
        text = '<Catalog>' + xml + '</Catalog>'
        source = dict(id=sid, name=sid + '.SC2Mod', fingerprint=hashlib.sha256(text.encode()).hexdigest(),
            metadata={'DocumentInfo': '<DocInfo><Dependencies/></DocInfo>'}, dependencies=[], diagnostics=[])
        sources.append(source)
        files.append(dict(source_id=sid, path='Base.SC2Data/GameData/GameData.xml', kind='xml',
            nodes=[encode_node(n) for n in parse_document(text).nodes], sha256=hashlib.sha256(text.encode()).hexdigest()))
    for sid, locale, text in texts:
        files.append(dict(source_id=sid, path=locale + '.SC2Data/LocalizedData/GameStrings.txt', kind='text',
            locale=locale, text=text, sha256=hashlib.sha256(text.encode()).hexdigest()))
    return Context(Catalog(sources, files))


class CatalogTests(unittest.TestCase):
    def test_hotkey_reference_resolves_literal_text_key(self):
        ctx = synthetic([('local','<CButton id="Build"><Hotkey value="Button/Hotkey/Build"/></CButton>')],
                        [('local','zhCN','Button/Hotkey/Build=B')])
        literal = ctx.fact('Button/Hotkey/Build','Button','Build','Hotkey')
        result = ctx.hotkey('Build','Button/Hotkey/Build',literal)
        self.assertEqual(result['value'], 'B')
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(len(result['evidence']), 2)
        self.assertEqual(ctx.sources[-1]['field'], 'Button/Hotkey/Build')

    def test_implicit_append_provenance_keeps_physical_local_index(self):
        ctx = synthetic([('core', '<CActorUnit id="Parent"><On Terms="ParentEvent" Send="First"/><On Terms="SecondEvent" Send="Second"/></CActorUnit>'),
            ('local', '<CActorUnit id="Child" parent="Parent"><On Terms="UnitBirth.Child" Send="Create"/></CActorUnit>')])
        self.assertEqual(ctx.fields('Actor', 'Child')['On|2|@Terms']['value'], 'UnitBirth.Child')
        sid = ctx.evidence('Actor', 'Child', 'On|2|@Terms')
        source = next(s for s in ctx.sources if s['id'] == sid)
        self.assertEqual(source['object'], 'Actor:Child')
        self.assertEqual(source['field'], 'On|@Terms')
        self.assertEqual(ctx.catalog._provider_field_maps[('local', source['path'], source['ordinal'], 'On|2|@Terms')], {'On|@Terms'})
    def test_constants_apply_only_to_declared_field_and_keep_raw_evidence(self):
        ctx = synthetic([('core', '<const id="Structure" path="CUnit.Collide.index" value="Land6"/>'
            '<const id="Ground" path="CUnit.Collide.index" value="Land2"/>'
            '<CUnit id="Building"><Attributes index="Structure" value="1"/><Collide index="Structure" value="1"/>'
            '<Mover value="Ground"/><Name value="Unit/Name/##id##"/></CUnit>'
            '<CBehaviorBuff id="Other"><Attributes index="Structure" value="1"/></CBehaviorBuff>')])
        node = ctx.node('Unit', 'Building')
        self.assertEqual(node.find('Attributes').get('index'), 'Structure')
        self.assertEqual(node.find('Collide').get('index'), 'Land6')
        self.assertEqual(node.find('Mover').get('value'), 'Ground')
        self.assertEqual(node.find('Name').get('value'), 'Unit/Name/Building')
        self.assertEqual(ctx.node('Behavior', 'Other').find('Attributes').get('index'), 'Structure')
        sid = ctx.evidence('Unit', 'Building', 'Collide|Land6|@value')
        source = next(s for s in ctx.sources if s['id'] == sid)
        self.assertEqual(source['field'], 'Collide|Structure')
        self.assertEqual(ctx.evidence('Unit', 'Building', 'Collide|Structure'), sid)
        from sc2_wiki_data.catalog_generate import classify
        self.assertEqual(classify(ctx, 'Building')['value'], 'building')
    def test_default_parent_overlay_and_zero(self):
        ctx = synthetic([
            ('core', '<CUnit default="1"><LifeMax value="1"/><Mover value="Ground"/></CUnit>'
                '<CUnit id="Base"><LifeMax value="90"/><EnergyMax value="200"/><Speed value="2.25"/></CUnit>'),
            ('campaign', '<CUnit id="Base"><CostResource index="Vespene" value="50"/></CUnit>'),
            ('local', '<CUnit id="Child" parent="Base"><ShieldsMax value="120"/><LifeArmor value="0"/></CUnit>')])
        self.assertEqual(base_stat(ctx, 'Child', 'LifeMax', 'life')['value'], 90)
        self.assertEqual(base_stat(ctx, 'Child', 'ShieldsMax', 'shields')['value'], 120)
        self.assertEqual(base_stat(ctx, 'Child', 'EnergyMax', 'energy')['value'], 200)
        self.assertEqual(base_stat(ctx, 'Child', 'Speed', 'catalog_speed')['value'], 2.25)
        self.assertEqual(base_stat(ctx, 'Child', 'LifeArmor', 'armor')['value'], 0)
        default_sid = ctx.evidence('Unit', 'Child', 'Mover')
        source = next(s for s in ctx.sources if s['id'] == default_sid)
        self.assertTrue(source['default'])
        self.assertEqual(source['object'], 'Unit:__default_CUnit')
        self.assertEqual(ctx.evidence('Unit', 'Child', 'ShieldsMax|@value'), ctx.evidence('Unit', 'Child', 'ShieldsMax'))
        with self.assertRaises(ValueError):
            ctx.evidence('Unit', 'Child', 'ShieldMax')

    def test_parent_same_family_and_array_deletion(self):
        ctx = synthetic([
            ('core', '<CEffectDamage id="Base"><Amount value="999"/></CEffectDamage>'
                '<CUnit id="Base"><LifeMax value="40"/><AbilArray index="0" Link="Attack"/><AbilArray index="1" Link="Move"/></CUnit>'),
            ('local', '<CUnit id="Child" parent="Base"><AbilArray index="0" removed="1"/></CUnit>')])
        self.assertEqual(base_stat(ctx, 'Child', 'LifeMax', '')['value'], 40)
        self.assertNotIn('Amount', ctx.fields('Unit', 'Child'))
        self.assertNotIn('AbilArray|0', ctx.fields('Unit', 'Child'))
        self.assertEqual(ctx.fields('Unit', 'Child')['AbilArray|1']['value'], 'Move')

    def test_actual_name_anchor_and_actor_binding(self):
        ctx = synthetic([('local', '<CUnit id="Child"><Name value="Unit/Name/Shared"/></CUnit>'
                '<CActorUnit id="DifferentActor" unitName="Child"><UnitIcon value="Assets\\Child.dds"/></CActorUnit>')],
            [('local', 'zhCN', 'Unit/Name/Shared=实际名称\nUnit/Name/Child=错误推测名称\n')])
        name = ctx.text('Unit', 'Child', ['Unit/Name/Child'])
        self.assertEqual(name['value'], '实际名称')
        self.assertEqual(name['key'], 'Unit/Name/Shared')
        icon, _, _, actors = visuals(ctx, 'Child')
        self.assertEqual(icon['value'], 'Assets\\Child.dds')
        self.assertEqual(actors[0]['id'], 'Actor:DifferentActor')

    def test_portrait_model_image_has_real_source(self):
        ctx = synthetic([('local', '<CUnit id="Child"/>'
            '<CActorUnit id="DifferentActor" unitName="Child"><PortraitModel value="Portrait"/></CActorUnit>'
            '<CModel id="Portrait"><Model value="Assets\\Portrait.m3"/><Image value="Assets\\Portrait.dds"/></CModel>')])
        _, portrait, image, _ = visuals(ctx, 'Child')
        self.assertEqual(image['value'], 'Assets\\Portrait.dds')
        self.assertEqual(portrait['value'], 'Assets\\Portrait.m3')
        providers = [s for s in ctx.sources if s['id'] in image['evidence']]
        self.assertTrue(any(s['object'] == 'Model:Portrait' and s['field'] == 'Image' for s in providers))

    def test_english_is_preserved_until_real_draft(self):
        ctx = synthetic([('local', '<CUnit id="Child"/>')], [('local', 'enUS', 'Unit/Name/Child=Sample\n')])
        name = ctx.text('Unit', 'Child', ['Unit/Name/Child'])
        self.assertEqual(name['value'], 'Sample')
        self.assertEqual(name['source_locale'], 'enUS')
        self.assertNotIn('translation_draft', name)

    def test_no_id_template_is_not_global_named_template(self):
        ctx = synthetic([('local', '<CUnit default="1"><LifeMax value="1"/></CUnit>'
            '<CUnit default="1" id="NamedTemplate"><LifeMax value="200"/></CUnit><CUnit id="Plain"/>')])
        self.assertEqual(base_stat(ctx, 'Plain', 'LifeMax', '')['value'], 1)

    def test_shared_wiki_preflight_accepts_sources_and_presentation(self):
        ctx = synthetic([('local', '<CUnit id="Child"><LifeMax value="40"/></CUnit>'
            '<CActorUnit id="DifferentActor" unitName="Child"><PortraitModel value="Portrait"/></CActorUnit>'
            '<CModel id="Portrait"><Image value="Assets\\Portrait.dds"/></CModel>')],
            [('local', 'zhCN', 'Unit/Name/Child=测试单位\nButton/Tooltip/Child=测试说明\n')])
        entry = build_entry(ctx, {'id': 'Unit:Child'}, {'name': '测试阵营'})
        data = dict(schema_version=1, project=dict(name='测试', primary_mod='local.SC2Mod', language='zhCN'),
            sources=ctx.sources, entries=[entry], selected_objects=['Unit:Child'], review_items=ctx.review_items)
        load_validator(HUB).validate(data)


if __name__ == '__main__':
    unittest.main()
