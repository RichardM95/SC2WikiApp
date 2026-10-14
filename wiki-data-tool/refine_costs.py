"""Refine isolated SCORE v2 base-state train costs, retaining every source ID."""
import argparse
import json
import os
from pathlib import Path
import re
import tempfile

from catalog_adapter import ComponentPool
from catalog_generate import load_validator
from mechanics import children, evidence, fields, number, value


SUFFIX = "v2-20261013"


def hydrate_sources(ctx, data):
    sources = data["sources"]
    expected = {"s%05d" % i for i in range(1, len(sources) + 1)}
    if {s["id"] for s in sources} != expected or len({s["id"] for s in sources}) != len(sources):
        raise ValueError("Cannot safely append providers: generated source IDs are not continuous")
    for source in sources:
        record = ctx._files.get((source.get("source_id"), source["path"]))
        if record is None:
            # Windows sources previously recorded a literal case-preserving path;
            # the active catalog indexes virtual paths in lowercase. Preserve the
            # original evidence record, accepting only one identical provider.
            matches = list({id(r):r for (sid, path), r in ctx._files.items()
                            if sid == source.get("source_id") and path.casefold() == source["path"].casefold()}.values())
            if len(matches) == 1:
                record = matches[0]
                ctx._files[(source.get("source_id"), source["path"])] = record
        if record is None or record["sha256"] != source["sha256"]:
            raise ValueError("Literal source changed or disappeared: " + source["id"])
    ctx.sources = sources
    ctx._source_ids = {}
    for s in sources:
        key = (s.get("source_id"), s["path"], s["object"], s["field"], s.get("ordinal"), s.get("locale"))
        if key in ctx._source_ids and ctx._source_ids[key] != s["id"]:
            raise ValueError("Conflicting IDs for the same literal provider")
        ctx._source_ids[key] = s["id"]


def inferred(value, ev, basis, unit=""):
    if not ev:
        raise ValueError("Inferred train cost requires literal operands/command evidence")
    return dict(value=value, unit=unit, status="inferred", evidence=list(dict.fromkeys(ev)), basis=basis)


def member_array(info, prefix):
    members = []
    if info.get("Unit"):
        members.append((info.get("Unit"), prefix+"|@Unit"))
    for node, path in children(info, prefix):
        if node.tag == "Unit" and node.get("removed") != "1":
            link = node.get("value") or node.get("Link")
            if link:
                members.append((link, path + ("|@value" if node.get("value") is not None else "|@Link")))
    return members


def unsafe_diagnostics(ctx, family, oid):
    bad = {"missing_parent", "unverified_type_defaults", "dependency_metadata_incomplete", "missing_dependency"}
    return [d for d in ctx.catalog.diagnostics if d.get("code") in bad and
            (not d.get("family") or (d.get("family") == family and d.get("id", d.get("object_id")) in (None, oid)))]


def refine_row(ctx, entry, row):
    aid = (row.get("ability") or "").removeprefix("Abil:")
    uid = entry["id"].removeprefix("Unit:")
    node = ctx.node("Abil", aid)
    if node is None or node.tag not in ("CAbilTrain", "CAbilWarpTrain"):
        return False
    match = next(((n,p) for n,p in children(node) if n.tag=="InfoArray" and n.get("index")==row.get("command")), None)
    if match is None:
        return False
    info, prefix = match
    members = member_array(info, prefix)
    if not members or not any(target == uid for target,path in members):
        return False
    member_ev = evidence(ctx, "Abil", aid, [p for target,p in members])
    if len(member_ev) != len({p for target,p in members}):
        # Several same-provider fields can legitimately share an ID only if path identical.
        row["cost"] = {k:ctx.missing("生产 Unit 成员证据未完整解析", member_ev) for k in ("minerals","vespene","supply")}
        return True
    command_ev = member_ev + evidence(ctx,"Abil",aid,["@id"])
    batch = [{"id":"Unit:"+target,"member_field":path,"evidence":evidence(ctx,"Abil",aid,[path])} for target,path in members]
    row["batch_units"] = batch
    row["batch_total_count"] = inferred(len(members),member_ev,"本命令完整有效 Unit 数组中的非空成员数")
    row["count"] = inferred(sum(target==uid for target,path in members),member_ev,
                            "本命令有效 Unit 数组中该词条目标出现的次数；单个显式成员为一，未使用缺失 Count 默认")
    row["cost_scope"] = "single_command_full_batch_base_state"
    row["cost_assumptions"] = []
    if node.tag=="CAbilWarpTrain" and len(members)!=1:
        row["cost"] = {k:ctx.missing("CAbilWarpTrain 只支持单一 Unit；源重复成员不能按批量折跃计费",command_ev) for k in ("minerals","vespene","supply")}
        return True
    flags = {c.get("index"):(c.get("value"),p+"|@value") for c,p in children(node) if c.tag=="Flags"}
    ignore, ignore_path = flags.get("IgnoreUnitCost", (None,None))
    if ignore not in (None,"0","1"):
        reason = "IgnoreUnitCost 有效值未解析为布尔位"
    elif unsafe_diagnostics(ctx,"Abil",aid) or any(unsafe_diagnostics(ctx,"Unit",target) for target,path in members):
        reason = "父链/类型默认/活动依赖存在未解析诊断，不能核算训练费用"
    else:
        condition, condition_path = value(node,"IgnoreUnitCostRequirements")
        reason = "条件式忽略单位费用需求尚未求值" if condition else None
    if reason:
        row["cost"] = {k:ctx.missing(reason,command_ev) for k in ("minerals","vespene","supply")}
        return True
    flag_ev = evidence(ctx,"Abil",aid,[ignore_path])
    if ignore is None:
        row["cost_assumptions"].append("完整有效 CAbilTrain/WarpTrain.Flags 未设置 IgnoreUnitCost，采用标准未设置标志语义；不是 XML literal default=0")
    else:
        row["cost_assumptions"].append("IgnoreUnitCost 显式="+ignore)
    total, extra = {}, {}
    for key,index in (("minerals","Minerals"),("vespene","Vespene")):
        extra_nodes = [(c,p) for c,p in children(info,prefix) if c.tag=="Resource" and c.get("index")==index]
        ev = command_ev + flag_ev
        if len(extra_nodes)>1:
            total[key] = ctx.missing("有效命令重复声明同资源调整",ev)
            continue
        if extra_nodes:
            c,p = extra_nodes[0]
            adjustment = number(c.get("value"))
            adjustment_ev = evidence(ctx,"Abil",aid,[p+"|@value"])
            if adjustment is None or not adjustment_ev:
                total[key] = ctx.missing("命令附加资源数值/证据未解析",ev)
                continue
            extra[key] = dict(value=adjustment,unit="",status="confirmed",evidence=adjustment_ev)
            ev += adjustment_ev
            adjustment_basis = "命令有符号资源调整="+str(adjustment)
        else:
            adjustment = 0
            extra[key] = inferred(0,command_ev,"完整有效命令未声明该可选 Resource 调整，推导无附加调整；未伪造资源字段默认 provider")
            adjustment_basis = "完整有效命令未声明可选 Resource["+index+"]，采用无附加调整前提"
            row["cost_assumptions"].append(adjustment_basis)
        operands = []
        if ignore != "1":
            for target,path in members:
                target_node = ctx.node("Unit",target)
                cost_nodes = [(c,p) for c,p in children(target_node) if c.tag=="CostResource" and c.get("index")==index]
                if len(cost_nodes)!=1:
                    operands = None
                    break
                c,p = cost_nodes[0]
                amount = number(c.get("value"))
                operand_ev = evidence(ctx,"Unit",target,[p+"|@value"])
                if amount is None or not operand_ev:
                    operands = None
                    break
                operands.append(amount)
                ev += operand_ev
        if operands is None:
            total[key] = ctx.missing("至少一个有效批次成员的 "+index+" 单价/来源未解析；不补零",ev)
        else:
            result = sum(operands)+adjustment
            flag_basis = "显式 IgnoreUnitCost=1，忽略单位单价" if ignore=="1" else "未设置 IgnoreUnitCost（标准未设置位语义）" if ignore is None else "显式 IgnoreUnitCost=0"
            if result<0:
                total[key]=ctx.missing("字段相加结果为负；引擎对负总费的退款/截断语义未证明",ev)
                total[key]["raw_calculation"]=result
            else:
                total[key] = inferred(result,ev,"基础状态一次命令完整批次：单位单价之和="+str(sum(operands))+"；"+adjustment_basis+"；"+flag_basis+"。按有效数组和资源相加规则推导，未验证游戏运行期升级/动态价格")
    # Food for morph/consumed producers has different net semantics. This pass
    # deliberately leaves every existing population fact and training time intact.
    total["supply"] = row.get("cost",{}).get("supply") or ctx.missing("人口净变化尚未证明",command_ev)
    row["cost"] = total
    row["additional_cost"] = extra
    row["energy_cost"] = [dict(vital=c.get("index"),amount=number(c.get("value")),evidence=evidence(ctx,"Abil",aid,[p+"|@value"]))
                          for c,p in children(info,prefix) if c.tag=="Vital" and c.get("index")=="Energy"]
    return True


def refine_data(ctx,data):
    original_ids = [s["id"] for s in data["sources"]]
    hydrate_sources(ctx,data)
    changed = sum(refine_row(ctx,entry,row) for entry in data["entries"] for row in entry.get("production",[]))
    resolved = resolve_condition_names(ctx,data)
    if resolved:
        note="本轮条件说明按明确 Unit/Upgrade 引用绑定实际中文名称；保留原生成说明、数值、逻辑和名称来源。"
        if note not in data.setdefault("review_items",[]):data["review_items"].append(note)
    for entry in data["entries"]:
        actors=entry.get("presentation",{}).get("actors",[])
        if len(actors)>1:
            note=entry["id"]+" 存在多个同优先级 Actor 绑定，展示选择不能消除歧义："+"、".join(a["id"] for a in actors)
            if not any(entry["id"] in old and "同优先级 Actor" in old for old in data.setdefault("review_items",[])):
                data["review_items"].append(note)
    if [s["id"] for s in data["sources"][:len(original_ids)]] != original_ids:
        raise ValueError("Refinement changed existing source IDs")
    return changed


def detail_references(detail):
    """Use typed condition fields, never catalog-name suffix guesses."""
    if isinstance(detail,dict):
        typ=detail.get("type")
        for path,oid in detail.get("fields",{}).items():
            tokens=path.split("|")
            if tokens[-1] not in ("@Link","@Alias","@value","@Value"):continue
            family=None
            if typ=="CRequirementCountUnit" and tokens[0]=="Count":family="Unit"
            elif typ=="CRequirementCountUpgrade" and tokens[0]=="Count":family="Upgrade"
            elif typ=="CValidatorUnitType" and tokens[0] in ("Value","Unit"):family="Unit"
            elif "Unit" in tokens or "UnitLink" in tokens:family="Unit"
            elif "Upgrade" in tokens or "UpgradeLink" in tokens:family="Upgrade"
            if family and isinstance(oid,str) and not oid.isdigit() and re.fullmatch(r"[A-Za-z0-9_]+",oid):
                yield family,oid
        for item in detail.values():yield from detail_references(item)
    elif isinstance(detail,list):
        for item in detail:yield from detail_references(item)


def resolve_condition_names(ctx,data):
    changed=0
    def visit(obj,path=()):
        nonlocal changed
        if isinstance(obj,dict):
            if {"value","unit","status","evidence"}<=obj.keys() and obj.get("status")=="inferred" and isinstance(obj.get("value"),str) and re.search(r"[\u3400-\u9fff]",obj["value"]) and any(p in ("conditions","condition","requirements") for p in path):
                original=obj["value"]; text=original; names=[]; ev=[]
                for family,oid in dict.fromkeys(detail_references(obj.get("details",{}))):
                    if ctx.node(family,oid) is None:continue
                    name=ctx.text(family,oid,[family+"/Name/"+oid])
                    label=name.get("value")
                    if name.get("status") not in ("confirmed","inferred") or not isinstance(label,str) or not re.search(r"[\u3400-\u9fff]",label):continue
                    replaced,n=re.subn(r"(?<![A-Za-z0-9_])"+re.escape(oid)+r"(?![A-Za-z0-9_])",lambda m:label,text)
                    if n:
                        text=replaced;ev+=name["evidence"];names.append(dict(id=family+":"+oid,name=label,evidence=name["evidence"]))
                if text!=original:
                    obj.setdefault("original_generated_value",original)
                    obj["value"]=text
                    obj["evidence"]=list(dict.fromkeys(obj["evidence"]+ev))
                    obj["resolved_names"]=names
                    obj["basis"]=obj.get("basis","")+"；按明确条件 Catalog 引用补入实际中文名称"
                    changed+=1
            for key,item in list(obj.items()):
                if key not in ("value","original_value","original_generated_value","fields","resolved_names"):
                    visit(item,path+(key,))
        elif isinstance(obj,list):
            for item in obj:visit(item,path)
    visit(data.get("entries",[]))
    return changed


def target_path(project,faction,suffix):
    if suffix != SUFFIX:
        raise ValueError("This task only permits new batches with suffix "+SUFFIX)
    project = Path(project).resolve()
    target = project/"wiki-data"/(faction["key"].removeprefix("SCORE-")+"-"+suffix)/"data.json"
    if not target.resolve().is_relative_to(project/"wiki-data") or "-v1" in target.parent.name:
        raise ValueError("Refused a legacy/out-of-project batch")
    return target


def atomic_replace(path,blob):
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb",prefix=".refine-costs-",suffix=".tmp",dir=path.parent,delete=False) as stream:
            staged = Path(stream.name)
            stream.write(blob); stream.flush(); os.fsync(stream.fileno())
        os.replace(staged,path)
    finally:
        if staged is not None and staged.exists():
            staged.unlink()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ("source-root","project-dir","batch-suffix","toolbox-root","game-root","gamedata-root","hub-root"):
        parser.add_argument("--"+key,required=True)
    parser.add_argument("--dry-run",action="store_true")
    args=parser.parse_args(argv)
    if args.batch_suffix!=SUFFIX:
        parser.error("Refuse legacy/other batches; expected "+SUFFIX)
    project=Path(args.project_dir).resolve()
    manifest=json.loads((project/"runtime/catalog/score-factions.json").read_text(encoding="utf-8-sig"))
    validator=load_validator(Path(args.hub_root).resolve())
    pool=ComponentPool(args.source_root,args.toolbox_root,args.game_root,args.gamedata_root)
    pending=[]
    for faction in manifest["factions"]:
        if faction["key"]=="SCORE-Other":continue
        target=target_path(project,faction,args.batch_suffix)
        original=target.read_bytes()
        data=json.loads(original.decode("utf-8-sig"))
        if data["project"].get("batch")!=SUFFIX or data["project"]["primary_mod"]!=faction["component"]:
            raise ValueError("Batch identity mismatch: "+str(target))
        ctx=pool.context(faction["component"])
        changed=refine_data(ctx,data)
        validator.validate(data)
        blob=json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False).encode("utf-8")
        pending.append((target,original,blob))
        print(json.dumps(dict(faction=faction["key"],refined_paths=changed,sources=len(data["sources"]),dry_run=args.dry_run),ensure_ascii=False),flush=True)
    if args.dry_run:return
    # All batches pass validation before any publication; detect concurrent edits.
    for path,original,blob in pending:
        if path.read_bytes()!=original:
            raise ValueError("Batch changed during refinement: "+str(path))
    written=[]
    try:
        for path,original,blob in pending:
            atomic_replace(path,blob); written.append((path,original))
    except BaseException:
        for path,original in reversed(written):atomic_replace(path,original)
        raise


if __name__=="__main__":
    main()
