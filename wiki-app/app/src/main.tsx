import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { PackContext, contentUrl, useResource, useSession, clearContentCache, api, getPreference, setPreference } from './service';
import { qualifiedRoute, parseNavigation, qualifyEntries, validateConfig, resolveQualified, visibleFactions } from './navigation.mjs';
import { UnitPage, ErrorBox } from './unit-page';
import { Overview } from './overview';
import { OtherPage, UpdatePage } from './system-pages';
import './style.css';
type Item = Record<string, any>;
function App() { const manifest = useResource<Item>('/manifest.json'); const configResource = useResource<Item>('/__app/config'); const [config, setConfig] = useState<any>(null); const [configWarning, setConfigWarning] = useState(''); const [hash, setHash] = useState(location.hash); const [showAudit, setAudit] = useState(() => getPreference('score.showEvidence', false)); const [auxiliary, setAuxiliary] = useState(() => getPreference('score.showAuxiliary', false)); const [showHidden, setHidden] = useState(() => getPreference('score.showHiddenFactions', false)); const [query, setQuery] = useState(''); const [branch, setBranch] = useState(''); const [global, setGlobal] = useState(false); const [message, setMessage] = useState(''); const [quitting, setQuitting] = useState(false); const changed = useCallback(() => { clearContentCache(); manifest.retry(); setMessage('数据版本已更新，当前页面已重新加载。'); }, [manifest.retry]); const serviceState = useSession(changed); const pack = manifest.data?.pack_id || ''; const index = useResource<Item>(pack ? contentUrl(pack, manifest.data?.files.index) : null); useEffect(() => { if (!configResource.data)
    return; try {
    setConfig(validateConfig(configResource.data.config));
    setConfigWarning(configResource.data.warning || '');
}
catch (e: any) {
    setConfigWarning(e.message);
} }, [configResource.data]); useEffect(() => { const handler = () => setHash(location.hash); addEventListener('hashchange', handler); return () => removeEventListener('hashchange', handler); }, []); const entries = useMemo(() => config && index.data ? qualifyEntries(index.data.entries, config) : [], [config, index.data]); const factions = useMemo(() => config ? visibleFactions(config, entries, showHidden).map((f: Item) => { const source = manifest.data?.factions.find((x: Item) => x.key === f.id) || manifest.data?.factions.find((x: Item) => entries.some((e: Item) => e.displayFaction === f.id && e.origin === x.key)); return { ...source, ...f }; }) : [], [config, entries, showHidden, manifest.data]); const nav = parseNavigation(hash, factions[0]?.id || 'Cerberus'); const found = nav.unit ? resolveQualified(entries, nav.source, nav.unit) : null; const selected = found?.member?.displayFaction || found?.entry.displayFaction || nav.faction; const configured = config?.factions.find((f: Item) => f.id === selected); const sourceFaction = manifest.data?.factions.find((f: Item) => f.key === (found?.entry.origin || nav.source)); const faction = { ...sourceFaction, ...(configured || factions.find((f: Item) => f.id === selected) || { name: '未归属' }), key: selected, origin: found?.entry.origin || nav.source }; useEffect(() => { setQuery(''); setBranch(''); }, [nav.faction]); useEffect(() => { if (found && selected !== nav.faction)
    location.replace(qualifiedRoute(selected, nav.unit, found.entry.origin)); }, [selected, nav.faction, nav.unit, found?.entry.origin]); async function reloadConfig() { try {
    const response = await api('config/reload');
    setConfig(validateConfig(response.config));
    setConfigWarning(response.warning || '');
    setMessage('阵营配置已重新加载。');
}
catch (e: any) {
    setConfigWarning(e.message);
} } async function quit() { try {
    await api('quit');
    setQuitting(true);
    setMessage('百科已退出，可以关闭此页面。');
}
catch (e: any) {
    setMessage(e.message);
} } function navigate(id: string) { location.hash = qualifiedRoute(id); } if (manifest.error || configResource.error)
    return <main><ErrorBox error={manifest.error || configResource.error} retry={() => { manifest.retry(); configResource.retry(); }}/><p>{serviceState}</p></main>; if (!manifest.data || !config)
    return <main><p role="status">正在加载百科…</p>{configWarning && <p role="alert">{configWarning}</p>}</main>; if (manifest.data.format_version !== 2 || manifest.data.min_client_version > 2)
    return <main><p role="alert">数据版本与当前程序不兼容，请更新完整离线包。</p></main>; return <PackContext.Provider value={pack}><div className={'app race-' + (faction.race || 'protoss')}><header className="topbar"><a className="brand" href={qualifiedRoute(factions[0]?.id || 'Cerberus')}>SCORE <span>星际百科</span></a><nav className="top-actions"><a href="#page=other">坠毁系统</a><a href="#page=update">数据更新</a><details className="settings"><summary>特别设置</summary><div className="settings-panel"><label><input type="checkbox" checked={showAudit} onChange={e => { setAudit(e.target.checked); setPreference('score.showEvidence', e.target.checked); }}/>显示数据与证据</label><label><input type="checkbox" checked={showHidden} onChange={e => { setHidden(e.target.checked); setPreference('score.showHiddenFactions', e.target.checked); }}/>显示隐藏阵营</label><button onClick={reloadConfig}>重新加载阵营配置</button><p>程序版本：{manifest.data.app_version || '4.0.0'}</p><p>数据版本：{manifest.data.batch}</p><p>运行状态：{quitting ? '已退出' : serviceState}</p></div></details><button className="quiet-button" disabled={quitting} onClick={quit}>退出百科</button></nav></header>{message && <p className="notice" role="status">{message}</p>}{!quitting && serviceState.startsWith('服务已断开') && <p className="error-box" role="alert">百科服务已停止，请双击启动器重新启动，然后刷新此页面。</p>}{configWarning && <p className="notice" role="alert">{configWarning}</p>}{configured?.defaultVisible === false && !showHidden && <p className="notice">此阵营默认隐藏。直达词条仍可查看，可在特别设置中显示隐藏阵营。</p>}{entries.some((e: Item) => e.displayFaction === 'Unassigned') && <p className="notice">部分对象的前缀未配置，已放入“未归属”。请编辑 config/factions.json 后重新加载。</p>}<main>{quitting ? <p>百科已退出，可以关闭此页面。</p> : nav.page === 'update' ? <UpdatePage changed={changed}/> : nav.page === 'other' ? <OtherPage pack={pack} path={manifest.data.files.other || 'data/other.json'} audit={showAudit}/> : index.error ? <ErrorBox error={index.error} retry={index.retry}/> : !index.data ? <p role="status">正在加载目录…</p> : nav.unit ? <UnitPage key={pack + '|' + faction.origin + '|' + nav.unit} faction={faction} id={nav.unit} index={entries} audit={showAudit} navigate={navigate}/> : <Overview factions={factions} faction={faction} entries={entries} query={query} setQuery={setQuery} branch={branch} setBranch={setBranch} auxiliary={auxiliary} setAuxiliary={(value: boolean) => { setAuxiliary(value); setPreference('score.showAuxiliary', value); }} global={global} setGlobal={setGlobal} navigate={navigate}/>}</main><footer>SCORE 星际百科{showAudit && <span> · {manifest.data.batch}</span>}</footer></div></PackContext.Provider>; }
createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
