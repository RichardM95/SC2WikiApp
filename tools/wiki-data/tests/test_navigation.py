import copy
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from sc2_wiki_data.navigation import attach_condition_targets, classify, discover_shapes, enrich, exclude_ammunition, group_entries, subfaction_label, text_field


class Context:
    def __init__(self, nodes):
        self.nodes = {(family,uid):ET.fromstring(xml) for (family,uid),xml in nodes.items()}
        self.sources = []
        self._texts = {}
    def node(self,family,oid):
        return self.nodes.get((family,oid))
    def evidence(self,family,oid,path):
        if not path:
            raise ValueError('no field')
        return family+':'+oid+'/'+path
    def _source(self,family,oid,key,record,locale=None):
        return family+':'+oid+'/'+key+'/'+locale
    def missing(self,reason,evidence=None):
        return dict(value=None,status='unresolved',unit='',reason=reason,evidence=list(evidence or []))


def entry(uid,name=None,classification='unit',sub='Webby'):
    return {'id':'Unit:'+uid,'kind':'unit','name':{'value':name or uid,'status':'confirmed','evidence':[]},
        'weapons':[],'modifiers':[],'presentation':{
            'classification':{'value':classification,'reason':'旧依据','evidence':['old']},
            'visibility':{'value':'public','reason':'公开','evidence':[]},
            'subfaction_key':sub,'editor_prefix':{'value':'- '+sub,'status':'confirmed','evidence':['prefix']}}}


class NavigationTests(unittest.TestCase):
    def test_regroup_guards_allow_condition_text_and_links_only(self):
        from sc2_wiki_data.condition_guards import unchanged_except_condition_texts,unchanged_except_condition_targets
        condition=dict(value='升级计数：0，派系，已完成',status='confirmed',unit='',evidence=['s1'],
            details={'type':'CRequirementCountUpgrade','fields':{'Count|@Link':'Choice','Flags|TechTreeCheat|@value':'0'}})
        old={'entries':[{'presentation':{'research':[{'id':'Upgrade:Choice'}]}}],
            'condition':condition,'sources':[{'id':'s1','field':'Count'}]}
        updated=copy.deepcopy(old);updated['condition']['value']='升级计数：派系，已完成'
        unchanged_except_condition_texts(old,updated)
        protected=copy.deepcopy(updated)
        updated['entries'][0]['presentation']['research'][0]['condition_targets']=[{'target':'Unit:U'}]
        unchanged_except_condition_targets(protected,updated)
        bad=copy.deepcopy(old);bad['condition']['details']['fields']['Count|@Link']='Different'
        with self.assertRaises(ValueError):unchanged_except_condition_texts(old,bad)
        bad=copy.deepcopy(old);bad['condition']['status']='inferred'
        with self.assertRaises(ValueError):unchanged_except_condition_texts(old,bad)
        bad=copy.deepcopy(protected);bad['sources'][0]['field']='Different'
        with self.assertRaises(ValueError):unchanged_except_condition_targets(protected,bad)
        missing=copy.deepcopy(old);missing['condition'].update(value=None,status='unresolved')
        bad=copy.deepcopy(missing);bad['condition']['value']='已解析'
        with self.assertRaises(ValueError):unchanged_except_condition_texts(missing,bad)

    def test_research_associations_preserve_and_condition_and_branch_identity(self):
        ids=('A','B')
        condition={'value':'同时满足两个派系状态','status':'inferred','unit':'','evidence':['sa','sb'],
            'details':{'type':'CRequirementAnd','operands':[
                {'condition':{'value':'已完成状态','status':'inferred','unit':'','evidence':[sid],
                    'details':{'id':'RequirementNode:'+upgrade,'type':'CRequirementCountUpgrade',
                        'fields':{'Count|@Link':upgrade,'Count|@State':'CompleteOnly'}}}}
                for upgrade,sid in zip(ids,('sa','sb'))]}}
        target=entry('UnitTarget');target['production']=[{'producer':'Unit:Factory','ability':'Abil:Train','command':'Train1','requirements':[condition],'evidence':['prod']}]
        factory=entry('Factory');factory['presentation']['research']=[{'id':'Upgrade:A','ability':'Abil:Research','command':'Research1'},{'id':'Upgrade:B','ability':'Abil:Research','command':'Research2'}]
        other=entry('Factory2');other['presentation']['research']=[{'id':'Upgrade:A','ability':'Abil:Research2','command':'Research1'}]
        data={'sources':[{'id':sid,'object':'RequirementNode:'+upgrade,'field':'Count'} for upgrade,sid in zip(ids,('sa','sb'))],
            'entries':[target,factory,other]}
        report=attach_condition_targets(data)
        self.assertEqual(report['research_rows'],3)
        for row in factory['presentation']['research']:
            link=row['condition_targets'][0]
            self.assertEqual(link['target'],'Unit:UnitTarget');self.assertEqual(link['condition'],condition)
            self.assertEqual(link['relation'],'production_requirement')
            self.assertNotIn('unlocks',link)
        self.assertEqual(len(other['presentation']['research'][0]['condition_targets']),1)

    def test_discovery_adds_actual_local_burrow_pair_not_another_species(self):
        ctx=Context({('Unit','A'):'<CUnit><AbilArray Link="Burrow"/><AbilArray Link="ChangeSpecies"/></CUnit>',
            ('Unit','B'):'<CUnit><LeaderAlias value="A"/><Name value="Unit/Name/A"/></CUnit>',
            ('Unit','Z'):'<CUnit/>',('Unit','Ammo'):'<CUnit><LeaderAlias value="A"/><Name value="Unit/Name/A"/></CUnit>',
            ('Abil','Burrow'):'<CAbilBurrow><Unit value="B"/></CAbilBurrow>',
            ('Abil','ChangeSpecies'):'<CAbilMorph><InfoArray Unit="Z"/></CAbilMorph>',
            ('Effect','Launch'):'<CEffectLaunchMissile><AmmoUnit value="Ammo"/></CEffectLaunchMissile>'})
        ctx.primary_sid='primary'
        ctx.catalog=type('Catalog',(),{'objects':{key:[{'source_id':'primary'}] for key in ctx.nodes if key[0] in ('Unit','Effect')}})()
        ctx._texts={('zhCN','Unit/Name/A'):('刺蛇',{}),('zhCN','Unit/Name/Z'):('潜伏者',{}),
            ('zhCN','Unit/EditorPrefix/A'):('- Webby',{}),('zhCN','Unit/EditorPrefix/B'):('- Webby',{})}
        e=entry('A','刺蛇');data={'entries':[e],'sources':[]}
        with patch('sc2_wiki_data.navigation.hydrate_sources'):
            results=discover_shapes(ctx,data)
        self.assertEqual([r['id'] for r in results],['B'])
        self.assertEqual(results[0]['from'],'Unit:A')
        self.assertTrue(results[0]['evidence'])
        self.assertIn('不同兵种',str(data['diagnostics']))

    def test_editor_prefix_does_not_reuse_name_or_tooltip(self):
        ctx = Context({('Unit','U'):'<CUnit><Name value="Wrong"/><Description value="Tooltip"/><EditorPrefix value="Explicit"/></CUnit>'})
        ctx._texts = {('zhCN','Wrong'):('错误名称',{}),('zhCN','Tooltip'):('错误说明',{}),
            ('zhCN','Explicit'):('- Deluxe',{}),('zhCN','Unit/EditorPrefix/U'):('- Webby',{})}
        result = text_field(ctx,'Unit','U','EditorPrefix','Unit/EditorPrefix/U')
        self.assertEqual(result['value'],'- Deluxe')
        self.assertEqual(result['key'],'Explicit')
        self.assertEqual(len(result['evidence']),2)

    def test_weapon_name_prefers_anchor_over_conventional_key(self):
        ctx = Context({('Weapon','W'):'<CWeapon><Name value="Weapon/Name/Other"/></CWeapon>'})
        ctx._texts = {('zhCN','Weapon/Name/Other'):('明确武器',{}),('zhCN','Weapon/Name/W'):('惯例武器',{})}
        self.assertEqual(text_field(ctx,'Weapon','W','Name','Weapon/Name/W')['value'],'明确武器')

    def test_hero_not_heroic_attribute(self):
        for xml,expected in [('<CUnit><Attributes index="Heroic" value="1"/></CUnit>','unit'),
            ('<CUnit><EditorCategories value="Race:Zerg,ObjectType:Hero"/></CUnit>','hero')]:
            ctx=Context({('Unit','U'):xml}); e=entry('U'); classify(ctx,e,ctx.node('Unit','U'))
            self.assertEqual(e['presentation']['classification']['value'],expected)

    def test_editor_technical_markers_are_always_excluded(self):
        ctx=Context({('Unit','U'):'<CUnit/>'})
        for prefix in ('Weapon -','Wrapon -','!'):
            e=entry('U');e['presentation']['editor_prefix']['value']=prefix
            classify(ctx,e,ctx.node('Unit','U'))
            self.assertEqual(e['presentation']['visibility']['value'],'excluded')

    def test_flying_does_not_mean_projectile(self):
        ctx=Context({('Unit','U'):'<CUnit><PlaneArray index="Air" value="1"/></CUnit>'})
        e=entry('U');classify(ctx,e,ctx.node('Unit','U'))
        self.assertEqual(e['presentation']['visibility']['value'],'public')
        ctx.nodes[('Unit','U')]=ET.fromstring('<CUnit><FlagArray index="Missile" value="1"/></CUnit>')
        classify(ctx,e,ctx.node('Unit','U'))
        self.assertEqual(e['presentation']['visibility']['value'],'excluded')

    def test_missile_template_and_ammo_effect_relation(self):
        ctx=Context({('Unit','U'):'<CUnit parent="MISSILE"/>'})
        e=entry('U');classify(ctx,e,ctx.node('Unit','U'))
        self.assertEqual(e['presentation']['visibility']['value'],'excluded')
        ammo=entry('Ammo');owner=entry('Owner')
        owner['weapons']=[{'effect':{'value':{'root':'Effect:Launch','effects':[
            {'type':'CEffectLaunchMissile','id':'Effect:Launch','fields':{'AmmoUnit|@value':'Ammo'}}]}}}]
        exclude_ammunition(ctx,{'entries':[owner,ammo]})
        self.assertEqual(ammo['presentation']['visibility']['value'],'excluded')
        self.assertEqual(owner['presentation']['visibility']['value'],'public')

    def test_unclassified_goes_other_and_auxiliary_stays_optional(self):
        ctx=Context({('Unit','U'):'<CUnit/>'})
        e=entry('U',classification='unclassified');classify(ctx,e,ctx.node('Unit','U'))
        self.assertEqual(e['presentation']['classification']['value'],'other')
        self.assertEqual(e['presentation']['classification']['previous_value'],'unclassified')
        e=entry('U',classification='auxiliary');classify(ctx,e,ctx.node('Unit','U'))
        self.assertEqual(e['presentation']['visibility']['value'],'auxiliary')

    def test_revive_chain_groups_three_forms_with_living_primary(self):
        ctx=Context({('Unit','Living'):'<CUnit><AbilArray Link="Death"/></CUnit>',
            ('Unit','Corpse'):'<CUnit><ReviveType value="Living"/><AbilArray Link="Revive"/></CUnit>',
            ('Unit','Cocoon'):'<CUnit/>',
            ('Abil','Death'):'<CAbilMorph><InfoArray Unit="Corpse"/></CAbilMorph>',
            ('Abil','Revive'):'<CAbilMorph><InfoArray Unit="Cocoon"/><InfoArray Unit="Living"/></CAbilMorph>'})
        entries=[entry('Cocoon','暴龙兽茧'),entry('Corpse','暴龙兽尸体'),entry('Living','暴龙兽')]
        group_entries(ctx,entries)
        self.assertEqual(entries[0]['presentation']['group']['primary'],'Unit:Living')
        self.assertEqual({m['id'] for m in entries[0]['presentation']['group']['members']},
            {'Unit:Living','Unit:Corpse','Unit:Cocoon'})
        self.assertTrue(all(m['evidence'] for m in entries[0]['presentation']['group']['members']))

    def test_same_name_alias_groups_modes_but_different_subfactions_do_not(self):
        ctx=Context({('Unit','A'):'<CUnit/>',('Unit','B'):'<CUnit><LeaderAlias value="A"/></CUnit>'})
        entries=[entry('A','刺蛇'),entry('B','刺蛇')];group_entries(ctx,entries)
        self.assertEqual(entries[1]['presentation']['group']['primary'],'Unit:A')
        entries=[entry('A','刺蛇',sub='Webby'),entry('B','刺蛇',sub='Deluxe')];group_entries(ctx,entries)
        self.assertEqual(len(entries[0]['presentation']['group']['members']),1)

    def test_differently_named_reversible_modes_require_selection_alias(self):
        ctx=Context({('Unit','A'):'<CUnit><AbilArray Link="Down"/></CUnit>',
            ('Unit','B'):'<CUnit><AbilArray Link="Up"/><SubgroupAlias value="A"/></CUnit>',
            ('Abil','Down'):'<CAbilMorph><InfoArray Unit="B"/></CAbilMorph>',
            ('Abil','Up'):'<CAbilMorph><InfoArray Unit="A"/></CAbilMorph>'})
        entries=[entry('B','恶蝠游骑兵',sub='CovertOps'),entry('A','恶火游骑兵',sub='CovertOps')]
        group_entries(ctx,entries)
        self.assertEqual(entries[0]['presentation']['group']['primary'],'Unit:A')
        self.assertEqual(len(entries[0]['presentation']['group']['members']),2)
        ev=entries[0]['presentation']['group']['members'][0]['evidence']
        self.assertTrue(any('SubgroupAlias' in source for source in ev))
        self.assertTrue(any('Down' in source for source in ev))
        self.assertTrue(any('Up' in source for source in ev))
        ctx.nodes[('Unit','B')]=ET.fromstring('<CUnit><AbilArray Link="Up"/></CUnit>')
        entries=[entry('A','恶火'),entry('B','恶蝠')];group_entries(ctx,entries)
        self.assertTrue(all(len(e['presentation']['group']['members'])==1 for e in entries))

    def test_one_way_morph_with_selection_alias_does_not_merge_species(self):
        ctx=Context({('Unit','A'):'<CUnit><AbilArray Link="Morph"/></CUnit>',
            ('Unit','B'):'<CUnit><SubgroupAlias value="A"/></CUnit>',
            ('Abil','Morph'):'<CAbilMorph><InfoArray Unit="B"/></CAbilMorph>'})
        entries=[entry('A','跳虫'),entry('B','毒爆虫')];group_entries(ctx,entries)
        self.assertTrue(all(len(e['presentation']['group']['members'])==1 for e in entries))

    def test_same_names_do_not_join_when_morphs_target_external_vanilla_units(self):
        ctx=Context({('Unit','J'):'<CUnit><AbilArray Link="Down"/></CUnit>',
            ('Unit','JT'):'<CUnit><AbilArray Link="Up"/><SubgroupAlias value="Vanilla"/></CUnit>',
            ('Abil','Down'):'<CAbilMorph><InfoArray Unit="VanillaTank"/></CAbilMorph>',
            ('Abil','Up'):'<CAbilMorph><InfoArray Unit="Vanilla"/></CAbilMorph>'})
        entries=[entry('J','掠夺者飞车党'),entry('JT','掠夺者飞车党')];group_entries(ctx,entries)
        self.assertTrue(all(len(e['presentation']['group']['members'])==1 for e in entries))

    def test_morph_to_different_unit_or_shared_cocoon_does_not_merge(self):
        ctx=Context({('Unit','Zergling'):'<CUnit><AbilArray Link="Morph"/></CUnit>',
            ('Unit','Baneling'):'<CUnit/>',('Unit','Egg'):'<CUnit/>',
            ('Abil','Morph'):'<CAbilMorph><InfoArray Unit="Egg"/><InfoArray Unit="Baneling"/></CAbilMorph>'})
        entries=[entry('Zergling','跳虫'),entry('Baneling','毒爆虫'),entry('Egg','虫卵')]
        group_entries(ctx,entries)
        self.assertTrue(all(len(e['presentation']['group']['members'])==1 for e in entries))

    def test_user_translations_and_deluxe_draft(self):
        from sc2_wiki_data.factions import default_config
        configured={row['id']:row['name'] for row in default_config()['factions']}
        self.assertEqual(subfaction_label('Silver')['name'],configured['Silver'])
        self.assertEqual(subfaction_label('Junker')['name'],'掠夺者')
        self.assertEqual(subfaction_label('Golden F')['name'],'远征军 F')
        self.assertTrue(subfaction_label('Deluxe')['draft'])

    def test_no_research_entry_is_not_fabricated(self):
        ctx=Context({('Unit','U'):'<CUnit/>',('Upgrade','X'):'<CUpgrade/>'})
        e=entry('U');e['modifiers']=[{'id':'Upgrade:X','kind':'upgrade','effect':{'value':'生命增加','status':'confirmed','evidence':['source']}}]
        data={'entries':[e],'sources':[]}
        with patch('sc2_wiki_data.navigation.hydrate_sources'),patch('sc2_wiki_data.navigation.research',return_value=[]):
            enrich(ctx,data)
        self.assertEqual(e['presentation']['available_upgrades'],[])
        self.assertEqual(data['diagnostics'][0]['upgrades'],['Upgrade:X'])

    def test_available_upgrade_uses_actual_button_and_keeps_two_branches(self):
        ctx=Context({('Unit','U'):'<CUnit/>',('Unit','Forge'):'<CUnit/>',
            ('Abil','Research'):'<CAbilResearch><InfoArray index="Research1" Upgrade="X"><Button DefaultButtonFace="BX"/></InfoArray><InfoArray index="Research2" Upgrade="X"><Button DefaultButtonFace="BX2"/></InfoArray></CAbilResearch>',
            ('Button','BX'):'<CButton/>',('Button','BX2'):'<CButton/>',('Upgrade','X'):'<CUpgrade parent="Y"/>'})
        ctx._texts={('zhCN','Button/Name/BX'):('防暴护盾',{}),('zhCN','Button/Tooltip/BX'):('生命值提高10点。',{}),
            ('zhCN','Button/Name/BX2'):('另一研发入口',{})}
        e=entry('U');e['modifiers']=[{'id':'Upgrade:X','kind':'upgrade','effect':{'value':'生命增加','status':'confirmed','evidence':['effect']},'evidence':['association']}]
        producer=entry('Forge','锻炉')
        producer['presentation']['research']=[{'id':'Upgrade:X','ability':'Abil:Research','command':command,
            'name':{'value':'原升级名','status':'confirmed','evidence':['name']}} for command in ('Research1','Research2')]
        with patch('sc2_wiki_data.navigation.hydrate_sources'):
            enrich(ctx,{'entries':[e,producer],'sources':[]})
        rows=e['presentation']['available_upgrades']
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['name']['value'],'防暴护盾')
        self.assertEqual(rows[0]['producer'],'Unit:Forge')
        self.assertEqual(rows[0]['association_evidence'],['association'])
        self.assertNotIn('levels',rows[0])


if __name__=='__main__':
    unittest.main()

