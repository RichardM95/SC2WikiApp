"""Source-backed SCORE relationships and command cards; no source/default mutation."""
from collections import Counter, defaultdict
import json
import math
import re


def number(value):
    try:
        value = float(value)
        return int(value) if value.is_integer() else value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def children(node, prefix=""):
    """Catalog paths: explicit array indices, otherwise per-tag implicit indices."""
    if node is None:
        return
    counts = Counter(c.tag for c in node)
    positions = defaultdict(int)
    for child in node:
        index = child.get("index")
        if index is None and (child.tag.endswith("Array") or child.tag in ("CardLayouts", "LayoutButtons") or counts[child.tag] > 1):
            index = str(positions[child.tag])
        positions[child.tag] += 1
        path = child.tag + ("|" + index if index is not None else "")
        yield child, prefix + ("|" if prefix else "") + path


def fields(node, prefix=""):
    for child, path in children(node, prefix):
        for key, value in child.attrib.items():
            if key != "index":
                yield path + "|@" + key, value
        yield from fields(child, path)


def refs(node, tag):
    return [(c.get("Link") or c.get("value"), p + ("|@Link" if c.get("Link") else "|@value"))
            for c, p in children(node) if c.tag == tag and (c.get("Link") or c.get("value"))]


def value(node, tag):
    if node is not None:
        for c, p in children(node):
            if c.tag == tag:
                for a in ("value", "Value", "Link"):
                    if c.get(a) is not None:
                        return c.get(a), p + "|@" + a
    return None, None


def nodes(ctx, family):
    result = ctx.find_nodes(family)
    return result if isinstance(result, dict) else {n.get("id"): n for n in result}


def evidence(ctx, family, oid, paths):
    out = []
    for path in paths:
        if not path:
            continue
        try:
            sid = ctx.evidence(family, oid, path)
        except (ValueError, KeyError):
            continue
        if sid not in out:
            out.append(sid)
    return out


def fact(ctx, val, family, oid, paths, unit="", generated=False, reason=None):
    ev = evidence(ctx, family, oid, paths)
    if val is None or val == "" or not ev:
        return ctx.missing(reason or "有效字段或来源证据尚未解析", evidence=ev)
    result = {"value": val, "unit": unit, "status": "inferred" if generated else "confirmed", "evidence": ev}
    if generated:
        result["basis"] = "根据所列有效 XML 字段整理机制说明"
    return result


FILTER_NAMES = {"Ground":"地面", "Air":"空中", "Structure":"建筑", "Light":"轻甲", "Armored":"重甲",
                "Biological":"生物", "Mechanical":"机械", "Psionic":"灵能", "Massive":"巨型", "Heroic":"英雄",
                "Missile":"导弹", "Stasis":"停滞", "Dead":"死亡", "Hidden":"隐藏", "Invulnerable":"无敌",
                "Visible":"可见", "Self":"自身", "Player":"玩家", "Ally":"盟友", "Enemy":"敌人", "Neutral":"中立",
                "Buried":"潜地", "Burrowed":"潜地", "Destructible":"可摧毁", "Resource":"资源"}


def target_fact(ctx, wid, raw, path):
    if raw is None:
        return ctx.missing("未解析有效目标筛选条件")
    parts = raw.split(";")
    included = "、".join(FILTER_NAMES.get(t,t) for t in parts[0].split(",") if t not in ("", "-"))
    excluded = "、".join(FILTER_NAMES.get(t,t) for t in parts[1].split(",") if t not in ("", "-")) if len(parts)>1 else ""
    # An empty required group means no positive restriction, not 'no targets'.
    text = "要求：" + (included or "无额外正向筛选") + ("；排除："+excluded if excluded else "")
    result = fact(ctx,text,"Weapon",wid,[path],generated=True)
    if result["status"] != "unresolved":
        result.update(translation_draft=True,original_value=raw,source_locale="und")
    return result


def scalar(ctx, family, oid, node, tag, unit="", absolute=False):
    v, p = value(node, tag)
    n = number(v)
    return fact(ctx, abs(n) if absolute and n is not None else n, family, oid, [p], unit=unit,
                reason="未解析有效字段 " + tag)


def localized(ctx, family, oid, kind):
    keys = [f"{family}/{kind}/{oid}"]
    if kind == "Tooltip":
        keys += [f"{family}/Description/{oid}"]
    return ctx.text(family, oid, keys)


def tree_condition(ctx, family, oid, active=()):
    if (family, oid) in active:
        return ctx.missing("条件图存在循环：" + oid)
    node = ctx.node(family, oid)
    if node is None:
        return ctx.missing("条件对象未解析：" + family + ":" + oid)
    data = {"id": family + ":" + oid, "type": node.tag, "fields": dict(fields(node))}
    ev = evidence(ctx, family, oid, data["fields"])
    errors, branches = [], []
    if node.tag == "CValidatorCombine":
        operator, _ = value(node, "Type")
        negate, _ = value(node, "Negate")
        data["operator"] = operator or "Or"
        data["metadata"] = {"negated":negate == "1"}
        if data["operator"] not in ("And", "Or") or negate not in (None, "0", "1"):
            errors.append("组合验证器的 Type/Negate 无法静态解释")
    for path, v in fields(node):
        if any(t in path.split("|") for t in ("OperandArray", "ValidatorArray", "CombineArray", "NodeArray")):
            target_family = "RequirementNode" if family in ("Requirement", "RequirementNode") else "Validator"
            child = tree_condition(ctx, target_family, v, active + ((family, oid),))
            branches.append({"field": path, "condition": child})
            ev.extend(x for x in child["evidence"] if x not in ev)
            if child["status"] == "unresolved":
                errors.append(child.get("reason", "子条件未解析"))
    if branches:
        data["operands"] = branches
    if errors or not ev:
        result = ctx.missing("；".join(errors) or "条件来源尚未解析", evidence=ev)
        result["raw_value"] = data
        return result
    label = condition_text(data)
    return {"value": label, "unit": "", "status": "inferred", "evidence": ev,
            "basis": "根据有效条件图整理中文机制说明", "details": data}


def condition_text(data):
    labels = {"CRequirementAnd":"同时满足", "CRequirementOr":"任一满足", "CRequirementNot":"不满足",
              "CValidatorCombine":"组合验证", "CRequirement":"需求", "CRequirementCountUnit":"单位计数",
              "CRequirementCountUpgrade":"升级计数", "CValidatorUnitCompareVital":"单位生命/能量比较",
              "CRequirementConst":"常数", "CRequirementEq":"等于", "CRequirementLT":"小于",
              "CRequirementGT":"大于", "CRequirementGTE":"至少", "CRequirementLTE":"至多"}
    translated = {"Caster":"施法者", "Target":"目标", "GT":"大于", "GE":"至少", "LT":"小于", "LE":"至多", "EQ":"等于",
                  "CompleteOnly":"已完成", "QueuedOrBetter":"已排队或更高", "CompleteOnlyAtUnit":"该单位已完成"}
    if data["type"] in ("CRequirementCountUnit","CRequirementCountUpgrade"):
        # Flags are behavior switches (e.g. TechTreeCheat=0), never a count result.
        raw = data["fields"]
        link = raw.get("Count|@Link")
        state = raw.get("Count|@State")
        return labels[data["type"]]+"："+(str(link) if link else "对象未解析")+"（"+translated.get(state,state or "状态未解析")+"）"
    parts = [("使用：" if "|Use|" in r["field"] else "显示：" if "|Show|" in r["field"] else "") +
             (r["condition"].get("value") or r["condition"].get("reason")) for r in data.get("operands", [])]
    if parts:
        if data["type"] == "CValidatorCombine":
            label = "同时满足" if data.get("operator") == "And" else "任一满足"
            return ("不满足" if data.get("metadata",{}).get("negated") else "") + label + "（" + "；".join(parts) + "）"
        return labels.get(data["type"], "条件") + "（" + "；".join(parts) + "）"
    values = [translated.get(str(v), str(v)) for p,v in data["fields"].items()]
    return labels.get(data["type"], "条件节点") + "：" + "，".join(values)


def refresh_condition_texts(data):
    """Rewrite saved count-condition text only; no catalog/source/status mutation."""
    count_types = {"CRequirementCountUnit","CRequirementCountUpgrade"}
    stats = {"examined":0,"changed":0,"skipped_unresolved":0,"by_type":{}}
    fact_keys = {"value","unit","status","evidence"}

    def has_count(tree):
        if isinstance(tree,list):
            return any(has_count(item) for item in tree)
        if not isinstance(tree,dict):
            return False
        if tree.get("type") in count_types:
            return True
        if fact_keys <= tree.keys():
            return has_count(tree.get("details")) or has_count(tree.get("raw_value"))
        return any(has_count(branch.get("condition")) for branch in tree.get("operands",[]))

    def visit(obj):
        if isinstance(obj,list):
            affected = False
            for item in obj:
                affected = visit(item) or affected
            return affected
        if not isinstance(obj,dict):
            return False
        is_fact = fact_keys <= obj.keys()
        detail = obj.get("details") if is_fact else None
        affected = (obj.get("type") in count_types) or has_count(detail)
        if is_fact and (obj.get("status")=="unresolved" or obj.get("value") is None):
            affected = affected or has_count(obj.get("raw_value"))
        for key,item in obj.items():
            if key not in ("value","raw_value","fields","sources","evidence","resolved_names","original_generated_value"):
                affected = visit(item) or affected
        typed = isinstance(detail,dict) and isinstance(detail.get("type"),str) and "fields" in detail
        combined = isinstance(detail,list) and bool(detail) and all(isinstance(c,dict) and fact_keys<=c.keys() for c in detail)
        if not is_fact or not affected or not (typed or combined or has_count(obj.get("raw_value"))):
            return affected
        stats["examined"] += 1
        if obj.get("status") not in ("confirmed","inferred") or not isinstance(obj.get("value"),str):
            stats["skipped_unresolved"] += 1
            return affected
        if typed:
            text = condition_text(detail)
            typ = detail["type"]
        elif combined and all(isinstance(c.get("value"),str) for c in detail):
            text = "；".join(c["value"] for c in detail)
            typ = "combined_conditions"
        else:
            return affected
        for name in sorted(obj.get("resolved_names",[]),key=lambda item:len(item.get("id","")),reverse=True):
            oid = name.get("id","").partition(":")[2]
            label = name.get("name")
            if oid and isinstance(label,str):
                text = re.sub(r"(?<![A-Za-z0-9_])"+re.escape(oid)+r"(?![A-Za-z0-9_])",lambda match:label,text)
        if obj["value"].startswith("自动施放条件："):
            text = "自动施放条件："+text
        if text != obj["value"]:
            obj["value"] = text
            stats["changed"] += 1
            stats["by_type"][typ] = stats["by_type"].get(typ,0)+1
        return affected

    visit(data)
    return stats


def conditions(ctx, family, oid, node, prefix=""):
    results = []
    for p, v in fields(node, prefix):
        parts = p.split("|")
        if p.endswith("|@Requirements") or "Requirements" in parts:
            result = tree_condition(ctx, "Requirement", v)
        elif any(t in parts for t in ("ValidatorArray", "AutoCastValidatorArray", "SmartValidatorArray", "Validators")) or p.endswith("|@Validator"):
            result = tree_condition(ctx, "Validator", v)
        else:
            continue
        link_ev = evidence(ctx, family, oid, [p])
        result["evidence"] = list(dict.fromkeys(link_ev + result["evidence"]))
        if "AutoCastValidatorArray" in parts and result["value"]:
            result["value"] = "自动施放条件：" + result["value"]
        results.append(result)
    return results


def combined_conditions(ctx, family, oid, node, prefix=""):
    rows = conditions(ctx, family, oid, node, prefix)
    if not rows:
        return ctx.missing("未提供已解析的显式释放/显示条件；不能推定无条件")
    ev = list(dict.fromkeys(s for c in rows for s in c["evidence"]))
    if any(c["status"] == "unresolved" for c in rows):
        result = ctx.missing("部分条件未解析", evidence=ev)
        result["raw_value"] = rows
        return result
    return {"value": "；".join(c["value"] for c in rows), "unit": "", "status": "inferred", "evidence": ev,
            "basis": "根据有效条件图整理中文机制说明", "details": rows}


def graph_condition(condition):
    """Damage structures reserve value for numeric leaves; conditions use text."""
    result = {"text": condition.get("value") or condition.get("reason"), "status": condition["status"], "evidence": condition["evidence"]}
    if "details" in condition:
        detail = condition["details"]
        result["structure"] = sanitize_graph(detail)
    return result


def sanitize_graph(data):
    if isinstance(data, dict):
        if {"value", "unit", "status", "evidence"} <= data.keys():
            return graph_condition(data)
        return {k:sanitize_graph(v) for k,v in data.items()}
    if isinstance(data, list):
        return [sanitize_graph(v) for v in data]
    return data


def effect_graph(ctx, root, *, root_family="Effect"):
    """Keep occurrences/path conditions. Cycle guards are path-local, never global."""
    components, effects, ev, errors, triggers = [], [], [], [], []
    visits = 0
    def visit(family, oid, chain=(), gates=(), schedule=(), count=1, target=None, trigger=None):
        nonlocal visits
        if not oid:
            errors.append("空执行引用：" + family)
            return
        visits += 1
        if visits > 400 or len(chain) > 28:
            errors.append("效果图超出安全遍历上限")
            return
        if (family, oid) in chain:
            errors.append("效果图存在循环：" + oid)
            return
        node = ctx.node(family, oid)
        if node is None:
            errors.append("缺失执行对象：" + family + ":" + oid)
            return
        allfields = dict(fields(node))
        ev.extend(x for x in evidence(ctx, family, oid, allfields) if x not in ev)
        selectors = {p:v for p,v in allfields.items() if p.split("|")[0] in
                     ("WhichUnit", "WhichLocation", "Location", "Target", "ImpactLocation", "LaunchLocation")}
        if selectors:
            target = {"inherited": target, "owner": family+":"+oid, "selectors": selectors}
        # Switch case validators gate their own branch, not every branch/default.
        own_gates = [tree_condition(ctx, "Validator", c.get("value") or c.get("Link"))
                     for c, p in children(node) if family != "Behavior" and c.tag in ("ValidatorArray", "Validators")
                     and (c.get("value") or c.get("Link"))]
        ev.extend(s for gate in own_gates for s in gate["evidence"] if s not in ev)
        gates = gates + tuple(own_gates)
        probability, pp = value(node, "Chance")
        if probability is not None and number(probability) != 1:
            gates += (fact(ctx,"按概率执行："+str(probability),family,oid,[pp],generated=True),)
        if any(c["status"] == "unresolved" for c in own_gates):
            errors.append("执行条件未解析：" + oid)
        effects.append({"id": family + ":" + oid, "type": node.tag, "fields": allfields,
                        "conditions": list(gates), "target": target, "trigger": trigger})
        chain = chain + ((family, oid),)
        if node.tag == "CEffectDamage":
            amount, ap = value(node, "Amount")
            n = number(amount)
            if n is None:
                errors.append("伤害 Amount 未解析：" + oid)
            bonuses = {c.get("index"): number(c.get("value")) for c, p in children(node)
                       if c.tag == "AttributeBonus"}
            if any(v is None for v in bonuses.values()):
                errors.append("属性加成未解析：" + oid)
            armor, _ = value(node, "ArmorReduction")
            areas = [{"field": p, **c.attrib} for c, p in children(node) if c.tag == "AreaArray"]
            kind, _ = value(node, "Kind")
            components.append({"effect": "Effect:" + oid, "amount": n, "armor_reduction": number(armor),
                               "bonus": bonuses, "count": count, "schedule": list(schedule),
                               "conditions": list(gates), "target": target, "areas": areas,
                               "kind": kind, "path": [f + ":" + i for f, i in chain], "trigger": trigger})
            if n is None:
                # Unknown numeric leaves cannot masquerade as numeric damage fields.
                components[-1].pop("amount")
                components[-1]["metadata"] = {"amount_status":"unresolved"}
            return
        if node.tag == "CEffectSwitch":
            for c, p in children(node):
                if c.tag == "CaseArray" and c.get("Effect"):
                    gate = tree_condition(ctx, "Validator", c.get("Validator")) if c.get("Validator") else ctx.missing("分支 Validator 未声明")
                    if gate["status"] == "unresolved":
                        errors.append("分支 Validator 未解析：" + oid)
                    gate["evidence"] = list(dict.fromkeys(evidence(ctx, family, oid, [p+"|@Validator", p+"|@Effect"]) + gate["evidence"]))
                    ev.extend(s for s in gate["evidence"] if s not in ev)
                    visit("Effect", c.get("Effect"), chain, gates + (gate,), schedule, count, target, trigger)
                elif c.tag == "CaseDefault" and c.get("value"):
                    default = fact(ctx, "其他分支未匹配时", family, oid, [p+"|@value"], generated=True)
                    visit("Effect", c.get("value"), chain, gates + (default,), schedule, count, target, trigger)
            return
        if node.tag == "CEffectCreatePersistent":
            pc, _ = value(node, "PeriodCount")
            periods = [(c, p) for c, p in children(node) if c.tag == "PeriodicEffectArray"]
            times = [number(c.get("value")) for c, p in children(node) if c.tag == "PeriodicPeriodArray"]
            offsets = [c.get("value") for c,p in children(node) if c.tag == "PeriodicOffsetArray"]
            flags = {c.get("index"):c.get("value") for c,p in children(node) if c.tag == "Flags"}
            random_period = flags.get("RandomPeriod") == "1"
            random_offset = flags.get("RandomOffset") == "1"
            rand_mode, _ = value(node, "PeriodRandMode")
            if random_period or random_offset:
                random_paths = [p for p in allfields if p.split("|")[0] in
                                ("Flags", "PeriodRandMode", "PeriodicPeriodArray", "PeriodicOffsetArray")]
                gates += (fact(ctx,"存在随机周期或位置选择；候选不代表固定执行顺序",family,oid,random_paths,generated=True),)
            n = number(pc)
            if n is None or n < 0 or int(n) != n:
                errors.append("持续效果次数未解析：" + oid)
            elif n > 100:
                errors.append("持续效果次数超过展开上限：" + oid)
            elif n and periods:
                for i in range(int(n)):
                    c, p = periods[i % len(periods)]
                    link = c.get("value") or c.get("Link")
                    if not link:
                        # Explicit empty periodic slots consume timing, execute nothing.
                        continue
                    timing = {"persistent": "Effect:"+oid, "occurrence": i,
                              "interval": times[i % len(times)] if times and not random_period else None}
                    if random_period:
                        timing.update(interval_candidates=times, period_random_mode=rand_mode)
                        timing.setdefault("flags",{})["random_period"] = True
                    if random_offset:
                        timing.update(offset_candidates=offsets)
                        timing.setdefault("flags",{})["random_offset"] = True
                    elif offsets:
                        timing["offset"] = offsets[i % len(offsets)]
                    visit("Effect", link, chain, gates, schedule + (timing,), count, target, trigger)
            elif n and not periods:
                errors.append("持续效果未解析周期执行引用：" + oid)
            for c, p in children(node):
                if c.tag in ("InitialEffect", "FinalEffect", "ExpireEffect") and c.get("value"):
                    phase_gates = gates
                    if c.tag != "InitialEffect":
                        phase_gates += (fact(ctx,"自然到期时" if c.tag=="ExpireEffect" else "持续效果终止时",
                                            family,oid,[p+"|@value"],generated=True),)
                    visit("Effect", c.get("value"), chain, phase_gates,
                          schedule + ({"phase": c.tag, "persistent": "Effect:"+oid},), count, target, trigger)
            return
        if node.tag in ("CEffectApplyBehavior", "CEffectTransferBehavior"):
            link, _ = value(node, "Behavior")
            if link:
                visit("Behavior", link, chain, gates, schedule, count, target, trigger)
            else:
                errors.append("行为引用未解析：" + oid)
            return
        if family == "Behavior":
            duration, _ = value(node, "Duration")
            period, _ = value(node, "Period")
            state_gates = []
            for c,p in children(node):
                if c.tag not in ("DisableValidatorArray", "RemoveValidatorArray", "ValidatorArray"):
                    continue
                link = c.get("value") or c.get("Link")
                if not link:
                    continue
                condition = tree_condition(ctx,"Validator",link)
                condition["evidence"] = list(dict.fromkeys(evidence(ctx,family,oid,
                    [p+("|@Link" if c.get("Link") else "|@value")])+condition["evidence"]))
                if c.tag != "ValidatorArray":
                    mode = "Disable" if c.tag=="DisableValidatorArray" else "Remove"
                else:
                    mode = c.get("type") or "Validate"
                state_gates.append({"mode":mode,"field":p,"condition":condition})
                ev.extend(s for s in condition["evidence"] if s not in ev)
                if condition["status"]=="unresolved":
                    errors.append("行为状态验证器未解析："+oid)
            effects[-1]["behavior_states"] = state_gates
            for p, link in allfields.items():
                tokens = [t.removeprefix("@") for t in p.split("|")]
                kind = next((t for t in tokens if t in ("InitialEffect", "FinalEffect", "ExpireEffect", "PeriodicEffect", "PeriodicEffectArray")),None)
                if "DamageResponse" in tokens and tokens[-1]=="Handled":
                    kind = "DamageResponse"
                elif "DeathResponse" in tokens and tokens[-1]=="Effect":
                    kind = "DeathResponse"
                elif tokens[-1] not in ("value","Link"):
                    continue
                if not kind or not link:
                    continue
                prefix = p.rsplit("|",1)[0]
                response = {key:v for key,v in allfields.items() if key.startswith(prefix+"|")}
                dynamic = {"kind":kind,"owner":"Behavior:"+oid,"field":p,"fields":response,
                           "metadata":{"runtime_dependent":True},"behavior_states":state_gates,
                           "state_fields":{key:v for key,v in allfields.items() if key.split("|")[0] in
                                           ("BehaviorFlags","Duration","Period","MaxCount","MaxCountPerCaster")}}
                triggers.append(dynamic)
                names = {"DamageResponse":"受到符合条件的伤害时响应", "DeathResponse":"死亡时响应",
                         "PeriodicEffect":"行为活动期间周期执行", "PeriodicEffectArray":"行为活动期间周期执行",
                         "InitialEffect":"行为应用时", "FinalEffect":"行为终止时", "ExpireEffect":"行为自然到期时"}
                gate = fact(ctx,names[kind],family,oid,response,generated=True)
                gate["details"] = dynamic
                ev.extend(s for s in gate["evidence"] if s not in ev)
                response_target = ({"inherited":target,"owner":"Behavior:"+oid,"response_fields":response}
                                   if kind in ("DamageResponse","DeathResponse") else target)
                visit("Effect", link, chain, gates+(gate,), schedule + ({"behavior": "Behavior:"+oid,
                      "field": p, "phase":kind,"period": number(period), "duration": number(duration)},), None, response_target, dynamic)
            return
        if node.tag == "CEffectLaunchMissile":
            for c,p in children(node):
                if c.tag not in ("ImpactEffect","LaunchEffect","InitialEffect","FinalEffect","ExpireEffect"):
                    continue
                link = c.get("value") or c.get("Link")
                if not link:
                    continue
                event = {"kind":c.tag,"owner":"Effect:"+oid,"field":p,"metadata":{"runtime_dependent":True}}
                label = {"ImpactEffect":"导弹命中时", "LaunchEffect":"导弹发射时", "InitialEffect":"导弹启动时",
                         "FinalEffect":"导弹终止时", "ExpireEffect":"导弹自然到期时"}[c.tag]
                gate = fact(ctx,label,family,oid,[p+("|@Link" if c.get("Link") else "|@value")],generated=True)
                visit("Effect",link,chain,gates+(gate,),schedule+({"missile":"Effect:"+oid,"phase":c.tag},),
                      count,target,trigger or event)
            return
        if node.tag == "CEffectRandomPointInCircle":
            gates += (fact(ctx,"在随机位置执行；命中目标由运行时确定",family,oid,allfields,generated=True),)
            target = {"inherited":target,"owner":"Effect:"+oid,"random_location":allfields}
        links = []
        for c, p in children(node):
            if c.tag == "AreaArray" and c.get("Effect"):
                links.append((c.get("Effect"), {"inherited":target,"search": "Effect:"+oid, **c.attrib}))
            elif c.tag in ("EffectArray", "Effect", "ImpactEffect", "LaunchEffect", "InitialEffect", "FinalEffect", "ExpireEffect", "CaseDefault", "SuccessEffect", "FailureEffect"):
                link = c.get("value") or c.get("Link")
                if link:
                    links.append((link, target))
        for link, area in links:
            visit("Effect", link, chain, gates, schedule, count, area, trigger)
        known_atomic = {"CEffectModifyUnit", "CEffectModifyPlayer", "CEffectCreateUnit", "CEffectIssueOrder",
                        "CEffectRemoveBehavior", "CEffectApplyForce", "CEffectTeleport", "CEffectDestroyPersistent",
                        "CEffectCancelOrder", "CEffectKill", "CEffectSet", "CEffectEnumArea", "CEffectSearch",
                        "CEffectLaunchMissile", "CEffectReturnMagazine", "CEffectLoadContainer", "CEffectUnloadContainer"}
        if not links and node.tag not in known_atomic:
            errors.append("尚未支持的执行节点：" + node.tag + ":" + oid)
        # Create-unit damage belongs to the spawned unit, not this weapon's direct hit.
    if root:
        visit(root_family, root)
    else:
        errors.append("实际执行 Effect 未声明或未解析")
    return sanitize_graph({"execution_root": root_family+":" + root if root else None, "components": components,
            "effects": effects,"triggers":triggers}), ev, list(dict.fromkeys(errors))


def behavior_effect_graph(ctx, bid):
    return effect_graph(ctx,bid,root_family="Behavior")


def weapons(ctx, unit):
    result = []
    for wid, link_path in refs(unit, "WeaponArray"):
        node = ctx.node("Weapon", wid)
        if node is None:
            continue
        root, rp = value(node, "Effect")
        display, dp = value(node, "DisplayEffect")
        data, ev, errors = effect_graph(ctx, root)
        ev = list(dict.fromkeys(evidence(ctx, "Weapon", wid, [rp, dp]) + ev))
        if display:
            shown, shown_ev, shown_errors = effect_graph(ctx, display)
            data["display"] = {"root": "Effect:" + display,
                               "components": [{k:v for k,v in c.items() if k!="amount" or v is not None} for c in shown["components"]],
                               "issues": shown_errors}
            ev += [s for s in shown_ev if s not in ev]
        if errors:
            damage = ctx.missing("；".join(errors), evidence=ev)
            damage["raw_value"] = data
        elif not data["components"]:
            damage = ctx.na("实际执行链不包含直接伤害；搜索/行为/召唤等功能见执行效果", evidence=ev)
        else:
            damage = {"value": data, "unit": "", "status": "confirmed", "evidence": ev}
            # Backwards-compatible amount only for one unconditional direct hit.
            c = data["components"]
            if len(c) == 1 and c[0]["count"]==1 and not c[0].get("trigger") and not c[0]["schedule"] and not c[0]["conditions"] and not c[0]["target"]:
                data["amount"] = c[0]["amount"]
                if c[0]["armor_reduction"] is not None:
                    data["armor_reduction"] = c[0]["armor_reduction"]
        tf, tp = value(node, "TargetFilters")
        row = {"id": "Weapon:" + wid, "target": target_fact(ctx, wid, tf, tp),
               "range": scalar(ctx, "Weapon", wid, node, "Range"),
               "period": scalar(ctx, "Weapon", wid, node, "Period", "s"), "damage": damage,
               "effect": {"value": data, "unit": "", "status": "confirmed" if not errors else "unresolved",
                          "evidence": ev}, "attack_count": scalar(ctx, "Weapon", wid, node, "DisplayAttackCount")}
        if errors:
            row["effect"].update(value=None, reason="；".join(errors), raw_value=data)
        rate, rate_path = value(node, "RateMultiplier")
        if rate is not None:
            row["rate_multiplier"] = fact(ctx, number(rate), "Weapon", wid, [rate_path])
        result.append(row)
    return result


def costs(ctx, family, oid, node, prefix="", tag="Resource", supply_node=None, supply_id=None):
    rows = {}
    for key, index in (("minerals", "Minerals"), ("vespene", "Vespene")):
        matches = [(c, p) for c, p in children(node, prefix) if c.tag == tag and c.get("index") == index]
        rows[key] = fact(ctx, number(matches[0][0].get("value")), family, oid,
                         [matches[0][1]+"|@value"]) if matches else ctx.missing("未解析有效 " + index + " 成本；不默认零")
    if supply_node is not None:
        food, fp = value(supply_node, "Food")
        food = number(food)
        rows["supply"] = fact(ctx, -food if food is not None and food<0 else 0 if food is not None else None,
                              "Unit",supply_id,[fp],generated=food is not None and food>=0,
                              reason="有效 Food 未解析，不能推定人口成本")
    return rows


def index_relations(ctx):
    if hasattr(ctx, "_mechanics_index"):
        return ctx._mechanics_index
    all_units, all_abilities = nodes(ctx, "Unit"), nodes(ctx, "Abil")
    producers = defaultdict(list)
    for uid, node in all_units.items():
        for aid, path in refs(node, "AbilArray"):
            producers[aid].append((uid, path))
    production = defaultdict(list)
    for aid, node in all_abilities.items():
        if node.tag not in ("CAbilTrain", "CAbilWarpTrain", "CAbilBuild", "CAbilMorph", "CAbilMorphPlacement", "CAbilMerge", "CAbilArmMagazine"):
            continue
        for info, path in children(node):
            if info.tag != "InfoArray":
                continue
            targets = [info.get("Unit")] if info.get("Unit") else []
            targets += [v for p, v in fields(info, path) if "Unit" in p.split("|") or "MorphUnit" in p.split("|")]
            if not targets:
                target, _ = value(node, "MorphUnit")
                if target:
                    targets = [target]
            for uid in dict.fromkeys(targets):
                production[uid].append((aid, node, info, path, producers.get(aid, [])))
    result = all_units, all_abilities, producers, production
    ctx._mechanics_index = result
    return result


def production(ctx, uid, unit):
    result = []
    for aid, anode, info, prefix, producer_rows in index_relations(ctx)[3].get(uid, []):
        for producer, producer_path in producer_rows or [(None, None)]:
            row = {"producer": "Unit:"+producer if producer else None, "ability": "Abil:"+aid,
                   "command": info.get("index"),
                   "cost": costs(ctx, "Unit", uid, unit, tag="CostResource", supply_node=unit, supply_id=uid),
                   "time": fact(ctx, number(info.get("Time")), "Abil", aid, [prefix+"|@Time"], unit="s"),
                   "requirements": conditions(ctx, "Abil", aid, info, prefix),
                   "evidence": evidence(ctx, "Abil", aid, [p for p, v in fields(info, prefix)])}
            row["base_unit_cost"] = row["cost"]
            row["cost"] = {key:ctx.missing("整次生产费用尚未合成：需核对数量、单位单价、附加资源和 IgnoreUnitCost/IgnoreFood 等有效标志",
                                          evidence=v["evidence"] + row["evidence"])
                           for key,v in row["base_unit_cost"].items()}
            if anode.tag in ("CAbilMorph", "CAbilMorphPlacement", "CAbilMerge"):
                row["kind"] = "morph"
            # Per-command resources are increments beyond unit cost; expose separately.
            if any(c.tag == "Resource" for c in info):
                row["additional_cost"] = costs(ctx, "Abil", aid, info, prefix)
            count, count_path = value(info, "Count")
            if count is None:
                count = info.get("Count")
                count_path = prefix+"|@Count"
            else:
                count_path = prefix+"|"+count_path
            row["count"] = fact(ctx, number(count), "Abil", aid, [count_path], reason="未解析生产数量；不默认一人")
            explicit_units=[p for p,v in fields(info,prefix) if "Unit" in p.split("|") and v==uid]
            if count is None and len(explicit_units)>1:
                row["count"] = fact(ctx,len(explicit_units),"Abil",aid,explicit_units,generated=True)
                row["count"]["basis"]="本命令显式 Unit 数组中该目标重复出现的次数"
            if producer:
                row["evidence"] += evidence(ctx, "Unit", producer, [producer_path])
            flags={p:v for p,v in fields(anode) if "Flags" in p.split("|") and any(k in p for k in ("IgnoreUnitCost","IgnoreFood","IgnoreUnitFood"))}
            flags.update({p:v for p,v in fields(info,prefix) if "Flags" in p.split("|") and any(k in p for k in ("IgnoreUnitCost","IgnoreFood","IgnoreUnitFood"))})
            row["cost_flags"]={"fields":flags,"evidence":evidence(ctx,"Abil",aid,flags)}
            result.append(row)
    return result


def charges(ctx, aid, node):
    result = []
    for cost, cp in children(node):
        if cost.tag not in ("Cost", "InfoArray"):
            continue
        for ch, p in children(cost, cp):
            if ch.tag != "Charge":
                continue
            row = {"command": cost.get("index"), "evidence": evidence(ctx, "Abil", aid, [f for f,v in fields(cost, cp) if "Charge" in f.split("|")])}
            for key, tag in (("maximum", "CountMax"), ("initial", "CountStart"), ("use", "CountUse"), ("recharge", "TimeUse"), ("start_time", "TimeStart")):
                raw, rp = value(ch, tag)
                if raw is None and ch.get(tag) is not None:
                    raw, rp = ch.get(tag), "@"+tag
                row[key] = fact(ctx, number(raw), "Abil", aid, [p+"|"+rp if rp else None], unit="s" if "time" in key or key=="recharge" else "")
            row["link"] = fact(ctx, ch.get("Link"), "Abil", aid, [p+"|@Link"])
            if ch.get("Link") == "":
                row["link"] = ctx.na("源字段明确为空，不使用共享充能关联", evidence(ctx, "Abil", aid, [p+"|@Link"]))
            result.append(row)
    return result


def abilities(ctx, unit):
    result = []
    for aid, path in refs(unit, "AbilArray"):
        node = ctx.node("Abil", aid)
        if node is None:
            continue
        cost_vitals, cd_values, cd_paths = [], [], []
        cost_paths = []
        for p, v in fields(node):
            if "Cost" in p.split("|") and "Vital" in p.split("|") and p.endswith("|@value"):
                parts=p.split("|"); idx=parts[parts.index("Vital")+1]
                cost_vitals.append({"vital": idx, "amount": number(v)})
                cost_paths.append(p)
            if "Cooldown" in p.split("|") and p.split("|")[-1] in ("@TimeUse", "@Time", "@value"):
                cd_values.append(number(v)); cd_paths.append(p)
        roots = [v for p,v in fields(node) if p.split("|")[0]=="Effect" and p.endswith("|@value")]
        graphs, graph_ev, issues = [], [], []
        for root in roots:
            graph, ev, err = effect_graph(ctx, root); graphs.append(graph); graph_ev.extend(ev); issues.extend(err)
        effect = localized(ctx, "Abil", aid, "Tooltip")
        if effect["status"] == "unresolved" and graphs:
            effect={"value": "执行效果链已解析，详见机制结构", "unit":"", "status":"inferred" if not issues else "unresolved", "evidence":list(dict.fromkeys(graph_ev)), "basis":"根据有效 XML 执行链提供说明", "details":{"execution":graphs}}
            if issues:
                effect.update(value=None, raw_value={"execution":graphs}, reason="；".join(dict.fromkeys(issues)))
        command_rows=[]
        for command_node, command_path in children(node):
            if command_node.tag in ("InfoArray","CmdButtonArray"):
                command_rows.append({"command":command_node.get("index"),
                                     "conditions":combined_conditions(ctx,"Abil",aid,command_node,command_path),
                                     "evidence":evidence(ctx,"Abil",aid,[p for p,v in fields(command_node,command_path)])})
        global_condition=combined_conditions(ctx,"Abil",aid,node)
        if any(c.tag=="InfoArray" for c in node):
            global_condition=fact(ctx,"不同命令的需求分别列示，不能合并为共同释放条件","Abil",aid,
                                  [p for p,v in fields(node)],generated=True)
            global_condition["details"]=command_rows
        row = {"id":"Abil:"+aid, "name":localized(ctx,"Abil",aid,"Name"),
               "cost":fact(ctx,cost_vitals if cost_vitals else None,"Abil",aid,cost_paths,reason="未解析显式生命/能量成本；不默认零"),
               "cooldown":fact(ctx,cd_values[0] if len(cd_values)==1 else None,"Abil",aid,cd_paths,unit="s",reason="技能存在多条命令冷却或未解析明确冷却，详见各命令"),
               "effect":effect,"conditions":global_condition,"charges":charges(ctx,aid,node),"commands":command_rows}
        row["execution"] = graphs
        result.append(row)
    return result


def command_cards(ctx, uid, unit):
    result = []
    for card, cp in children(unit):
        if card.tag != "CardLayouts":
            continue
        row={"id":card.get("CardId") or ("root" if not result else "card:"+str(card.get("index") or len(result))), "buttons":[]}
        for button, bp in children(card, cp):
            if button.tag != "LayoutButtons":
                continue
            face=button.get("Face"); typ=button.get("Type")
            cmd=button.get("AbilCmd") or ""
            aid, _, command=cmd.partition(",")
            if not aid:
                aid=button.get("Ability") or ""
                command=button.get("AbilityCmd") or ""
            ev=evidence(ctx,"Unit",uid,[bp+"|@"+k for k in button.attrib if k!="index"])
            bn=ctx.node("Button",face) if face else None
            cond=conditions(ctx,"Unit",uid,button,bp)
            if bn is not None:
                cond+=conditions(ctx,"Button",face,bn)
            an=ctx.node("Abil",aid) if aid else None
            if an is not None:
                for part, pp in children(an):
                    if part.tag in ("InfoArray","CmdButtonArray") and part.get("index")==command:
                        cond+=conditions(ctx,"Abil",aid,part,pp)
            condition={"value":"；".join(c["value"] for c in cond),"unit":"","status":"inferred","basis":"根据命令绑定与有效条件图整理说明","details":cond,"evidence":list(dict.fromkeys(s for c in cond for s in c["evidence"]))} if cond and all(c["status"]!="unresolved" for c in cond) else ctx.missing("命令显示/使用条件未完整解析", evidence=list(dict.fromkeys(s for c in cond for s in c["evidence"])))
            if cond and condition["status"]=="unresolved":condition["raw_value"]=cond
            icon, ip=value(bn,"Icon"); hk,hp=value(bn,"Hotkey")
            b={"row":number(button.get("Row")),"column":number(button.get("Column")),"face":face,"type":typ,
               "ability":"Abil:"+aid if aid else None,"command":command or None,"submenu":button.get("SubmenuCardId"),
               "name":localized(ctx,"Button",face,"Name") if face else ctx.missing("命令按钮未声明 Face"),
               "description":localized(ctx,"Button",face,"Tooltip") if face else ctx.missing("命令按钮未声明 Face"),
               "icon":fact(ctx,icon,"Button",face,[ip]),"hotkey":fact(ctx,hk,"Button",face,[hp]),
               "conditions":condition,"evidence":ev}
            if hasattr(ctx, 'hotkey'):
                b['hotkey'] = ctx.hotkey(face, hk, b['hotkey'])
            # Invalid coordinates remain explicit for validation instead of inventing positions.
            if b["row"] not in range(3) or b["column"] not in range(5):b["coordinate_issue"]="原命令位置未解析或超出 3×5 范围"
            row["buttons"].append(b)
        result.append(row)
    return result


def research(ctx, uid, unit):
    result=[]
    for aid, ap in refs(unit,"AbilArray"):
        node=ctx.node("Abil",aid)
        if node is None or node.tag!="CAbilResearch":continue
        for info,prefix in children(node):
            if info.tag!="InfoArray":continue
            upid=info.get("Upgrade")
            if not upid:continue
            button=info.find("Button"); face=button.get("DefaultButtonFace") if button is not None else None
            bn=ctx.node("Button",face) if face else None
            icon,ip=value(bn,"Icon"); hk,hp=value(bn,"Hotkey")
            un=ctx.node("Upgrade",upid)
            row={"id":"Upgrade:"+upid,"ability":"Abil:"+aid,"command":info.get("index"),
                 "name":localized(ctx,"Upgrade",upid,"Name"),"description":localized(ctx,"Button",face,"Tooltip") if face else localized(ctx,"Upgrade",upid,"Tooltip"),
                 "icon":fact(ctx,icon,"Button",face,[ip]),"hotkey":fact(ctx,hk,"Button",face,[hp]),
                 "cost":costs(ctx,"Abil",aid,info,prefix),
                 "time":fact(ctx,number(info.get("Time")),"Abil",aid,[prefix+"|@Time"],unit="s"),
                 "requirements":conditions(ctx,"Abil",aid,info,prefix),"levels":[],
                 "evidence":evidence(ctx,"Abil",aid,[prefix+"|@Upgrade"])}
            if hasattr(ctx, 'hotkey'):
                row['hotkey'] = ctx.hotkey(face, hk, row['hotkey'])
            ml,mp=value(un,"MaxLevel")
            if number(ml) is not None and number(ml)>1:
                row["max_level"]=fact(ctx,number(ml),"Upgrade",upid,[mp])
            explicit_level=info.get("Level")
            if number(explicit_level) is not None:
                row["levels"]=[{"level":fact(ctx,number(explicit_level),"Abil",aid,[prefix+"|@Level"]),
                                 "cost":row["cost"],"time":row["time"]}]
            result.append(row)
    return result


def modifiers(ctx, uid, unit):
    result=[]
    for bid,bp in refs(unit,"BehaviorArray"):
        node=ctx.node("Behavior",bid)
        if node is None:continue
        effect=localized(ctx,"Behavior",bid,"Tooltip")
        if effect["status"]=="unresolved":
            effect=mechanical_summary(ctx,"Behavior",bid,node)
        result.append({"id":"Behavior:"+bid,"kind":"buff","condition":combined_conditions(ctx,"Behavior",bid,node),
                       "effect":effect,"mechanics":dict(fields(node)),"evidence":evidence(ctx,"Behavior",bid,[p for p,v in fields(node)])})
    relevant={"Unit:"+uid}
    relevant.update("Weapon:"+wid for wid,p in refs(unit,"WeaponArray"))
    for aid,p in refs(unit,"AbilArray"):relevant.add("Abil:"+aid)
    for wid,p in refs(unit,"WeaponArray"):
        wn=ctx.node("Weapon",wid); root,rp=value(wn,"Effect")
        graph,ev,err=effect_graph(ctx,root)
        relevant.update(e["id"] for e in graph["effects"])
    for upid,node in nodes(ctx,"Upgrade").items():
        matching=[]
        for c,p in children(node):
            if c.tag=="AffectedUnitArray" and (c.get("value")==uid or c.get("Link")==uid):matching.append(p)
            elif c.tag=="EffectArray":
                ref=c.get("Reference")
                catalog=c.get("Catalog"); entry=c.get("Entry")
                target=(ref.split(",")[:2] if ref else [catalog,entry])
                if len(target)==2 and ":".join(str(v) for v in target) in relevant:matching.append(p)
        if not matching:continue
        source_paths=[p for p,v in fields(node) if any(p.startswith(m+"|") for m in matching)]
        effect=localized(ctx,"Upgrade",upid,"Tooltip")
        if effect["status"]=="unresolved":
            effect=mechanical_summary(ctx,"Upgrade",upid,node,matching)
        result.append({"id":"Upgrade:"+upid,"kind":"upgrade",
                       "condition":fact(ctx,"研发该升级后应用其有效字段修改","Upgrade",upid,source_paths,generated=True),
                       "effect":effect,
                       "mechanics":{p:v for p,v in fields(node) if any(p.startswith(m+"|") for m in matching)},
                       "evidence":evidence(ctx,"Upgrade",upid,source_paths)})
    return result


def mechanical_summary(ctx,family,oid,node,prefixes=None):
    raw={p:v for p,v in fields(node) if (prefixes is None or any(p.startswith(m+"|") for m in prefixes))
         and any(t in p.split("|") for t in ("EffectArray","AffectedUnitArray","Modification","Duration","Period","PeriodicEffect","DamageResponse","InitialEffect","FinalEffect"))}
    if not raw:
        return ctx.missing("未提供可整理为效果说明的有效机制字段")
    labels={"LifeMax":"最大生命","ShieldsMax":"最大护盾","EnergyMax":"最大能量","LifeArmor":"护甲",
            "MovementSpeedMultiplier":"移动速度倍率","MoveSpeedMultiplier":"移动速度倍率","WeaponRange":"武器射程加成",
            "AttackSpeedMultiplier":"攻击速度倍率","DamageDealtFraction":"伤害加成","Duration":"持续时间",
            "Period":"周期","PeriodicEffect":"周期效果","Value":"数值","Operation":"操作",
            "Add":"增加","Subtract":"减少","Multiply":"乘以","Set":"设为","AffectedUnitArray":"影响单位"}
    parts=[]
    for c,p in children(node):
        if c.tag=="EffectArray" and (prefixes is None or p in prefixes):
            reference=c.get("Reference") or ",".join(c.get(k,"") for k in ("Catalog","Entry","Field"))
            op=labels.get(c.get("Operation"),c.get("Operation") or "修改")
            target=reference.split(",")
            target_text=" / ".join(labels.get(t,t) for t in target if t)
            amount=c.get("Value") or c.get("value")
            parts.append(target_text+"："+op+(" "+amount if amount is not None else "（数值未解析）"))
    if not parts:
        for p,v in raw.items():
            tokens=[t.removeprefix("@") for t in p.split("|") if not t.isdigit()]
            relevant=next((t for t in reversed(tokens) if t in labels),None)
            if relevant:
                parts.append(labels[relevant]+"："+labels.get(v,str(v)))
    result=fact(ctx,"；".join(parts[:12]) or "有效机制字段已提取，详见结构与来源",family,oid,raw,generated=True)
    if result["status"]!="unresolved":
        result.update(translation_draft=True,original_value=json.dumps(raw,ensure_ascii=False),source_locale="catalog")
        result["basis"]="根据有效 XML 机制字段编写的中文说明草稿，尚待审校"
    return result


def variants(ctx, uid, unit):
    result=[]; seen=set()
    for aid,ap in refs(unit,"AbilArray"):
        node=ctx.node("Abil",aid)
        if node is None:continue
        morph_train=node.tag=="CAbilTrain" and any(c.tag=="Flags" and c.get("index")=="KillOnFinish" and c.get("value")=="1" for c in node)
        if node.tag not in ("CAbilMorph","CAbilMorphPlacement","CAbilMerge") and not morph_train:continue
        for p,v in fields(node):
            if "Unit" not in p.split("|") and "MorphUnit" not in p.split("|"):continue
            if v==uid or v in seen or ctx.node("Unit",v) is None:continue
            seen.add(v)
            result.append({"id":"Unit:"+v,"label":localized(ctx,"Unit",v,"Name"),"kind":"morph",
                           "evidence":evidence(ctx,"Unit",uid,[ap])+evidence(ctx,"Abil",aid,[p])})
    return result


def enrich_entry(ctx, entry, uid):
    unit=ctx.node("Unit",uid)
    if unit is None:return
    entry["production"]=production(ctx,uid,unit)
    entry["weapons"]=weapons(ctx,unit)
    entry["abilities"]=abilities(ctx,unit)
    entry["modifiers"]=modifiers(ctx,uid,unit)
    presentation=entry.setdefault("presentation",{})
    presentation["command_cards"]=command_cards(ctx,uid,unit)
    presentation["research"]=research(ctx,uid,unit)
    presentation["variants"]=variants(ctx,uid,unit)
    for key,tag,unitname in (("life_regen","LifeRegenRate","/s"),("shield_regen","ShieldRegenRate","/s"),
                             ("energy_regen","EnergyRegenRate","/s"),("acceleration","Acceleration",""),
                             ("cargo_size","CargoSize",""),("supply","Food","")):
        raw,p=value(unit,tag)
        if raw is not None:
            entry.setdefault("stats",{})[key]=fact(ctx,abs(number(raw)) if key=="supply" and number(raw) is not None else number(raw),"Unit",uid,[p],unit=unitname)
    tags=[c.get("index") for c,p in children(unit) if c.tag=="Attributes" and c.get("value")=="1"]
    tagpaths=[p+"|@value" for c,p in children(unit) if c.tag=="Attributes" and c.get("value")=="1"]
    if tags:
        entry["tags"]=fact(ctx,[FILTER_NAMES.get(t,t) for t in tags],"Unit",uid,tagpaths,generated=True)
        if entry["tags"]["status"]!="unresolved":
            entry["tags"].update(original_value=",".join(tags),source_locale="und",translation_draft=True)


def refresh_effects(ctx, entry):
    """Refresh execution evidence only; keep reviewed costs, text, bindings and URLs."""
    uid = entry["id"].removeprefix("Unit:")
    unit = ctx.node("Unit",uid)
    if unit is None:
        return entry
    refreshed = {w["id"]:w for w in weapons(ctx,unit)}
    for row in entry.get("weapons",[]):
        if row["id"] in refreshed:
            for key in ("damage","effect"):
                row[key] = refreshed[row["id"]][key]
    for row in entry.get("abilities",[]):
        aid = row["id"].removeprefix("Abil:")
        node = ctx.node("Abil",aid)
        if node is None:
            continue
        roots = [(v,p) for p,v in fields(node) if p.split("|")[0]=="Effect" and p.endswith("|@value")]
        graphs, ev, issues = [], [], []
        for root,path in roots:
            graph, source_ids, errors = effect_graph(ctx,root)
            graphs.append(graph)
            ev += evidence(ctx,"Abil",aid,[path])+source_ids
            issues += errors
        row["execution"] = graphs
        if not graphs:
            continue
        previous = row.get("effect",{})
        generated = previous.get("value") == "执行效果链已解析，详见机制结构" or "执行链" in previous.get("basis","")
        if previous.get("status")=="unresolved" or generated:
            if issues:
                current = ctx.missing("；".join(dict.fromkeys(issues)),list(dict.fromkeys(ev)))
                current["raw_value"] = {"execution":graphs}
            else:
                current = {"value":"执行效果链已解析，详见机制结构","unit":"","status":"inferred",
                           "evidence":list(dict.fromkeys(ev)),"basis":"根据有效 XML 执行链提供说明",
                           "details":{"execution":graphs}}
            row["effect"] = current
        else:
            details = previous.setdefault("details",{})
            if isinstance(details,dict):
                details["execution"] = graphs
    for row in entry.get("modifiers",[]):
        if row.get("kind") != "buff":
            continue
        bid = row["id"].removeprefix("Behavior:")
        node = ctx.node("Behavior",bid)
        if node is None:
            continue
        row["mechanics"] = dict(fields(node))
        graph, ev, issues = behavior_effect_graph(ctx,bid)
        row.pop("execution",None)
        current = ctx.missing("；".join(issues),ev) if issues else {
            "value":graph,"unit":"","status":"confirmed","evidence":ev}
        if issues:
            current["raw_value"] = graph
        row["execution_fact"] = current
        details = row.get("effect",{}).get("details")
        if isinstance(details,dict):
            details.pop("execution",None)
            details.pop("execution_issues",None)
    return entry
