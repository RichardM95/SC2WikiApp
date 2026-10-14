import copy
import unittest
from pathlib import Path

from test_catalog import synthetic
from sc2_wiki_data.refine_costs import refine_data,target_path,SUFFIX


def data_for(ctx,uid,aid,command="Train1"):
    # Existing source identity represents already-generated evidence.
    ctx.evidence("Unit",uid,"@id")
    return dict(sources=copy.deepcopy(ctx.sources),entries=[dict(id="Unit:"+uid,production=[dict(
        ability="Abil:"+aid,command=command,time=dict(value=60,status="confirmed",evidence=[],unit="s"),
        cost={k:ctx.missing("Unrefined") for k in ("minerals","vespene","supply")},
        base_unit_cost=dict(minerals=dict(value=750,status="confirmed",evidence=[],unit="")))])])


class CostTests(unittest.TestCase):
    def test_single_explicit_member_and_ids_time_preserved(self):
        ctx=synthetic([("local",'<CUnit id="Thor"><CostResource index="Minerals" value="750"/><CostResource index="Vespene" value="500"/></CUnit><CAbilTrain id="Train"><InfoArray index="Train1" Time="60"><Unit value="Thor"/></InfoArray></CAbilTrain>')])
        data=data_for(ctx,"Thor","Train"); old=copy.deepcopy(data["sources"])
        self.assertEqual(refine_data(ctx,data),1)
        row=data["entries"][0]["production"][0]
        self.assertEqual(row["count"]["value"],1)
        self.assertEqual(row["batch_total_count"]["value"],1)
        self.assertEqual(row["cost"]["minerals"]["value"],750)
        self.assertEqual(row["cost"]["vespene"]["value"],500)
        self.assertEqual(row["cost"]["minerals"]["status"],"inferred")
        self.assertEqual(row["time"]["value"],60)
        self.assertEqual(data["sources"][:len(old)],old)
        self.assertEqual(row["cost"]["supply"]["status"],"unresolved")

    def test_eight_members_negative_adjustment_once(self):
        units='<Unit value="Scourge"/>'*8
        ctx=synthetic([("local",'<CUnit id="Scourge"><CostResource index="Minerals" value="50"/><CostResource index="Vespene" value="25"/></CUnit><CAbilTrain id="Train"><InfoArray index="Train1">'+units+'<Resource index="Minerals" value="-200"/><Resource index="Vespene" value="-100"/></InfoArray></CAbilTrain>')])
        data=data_for(ctx,"Scourge","Train");refine_data(ctx,data)
        row=data["entries"][0]["production"][0]
        self.assertEqual(row["count"]["value"],8)
        self.assertEqual(row["cost"]["minerals"]["value"],200)
        self.assertEqual(row["cost"]["vespene"]["value"],100)

    def test_mixed_batch_whole_price_target_count(self):
        ctx=synthetic([("local",'<CUnit id="A"><CostResource index="Minerals" value="50"/><CostResource index="Vespene" value="10"/></CUnit><CUnit id="B"><CostResource index="Minerals" value="100"/><CostResource index="Vespene" value="20"/></CUnit><CAbilTrain id="Train"><InfoArray index="Train1"><Unit value="A"/><Unit value="B"/></InfoArray></CAbilTrain>')])
        data=data_for(ctx,"A","Train");refine_data(ctx,data)
        row=data["entries"][0]["production"][0]
        self.assertEqual(row["count"]["value"],1)
        self.assertEqual(row["batch_total_count"]["value"],2)
        self.assertEqual(row["cost"]["minerals"]["value"],150)
        self.assertEqual(row["cost"]["vespene"]["value"],30)

    def test_ignore_unit_cost_still_keeps_energy(self):
        ctx=synthetic([("local",'<CUnit id="A"/><CAbilTrain id="Train"><Flags index="IgnoreUnitCost" value="1"/><InfoArray index="Train1"><Unit value="A"/><Vital index="Energy" value="25"/></InfoArray></CAbilTrain>')])
        data=data_for(ctx,"A","Train");refine_data(ctx,data)
        row=data["entries"][0]["production"][0]
        self.assertEqual(row["cost"]["minerals"]["value"],0)
        self.assertEqual(row["energy_cost"][0]["amount"],25)

    def test_missing_unit_price_is_not_zero(self):
        ctx=synthetic([("local",'<CUnit id="A"><CostResource index="Minerals" value="50"/></CUnit><CAbilTrain id="Train"><InfoArray index="Train1"><Unit value="A"/></InfoArray></CAbilTrain>')])
        data=data_for(ctx,"A","Train");refine_data(ctx,data)
        row=data["entries"][0]["production"][0]
        self.assertEqual(row["cost"]["minerals"]["value"],50)
        self.assertIsNone(row["cost"]["vespene"]["value"])

    def test_repeated_refine_reuses_sources(self):
        ctx=synthetic([("local",'<CUnit id="A"><CostResource index="Minerals" value="10"/></CUnit><CAbilTrain id="Train"><InfoArray index="Train1"><Unit value="A"/></InfoArray></CAbilTrain>')])
        data=data_for(ctx,"A","Train");refine_data(ctx,data)
        old=copy.deepcopy(data["sources"])
        refine_data(ctx,data)
        self.assertEqual(data["sources"],old)

    def test_conditional_free_cost_remains_unknown(self):
        ctx=synthetic([("local",'<CUnit id="A"><CostResource index="Minerals" value="10"/></CUnit><CRequirement id="Free"/><CAbilTrain id="Train"><IgnoreUnitCostRequirements value="Free"/><InfoArray index="Train1"><Unit value="A"/></InfoArray></CAbilTrain>')])
        data=data_for(ctx,"A","Train");refine_data(ctx,data)
        row=data["entries"][0]["production"][0]
        self.assertIsNone(row["cost"]["minerals"]["value"])
        self.assertIn("条件式",row["cost"]["minerals"]["reason"])

    def test_morph_and_charge_time_untouched(self):
        ctx=synthetic([("local",'<CUnit id="A"/><CAbilMorph id="Morph"><InfoArray index="Train1" Unit="A"><Charge TimeUse="8"/></InfoArray></CAbilMorph>')])
        data=data_for(ctx,"A","Morph");old=copy.deepcopy(data["entries"])
        self.assertEqual(refine_data(ctx,data),0)
        self.assertEqual(data["entries"],old)

    def test_guard_refuses_legacy_suffix(self):
        with self.assertRaises(ValueError):target_path(Path.cwd(),{"key":"SCORE-Test"},"v1")
        self.assertTrue(target_path(Path.cwd(),{"key":"SCORE-Test"},SUFFIX).parent.name.endswith(SUFFIX))

    def test_condition_names_preserve_prefix_ids_numbers_and_structure(self):
        ctx=synthetic([("local",'<CUnit id="ArmoryC"/><CUnit id="A"/>')],[("local","zhCN","Unit/Name/ArmoryC=军械库\n")])
        ctx.evidence("Unit","A","@id")
        detail=dict(type="CRequirementCountUnit",fields={"Count|@Link":"ArmoryC","Count|@State":"CompleteOnly"})
        condition=dict(value="单位计数：ArmoryC，ArmoryCX，至少 2",unit="",status="inferred",evidence=[],basis="源逻辑",details=detail)
        data=dict(sources=copy.deepcopy(ctx.sources),entries=[dict(id="Unit:A",production=[],abilities=[dict(conditions=condition)])])
        refine_data(ctx,data)
        self.assertEqual(condition["value"],"单位计数：军械库，ArmoryCX，至少 2")
        self.assertEqual(condition["details"]["fields"]["Count|@Link"],"ArmoryC")
        self.assertEqual(condition["original_generated_value"],"单位计数：ArmoryC，ArmoryCX，至少 2")
        self.assertTrue(condition["evidence"])


if __name__=="__main__":unittest.main()
