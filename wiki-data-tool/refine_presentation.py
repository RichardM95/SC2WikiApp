"""Evidence-only classification and explicitly marked descriptions for this draft."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from catalog_generate import load_validator
from mechanics import children, fields, number
from refine_costs import SUFFIX, atomic_replace, target_path


class LiteralPool:
    """Read a few literal catalogs; never rebuild or substitute an effective catalog."""
    def __init__(self):self.cache={}

    def read(self,root,path):
        root=Path(root).resolve();file=(root/path).resolve()
        if not file.is_relative_to(root):raise ValueError("Source path escaped component")
        if file not in self.cache:
            blob=file.read_bytes()
            parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True,insert_pis=True))
            catalog=ET.fromstring(blob,parser=parser)
            # Catalog ordinals count root text/comments as well as typed elements.
            ordinal=1 if catalog.text else 0
            rows={}
            for node in catalog:
                if isinstance(node.tag,str) and node.get("id"):
                    rows.setdefault(node.get("id"),[]).append((node,ordinal))
                ordinal+=1
                if node.tail:ordinal+=1
            self.cache[file]=(rows,hashlib.sha256(blob).hexdigest())
        return self.cache[file]


class Sources:
    def __init__(self,data,pool):
        self.data=data;self.pool=pool;self.rows=data["sources"]
        self.keys={(s.get("source_id"),s["path"],s["object"],s["field"],s.get("ordinal"),s.get("locale")):s["id"] for s in self.rows}
        self.roots={Path(p).name.casefold():Path(p) for p in data["project"]["component_roots"]}
        self.components={s["id"]:s for s in data["dependency_context"]["sources"]}
        self.primary=data["project"]["primary_mod"]
        self.sid="project:"+self.primary

    def add(self,sid,path,oid,field,ordinal,sha):
        for suffix in ("|@value","|@Value","|@Link"):
            if field.endswith(suffix):field=field[:-len(suffix)];break
        key=(sid,path,oid,field,ordinal,None)
        if key in self.keys:return self.keys[key]
        next_id="s%05d"%(max((int(s["id"][1:]) for s in self.rows if s["id"].startswith("s") and s["id"][1:].isdigit()),default=0)+1)
        component=self.components[sid]["name"]
        self.rows.append(dict(id=next_id,component=component,path=path,object=oid,field=field,sha256=sha,
                              source_class="local_mod" if sid==self.sid else "active_component_dependency",source_id=sid,ordinal=ordinal))
        self.keys[key]=next_id
        return next_id

    def local_unit(self,uid):
        anchor=next((s for s in self.rows if s.get("source_id")==self.sid and s["object"]=="Unit:"+uid and s["field"]=="@id"),None)
        if anchor is None:return None
        rows,sha=self.pool.read(self.roots[self.primary.casefold()],anchor["path"])
        matches=rows.get(uid,[])
        if len(matches)!=1 or matches[0][1]!=anchor["ordinal"] or sha!=anchor["sha256"]:
            raise ValueError("Local Unit source/ordinal changed: "+uid)
        node,ordinal=matches[0]
        return node,lambda p:self.add(self.sid,anchor["path"],"Unit:"+uid,p,ordinal,sha)

    def kill_chain(self,eid,seen=()):
        if not eid or eid in seen or len(seen)>16:return []
        # Only explicit terminal kill flags and explicit effect edges qualify.
        for root in self.data["project"]["component_roots"]:
            root=Path(root)
            component=next((s for s in self.components.values() if s["name"].casefold()==root.name.casefold()),None)
            if component is None:continue
            path="Base.SC2Data/GameData/EffectData.xml"
            if not (root/path).is_file():continue
            rows,sha=self.pool.read(root,path)
            matches=rows.get(eid,[])
            if not matches:continue
            if len(matches)!=1:return []
            node,ordinal=matches[0]
            raw=dict(fields(node))
            for p,v in raw.items():
                if p in ("Flags|Kill|@value","Flags|Kill|@Value") and v=="1":
                    return [self.add(component["id"],path,"Effect:"+eid,p,ordinal,sha)]
            result=[]
            for c,p in children(node):
                if c.tag in ("EffectArray","Effect","FinalEffect","ExpireEffect","ImpactEffect") and c.get("value"):
                    killed=self.kill_chain(c.get("value"),seen+(eid,))
                    if killed:
                        result+=killed+[self.add(component["id"],path,"Effect:"+eid,p+"|@value",ordinal,sha)]
            return list(dict.fromkeys(result))
        return []


def source_ids(data,obj,field):
    return [s["id"] for s in data["sources"] if s["object"]==obj and s["field"]==field]


def creators(data):
    out={}
    def visit(value):
        if isinstance(value,dict):
            if value.get("type")=="CEffectCreateUnit":
                for p,uid in value.get("fields",{}).items():
                    if p.split("|")[0] in ("UnitType","Unit","SpawnUnit") and isinstance(uid,str):
                        ev=source_ids(data,value["id"],p.removesuffix("|@value").removesuffix("|@Link"))
                        if ev:out.setdefault(uid,[]).extend(ev)
            for item in value.values():visit(item)
        elif isinstance(value,list):
            for item in value:visit(item)
    visit(data["entries"])
    return {uid:list(dict.fromkeys(ev)) for uid,ev in out.items()}


def classify_entry(entry,sources,created):
    old=entry["presentation"]["classification"]
    if old["value"]=="auxiliary":return None
    if old["value"]=="other" and not old["reason"].startswith("实际 CreateUnit"):return None
    uid=entry["id"].split(":",1)[1];local=sources.local_unit(uid)
    if local is None:return None
    node,add=local;raw=dict(fields(node))
    flags={c.get("index"):(c.get("value"),p+"|@value") for c,p in children(node) if c.tag=="FlagArray"}
    def yes(name):return flags.get(name,(None,None))[0]=="1"
    def ev(names):return [add(flags[n][1]) for n in names if yes(n)]
    lifetime=[]
    for mod in entry.get("modifiers",[]):
        if mod.get("kind")!="buff":continue
        fields_=mod.get("mechanics",{})
        duration=number(fields_.get("Duration|@value"))
        if duration is None or duration<=0:continue
        for field in ("FinalEffect","ExpireEffect"):
            terminal=fields_.get(field+"|@value")
            kill=sources.kill_chain(terminal)
            duration_ev=source_ids(sources.data,mod["id"],"Duration")
            terminal_ev=source_ids(sources.data,mod["id"],field)
            if kill and duration_ev and terminal_ev:
                lifetime+=duration_ev+terminal_ev+kill
    if yes("Uncommandable") and yes("Unselectable"):
        return dict(value="auxiliary",reason="本地对象明确不可下达指令且不可选择，归为技术/效果载体；保留附录",evidence=ev(("Uncommandable","Unselectable")))
    if uid in created and lifetime:
        return dict(value="other",reason="实际 CreateUnit 创建且挂载有限时长、结束时执行 Kill 的行为；归为临时召唤/效果对象",evidence=list(dict.fromkeys(created[uid]+lifetime)))
    if yes("Worker") and not yes("Uncommandable") and not yes("Unselectable"):
        return dict(value="unit",reason="本地对象明确标记 Worker，属于工作单位",evidence=ev(("Worker",)))
    return None


def description_draft(entry,sources):
    if entry["description"]["status"]!="unresolved":return False
    phrases=[];ev=[];original={}
    for key,label in (("life","基础生命"),("shields","基础护盾"),("armor","基础护甲"),("speed","基础移动速度")):
        f=entry.get("stats",{}).get(key,{})
        if f.get("status") in ("confirmed","inferred") and number(f.get("value")) is not None and f.get("evidence"):
            phrases.append(label+"为"+str(f["value"]))
            ev+=f["evidence"];original[key]=f["value"]
    # Existing modifier descriptions are eligible only with an explicit attachment.
    local=sources.local_unit(entry["id"].split(":",1)[1])
    if local:
        node,add=local
        links={"Behavior:"+c.get("Link"):p+"|@Link" for c,p in children(node) if c.tag=="BehaviorArray" and c.get("Link")}
        for mod in entry.get("modifiers",[]):
            f=mod.get("effect",{})
            if mod["id"] in links and f.get("status") in ("confirmed","inferred") and isinstance(f.get("value"),str) and f.get("evidence"):
                phrases.append("挂载行为的已记录效果："+f["value"])
                ev+=f["evidence"]+[add(links[mod["id"]])];original[mod["id"]]=mod.get("mechanics",{})
                break
    if not phrases or not ev:return False
    entry["description"]=dict(value="机制说明草稿："+"；".join(phrases)+"。",unit="",status="inferred",
        evidence=list(dict.fromkeys(ev)),translation_draft=True,source_locale="catalog",
        original_value=json.dumps(original,ensure_ascii=False),basis="原组件未提供已解析的说明文案；根据该对象有效数值及明确挂载行为编写的中文草稿，尚待审校，不作为官方说明确认")
    return True


def refine(data,pool):
    sources=Sources(data,pool);created=creators(data);changes=[];drafts=[]
    for entry in data["entries"]:
        new=classify_entry(entry,sources,created)
        if new and new != entry['presentation']['classification']:
            entry["presentation"]["classification"]=new
            changes.append(dict(id=entry["id"],classification=new["value"],reason=new["reason"]))
        if description_draft(entry,sources):drafts.append(entry["id"])
    return changes,drafts


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for key in ("source-root","project-dir","batch-suffix","toolbox-root","game-root","gamedata-root","hub-root"):
        p.add_argument("--"+key,required=True)
    p.add_argument("--dry-run",action="store_true")
    args=p.parse_args(argv)
    if args.batch_suffix!=SUFFIX:p.error("Only this task's isolated draft is permitted")
    project=Path(args.project_dir).resolve();validator=load_validator(Path(args.hub_root).resolve())
    manifest=json.loads((project/"runtime/catalog/score-factions.json").read_text(encoding="utf-8-sig"))
    pool=LiteralPool();pending=[]
    for faction in manifest["factions"]:
        if faction["key"]=="SCORE-Other":continue
        path=target_path(project,faction,args.batch_suffix);original=path.read_bytes();data=json.loads(original.decode("utf-8-sig"))
        if data["project"].get("batch")!=SUFFIX or data["project"]["primary_mod"]!=faction["component"] or Path(data["project"]["source_root"]).resolve()!=Path(args.source_root).resolve():raise ValueError("Draft identity mismatch")
        changes,drafts=refine(data,pool);validator.validate(data)
        blob=json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False).encode("utf-8")
        pending.append((path,original,blob))
        print(json.dumps(dict(faction=faction["key"],changes=changes,description_drafts=drafts,classes=dict(Counter(e["presentation"]["classification"]["value"] for e in data["entries"]))),ensure_ascii=False),flush=True)
    if args.dry_run:return
    for path,original,blob in pending:
        if path.read_bytes()!=original:raise ValueError("Draft changed during prevalidation")
    written=[]
    try:
        for path,original,blob in pending:atomic_replace(path,blob);written.append((path,original))
    except BaseException:
        for path,original in reversed(written):atomic_replace(path,original)
        raise


if __name__=="__main__":
    if hasattr(sys.stdout,"reconfigure"):sys.stdout.reconfigure(encoding="utf-8")
    main()
