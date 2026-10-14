import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sc2_wiki_data.factions import assignment,default_config,validate_config
from sc2_wiki_data.priority import add_priority,protected_equal,predecessor_metadata
from sc2_wiki_data.export import build_packet,group_priority,update_zip,safe_packet_path
from sc2_wiki_data.other import parse_other
from test_projection import Assets,entry,fact
from sc2_wiki_data.generate import source_changes
from sc2_wiki_data.source_inventory import inventory,compare

class DataV4Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir=Path(__file__).resolve().parents[3]/'test-tmp'
        self.temp_dir.mkdir(exist_ok=True)
    def test_config_aliases_and_duplicate_normalization(self):
        config=validate_config(default_config());self.assertEqual(len(config['factions']),18)
        for alias in ('- Golden','Golden F','黄金'):self.assertEqual(assignment(alias,config),'Golden')
        self.assertEqual(assignment('Unknown',config),'Unassigned')
        bad=copy.deepcopy(config);bad['factions'][0]['prefixes'].append(' - golden ')
        with self.assertRaises(ValueError):validate_config(bad)
        bad=copy.deepcopy(config);bad['factions'][0]['defaultVisible']=1
        with self.assertRaises(ValueError):validate_config(bad)

    def test_priority_literal_zero_and_missing(self):
        class Ctx:
            def fields(self,*args):return {'GlossaryPriority':{'value':'0','source':{'file':'UnitData.xml'}}}
            def fact(self,value,*args):return fact(value)
            def missing(self,reason):return {'value':None,'status':'unresolved','unit':'','evidence':[],'reason':reason}
        e=entry();self.assertTrue(add_priority(Ctx(),e));self.assertEqual(e['presentation']['glossary_priority']['value'],0)
        ctx=Ctx();ctx.fields=lambda *args:{};e=entry();self.assertFalse(add_priority(ctx,e))
        self.assertIsNone(e['presentation']['glossary_priority']['value'])

    def test_priority_group_primary_then_min(self):
        a=entry('Normal');b=entry('Corpse');a['presentation']['group']['members'].append({'id':b['id']})
        a['presentation']['glossary_priority']=fact(0);b['presentation']['glossary_priority']=fact(-2)
        catalog={('Origin',a['id']):a,('Origin',b['id']):b}
        self.assertEqual(group_priority(a,catalog,'Origin')['value'],0)
        a['presentation']['glossary_priority']['value']=None
        result=group_priority(a,catalog,'Origin');self.assertEqual(result['value'],-2);self.assertTrue(result['inferred'])

    def test_protected_previous_facts_unchanged(self):
        old={'entries':[entry()],'project':{'batch':'old'},'sources':[{'id':'s00001'}]}
        new=copy.deepcopy(old);new['entries'][0]['presentation']['glossary_priority']=fact(0);new['sources'].append({'id':'s00002'});new['project']['batch']='new'
        protected_equal(old,new)
        new['entries'][0]['stats']['life']['value']=46
        with self.assertRaises(ValueError):protected_equal(old,new)

    def test_predecessor_hash_matches_actual_input_not_older_ancestor(self):
        import hashlib
        raw=b'{"project":{"batch":"v3-3"}}'
        data={'project':{'batch':'v3-3','predecessor_batch':'v3-2','predecessor_sha256':'older-ancestor'}}
        predecessor_metadata(data,'v4-new','v3-3',raw)
        self.assertEqual(data['project']['batch'],'v4-new')
        self.assertEqual(data['project']['predecessor_batch'],'v3-3')
        self.assertEqual(data['project']['predecessor_sha256'],hashlib.sha256(raw).hexdigest())

    def test_other_plain_table_order_evidence_and_script_exclusion(self):
        with tempfile.TemporaryDirectory(dir=self.temp_dir) as temp:
            root=Path(temp);legacy=root/'legacy';legacy.mkdir();out=root/'out';out.mkdir()
            (legacy/'other.html').write_text('<main><h1>坠毁系统</h1><p>保留正文</p><script>evil()</script><table><tr><th>分级</th></tr><tr><td>巨型</td></tr></table><h2>来源</h2><p>证据</p></main>',encoding='utf8')
            result=parse_other(legacy,out);self.assertEqual(result['title'],'坠毁系统')
            self.assertEqual(result['blocks'][1]['rows'],[['巨型']]);self.assertTrue(result['blocks'][-1]['evidence'])
            self.assertNotIn('evil',json.dumps(result))

    def test_packet_preserves_qualified_origin_and_safe_update(self):
        with tempfile.TemporaryDirectory(dir=self.temp_dir) as temp:
            root=Path(temp);legacy=root/'legacy';legacy.mkdir()
            e=entry();e['presentation']['editor_prefix']=fact('- Golden F');e['presentation']['glossary_priority']=fact(0)
            data={'schema_version':1,'project':{'batch':'v4-test'},'entries':[e],'sources':[{'id':'s00001','object':'Unit:U'}]}
            with patch('sc2_wiki_data.projection.AssetBuilder',Assets):manifest=build_packet([('Umojan',data)],root/'packet',root,None,None,legacy)
            self.assertEqual(manifest['min_client_version'],2);self.assertNotIn('other.html',manifest['file_hashes'])
            index=json.loads((root/'packet/data/index.json').read_text(encoding='utf8'))['entries'][0]
            self.assertEqual(index['origin'],'Umojan');self.assertEqual(index['editorPrefix'],'Golden F');self.assertEqual(index['groupPriority'],0)
            self.assertEqual(index['members'][0]['origin'],'Umojan')
            result=update_zip(root/'packet',root/'update.zip');self.assertGreater(result['bytes'],0)
            with self.assertRaises(FileExistsError):update_zip(root/'packet',root/'update.zip')
            (root/'packet/data/index.json').write_text('{}',encoding='utf8')
            with self.assertRaises(ValueError):update_zip(root/'packet',root/'bad.zip')

    def test_packet_paths_reject_active_content(self):
        for path in ('data/../bad.json','assets/images/app.js','config/factions.json','data/x.JSON','data/a\\b.json','index.html','assets/images/a.svg'):
            self.assertFalse(safe_packet_path(path),path)

    def test_update_plan_full_fingerprint_and_dependency_order(self):
        before={'sources':[{'id':'main','fingerprint':'all-files-A'},{'id':'shared','fingerprint':'B'}],'engine':{'sha':'E'},'load_order':['shared','main']}
        after=copy.deepcopy(before);after['sources'][0]['fingerprint']='unreferenced-file-changed'
        changed,reasons=source_changes(before,after);self.assertEqual(changed,['main']);self.assertFalse(reasons)
        after=copy.deepcopy(before);after['load_order'].reverse()
        changed,reasons=source_changes(before,after);self.assertFalse(changed);self.assertEqual(reasons,['dependency_order'])

    def test_asset_inventory_detects_opaque_model_only_change(self):
        with tempfile.TemporaryDirectory(dir=self.temp_dir) as temp:
            root=Path(temp);component=root/'SCORE-Test.SC2Mod';component.mkdir()
            model=component/'opaque.m3';model.write_bytes(b'old-model')
            before=inventory(root);model.write_bytes(b'new-model')
            changed,assets=compare(before,inventory(root))
            self.assertEqual(changed,['project:SCORE-Test.SC2Mod']);self.assertFalse(assets)

if __name__=='__main__':unittest.main()
