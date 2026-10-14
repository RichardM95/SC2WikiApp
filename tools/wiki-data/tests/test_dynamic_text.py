import copy
from decimal import Decimal
import unittest
from unittest.mock import patch

from sc2_wiki_data.dynamic_text import UnprovedText, expand_tag, normalize_field, resolve_texts, safe_arithmetic


class Context:
    def __init__(self):
        self.sources=[]
        self.values={('Behavior','Shield'):{'Duration':{'value':'10','source':{'file':'Behavior.xml'}},
            'Modification|@AttackSpeedMultiplier':{'value':'1.15','source':{'file':'Behavior.xml'}}},
            ('Effect','Storm'):{'PeriodCount':{'value':'8','source':{'file':'Effect.xml'}},
                'PeriodicPeriodArray|0':{'value':'0.5','raw_field':'PeriodicPeriodArray|0','source':{'file':'Effect.xml'}}},
            ('Behavior','Armor'):{'DamageResponse|Cost|Cooldown|@TimeUse':{'value':'6','source':{'file':'Behavior.xml'}}}}
    def fields(self,family,oid):return self.values.get((family,oid),{})
    def evidence(self,family,oid,path):return family+':'+oid+'/'+path


def fact(text):return dict(value=text,status='confirmed',unit='',evidence=['template'])


class DynamicTextTests(unittest.TestCase):
    def test_exact_ref_and_attribute_path(self):
        c=Context()
        self.assertEqual(expand_tag(c,'<d ref="Behavior,Shield,Duration"/>')[0],'10')
        self.assertEqual(expand_tag(c,'<d ref="Behavior,Armor,DamageResponse.Cost.Cooldown.TimeUse"/>')[0],'6')

    def test_catalog_arithmetic_and_explicit_array(self):
        c=Context()
        text,proof=expand_tag(c,'<d ref="Effect,Storm,PeriodCount * Effect,Storm,PeriodicPeriodArray[0]"/>')
        self.assertEqual(text,'4');self.assertEqual(len(proof['operands']),2)
        text,_=expand_tag(c,'<d ref="(Behavior,Shield,Modification.AttackSpeedMultiplier - 1) * 100"/>')
        self.assertEqual(text,'15')

    def test_constant_expr_time_and_precision(self):
        c=Context()
        self.assertEqual(expand_tag(c,'<d expr="(2+3)*4/2"/>')[0],'10')
        self.assertEqual(expand_tag(c,'<d time="10"/>')[0],'10')
        self.assertEqual(expand_tag(c,'<d expr="1.255" precision="2"/>')[0],'1.26')
        self.assertEqual(safe_arithmetic('-2+3.5'),Decimal('1.5'))

    def test_no_functions_variables_powers_nonfinite_or_zero_division(self):
        for expr in ('__import__("os").system("echo x")','abs(-2)','2**10000','True+1','1/0','1e999','2%1','1_000','player_level+2'):
            with self.subTest(expr=expr),self.assertRaises(UnprovedText):safe_arithmetic(expr)

    def test_runtime_player_and_stack_count_are_unproved(self):
        for tag in ('<d ref="Behavior,Shield,Duration" player="current"/>',
            '<d ref="$BehaviorStackCount:AdeptSoulAbsorptionDebuffIAC$"/>',
            '<d ref="Behavior,Shield,Duration + _v0"/>'):
            with self.assertRaises(UnprovedText):expand_tag(Context(),tag)

    def test_missing_field_unproven_enum_index_and_deleted_array(self):
        c=Context()
        for tag in ('<d ref="Effect,Storm,PeriodicPeriodArray[1]"/>',
            '<d ref="Behavior,Shield,Modification.VitalRegenArray[2]"/>'):
            with self.assertRaises(UnprovedText):expand_tag(c,tag)
        c.values[('Behavior','Shield')]['Duration']['source']=None
        with self.assertRaises(UnprovedText):expand_tag(c,'<d ref="Behavior,Shield,Duration"/>')

    def test_ambiguous_normalized_attribute_not_chosen(self):
        c=Context();c.values[('Behavior','Shield')]['Duration|@value']={'value':'20','source':{'file':'other.xml'}}
        with self.assertRaises(UnprovedText):expand_tag(c,'<d ref="Behavior,Shield,Duration"/>')

    def test_fact_resolution_retains_template_source_and_marks_inferred(self):
        c=Context();description=fact('持续<d ref="Behavior,Shield,Duration"/>秒。')
        data={'sources':[],'entries':[{'id':'Unit:U','description':description}]}
        with patch('sc2_wiki_data.dynamic_text.hydrate_sources'):report=resolve_texts(c,data)
        self.assertEqual(description['value'],'持续10秒。')
        self.assertEqual(description['status'],'inferred')
        self.assertIn('template',description['evidence'])
        self.assertIn('Behavior:Shield/Duration',description['evidence'])
        self.assertIn('<d ',description['raw_template'])
        self.assertEqual(report['tags_resolved'],1)

    def test_partial_sentence_never_becomes_confirmed(self):
        c=Context();description=fact('持续<d ref="Behavior,Shield,Duration"/>秒，恢复<d ref="Unit,Missing,LifeMax"/>点。')
        data={'sources':[],'entries':[{'description':description}]}
        with patch('sc2_wiki_data.dynamic_text.hydrate_sources'):report=resolve_texts(c,data)
        self.assertIsNone(description['value']);self.assertEqual(description['status'],'unresolved')
        self.assertEqual(description['partial_text'],'持续10秒，恢复无点。')
        self.assertEqual(report['tags_unresolved'],1)
        self.assertEqual(data['diagnostics'][0]['path'],'/entries/0/description')

    def test_name_and_asset_paths_unchanged(self):
        c=Context();data={'sources':[],'entries':[{'name':fact('单位<d expr="1+2"/>'),
            'icon':fact('Assets/<d expr="1+2"/>.dds'),'portrait':fact('Models/<d expr="1+2"/>.m3')} ]}
        before=copy.deepcopy(data['entries'])
        with patch('sc2_wiki_data.dynamic_text.hydrate_sources'):report=resolve_texts(c,data)
        self.assertEqual(data['entries'],before);self.assertEqual(report['tags_found'],0)

    def test_repeated_run_does_not_reinterpret_expanded_sentence(self):
        c=Context();data={'sources':[],'entries':[{'description':fact('伤害<d expr="2*3"/>。')}]}
        with patch('sc2_wiki_data.dynamic_text.hydrate_sources'):
            resolve_texts(c,data);before=copy.deepcopy(data['entries']);again=resolve_texts(c,data)
        self.assertEqual(data['entries'],before);self.assertEqual(again['tags_found'],0)


if __name__=='__main__':unittest.main()
