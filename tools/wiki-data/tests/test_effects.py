"""Execution semantics guards and five read-only SCORE source regressions."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import unittest

from sc2_wiki_data import mechanics as m
from test_mechanics import Context, SOURCE

HUB = Path(os.environ.get("SCORE_HUB_ROOT",str(Path(__file__).resolve().parents[3])))
spec = importlib.util.spec_from_file_location("effects_v3_authority",HUB/"SC2WikiAgent/tools/sc2-wiki-data.py")
authority = importlib.util.module_from_spec(spec)
spec.loader.exec_module(authority)


class EffectV3Tests(unittest.TestCase):
    def context(self):
        c = Context()
        c.add("Effect",'<CEffectDamage id="D"><Amount value="7"/></CEffectDamage>')
        c.add("Validator",'<CValidatorUnitCompareVital id="Alive"><Compare value="GT"/><Value value="0"/></CValidatorUnitCompareVital>')
        return c

    def count_context(self):
        c=self.context()
        c.add("RequirementNode",'<CRequirementCountUpgrade id="UpgradeCount"><Flags index="TechTreeCheat" value="0"/>'
              '<Count Link="UpgradeA" State="CompleteOnly"/></CRequirementCountUpgrade>')
        c.add("RequirementNode",'<CRequirementCountUnit id="UnitCount"><Flags index="TechTreeCheat" value="0"/>'
              '<Count Link="UnitA" State="QueuedOrBetter"/></CRequirementCountUnit>')
        c.add("RequirementNode",'<CRequirementConst id="Zero"><Value value="0"/></CRequirementConst>')
        c.add("RequirementNode",'<CRequirementEq id="CompareZero"><OperandArray value="UnitCount"/><OperandArray value="Zero"/></CRequirementEq>')
        for tag in ("And","Or"):
            c.add("RequirementNode",'<CRequirement'+tag+' id="'+tag+'"><OperandArray value="UpgradeCount"/><OperandArray value="CompareZero"/></CRequirement'+tag+'>')
        return c

    def test_count_flags_are_not_values_and_real_zero_is_kept(self):
        c=self.count_context()
        upgrade=m.tree_condition(c,"RequirementNode","UpgradeCount")
        self.assertNotIn("0",upgrade["value"])
        self.assertIn("UpgradeA",upgrade["value"])
        self.assertIn("已完成",upgrade["value"])
        compare=m.tree_condition(c,"RequirementNode","CompareZero")
        self.assertIn("等于",compare["value"])
        self.assertIn("常数：0",compare["value"])
        self.assertNotIn("单位计数：0",compare["value"])

    def test_refresh_saved_compounds_names_and_source_ids(self):
        c=self.count_context()
        tree=m.tree_condition(c,"RequirementNode","And")
        leaf=tree["details"]["operands"][0]["condition"]
        leaf["value"]="升级计数：0，中文升级，已完成"
        leaf["resolved_names"]=[{"id":"Upgrade:UpgradeA","name":"中文升级","evidence":["NameSource"]}]
        leaf["evidence"].append("NameSource")
        tree["value"]="同时满足（"+leaf["value"]+"；旧比较）"
        container={"value":"旧包装","unit":"","status":"inferred","evidence":["wrapper"],
                   "basis":"根据有效条件图整理中文机制说明","details":[tree]}
        unknown={"value":None,"unit":"","status":"unresolved","evidence":["unknown"],
                 "reason":"Missing state","raw_value":copy.deepcopy(leaf["details"])}
        data={"requirements":[container,unknown],"sources":[{"id":"NameSource","field":"Upgrade/Name/UpgradeA"}],
              "description":{"value":"原始描述中的计数0","unit":"","status":"confirmed","evidence":["desc"]}}
        before=copy.deepcopy(data)
        stats=m.refresh_condition_texts(data)
        self.assertEqual(stats["changed"],3)
        self.assertEqual(stats["skipped_unresolved"],1)
        self.assertIn("中文升级",container["value"])
        self.assertNotIn("升级计数：0",container["value"])
        self.assertIn("常数：0",container["value"])
        self.assertEqual(data["sources"],before["sources"])
        self.assertEqual(data["description"],before["description"])
        self.assertEqual(unknown,before["requirements"][1])
        def immutable(node):
            if isinstance(node,dict):return {k:immutable(v) for k,v in node.items() if k!="value"}
            if isinstance(node,list):return [immutable(v) for v in node]
            return node
        self.assertEqual(immutable(data),immutable(before))
        self.assertEqual(m.refresh_condition_texts(data)["changed"],0)

    def test_refresh_or_keeps_any_semantics(self):
        tree=m.tree_condition(self.count_context(),"RequirementNode","Or")
        tree["details"]["operands"][0]["condition"]["value"]="升级计数：0，UpgradeA，已完成"
        tree["value"]="任一满足（旧文本）"
        stats=m.refresh_condition_texts(tree)
        self.assertEqual(stats["changed"],2)
        self.assertTrue(tree["value"].startswith("任一满足（"))
        self.assertNotIn("升级计数：0",tree["value"])

    def test_death_and_fatal_are_distinct_runtime_events(self):
        c = self.context()
        c.add("Behavior",'<CBehaviorBuff id="B"><Modification><DeathResponse Effect="D" Chance="1"/></Modification>'
              '<DamageResponse Handled="D" Fatal="1" ModifyFraction="0"><Chance value="1"/></DamageResponse></CBehaviorBuff>')
        c.add("Effect",'<CEffectApplyBehavior id="Apply"><Behavior value="B"/></CEffectApplyBehavior>')
        c.add("Weapon",'<CWeapon id="W"><Effect value="Apply"/></CWeapon>')
        c.add("Unit",'<CUnit id="U"><WeaponArray Link="W"/></CUnit>')
        weapon = m.weapons(c,c.node("Unit","U"))[0]
        graph = weapon["effect"]["value"]
        self.assertEqual({t["kind"] for t in graph["triggers"]},{"DamageResponse","DeathResponse"})
        self.assertEqual(len(graph["components"]),2)
        self.assertTrue(all(hit["count"] is None and hit["trigger"]["metadata"]["runtime_dependent"] for hit in graph["components"]))
        self.assertNotIn("amount",graph)
        self.assertTrue(all(hit["conditions"] for hit in graph["components"]))
        json.dumps(graph,allow_nan=False)
        authority.validate_damage(graph,"death_and_fatal")

    def test_response_filters_and_frequency_are_retained(self):
        c = self.context()
        c.add("Behavior",'<CBehaviorBuff id="B"><DamageResponse Handled="D" Location="Attacker">'
              '<ExcludeEffectArray value="SelfDamage"/><Chance value="0.3"/>'
              '<Cost><Cooldown Link="Shared" TimeUse="0.0625"/></Cost></DamageResponse></CBehaviorBuff>')
        graph,ev,issues = m.behavior_effect_graph(c,"B")
        self.assertFalse(issues)
        response = graph["triggers"][0]["fields"]
        self.assertEqual(response["DamageResponse|@Location"],"Attacker")
        self.assertEqual(response["DamageResponse|ExcludeEffectArray|0|@value"],"SelfDamage")
        self.assertEqual(response["DamageResponse|Cost|Cooldown|@TimeUse"],"0.0625")
        self.assertEqual(response["DamageResponse|Chance|@value"],"0.3")
        self.assertTrue(any("@Handled" in sid for sid in ev))

    def test_behavior_disable_remove_and_end_phases(self):
        c = self.context()
        c.add("Behavior",'<CBehaviorBuff id="B"><DisableValidatorArray value="Alive"/><RemoveValidatorArray value="Alive"/>'
              '<Duration value="2"/><FinalEffect value="D"/><ExpireEffect value="D"/></CBehaviorBuff>')
        graph,ev,issues = m.behavior_effect_graph(c,"B")
        self.assertFalse(issues)
        self.assertEqual({s["mode"] for s in graph["effects"][0]["behavior_states"]},{"Disable","Remove"})
        self.assertEqual({t["kind"] for t in graph["triggers"]},{"FinalEffect","ExpireEffect"})
        self.assertTrue(all(c["count"] is None for c in graph["components"]))

    def test_combine_and_or_negate_and_invalid_type(self):
        c = self.context()
        c.add("Validator",'<CValidatorCombine id="And"><Type value="And"/><Negate value="1"/><CombineArray value="Alive"/></CValidatorCombine>')
        # Actual CombineArray is an operand array, not a scalar comparison field.
        condition = m.tree_condition(c,"Validator","And")
        self.assertEqual(condition["details"]["operator"],"And")
        self.assertTrue(condition["details"]["metadata"]["negated"])
        self.assertIn("不满足同时满足",condition["value"])
        c.add("Validator",'<CValidatorCombine id="Or"><CombineArray value="Alive"/></CValidatorCombine>')
        self.assertEqual(m.tree_condition(c,"Validator","Or")["details"]["operator"],"Or")
        c.add("Validator",'<CValidatorCombine id="Invalid"><Type value="Not"/><CombineArray value="Alive"/></CValidatorCombine>')
        bad=m.tree_condition(c,"Validator","Invalid")
        self.assertEqual(bad["status"],"unresolved")
        self.assertIsNone(bad["value"])

    def test_random_intervals_and_positions_not_fixed(self):
        c = self.context()
        c.add("Effect",'<CEffectCreatePersistent id="P"><PeriodCount value="2"/><Flags index="RandomPeriod" value="1"/>'
              '<Flags index="RandomOffset" value="1"/><PeriodRandMode value="RandomNoRepeat"/>'
              '<PeriodicEffectArray value="D"/><PeriodicPeriodArray value="0"/><PeriodicPeriodArray value="0.2"/>'
              '<PeriodicOffsetArray value="0,0,0"/><PeriodicOffsetArray value="1,0,0"/></CEffectCreatePersistent>')
        graph,ev,issues=m.effect_graph(c,"P")
        self.assertFalse(issues)
        self.assertEqual(len(graph["components"]),2)
        for hit in graph["components"]:
            timing=hit["schedule"][0]
            self.assertIsNone(timing["interval"])
            self.assertEqual(timing["interval_candidates"],[0,0.2])
            self.assertEqual(timing["offset_candidates"],["0,0,0","1,0,0"])
            self.assertEqual(timing["period_random_mode"],"RandomNoRepeat")
            self.assertTrue(hit["conditions"])
        authority.validate_damage(graph,"random_persistent")

    def test_missile_events_preserve_target_context(self):
        c = self.context()
        c.add("Effect",'<CEffectLaunchMissile id="LM"><WhichLocation Value="TargetPoint"/>'
              '<LaunchEffect value="D"/><ImpactEffect value="D"/></CEffectLaunchMissile>')
        graph,ev,issues=m.effect_graph(c,"LM")
        self.assertFalse(issues)
        self.assertEqual({hit["trigger"]["kind"] for hit in graph["components"]},{"LaunchEffect","ImpactEffect"})
        self.assertEqual(graph["components"][0]["target"]["selectors"]["WhichLocation|@Value"],"TargetPoint")
        self.assertTrue(all(hit["schedule"] and hit["conditions"] for hit in graph["components"]))
        authority.validate_damage(graph,"missile")

    def test_combine_boolean_metadata_inside_damage_graph(self):
        c=self.context()
        c.add("Validator",'<CValidatorCombine id="Gate"><Type value="And"/><Negate value="1"/><CombineArray value="Alive"/></CValidatorCombine>')
        c.add("Effect",'<CEffectDamage id="GatedDamage"><Amount value="13"/><ValidatorArray value="Gate"/></CEffectDamage>')
        graph,ev,issues=m.effect_graph(c,"GatedDamage")
        self.assertFalse(issues)
        self.assertTrue(graph["components"][0]["conditions"][0]["structure"]["metadata"]["negated"])
        authority.validate_damage(graph,"combine_damage")

    def test_refresh_preserves_reviewed_metadata_and_unresolved_fact(self):
        c = self.context()
        c.add("Unit",'<CUnit id="U"><WeaponArray Link="W"/><AbilArray Link="A"/><BehaviorArray Link="B"/></CUnit>')
        c.add("Weapon",'<CWeapon id="W"><Effect value="Missing"/></CWeapon>')
        c.add("Abil",'<CAbilEffectInstant id="A"><Effect value="D"/></CAbilEffectInstant>')
        c.add("Behavior",'<CBehaviorBuff id="B"><PeriodicEffect value="Missing"/></CBehaviorBuff>')
        entry={"id":"Unit:U","production":["reviewed"],"presentation":{"command_cards":["hotkey"]},
               "weapons":[{"id":"Weapon:W","name":"reviewed","icon":"reviewed","period":"reviewed"}],
               "abilities":[{"id":"Abil:A","name":"reviewed","cost":{"energy":17},"charges":[3],"effect":c.missing("old")}],
               "modifiers":[{"id":"Behavior:B","kind":"buff","effect":{"value":"reviewed"}}]}
        before=copy.deepcopy(entry)
        self.assertIs(m.refresh_effects(c,entry),entry)
        self.assertEqual(entry["production"],before["production"])
        self.assertEqual(entry["presentation"],before["presentation"])
        self.assertEqual(entry["abilities"][0]["cost"],before["abilities"][0]["cost"])
        self.assertEqual(entry["abilities"][0]["charges"],[3])
        self.assertEqual(entry["weapons"][0]["name"],"reviewed")
        self.assertEqual(entry["weapons"][0]["period"],"reviewed")
        self.assertNotIn("execution",entry["modifiers"][0])
        self.assertNotIn("execution",entry["modifiers"][0]["effect"].get("details",{}))
        for fact in (entry["weapons"][0]["effect"],entry["modifiers"][0]["execution_fact"]):
            self.assertEqual(fact["status"],"unresolved")
            self.assertIsNone(fact["value"])
            self.assertTrue(fact["raw_value"])


@unittest.skipUnless(SOURCE.exists(),"SCORE source unavailable")
class ActualScoreV3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.primal=Context("Primal")
        cls.taldarim=Context("Taldarim")

    def test_actual_taldarim_upgrade_count_flag_and_saved_production(self):
        oid="CountUpgradeTaldarimFactionsSonsOfSlaynCompleteOnly"
        leaf=m.tree_condition(self.taldarim,"RequirementNode",oid)
        self.assertNotIn("0",leaf["value"])
        self.assertEqual(leaf["details"]["fields"]["Flags|TechTreeCheat|@value"],"0")
        self.assertTrue(any("Flags|TechTreeCheat|@value" in s for s in leaf["evidence"]))
        path=HUB.parent/"sc2-agent-hub-data/projects/SCORE-fa82e2d014/wiki-data/Taldarim-v3-20261013-1/data.json"
        if not path.exists():self.skipTest("Saved SCORE v3-1 production package unavailable")
        saved=json.loads(path.read_text(encoding="utf-8-sig"))
        entry=next(e for e in saved["entries"] if e["id"]=="Unit:AdeptD")
        production=next(p for p in entry["production"] if p.get("requirements") and "升级计数：0" in json.dumps(p["requirements"],ensure_ascii=False))
        before=copy.deepcopy(production)
        source_ids=copy.deepcopy(saved["sources"])
        stats=m.refresh_condition_texts(production)
        self.assertGreater(stats["changed"],0)
        def facts(node):
            if isinstance(node,dict):
                if {"value","unit","status","evidence"}<=node.keys():yield node
                for key,value in node.items():
                    if key not in ("value","raw_value","fields"):yield from facts(value)
            elif isinstance(node,list):
                for item in node:yield from facts(item)
        old=list(facts(before));new=list(facts(production))
        self.assertEqual(len(old),len(new))
        for old_fact,new_fact in zip(old,new):
            self.assertEqual(old_fact["evidence"],new_fact["evidence"])
            self.assertEqual(old_fact["status"],new_fact["status"])
            if new_fact.get("details",{}).__class__==dict and new_fact.get("details",{}).get("type")=="CRequirementCountUpgrade":
                self.assertNotIn("升级计数：0",new_fact["value"])
        self.assertEqual(saved["sources"],source_ids)
        self.assertIn("斯雷恩之嗣",json.dumps(production["requirements"],ensure_ascii=False))

    def test_primal_baneling_death_and_fatal_sources(self):
        graph,ev,issues=m.behavior_effect_graph(self.primal,"BanelingExplodePZ")
        authority.validate_damage(graph,"actual_baneling")
        self.assertEqual({t["kind"] for t in graph["triggers"]},{"DeathResponse","DamageResponse"})
        self.assertTrue(any("DeathResponse|@Effect" in s for s in ev))
        self.assertTrue(any("DamageResponse|@Handled" in s for s in ev))

    def test_tyrannozor_thorns_has_shared_charge_cooldown(self):
        graph,ev,issues=m.behavior_effect_graph(self.primal,"TyrannozorBhvPZU")
        authority.validate_damage(graph,"actual_tyrannozor")
        trigger=next(t for t in graph["triggers"] if t["kind"]=="DamageResponse")
        self.assertEqual(trigger["fields"]["DamageResponse|Cost|Cooldown|@TimeUse"],"0.0625")
        self.assertEqual(trigger["fields"]["DamageResponse|Cost|Charge|@Link"],"Behavior/PrimalGeneHugeThornsPZ1")
        self.assertTrue(any("ExcludeEffectArray" in key for key in trigger["fields"]))

    def test_adept_timer_preserves_state_and_distinct_phases(self):
        graph,ev,issues=m.behavior_effect_graph(self.taldarim,"AdeptPhaseShiftTimerD")
        authority.validate_damage(graph,"actual_adept")
        states=graph["effects"][0]["behavior_states"]
        self.assertEqual([s["mode"] for s in states],["Disable","Disable","Remove","Remove"])
        self.assertEqual({t["kind"] for t in graph["triggers"]},{"FinalEffect","ExpireEffect"})

    def test_kraith_random_offset_and_period(self):
        graph,ev,issues=m.effect_graph(self.primal,"KraithSpineVolleyCreatePersistentPZ")
        authority.validate_damage(graph,"actual_kraith")
        hits=[e for e in graph["effects"] if e["id"]=="Effect:KraithSpineVolleyLaunchMissilePZ"]
        self.assertTrue(hits)
        self.assertTrue(hits[0]["conditions"])
        self.assertTrue(any("RandomPeriod" in s for s in ev))
        self.assertTrue(any("RandomOffset" in s for s in ev))

    def test_murvar_random_no_repeat_fields_retained(self):
        graph,ev,issues=m.effect_graph(self.primal,"CoopMurvarSpawnCreepersPZ2")
        authority.validate_damage(graph,"actual_murvar")
        root=graph["effects"][0]
        self.assertEqual(root["fields"]["PeriodRandMode|@value"],"RandomNoRepeat")
        self.assertEqual(root["fields"]["PeriodCount|@value"],"12")
        self.assertTrue(all(e["conditions"] for e in graph["effects"][1:]))


if __name__=="__main__":
    unittest.main()
