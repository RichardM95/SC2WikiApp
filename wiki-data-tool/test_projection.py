import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from projection import build, collect_evidence, friendly, graph_summary, other_page, player_entry, project_value


class Assets:
    def __init__(self,output,*args):
        self.output=Path(output)
        (self.output/'assets').mkdir(parents=True,exist_ok=True)
        self.records=[]
    def image(self,fact,data,role='icon',m3_fallback=None):
        return None
    def save(self):
        (self.output/'assets/manifest.json').write_text('{"assets":[]}',encoding='utf-8')


def fact(value,**extra):
    return dict(value=value,status='confirmed',unit='',evidence=['s00001'],**extra)


def entry(uid='U',visibility='public'):
    return {'id':'Unit:'+uid,'kind':'unit','name':fact('陆战队员'),
        'description':fact('作战单位'),'faction':fact('SCORE-Umojan'),'icon':fact('Assets/icon.dds'),
        'portrait':fact('Assets/model.m3'),'stats':{'life':fact(45),'energy':fact(0)},
        'weapons':[{'id':'Weapon:Gun','name':fact('步枪'),'icon':fact('Assets/gun.dds'),
            'damage':fact({'execution_root':'Effect:Damage','components':[{'effect':'Effect:Damage','amount':6,'count':1,'path':['Effect:Damage']}]}),
            'effect':fact({'root':'Effect:Damage','effects':[{'id':'Effect:Damage','type':'CEffectDamage','fields':{'Amount':'6'}}],'issues':[]})}],
        'abilities':[{'id':'Abil:move','name':fact('移动')},{'id':'Abil:Stim','name':fact('兴奋剂'),'effect':fact('施加 Behavior:Stim') }],
        'modifiers':[],'production':[],'related_entries':['Unit:Hidden'],
        'presentation':{'classification':{'value':'unit','reason':'玩家单位','evidence':['s00001']},
            'visibility':{'value':visibility,'reason':'测试','evidence':['s00001']},
            'subfaction_key':'Umojan','group':{'primary':'Unit:'+uid,'members':[{'id':'Unit:'+uid,'kind':'primary','evidence':['s00001']}]},
            'command_cards':[],'available_upgrades':[]}}


class ExportTests(unittest.TestCase):
    def test_research_condition_links_exclude_technical_targets_and_producers(self):
        e=entry();hidden=entry('Hidden','excluded')
        links=[dict(target=e['id'],producer=hidden['id'],condition=fact('需要条件'),evidence=['s00001']),
            dict(target=hidden['id'],producer=e['id'],condition=fact('需要条件'),evidence=['s00001'])]
        e['presentation']['research']=[dict(id='Upgrade:Choice',condition_targets=copy.deepcopy(links))]
        e['presentation']['available_upgrades']=copy.deepcopy(e['presentation']['research'])
        original=copy.deepcopy(e)
        assets=type('ReadOnlyAssets',(),{'image':lambda *args,**kwargs:None})()
        result=player_entry(e,{'_faction':'Umojan'},assets,{('Umojan',e['id']):e,('Umojan',hidden['id']):hidden},{})
        for rows in (result['presentation']['research'],result['presentation']['available_upgrades']):
            self.assertEqual(len(rows[0]['condition_targets']),1)
            self.assertEqual(rows[0]['condition_targets'][0]['target'],e['id'])
            self.assertIsNone(rows[0]['condition_targets'][0]['producer'])
        self.assertEqual(e,original)

    def test_queue_is_hidden_in_skills_but_retained_in_commands_and_source(self):
        e=entry();e['abilities'].append({'id':'Abil:que1','catalog_type':'CAbilQueue','name':fact('队列(1)')})
        e['presentation']['command_cards']=[{'id':'root','buttons':[{'ability':'Abil:que1','name':fact('队列(1)')}]}]
        before=copy.deepcopy(e)
        assets=type('ReadOnlyAssets',(),{'image':lambda *args,**kwargs:None})()
        projected=player_entry(e,{'_faction':'Umojan'},assets,{('Umojan',e['id']):e},{})
        self.assertNotIn('Abil:que1',[ability['id'] for ability in projected['abilities']])
        self.assertEqual(projected['presentation']['command_cards'][0]['buttons'][0]['ability'],'Abil:que1')
        self.assertEqual(e,before)

    def test_partial_text_is_safe_and_audit_metadata_is_removed(self):
        e=entry();e['description'].update(value=None,status='unresolved',
            partial_text='持续10秒，恢复无点，执行 Effect:Internal。',
            raw_template='持续<d ref="Behavior,X,Duration"/>秒。',
            dynamic_text={'expansions':[{'tag':'<d ref="Behavior,X,Duration"/>','operands':[]}]})
        original=copy.deepcopy(e)
        assets=type('ReadOnlyAssets',(),{'image':lambda *args,**kwargs:None})()
        result=player_entry(e,{'_faction':'Umojan'},assets,{('Umojan',e['id']):e},{})
        description=result['description']
        self.assertIsNone(description['value']);self.assertEqual(description['status'],'unresolved')
        self.assertEqual(description['partial_text'],'持续10秒，恢复无点，执行 对应效果。')
        self.assertEqual(description['partial'],description['partial_text'])
        self.assertNotIn('raw_template',description);self.assertNotIn('dynamic_text',description)
        self.assertEqual(e,original)
        self.assertEqual(friendly('伤害<d ref="Effect,X,Amount"/>点。',{}),'伤害无点。')

    def test_graph_summary_retains_events_randomness_without_internal_ids(self):
        graph={'execution_root':'Effect:PrivateRoot','effects':[
            {'id':'Effect:PrivatePersistent','type':'CEffectCreatePersistent','fields':{'Flags|RandomPeriod|@value':'1','Flags|RandomOffset|@value':'1'}},
            {'id':'Effect:PrivateDamage','type':'CEffectDamage','trigger':{'kind':'ImpactEffect','owner':'Effect:PrivateMissile','metadata':{'runtime_dependent':True}}}],
            'triggers':[{'kind':'DamageResponse','owner':'Behavior:PrivateBuff','fields':{'DamageResponse|@Fatal':'1'},'behavior_states':[{'mode':'Remove'}]}],
            'components':[{'schedule':[{'phase':'ExpireEffect','behavior':'Behavior:PrivateBuff'}]}]}
        summary=graph_summary(graph)
        for expected in ('导弹命中时','受到致死伤害时响应','行为自然到期时','随机周期选择','随机位置选择','行为移除条件'):
            self.assertIn(expected,summary)
        self.assertNotIn('Private',summary);self.assertNotIn('Effect:',summary)
        self.assertEqual(project_value(graph,{})['summary'],summary)

    def test_marine_nine_damage_keeps_components_and_private_targets_out(self):
        graph={'execution_root':'Effect:PrivateRifle','components':[{'effect':'Effect:PrivateRifle',
            'amount':9,'count':1,'bonus':{},'conditions':[],'schedule':[],
            'target':{'inherited':None,'owner':'Effect:PrivateRifle','selectors':{
                'LaunchLocation|@Value':'SourceUnit','ImpactLocation|@Value':'TargetUnitOrPoint'}},
            'areas':[],'path':['Effect:PrivateRifle'],'trigger':None}],
            'effects':[{'id':'Effect:PrivateRifle','type':'CEffectDamage','fields':{'Amount|@value':'9'}}],
            'display':{'root':'Effect:PrivateDisplay','components':[{'amount':6,'count':1}]}}
        projected=project_value(graph,{})
        self.assertEqual(projected['components'][0]['amount'],9)
        self.assertEqual(projected['components'][0]['count'],1)
        self.assertEqual(projected['display']['components'][0]['amount'],6)
        self.assertEqual(projected['components'][0]['target']['description'],'施法者 → 目标单位或位置')
        serialized=json.dumps(projected,ensure_ascii=False)
        for internal in ('Private','selectors','owner','inherited','LaunchLocation','execution_root','fields'):
            self.assertNotIn(internal,serialized)
        self.assertNotIn('total',projected)

    def test_multihit_conditional_bonus_events_and_zero_are_retained(self):
        graph={'execution_root':'Effect:PrivateMulti','components':[
            {'effect':'Effect:Hit1','amount':10,'count':2,'bonus':{'Armored':5},
                'conditions':[{'text':'目标为重甲','status':'confirmed','structure':{'id':'Validator:Private'}}],
                'schedule':[{'persistent':'Effect:Private','interval':0,'occurrence':0}],
                'target':{'owner':'Effect:Private','selectors':{'WhichUnit|@Value':'Unknown'}},'areas':[{'Radius':'2','Fraction':'0','Effect':'HiddenID'}]},
            {'effect':'Effect:Hit2','amount':0,'count':None,'bonus':{},
                'trigger':{'kind':'DamageResponse','owner':'Behavior:Private','fields':{'DamageResponse|@Fatal':'1','DamageResponse|@ModifyFraction':'0.5'}},
                'schedule':[{'phase':'DamageResponse','behavior':'Behavior:Private','period':0,'duration':0}]}],
            'effects':[{'type':'CEffectDamage','fields':{'Amount':'10'}}]}
        projected=project_value(graph,{})
        first,second=projected['components']
        self.assertEqual(first['bonus'],{'Armored':5})
        self.assertEqual(first['conditions'][0]['text'],'目标为重甲')
        self.assertEqual(first['schedule'][0]['interval'],0)
        self.assertEqual(first['areas'][0],{'radius':2,'fraction':0})
        self.assertEqual(first['target']['description'],'目标上下文待解析')
        self.assertEqual(second['amount'],0);self.assertIsNone(second['count'])
        self.assertEqual(second['trigger']['fraction'],0.5)
        self.assertEqual(second['trigger']['description'],'受到致死伤害时响应')
        self.assertEqual(second['schedule'][0]['duration'],0)
        self.assertNotIn('total',projected)

    def test_behavior_graph_retains_duration_and_period(self):
        graph={'execution_root':'Behavior:Private','effects':[{'id':'Behavior:Private','type':'CBehaviorBuff',
            'fields':{'Duration|@value':'15','Period|@value':'0.5','DamageResponse|@ModifyLimit':'0'}}],
            'triggers':[{'kind':'PeriodicEffect','owner':'Behavior:Private','state_fields':{'Duration|@value':'15','Period|@value':'0.5'}}]}
        projected=project_value(graph,{})
        self.assertEqual(projected['events'][0]['duration'],15)
        self.assertEqual(projected['events'][0]['period'],0.5)
        self.assertEqual(projected['events'][1]['maximum'],0)
        self.assertNotIn('Private',json.dumps(projected))

    def test_fact_details_graph_is_summarized_and_never_copied(self):
        e=entry();e['abilities'][1]['effect']['details']={'execution':[{
            'execution_root':'Behavior:Private','effects':[{'type':'CEffectDamage'}],
            'triggers':[{'kind':'PeriodicEffect','owner':'Behavior:Private'}]}]}
        assets=type('ReadOnlyAssets',(),{'image':lambda *args,**kwargs:None})()
        result=player_entry(e,{'_faction':'Umojan'},assets,{('Umojan',e['id']):e},{'Behavior:Stim':'兴奋剂'})
        effect=result['abilities'][0]['effect']
        self.assertNotIn('details',effect)
        self.assertIn('行为活动期间周期执行',effect['value'])
        self.assertNotIn('Private',effect['value'])

    def test_other_hides_technical_content_and_copies_local_resources(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('SCORE_TEST_TEMP',Path(__file__).parent.parent)) as tmp:
            root=Path(tmp);legacy=root/'legacy';output=root/'output'
            (legacy/'assets').mkdir(parents=True);output.mkdir()
            (legacy/'assets/theme.css').write_text('body{background:url("pic.png")}',encoding='utf-8')
            (legacy/'assets/pic.png').write_bytes(b'local image fixture')
            (legacy/'other.html').write_text('<html><head><link rel="stylesheet" href="assets/theme.css"></head><body><img src="assets/pic.png"><span class="src">CrashID</span><span class="badge">已确认</span><h2>防重复校验器</h2><p>HiddenInternalID</p><h2>来源</h2><p>source.xml</p></main></body></html>',encoding='utf-8')
            other_page(legacy,output,set())
            document=(output/'other.html').read_text(encoding='utf-8')
            self.assertIn('legacy-debug',document)
            self.assertIn('score.showEvidence',document)
            self.assertIn('.src',document)
            self.assertNotIn('src="assets/pic.png"',document.split('历史原文')[0])
            proof=json.loads((output/'assets/legacy-manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(len(proof['resources']),2)
            self.assertTrue(all((output/path).is_file() for path in proof['resources']))
            (legacy/'other.html').write_text('<html><head></head><body><img src="../outside.png"></body></html>',encoding='utf-8')
            with self.assertRaises(ValueError):
                other_page(legacy,output,set())

    def test_player_projection_retains_zero_and_simplifies_effects(self):
        e=entry(); hidden=entry('Hidden','excluded');original=copy.deepcopy(e)
        data={'_faction':'Umojan'}
        assets=type('ReadOnlyAssets',(),{'image':lambda *args,**kwargs:None})()
        result=player_entry(e,data,assets,
            {('Umojan',e['id']):e,('Umojan',hidden['id']):hidden},{'Behavior:Stim':'兴奋剂'})
        self.assertEqual(result['stats']['energy']['value'],0)
        self.assertEqual(result['related_entries'],[])
        self.assertEqual(result['abilities'][0]['id'],'Abil:Stim')
        self.assertEqual(result['abilities'][0]['effect']['value'],'施加 兴奋剂')
        self.assertEqual(result['weapons'][0]['damage']['value']['components'],[{'amount':6,'count':1}])
        self.assertIn('伤害',result['weapons'][0]['effect']['value']['summary'])
        self.assertEqual(e,original)

    def test_sources_collect_includes_association_evidence(self):
        self.assertEqual(collect_evidence({'evidence':['a'],'nested':[{'association_evidence':['b']}]}),{'a','b'})

    def test_build_splits_data_preserves_audit_and_excludes_technical(self):
        e=entry();hidden=entry('Hidden','excluded')
        data={'schema_version':1,'project':{'batch':'test'},'entries':[e,hidden],
            'sources':[{'id':'s00001','path':'UnitData.xml'}],'dependency_context':{'order':['core','mod']}}
        before=copy.deepcopy(data)
        with tempfile.TemporaryDirectory(dir=os.environ.get('SCORE_TEST_TEMP',Path(__file__).parent.parent)) as tmp:
            output=Path(tmp)/'output'
            with patch('projection.AssetBuilder',Assets):
                manifest=build([('Umojan',data)],output,Path(tmp),None,None,Path(tmp)/'legacy')
            index=json.loads((output/'data/index.json').read_text(encoding='utf-8'))
            self.assertEqual(len(index['entries']),1)
            self.assertEqual(manifest['excluded_count'],1)
            self.assertEqual(manifest['public_count'],1)
            source_audit=json.loads((output/'data/evidence/Umojan/U.json').read_text(encoding='utf-8'))
            self.assertEqual(source_audit['entry'],e)
            self.assertIn('data/entries/Umojan/U.json',manifest['file_hashes'])
            self.assertFalse((output/'data/entries/Umojan/Hidden.json').exists())
            with self.assertRaises(FileExistsError):
                build([('Umojan',data)],output,Path(tmp),None,None,Path(tmp)/'legacy')
            self.assertLess(manifest['sizes']['index_bytes'],2*1024*1024)
        self.assertEqual(data,before)

    def test_invalid_batches_and_paths_rejected_before_output(self):
        data={'schema_version':1,'project':{'batch':'one'},'entries':[entry()],'sources':[]}
        other=copy.deepcopy(data);other['project']['batch']='two'
        with tempfile.TemporaryDirectory(dir=os.environ.get('SCORE_TEST_TEMP',Path(__file__).parent.parent)) as tmp:
            output=Path(tmp)/'output'
            with self.assertRaises(ValueError):
                build([('Umojan',data),('Webby',other)],output,Path(tmp),None,None,Path(tmp))
            self.assertFalse(output.exists())
            bad=copy.deepcopy(data);bad['entries'][0]['id']='Unit:../unsafe'
            with self.assertRaises(ValueError):
                build([('Umojan',bad)],output,Path(tmp),None,None,Path(tmp))
            self.assertFalse(output.exists())


if __name__=='__main__':
    unittest.main()
