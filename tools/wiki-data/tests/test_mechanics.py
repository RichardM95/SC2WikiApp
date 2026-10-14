"""Read-only source cases plus synthetic guards; never catalog_generate external output."""
from pathlib import Path
import json
import unittest
import xml.etree.ElementTree as ET

from sc2_wiki_data import mechanics as m
SOURCE = Path(r"C:\Users\43732\Desktop\SCORE")


class Context:
    def __init__(self, faction=None):
        self.tables = {}
        self.sources = []
        if faction:
            for mod in ("Other", faction):
                folder = SOURCE / ("SCORE-" + mod + ".SC2Mod") / "Base.SC2Data/GameData"
                for p in folder.glob("*Data.xml"):
                    for node in ET.parse(p).getroot():
                        if node.get("id"):
                            if p.stem == "RequirementNodeData":
                                self.tables.setdefault("RequirementNode", {})[node.get("id")] = node
                                continue
                            family = node.tag[1:]
                            for name in ("RequirementNode", "Requirement", "Validator", "Behavior", "Weapon", "Upgrade", "Button", "Effect", "Abil", "Unit"):
                                if family.startswith(name):
                                    self.tables.setdefault(name, {})[node.get("id")] = node
                                    break

    def add(self, family, xml):
        node = ET.fromstring(xml)
        self.tables.setdefault(family, {})[node.get("id")] = node

    def node(self, family, oid):
        return self.tables.get(family, {}).get(oid)

    def find_nodes(self, family):
        return self.tables.get(family, {})

    def evidence(self, family, oid, field):
        n = self.node(family, oid)
        if n is None or field not in dict(m.fields(n)):
            raise ValueError("No literal field: " + str(field))
        sid = family + ":" + oid + "/" + field
        return sid

    def missing(self, reason, evidence=None):
        return dict(value=None, unit="", status="unresolved", reason=reason, evidence=evidence or [])

    def na(self, reason, evidence=None):
        return dict(value=None, unit="", status="not_applicable", reason=reason, evidence=evidence or [])

    def text(self, family, oid, keys):
        return self.missing("Synthetic Context does not load localization")


@unittest.skipUnless(SOURCE.exists(), "SCORE source unavailable")
class RealSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cerberus = Context("Cerberus")
        cls.covert = Context("CovertOps")
        cls.taldarim = Context("Taldarim")

    def test_zergling_switch_and_caster_life(self):
        graph, ev, errors = m.effect_graph(self.cerberus, "ZerglingClawsSwitch")
        self.assertEqual({c["amount"] for c in graph["components"]}, {10, 20})
        self.assertFalse(errors)
        self.assertTrue(any(e["id"] == "Effect:ZerglingAttackMUR" and e["fields"].get("VitalArray|Life|Change|@value") == "-5" for e in graph["effects"]))
        twenty = next(c for c in graph["components"] if c["amount"] == 20)
        ten = next(c for c in graph["components"] if c["amount"] == 10)
        self.assertTrue(any(c.get("structure",{}).get("id") == "Validator:CasterLifeGT15R" for c in twenty["conditions"]))
        self.assertFalse(any(c.get("structure",{}).get("id") == "Validator:CasterLifeGT15R" for c in ten["conditions"]))
        self.assertTrue(ev)

    def test_thor_repeated_hits_and_mixed_effects(self):
        graph, ev, errors = m.effect_graph(self.covert, "ThorsHammerC")
        hits = [c for c in graph["components"] if c["effect"] == "Effect:ThorsHammerDamageC"]
        self.assertEqual(len(hits), 2)
        self.assertEqual([c["amount"] for c in hits], [50, 50])
        self.assertEqual(len([e for e in graph["effects"] if e["id"] == "Effect:JavelinMissileLaunchersLMC"]), 4)
        self.assertEqual([c["schedule"][-1]["occurrence"] for c in hits], [0, 3])

    def test_current_draft_thor_keeps_six_hits_when_conditions_unresolved(self):
        path=Path(r"E:\Program Files (x86)\sc2-agent-hub-data\projects\SCORE-fa82e2d014\wiki-data\CovertOps-v2-20261013\data.json")
        if not path.exists():self.skipTest("Current task's isolated draft has not been generated")
        data=json.loads(path.read_text(encoding="utf-8-sig"))
        entry=next(e for e in data["entries"] if e["id"]=="Unit:ThorC")
        weapon=next(w for w in entry["weapons"] if w["id"]=="Weapon:ThorsHammerC")
        damage=weapon["damage"]
        graph=damage.get("value") or damage.get("raw_value")
        self.assertIsInstance(graph,dict)
        hits=graph["components"]
        self.assertEqual(len(hits),6)
        self.assertEqual(sum(c["effect"]=="Effect:ThorsHammerDamageC" and c["amount"]==50 for c in hits),2)
        self.assertEqual(sum(c["effect"]=="Effect:JavelinMissileLaunchersDamageC" and c["amount"]==10 for c in hits),4)
        self.assertEqual(sorted(c["schedule"][0]["occurrence"] for c in hits),list(range(6)))
        if damage["status"]=="unresolved":
            self.assertTrue(damage["reason"])
            self.assertTrue(any(c["status"]=="unresolved" for hit in hits for c in hit["conditions"]))

    def test_target_locator_non_direct_damage(self):
        unit = self.covert.node("Unit", "GehennaCruiserC")
        weapon = next(w for w in m.weapons(self.covert, unit) if w["id"] == "Weapon:TargetLocatorCG")
        self.assertEqual(weapon["damage"]["status"], "not_applicable")
        self.assertEqual(weapon["effect"]["value"]["execution_root"], "Effect:TargetLocatorCreatePersistentCG")
        self.assertEqual(weapon["effect"]["value"]["display"]["root"], "Effect:FakeWeaponDummyDamageCG")

    def test_thor_factory_requirements(self):
        result = m.production(self.covert, "ThorC", self.covert.node("Unit", "ThorC"))
        row = next(r for r in result if r["ability"] == "Abil:FactoryTrainC")
        self.assertEqual(row["command"], "Train5")
        self.assertEqual(row["time"]["value"], 60)
        self.assertEqual(row["base_unit_cost"]["minerals"]["value"], 750)
        self.assertEqual(row["cost"]["minerals"]["status"], "unresolved")
        self.assertTrue(row["requirements"])
        self.assertEqual(row["requirements"][0]["details"]["id"], "Requirement:HaveArmoryAndAttachedTechLabC")

    def test_roach_charge_separate_from_cooldown(self):
        unit = self.cerberus.node("Unit", "RoachR")
        row = next(a for a in m.abilities(self.cerberus, unit) if a["id"] == "Abil:RoachBarrierShieldR")
        self.assertEqual(row["cooldown"]["value"], 4)
        self.assertEqual(row["charges"][0]["recharge"]["value"], 8)
        self.assertEqual(row["charges"][0]["maximum"]["value"], 3)
        self.assertEqual(row["charges"][0]["initial"]["value"], 3)
        self.assertEqual(row["charges"][0]["use"]["value"], 1)

    def test_nomad_submenus_same_cell(self):
        cards = m.command_cards(self.covert, "NomadC", self.covert.node("Unit", "NomadC"))
        self.assertEqual({c["id"] for c in cards}, {"root", "TBl1", "TBl2"})
        submenu = next(c for c in cards if c["id"] == "TBl1")
        same = [b for b in submenu["buttons"] if b["row"] == 0 and b["column"] == 0]
        self.assertEqual({b["face"] for b in same}, {"CommandCenterC", "SpaceStationC"})

    def test_firebat_same_cell_commands_not_dropped(self):
        cards = m.command_cards(self.covert, "FirebatJ", self.covert.node("Unit", "FirebatJ"))
        buttons = [b for c in cards for b in c["buttons"]]
        self.assertTrue(buttons)
        counts = {}
        for b in buttons:
            key = b["row"], b["column"]
            counts[key] = counts.get(key, 0) + 1
        self.assertTrue(any(v > 1 for v in counts.values()))

    def test_research_branches_are_not_invented_levels(self):
        producers = [u for u,n in self.taldarim.find_nodes("Unit").items() if any(a=="TaldarimChooseSecondFactions" for a,p in m.refs(n,"AbilArray"))]
        self.assertTrue(producers)
        rows = m.research(self.taldarim, producers[0], self.taldarim.node("Unit", producers[0]))
        choose = [r for r in rows if r["ability"] == "Abil:TaldarimChooseSecondFactions"]
        self.assertEqual({r["command"] for r in choose}, {"Research1", "Research2"})
        self.assertTrue(all(not r["levels"] for r in choose))
        self.assertTrue(all(r["cost"]["minerals"]["value"] == 1000 for r in choose))
        self.assertNotEqual(choose[0]["requirements"], choose[1]["requirements"])


class GraphGuardTests(unittest.TestCase):
    def test_repeated_unit_count_keeps_single_price_separate(self):
        c=Context()
        c.add("Unit", '<CUnit id="U"><CostResource index="Minerals" value="25"/><Food value="-0.5"/></CUnit>')
        c.add("Unit", '<CUnit id="P"><AbilArray Link="Train"/></CUnit>')
        c.add("Abil", '<CAbilTrain id="Train"><InfoArray index="Train1" Time="10"><Unit value="U"/><Unit value="U"/></InfoArray></CAbilTrain>')
        row=m.production(c,"U",c.node("Unit","U"))[0]
        self.assertEqual(row["count"]["value"],2)
        self.assertEqual(row["base_unit_cost"]["minerals"]["value"],25)
        self.assertIsNone(row["cost"]["minerals"]["value"])

    def test_reverse_upgrade_uses_real_modifier_summary_draft(self):
        c=Context()
        c.add("Unit", '<CUnit id="U"/>')
        c.add("Upgrade", '<CUpgrade id="Life"><EffectArray Reference="Unit,U,LifeMax" Operation="Add" Value="20"/></CUpgrade>')
        rows=m.modifiers(c,"U",c.node("Unit","U"))
        self.assertEqual(len(rows),1)
        effect=rows[0]["effect"]
        self.assertEqual(effect["status"],"inferred")
        self.assertTrue(effect["translation_draft"])
        self.assertIn("最大生命",effect["value"])
        self.assertIn("20",effect["value"])
        self.assertEqual(effect["source_locale"],"catalog")

    def test_same_effect_twice_and_cycle(self):
        c = Context()
        c.add("Effect", '<CEffectSet id="Set"><EffectArray value="Damage"/><EffectArray value="Damage"/></CEffectSet>')
        c.add("Effect", '<CEffectDamage id="Damage"><Amount value="7"/></CEffectDamage>')
        graph, ev, errors = m.effect_graph(c, "Set")
        self.assertEqual([r["amount"] for r in graph["components"]], [7, 7])
        self.assertFalse(errors)
        c.add("Effect", '<CEffectSet id="Cycle"><EffectArray value="Cycle"/></CEffectSet>')
        self.assertTrue(m.effect_graph(c,"Cycle")[2])

    def test_missing_execution_not_display_damage_fallback(self):
        c = Context()
        c.add("Unit", '<CUnit id="U"><WeaponArray Link="W"/></CUnit>')
        c.add("Weapon", '<CWeapon id="W"><DisplayEffect value="Damage"/></CWeapon>')
        c.add("Effect", '<CEffectDamage id="Damage"><Amount value="7"/></CEffectDamage>')
        row=m.weapons(c,c.node("Unit","U"))[0]
        self.assertEqual(row["damage"]["status"],"unresolved")
        self.assertIsNone(row["damage"]["raw_value"]["execution_root"])

    def test_unknown_condition_does_not_claim_can_cast(self):
        c=Context()
        c.add("Abil", '<CAbilEffectInstant id="A"><Effect value="Damage"/></CAbilEffectInstant>')
        self.assertEqual(m.combined_conditions(c,"Abil","A",c.node("Abil","A"))["status"],"unresolved")


if __name__ == "__main__":
    unittest.main()
