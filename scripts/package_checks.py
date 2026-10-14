"""Actual Windows EXE lifecycle, configuration and offline-update checks in owned copy."""
import argparse,hashlib,http.client,json,os,shutil,socket,subprocess,time,uuid,zipfile
from pathlib import Path
def check(ok,msg):
    if not ok:raise AssertionError(msg)
def request(port,path,body=None,method=None,token=None,kind='application/json'):
    c=http.client.HTTPConnection('127.0.0.1',port,timeout=20)
    headers={'Origin':f'http://127.0.0.1:{port}'}
    if body is not None:
        headers['Content-Type']=kind
        if kind=='application/json':body=json.dumps(body).encode()
    if token:headers['X-SCORE-Token']=token
    try:
        c.request(method or ('POST' if body is not None else 'GET'),path,body,headers);r=c.getresponse();raw=r.read()
        return r.status,json.loads(raw) if raw else None
    finally:c.close()
def wait_health(port,proc,timeout=10):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        check(proc.poll() is None,'Process exited before ready')
        try:
            status,h=request(port,'/__app/health')
            if status==200:return h
        except (OSError,ValueError):pass
        time.sleep(.05)
    raise AssertionError('Startup timeout')
def session(port,token,name):
    c=http.client.HTTPConnection('127.0.0.1',port,timeout=10)
    c.request('POST','/__app/session',json.dumps({'token':token,'client_id':name}),{'Origin':f'http://127.0.0.1:{port}','Content-Type':'application/json'})
    r=c.getresponse();check(r.status==200,'Session rejected');check(json.loads(r.readline())['event']=='hello','Missing session hello')
    return c,r
def pack_zip(pack,batch,corrupt=False,traversal=False,incompatible=False):
    m=json.loads((pack/'manifest.json').read_text(encoding='utf-8'));files={n:(pack/n).read_bytes() for n in m['file_hashes']}
    index=json.loads(files[m['files']['index']]);index['entries'][0]['name']+='（更新测试）'
    files[m['files']['index']]=json.dumps(index,ensure_ascii=False,separators=(',',':')).encode()
    m['batch']=batch;m['file_hashes']={n:hashlib.sha256(b).hexdigest() for n,b in files.items()}
    m['pack_id']=batch+'-'+hashlib.sha256(json.dumps(m['file_hashes'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:12]
    if incompatible:m['min_client_version']=99
    if corrupt:files[m['files']['index']]+=b' '
    from io import BytesIO
    stream=BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as z:
        z.writestr('manifest.json',json.dumps(m,ensure_ascii=False,separators=(',',':')))
        for n,b in files.items():z.writestr(n,b)
        if traversal:z.writestr('../escaped.txt','bad')
    return stream.getvalue(),m
def main():
    p=argparse.ArgumentParser()
    for n in ('project-dir','package','report'):p.add_argument('--'+n,required=True)
    a=p.parse_args();project=Path(a.project_dir).resolve();source=Path(a.package).resolve();report=Path(a.report).resolve()
    check(report.is_relative_to(project/'runtime/reports') and not report.exists(),'Fresh owned report required')
    root=project/'runtime/tmp'/('SCORE  实包 验收-'+str(uuid.uuid4()));check(root.is_relative_to(project/'runtime/tmp'),'Unsafe copy');shutil.copytree(source,root)
    exe=root/'SCORE百科.exe';active=json.loads((root/'active.json').read_text(encoding='utf-8'));pack=root/'packs'/active['current']
    env=dict(os.environ,PATH='',LOCALAPPDATA=str(root/'test-cache'));cases=[];processes=[];connections=[]
    def launch(port,grace=30,start=300):
        proc=subprocess.Popen([str(exe),'--headless','--no-browser','--port',str(port),'--idle-grace-seconds',str(grace),'--startup-timeout-seconds',str(start)],cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE);processes.append(proc);return proc
    def passed(name,details=None):cases.append({'case':name,'status':'passed','details':details});print(name,flush=True)
    try:
        port=18782;proc=launch(port);h=wait_health(port,proc);token=h['token'];check(h['client_version']==2,'Wrong client');check(Path(h['root']).resolve()==root,'Wrong relative root')
        passed('Chinese_space_path_empty_PATH_relative_root',{'batch':h['batch']})
        c,r=session(port,token,'qa-a');connections.append((c,r));c2,r2=session(port,token,'qa-b');connections.append((c2,r2))
        duplicate=launch(port);duplicate.wait(timeout=8);check(duplicate.returncode==0,'Duplicate process failed');passed('duplicate_reuses_service')
        _,cfg=request(port,'/__app/config');check(len(cfg['config']['factions'])==18,'Wrong initial faction count')
        config=root/'config/factions.json';original=config.read_bytes();config.write_text('{bad',encoding='utf-8')
        status,bad=request(port,'/__app/config/reload',{'token':token});check(status>=400,'Invalid config accepted');_,still=request(port,'/__app/config');check(still['config']==cfg['config'],'Invalid config replaced valid')
        modified=json.loads(original);next(x for x in modified['factions'] if x['id']=='Junker')['defaultVisible']=False;config.write_text(json.dumps(modified,ensure_ascii=False),encoding='utf-8')
        status,new=request(port,'/__app/config/reload',{'token':token});check(status==200,'Config reload failed');saved=config.read_bytes();passed('config_reload_invalid_retains_valid_then_hide')
        for name,args in [('bad_hash',{'corrupt':True}),('traversal',{'traversal':True}),('incompatible_client',{'incompatible':True})]:
            blob,_=pack_zip(pack,'v4-qa-'+name,**args);status,result=request(port,'/__app/update/check',blob,token=token,kind='application/zip');check(status>=400,'Bad package accepted '+name);_,state=request(port,'/__app/update/status');check(state['current']['pack_id']==active['current'],'Bad package activated');passed(name)
        blob,m=pack_zip(pack,'v4-qa-update');status,staged=request(port,'/__app/update/check',blob,token=token,kind='application/zip');check(status==200,'Valid ZIP check failed '+str(staged));_,state=request(port,'/__app/update/status');check(state['current']['pack_id']==active['current'],'Check prematurely activated')
        status,result=request(port,'/__app/update/install',{'token':token,'staging_id':staged['staging_id']});check(status==200,'Install failed '+str(result));_,state=request(port,'/__app/update/status');check(state['current']['pack_id']==m['pack_id'],'New pointer missing');check(state['previous']['pack_id']==active['current'],'Previous snapshot lost');check(config.read_bytes()==saved,'User config changed');passed('check_then_install_and_config_preservation')
        status,oldjson=request(port,'/__content/'+active['current']+'/'+json.loads((pack/'manifest.json').read_text(encoding='utf-8'))['files']['index']);check(status==200,'Pinned old pack unavailable');passed('version_pinned_old_content_retained')
        status,result=request(port,'/__app/update/rollback',{'token':token});check(status==200,'Rollback failed');_,state=request(port,'/__app/update/status');check(state['current']['pack_id']==active['current'],'Rollback wrong version');check(config.read_bytes()==saved,'Rollback changed config');passed('rollback_exact_previous')
        status,_=request(port,'/__app/quit',{'token':'bad'});check(status==403,'Bad quit token accepted')
        status,_=request(port,'/__app/quit',{'token':token});check(status==200,'Quit rejected');proc.wait(timeout=10);check(proc.returncode==0,'Quit exit error');passed('explicit_quit_stops_process')
        for conn,response in connections:response.close();conn.close()
        connections.clear()
        idle=launch(port,1,10);hh=wait_health(port,idle);cx,rx=session(port,hh['token'],'one');cy,ry=session(port,hh['token'],'two');rx.close();cx.close();time.sleep(1.5);check(idle.poll() is None,'Closing one page stopped other')
        ry.close();cy.close();idle.wait(timeout=8);check(idle.returncode==0,'Last close failed');passed('two_pages_one_close_survives_last_close_exits')
        no_page=launch(port,1,2);wait_health(port,no_page);no_page.wait(timeout=8);check(no_page.returncode==0,'No-page deadline failed');passed('startup_without_browser_page_exits')
        blocker=socket.socket();blocker.bind(('127.0.0.1',port));blocker.listen()
        try:
            conflict=launch(port,1,2);wait_health(port+1,conflict);conflict.wait(timeout=8);passed('occupied_port_falls_back')
        finally:blocker.close()
        status='passed'
    except BaseException as exc:
        cases.append({'case':'remaining_scenarios','status':'failed','error':str(exc)});status='failed'
        raise
    finally:
        for conn,response in connections:
            try:response.close();conn.close()
            except Exception:pass
        for proc in processes:
            if proc.poll() is None:proc.terminate();proc.wait(timeout=10)
        result={'status':status,'cases':cases,'test_copy':str(root),'exe_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),'scope':'Actual Windows EXE with no runtime PATH dependencies; task-owned data only'}
        report.parent.mkdir(parents=True,exist_ok=True)
        with report.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({'status':status,'cases':len(cases),'report':str(report)},ensure_ascii=False))
if __name__=='__main__':main()
