"""Assemble an immutable  content pack with the separately versioned React client."""
import argparse,hashlib,json,os,shutil,subprocess,sys,uuid
from pathlib import Path
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()
def main():
    p=argparse.ArgumentParser()
    for n in ('project-dir','output','content-pack','frontend-dist','launcher-dir','go-exe','default-config','hub-root'):p.add_argument('--'+n,required=True)
    a=p.parse_args();project=Path(a.project_dir).resolve();out=Path(a.output).resolve();pack=Path(a.content_pack).resolve()
    if not out.is_relative_to(project/'products') or out.exists():raise ValueError('Fresh product output required')
    m=json.loads((pack/'manifest.json').read_text(encoding='utf-8'))
    if m['format_version']!=2 or m['project_id']!='SCORE-fa82e2d014':raise ValueError('Incompatible content pack')
    for n,h in m['file_hashes'].items():
        f=(pack/n).resolve()
        if not f.is_relative_to(pack) or sha(f)!=h:raise ValueError('Content hash mismatch '+n)
    out.mkdir(parents=True);shutil.copytree(pack,out/'packs'/m['pack_id'])
    dist=Path(a.frontend_dist).resolve()
    for f in dist.rglob('*'):
        if f.is_file():
            t=out/f.relative_to(dist);t.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(f,t)
    (out/'config').mkdir();(out/'defaults').mkdir()
    for dest in ('config/factions.json','defaults/factions.json'):shutil.copyfile(a.default_config,out/dest)
    (out/'active.json').write_text(json.dumps({'version':1,'current':m['pack_id'],'previous':None}),encoding='utf-8')
    tmp=Path(a.hub_root)/'tmp/score-wiki/go-build'/str(uuid.uuid4());tmp.mkdir(parents=True)
    env=dict(os.environ,GOCACHE=str(tmp.parent/'cache'),GOTMPDIR=str(tmp),TEMP=str(tmp),TMP=str(tmp),CGO_ENABLED='0',GOOS='windows',GOARCH='amd64')
    subprocess.run([a.go_exe,'build','-trimpath','-ldflags=-s -w -H=windowsgui','-o',str(tmp/'SCORE百科.exe'),'.'],cwd=a.launcher_dir,env=env,check=True)
    shutil.copyfile(tmp/'SCORE百科.exe',out/'SCORE百科.exe')
    appfiles={f.relative_to(out).as_posix():sha(f) for f in out.rglob('*') if f.is_file() and f.relative_to(out).parts[0] not in ('packs','config') and f.name not in ('active.json','SCORE百科.exe')}
    app={'format_version':2,'app_version':'4.0.0','client_version':2,'content_format_version':2,'project_id':m['project_id'],'file_hashes':appfiles}
    (out/'app-manifest.json').write_text(json.dumps(app,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'使用说明.md').write_text('''# SCORE 百科

完整解压后双击 SCORE百科.exe，无需开发工具或游戏。启动器控制窗口显示运行状态；关闭窗口或点击退出可停止服务。最后一个百科页面关闭后约30秒自动停止。浏览器回收后台页面后若提示服务已停止，请重新双击启动。

config/factions.json 控制阵营前缀、中文名、顺序和默认显隐。保存后在特别设置选择“重新加载阵营配置”。隐藏不删除词条；技术对象不会被配置恢复。错误配置保留上一份有效值，按页面提示修正。

在“数据更新”页选择可信的 SCORE 数据 ZIP，校验后查看摘要，再确认安装。更新不覆盖个人配置；“回退上一版”恢复上一个完整数据包。客户端版本不兼容时需下载完整新版程序包。不要手动混合 packs 内文件或在运行中编辑 active.json。目录不可写时移至可写文件夹，不自动提权。

数据、图片和程序均在本机；仅监听127.0.0.1，默认18765，端口占用后18766–18775。再次启动同一目录复用服务。数据更新包不能替换EXE、网页脚本或个人配置。

出现错误请查看用户本地缓存 SCOREWiki/launcher.log。静态来源解析不能替代 SC2 编辑器或游戏运行证据。
''',encoding='utf-8')
    release={'batch':m['batch'],'pack_id':m['pack_id'],'app_version':'4.0.0','file_hashes':{f.relative_to(out).as_posix():sha(f) for f in out.rglob('*') if f.is_file()}}
    (out/'release-manifest.json').write_text(json.dumps(release,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'output':str(out),'pack_id':m['pack_id'],'files':len(release['file_hashes'])},ensure_ascii=False))
if __name__=='__main__':sys.stdout.reconfigure(encoding='utf-8');main()

