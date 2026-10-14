import copy
import unittest
import xml.etree.ElementTree as ET
from sc2_wiki_data.refine_presentation import classify_entry


class Sources:
    data={'sources':[]}
    def __init__(self,xml):self.node=ET.fromstring(xml)
    def local_unit(self,uid):return self.node,lambda field:field


class ClassificationTests(unittest.TestCase):
    def test_structure_and_army_flags_do_not_expose_unusable_helpers(self):
        source=Sources('<CUnit id="U"><FlagArray index="Uncommandable" value="1"/><FlagArray index="Unselectable" value="1"/></CUnit>')
        for prior in ('building','unit'):
            entry={'id':'Unit:U','presentation':{'classification':{'value':prior,'reason':'旧分类'}},'modifiers':[]}
            original=copy.deepcopy(entry);result=classify_entry(entry,source,{})
            self.assertEqual(result['value'],'auxiliary');self.assertEqual(len(result['evidence']),2)
            self.assertEqual(entry,original)
    def test_one_restriction_alone_does_not_hide_player_objects(self):
        source=Sources('<CUnit id="U"><FlagArray index="Uncommandable" value="1"/><FlagArray index="Unselectable" value="0"/></CUnit>')
        entry={'id':'Unit:U','presentation':{'classification':{'value':'building','reason':'建筑'}},'modifiers':[]}
        self.assertIsNone(classify_entry(entry,source,{}))


if __name__=='__main__':unittest.main()
