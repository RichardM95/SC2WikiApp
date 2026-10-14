"""Backup and publish only manifest-owned SCORE files; preserve user config."""
import argparse,hashlib,json,os,shutil,sys
from pathlib import Path
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()
def verify(root,files,skip=()):
    for n,d in files.items():
        f=(root/n).resolve()
        if n not in skip and (not f.is_relative_to(root) or not f.is_file() or sha(f)!=d):raise ValueError('Hash mismatch '+n)
def publish(project,preview,backup,baseline,acceptance,report):
    project,preview,backup,report=map(lambda p:Path(p).resolve(),(project,preview,backup,report))
    current=project/'products/SCORE-wiki'
    if any(not p.is_relative_to(project/'products') for p in (preview,backup)) or backup.exists() or report.exists() or not report.is_relative_to(project/'runtime/reports'):raise ValueError('Fresh owned product/report paths required')
    a=json.loads(Path(acceptance).read_text(encoding='utf-8'));m=json.loads((preview/'release-manifest.json').read_text(encoding='utf-8'))
    if a.get('validation')!='static validation passed' or a.get('offline_launcher')!='passed' or a.get('browser')!='passed' or a.get('batch')!=m['batch'] or a.get('release_manifest_sha256')!=sha(preview/'release-manifest.json'):raise ValueError('Acceptance does not match release')
    verify(preview,m['file_hashes'])
    protected=json.loads(Path(baseline).read_text(encoding='utf-8'))
    for n,d in protected.items():
        if not Path(n).is_file() or sha(Path(n))!=d:raise ValueError('Protected baseline changed '+n)
    shutil.copytree(current,backup)
    old={f.relative_to(current).as_posix():sha(f) for f in current.rglob('*') if f.is_file()}
    verify(backup,old)
    prior_file=backup/'release-manifest.json'
    prior=json.loads(prior_file.read_text(encoding='utf-8')) if prior_file.exists() else json.loads((backup/'manifest.json').read_text(encoding='utf-8'))
    owned=set(prior['file_hashes'])
    owned.update(n for n in ('manifest.json','release-manifest.json') if n in old)
    user_config='config/factions.json';preserved=user_config in old
    obsolete=owned-set(m['file_hashes'])-{'release-manifest.json',user_config}
    touched=[]
    try:
        for n in sorted(m['file_hashes'],key=lambda n:(n=='active.json',n)):
            if n==user_config and preserved:continue
            target=(current/n).resolve()
            if not target.is_relative_to(current):raise ValueError('Publication escaped current')
            target.parent.mkdir(parents=True,exist_ok=True);temporary=target.with_name(target.name+'.pending')
            if temporary.exists():raise ValueError('Unexpected pending file')
            shutil.copyfile(preview/n,temporary)
            if sha(temporary)!=m['file_hashes'][n]:raise ValueError('Copy hash mismatch')
            os.replace(temporary,target);touched.append(n)
        for n in sorted(obsolete):
            target=(current/n).resolve()
            if not target.is_relative_to(current):raise ValueError('Obsolete path escaped current')
            if target.is_file() and n in old:
                if sha(target)!=old[n]:raise ValueError('Old generated file changed')
                target.unlink();touched.append(n)
        shutil.copyfile(preview/'release-manifest.json',current/'release-manifest.json');touched.append('release-manifest.json')
        verify(current,m['file_hashes'],[user_config] if preserved else [])
        if preserved and sha(current/user_config)!=old[user_config]:raise ValueError('User config changed')
    except BaseException:
        for n in reversed(touched):
            target=current/n
            if n in old:target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(backup/n,target)
            elif target.is_file():target.unlink()
        raise
    result={'validation':'static validation passed','current':str(current),'backup':str(backup),'batch':m['batch'],'protected_files_verified':len(protected),'backup_hashes':old,'user_config_preserved':preserved,'updated_or_removed_files':len(touched)}
    report.parent.mkdir(parents=True,exist_ok=True)
    with report.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!='backup_hashes'},ensure_ascii=False))
def main():
    p=argparse.ArgumentParser()
    for n in ('project-dir','preview','backup','baseline','acceptance','report'):p.add_argument('--'+n,required=True)
    a=p.parse_args();publish(a.project_dir,a.preview,a.backup,a.baseline,a.acceptance,a.report)
if __name__=='__main__':sys.stdout.reconfigure(encoding='utf-8');main()
