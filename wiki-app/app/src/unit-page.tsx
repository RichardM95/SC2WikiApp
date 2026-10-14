import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { createPortal } from 'react-dom';
import { plain, scalar, factValue, parseRoute, isBasicAbility, matchEntry, commandSlots, memberPath, factParts, hasModifierBody, weaponDamageSummary, formNames, resourceAmount, abilityPresentation } from './logic.mjs';
import { PackContext, contentUrl, useResource } from './service';
import { qualifiedRoute as route, resolveQualified } from './navigation.mjs';
type Item = Record<string, any>;
type Manifest = {
    format_version: number;
    batch: string;
    min_client_version: number;
    factions: Item[];
    files: {
        index: string;
    };
    public_count: number;
};
const labels: Record<string, string> = { life: '生命值', shields: '护盾值', energy: '能量值', armor: '护甲', speed: '移动速度', sight: '视野', radius: '半径', life_regen: '生命恢复', shield_regen: '护盾恢复', energy_regen: '能量恢复', acceleration: '加速度', cargo_size: '载物尺寸', supply: '人口', minerals: '晶体矿', vespene: '高能瓦斯', time: '时间', cooldown: '冷却时间', charges: '充能', cost: '消耗', maximum: '上限', max: '上限', initial: '初始', start: '初始', use: '消耗', recovery: '恢复', recharge: '恢复时间', start_time: '首次恢复时间', damage: '伤害', amount: '基础伤害', bonus: '属性加成', count: '次数', period: '攻击间隔', range: '射程', target: '目标', duration: '持续时间', conditions: '条件', requirements: '科技要求', effect: '效果', effects: '效果', components: '伤害组成', schedule: '时序', interval: '间隔', armor_reduction: '护甲结算', fraction: '比例', attributes: '属性', Light: '轻甲', Armored: '重甲', Mechanical: '机械', Biological: '生物', Structure: '建筑', Massive: '重型', Psionic: '灵能', Heroic: '英雄', Ground: '地面', Air: '空中', Ranged: '远程', Melee: '近战', Splash: '范围', text: '说明', description: '说明', summary: '机制', branches: '条件分支', actual: '实际效果', display: '面板效果', events: '触发事件', trigger: '触发条件', execution: '执行机制', production: '生产', Research1: '研究', level: '等级', name: '名称', total: '总计', initial_time: '初始时间', offset: '偏移', offsets: '偏移', area: '区域', areas: '区域', flags: '标志', occurrence: '触发序号', event: '触发事件', timing: '触发时机', phase: '执行阶段', location: '位置', target_context: '作用目标', target_count: '目标数量', minimum: '下限', random: '随机机制', random_periods: '随机周期', random_offsets: '随机偏移', selection: '选择方式', initial_effect: '初始效果', periodic_effect: '周期效果', final_effect: '结束效果', damage_response: '受伤响应', death_response: '死亡响应', stages: '阶段', end: '结束' };
const statOrder = ['shields', 'life', 'energy', 'armor', 'speed', 'sight', 'radius', 'cargo_size', 'supply', 'life_regen', 'shield_regen', 'energy_regen', 'acceleration'];
const unitLabels: Record<string, string> = { s: '秒', game_seconds: '秒', life: '点', energy: '点', shields: '点', armor: '点', '/s': '点/秒', catalog_distance: '码', catalog_speed: '' };
const statusLabels: Record<string, string> = { confirmed: '已确认', inferred: '推导', unresolved: '未解析', not_applicable: '不适用' };
const debugKeys = new Set(['id', 'path', 'fields', 'raw_value', 'raw_field', 'evidence', 'source', 'sources', 'basis', 'reason', 'key', 'original_value', 'original_reference', 'source_locale', 'source_language', 'status', 'translation_draft', 'execution_root', 'effect', 'persistent', 'search', 'link', 'type', 'kind', 'structure', 'metadata', 'issues', 'command', 'member_field', 'cost_scope', 'cost_assumptions', 'owner', 'inherited', 'selector', 'selectors', 'field', 'raw_fields', 'raw_template', 'dynamic_text']);
function isFact(v: any): v is Item { return !!v && typeof v === 'object' && 'value' in v && 'status' in v; }
function safeText(v: any): string { const t = plain(v); return /(?:Unit|Abil|Effect|Behavior|Validator|Weapon|Upgrade|Requirement|Button|Model|Actor):/.test(t) ? '相关对象' : labels[t] || t; }
function imagePath(v: any): string | null { const value = factValue(v); return typeof value === 'string' && /\.(png|jpe?g|webp|gif|svg)$/i.test(value) ? value : null; }
export function Image({ src, name, portrait = false }: {
    src: any;
    name: string;
    portrait?: boolean;
}) { const [failed, setFailed] = useState(false); const pack = React.useContext(PackContext); const path = imagePath(src); useEffect(() => setFailed(false), [path]); return path && !failed ? <img className={portrait ? 'portrait-image' : 'game-image'} src={contentUrl(pack, path)} alt={name} loading="lazy" onError={() => setFailed(true)}/> : <span className={portrait ? 'portrait-placeholder' : 'image-placeholder'} role="img" aria-label={name + '：暂无' + (portrait ? '肖像' : '图标')}>{portrait ? '暂无肖像' : '暂无图标'}</span>; }
function Value({ item, audit = false }: {
    item: any;
    audit?: boolean;
}) { const { overall: value, partial } = factParts(item); if (value == null && partial != null)
    return <span className="value partial-value"><span className="muted">无（整体值）</span><span className="partial-heading">已解析部分：</span><Value item={partial} audit={audit}/></span>; if (value == null)
    return <span className="value muted">无{audit && isFact(item) && <small className="fact-status">{statusLabels[item.status] || item.status}</small>}</span>; let content: React.ReactNode; if (Array.isArray(value))
    content = value.length ? <ul className="mechanism-list">{value.map((v, i) => <li key={i}><Value item={v} audit={audit}/></li>)}</ul> : '无';
else if (typeof value === 'object') {
    const resource = resourceAmount(value);
    const pairs = Object.entries(value).filter(([k]) => !debugKeys.has(k) && !!labels[k]);
    content = resource ? <span>{resource.label}：<Value item={resource.amount} audit={audit}/></span> : pairs.length ? <dl className="mechanism">{pairs.map(([k, v]) => <React.Fragment key={k}><dt>{labels[k]}</dt><dd><Value item={v} audit={audit}/></dd></React.Fragment>)}</dl> : Object.keys(value).length ? <span>详见机制说明</span> : <span>无</span>;
}
else
    content = safeText(value) || scalar(value); return <span className="value">{content}{isFact(item) && unitLabels[item.unit] && <small> {unitLabels[item.unit]}</small>}{audit && isFact(item) && <small className="fact-status">{statusLabels[item.status] || item.status}{item.translation_draft ? ' · 中文草稿' : ''}</small>}</span>; }
export function ErrorBox({ error, retry }: {
    error: string;
    retry: () => void;
}) { return <div className="error-box" role="alert"><p>{error}</p><button onClick={retry}>重试</button></div>; }
export function UnitPage({ faction, id, index, audit, navigate }: {
    faction: Item;
    id: string;
    index: Item[];
    audit: boolean;
    navigate: (f: string, id?: string) => void;
}) { const pack = React.useContext(PackContext); const found = resolveQualified(index, faction.origin, id); const resource = useResource<Item>(found?.path ? contentUrl(pack, found.path) : null); const auditData = useResource<Item>(audit && found?.audit ? contentUrl(pack, found.audit) : null); if (!found)
    return <section className="empty"><h1>该对象未收录于玩家百科</h1><a href={route(faction.key)}>返回阵营</a></section>; if (resource.error)
    return <ErrorBox error={resource.error} retry={resource.retry}/>; if (resource.loading || !resource.data)
    return <p role="status">正在加载词条…</p>; const entry = resource.data; const name = entry.navigation?.name || plain(entry.name) || '单位'; const members = formNames(entry.navigation?.members || found.entry.members || [], entry.navigation?.primary || found.entry.group || found.entry.id); const commandButtons = (entry.presentation?.command_cards || []).flatMap((c: Item) => c.buttons || []); const abilities = (entry.abilities || []).filter((a: Item) => !isBasicAbility(a)).map((a: Item) => abilityPresentation(a, commandButtons)); const passive = (entry.presentation?.command_cards || []).flatMap((c: Item) => c.buttons || []).filter((b: Item) => b.type === 'Passive'); const seen = new Set(abilities.map((a: Item) => plain(a.name))); const uniquePassive = passive.filter((b: Item) => { const label = plain(b.name); if (seen.has(label))
    return false; seen.add(label); return true; }); const upgrades = entry.presentation?.available_upgrades || []; return <article><header className="unit-heading"><a href={route(faction.key)}>‹ {faction.name}</a><h1>{name}</h1><p>{entry.navigation?.subfactionName || found.entry.subfactionName || ''}</p>{members.length > 1 && <nav className="form-tabs" aria-label="单位形态">{members.map((m: Item) => <a href={route(faction.key, m.id, m.origin || faction.origin)} className={m.id === id ? 'selected' : ''} aria-current={m.id === id ? 'page' : undefined} key={m.id}>{m.label}</a>)}</nav>}</header><div className="unit-layout"><div className="unit-main"><section className="intro metal-frame"><Image src={entry.icon} name={name}/><p className="description">{plain(entry.description) || '无'}</p></section><div className="portrait-command"><div className="portrait-frame"><Image src={entry.portrait} name={name} portrait/></div><CommandCard cards={entry.presentation?.command_cards || []} audit={audit}/></div><section className="content-section"><h2>技能</h2>{abilities.length || uniquePassive.length ? <div className="card-columns">{abilities.map((a: Item, i: number) => <Ability key={a.id || i} ability={a} index={i} audit={audit}/>)}{uniquePassive.map((b: Item, i: number) => <Ability key={'passive' + i} ability={{ ...b, effect: b.description }} index={abilities.length + i} audit={audit}/>)}</div> : <p className="muted">无</p>}</section>{upgrades.length > 0 && <section className="content-section"><h2>可用升级</h2><div className="card-columns">{upgrades.map((r: Item, i: number) => <Research item={r} index={i} audit={audit} entries={index} faction={faction.origin}/>)}</div></section>}{entry.presentation?.research?.length > 0 && <section className="content-section"><h2>研发项目</h2><div className="card-columns">{entry.presentation.research.map((r: Item, i: number) => <Research item={r} index={i} audit={audit} entries={index} faction={faction.origin}/>)}</div></section>}<Modifiers items={entry.modifiers || []} researched={upgrades} audit={audit}/><Production items={entry.production || []} entries={index} faction={faction.origin} audit={audit}/><Relations entry={entry} entries={index} faction={faction.origin}/></div><aside className="stats metal-frame" aria-label="单位属性"><table><tbody>{statOrder.filter(k => k in (entry.stats || {})).map(k => <tr key={k}><th>{k === 'radius' ? '碰撞半径' : labels[k]}</th><td className={'stat-' + k}><Value item={entry.stats[k]} audit={audit}/></td></tr>)}{entry.tags && <tr><th>标签</th><td><Value item={entry.tags} audit={audit}/></td></tr>}{(entry.production || []).length > 0 && <tr><th>生产</th><td><Cost cost={entry.production[0].cost} time={entry.production[0].time} audit={audit}/></td></tr>}<tr><th>武器</th><td>{entry.weapons?.length ? entry.weapons.map((w: Item, i: number) => <Weapon key={w.id || i} weapon={w} index={i} audit={audit}/>) : '无'}</td></tr></tbody></table></aside></div>{audit && <section className="audit-section"><h2>数据与证据</h2>{auditData.error ? <ErrorBox error={auditData.error} retry={auditData.retry}/> : auditData.loading ? <p role="status">正在加载证据…</p> : auditData.data ? <Audit data={auditData.data}/> : <p>无</p>}</section>}</article>; }
function Cost({ cost, time, audit }: {
    cost: any;
    time?: any;
    audit: boolean;
}) { const data = factValue(cost); return <div className="cost-line">{data && typeof data === 'object' && !Array.isArray(data) && !resourceAmount(data) && Object.keys(data).some(k => ['minerals', 'vespene', 'supply', 'energy', 'life'].includes(k)) ? Object.entries(data).filter(([k]) => ['minerals', 'vespene', 'supply', 'energy', 'life'].includes(k)).map(([k, v]) => <span key={k} className={'resource-' + k}><span className="resource-symbol" aria-hidden="true">{k === 'minerals' ? '◆' : k === 'vespene' ? '●' : k === 'supply' ? '◈' : '◇'}</span><span className="sr-only">{labels[k] || k}</span><Value item={v} audit={audit}/></span>) : <Value item={cost} audit={audit}/>} {time != null && <span className="resource-time"><span aria-hidden="true">◷ </span><span className="sr-only">时间</span><Value item={time} audit={audit}/></span>}</div>; }
function Weapon({ weapon, index, audit }: {
    weapon: Item;
    index: number;
    audit: boolean;
}) { const damage = weaponDamageSummary(weapon.damage); return <details className="weapon-card"><summary><span className="weapon-heading"><Image src={weapon.icon} name={plain(weapon.name) || '武器'}/><strong>{plain(weapon.name) || `武器 ${index + 1}`}</strong></span><span className="weapon-summary">{damage.kind === 'base' ? <>基础伤害：<Value item={damage.amount} audit={audit}/></> : damage.kind === 'total' ? <>总伤害：<Value item={damage.amount} audit={audit}/></> : damage.kind === 'segments' ? <>伤害：分段机制（展开查看）</> : damage.kind === 'unknown' ? <>伤害：无{damage.partial ? '（已解析部分见详情）' : ''}</> : <>伤害：查看机制说明</>}<br />攻击间隔：<Value item={weapon.period} audit={audit}/><br />射程：<Value item={weapon.range} audit={audit}/></span><span className="expand-label">查看详情</span></summary><div className="expanded"><p>目标：<Value item={weapon.target} audit={audit}/></p><p>伤害机制：</p><Value item={weapon.damage} audit={audit}/>{weapon.effect_summary && <Value item={weapon.effect_summary} audit={audit}/>}</div></details>; }
function Ability({ ability, index, audit }: {
    ability: Item;
    index: number;
    audit: boolean;
}) { return <details className="ability-card metal-frame"><summary><span className="card-heading"><Image src={ability.icon} name={plain(ability.name) || '技能'}/><span><strong>{plain(ability.name) || `技能 ${index + 1}`}</strong>{plain(ability.hotkey) && <kbd>{plain(ability.hotkey)}</kbd>}</span></span><span className="card-preview">{plain(ability.description) || ((typeof factValue(ability.effect) === 'string') ? plain(ability.effect) : '查看技能说明')}</span><span className="expand-label">展开技能详情</span></summary><div className="expanded"><p>效果：<Value item={ability.effect} audit={audit}/></p><p>消耗：<Value item={ability.cost} audit={audit}/></p><p>冷却：<Value item={ability.cooldown} audit={audit}/></p>{ability.charges?.length > 0 && <><h4>充能</h4><Value item={ability.charges} audit={audit}/></>}<p>条件：<Value item={ability.conditions} audit={audit}/></p>{(ability.commands || []).map((c: Item, i: number) => <details className="command-detail" key={i}><summary>操作 {i + 1}</summary><Cost cost={c.cost} time={c.time} audit={audit}/><p>{plain(c.name || c.description)}</p><p>冷却：<Value item={c.cooldown} audit={audit}/></p><p>条件：<Value item={c.conditions} audit={audit}/></p>{c.charges && <Value item={c.charges} audit={audit}/>}</details>)}{ability.execution?.length > 0 && <><h4>执行机制</h4><Value item={ability.execution} audit={audit}/></>}</div></details>; }
function UnitLink({ id, entries, faction }: {
    id: any;
    entries: Item[];
    faction: string;
}) { const key = factValue(id); const found = resolveQualified(entries, faction, key); if (!found)
    return <span>相关对象</span>; const { entry: target, member } = found; return <a href={route(member?.displayFaction || target.displayFaction, key, member?.origin || target.origin)}>{member?.name || target.name}</a>; }
function Research({ item, index, audit, entries, faction }: {
    item: Item;
    index: number;
    audit: boolean;
    entries: Item[];
    faction: string;
}) { const data = item.research || item; return <div className="upgrade-card metal-frame"><div className="card-heading"><Image src={data.icon || item.icon} name={plain(data.name || item.name) || '升级'}/><div><strong>{plain(data.name || item.name) || `升级 ${index + 1}`}</strong>{plain(data.hotkey) && <kbd>{plain(data.hotkey)}</kbd>}</div>{(data.producer || item.producer) && <span className="research-building"><UnitLink id={data.producer || item.producer} entries={entries} faction={faction}/></span>}</div><Cost cost={data.cost} time={data.time} audit={audit}/><p className="upgrade-description">{plain(data.description || item.description) || '无'}</p><details><summary>要求与效果</summary><div className="expanded"><h4>科技要求</h4><Value item={data.requirements || data.conditions} audit={audit}/>{item.modifiers && <><h4>升级效果</h4><Value item={item.modifiers} audit={audit}/></>}{data.effect && <Value item={data.effect} audit={audit}/>}</div></details><ConditionAssociations items={data.condition_targets || item.condition_targets || []} entries={entries} faction={faction} audit={audit}/></div>; }
function ConditionAssociations({ items, entries, faction, audit }: {
    items: Item[];
    entries: Item[];
    faction: string;
    audit: boolean;
}) { if (!items.length)
    return null; return <details><summary>关联生产与指令条件</summary><div className="expanded"><p>以下为状态条件关联，不表示该项独自解锁对象或提升属性。</p>{items.map((row, i) => <section key={i}><h4>{row.relation === 'production_requirement' ? '生产条件' : '指令条件'}：<UnitLink id={row.target} entries={entries} faction={faction}/></h4>{row.producer && <p>入口：<UnitLink id={row.producer} entries={entries} faction={faction}/></p>}<Value item={row.condition} audit={audit}/></section>)}</div></details>; }
function Modifiers({ items, researched, audit }: {
    items: Item[];
    researched: Item[];
    audit: boolean;
}) { const researchIds = new Set(researched.map(r => r.id)); const shown = items.filter(m => hasModifierBody(m) && !(m.kind === 'upgrade' && researchIds.has(m.id))); if (!shown.length)
    return null; return <section className="content-section"><h2>条件升级与增益</h2><div className="card-columns">{shown.map((m, i) => <details className="modifier-card metal-frame" key={m.id || i}><summary><strong>{plain(m.name) || (m.kind === 'upgrade' ? '条件升级' : '条件增益')}</strong><span className="expand-label">展开条件与效果</span></summary><div className="expanded">{plain(m.description) && <p>{plain(m.description)}</p>}{factValue(m.effect) != null && <><h4>效果</h4><Value item={m.effect} audit={audit}/></>}{m.execution_fact && <><h4>触发机制</h4><Value item={m.execution_fact} audit={audit}/></>}<h4>生效条件</h4><Value item={m.condition} audit={audit}/></div></details>)}</div></section>; }
function Production({ items, entries, faction, audit }: {
    items: Item[];
    entries: Item[];
    faction: string;
    audit: boolean;
}) { if (!items.length)
    return null; return <section className="content-section"><h2>生产方式</h2><div className="card-columns">{items.map((p, i) => <details className="production-card metal-frame" key={i}><summary><strong><UnitLink id={p.producer} entries={entries} faction={faction}/></strong><Cost cost={p.cost} time={p.time} audit={audit}/><span className="expand-label">展开生产详情</span></summary><div className="expanded"><p>数量：<Value item={p.count} audit={audit}/></p><h4>科技要求</h4><Value item={p.requirements} audit={audit}/></div></details>)}</div></section>; }
function Relations({ entry, entries, faction }: {
    entry: Item;
    entries: Item[];
    faction: string;
}) { const relations = (entry.related_entries || entry.related || []).filter((r: any) => entries.some(e => e.id === (r.id || r))); return relations.length ? <section className="content-section"><h2>关联对象</h2><div className="related-list">{relations.map((r: any, i: number) => <UnitLink key={i} id={r.id || r} entries={entries} faction={faction}/>)}</div></section> : null; }
function CommandCard({ cards, audit }: {
    cards: Item[];
    audit: boolean;
}) {
    const [cardId, setCardId] = useState('root');
    const [selected, setSelected] = useState<Item | null>(null);
    const [bubble, setBubble] = useState<{
        buttons: Item[];
        left: number;
        top: number;
    } | null>(null);
    const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const container = useRef<HTMLDivElement>(null);
    const suppressFocus = useRef(false);
    const trigger = useRef<HTMLElement | null>(null);
    const current = cards.find(c => c.id === cardId) || cards[0];
    const { slots, unplaced } = commandSlots(current?.buttons || []);
    useEffect(() => { function esc(e: KeyboardEvent) { if (e.key === 'Escape') {
        setBubble(null);
        suppressFocus.current = true;
        trigger.current?.focus();
    } } addEventListener('keydown', esc); return () => { removeEventListener('keydown', esc); if (closeTimer.current)
        clearTimeout(closeTimer.current); }; }, []);
    function show(buttons: Item[], el: HTMLElement) { if (suppressFocus.current) {
        suppressFocus.current = false;
        return;
    } trigger.current = el; if (closeTimer.current)
        clearTimeout(closeTimer.current); const rect = el.getBoundingClientRect(); setBubble({ buttons, left: Math.max(12, Math.min(rect.left, innerWidth - 372)), top: Math.max(12, Math.min(rect.bottom + 8, innerHeight - 320)) }); }
    function scheduleClose() { closeTimer.current = setTimeout(() => setBubble(null), 180); }
    function choose(button: Item) { setBubble(null); if (button.type === 'CancelSubmenu') {
        setCardId('root');
        setSelected(null);
    }
    else if (button.submenu && cards.some(c => c.id === button.submenu)) {
        setCardId(button.submenu);
        setSelected(null);
    }
    else
        setSelected(button); }
    return <div className="command-area" ref={container}><div className="command-toolbar"><span>{cardId === 'root' ? '命令卡' : '建造菜单'}</span>{cardId !== 'root' && <button onClick={() => { setCardId('root'); setSelected(null); setBubble(null); }}>返回主卡</button>}</div><div className="command-grid" aria-label="命令卡">{slots.map((buttons: Item[], i: number) => <div className="command-slot" key={i} onMouseEnter={e => buttons.length && show(buttons, e.currentTarget)} onMouseLeave={scheduleClose}>{buttons.length > 0 && <><button className="command-button" onFocus={e => show(buttons, e.currentTarget)} onClick={() => choose(buttons[0])} aria-label={plain(buttons[0].name) || '命令'}><Image src={buttons[0].icon} name={plain(buttons[0].name) || '命令'}/>{plain(buttons[0].hotkey) && <kbd>{plain(buttons[0].hotkey)}</kbd>}</button>{buttons.length > 1 && <button className="candidate-count" aria-label={`查看此格 ${buttons.length} 个按钮`} aria-expanded={!!bubble && bubble.buttons === buttons} onClick={e => show(buttons, e.currentTarget)}>{buttons.length}</button>}</>}</div>)}</div><button className="skill-toggle" onClick={() => (document.querySelector('.ability-card') || document.querySelector('.content-section'))?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>展开技能详情</button>{selected && <div className="selected-command metal-frame"><button className="close-command" onClick={() => setSelected(null)} aria-label="关闭命令说明">×</button><strong>{plain(selected.name) || '命令说明'}</strong><p>{plain(selected.description) || '无'}</p><p>条件：<Value item={selected.conditions} audit={audit}/></p></div>}{unplaced.length > 0 && <details className="unplaced"><summary>未定位按钮</summary>{unplaced.map((b: Item, i: number) => <button key={i} onClick={() => choose(b)}>{plain(b.name) || '命令'}</button>)}</details>}{bubble && createPortal(<div role="dialog" aria-label="命令说明与同格候选" className="command-bubble metal-frame" style={{ left: bubble.left, top: bubble.top, '--accent': container.current ? getComputedStyle(container.current).getPropertyValue('--accent') : '#6891ff' } as React.CSSProperties} onMouseEnter={() => { if (closeTimer.current)
        clearTimeout(closeTimer.current); }} onMouseLeave={scheduleClose}><div className="bubble-title"><strong>{bubble.buttons.length > 1 ? '同格按钮' : '命令说明'}</strong><button onClick={() => setBubble(null)} aria-label="关闭气泡">×</button></div>{bubble.buttons.map((b: Item, i: number) => <div className="bubble-candidate" key={i}><button onClick={() => choose(b)}><Image src={b.icon} name={plain(b.name) || '命令'}/><strong>{plain(b.name) || '命令'}</strong>{plain(b.hotkey) && <kbd>{plain(b.hotkey)}</kbd>}</button><p>{plain(b.description) || '无'}</p>{factValue(b.conditions) != null && <p>条件：<Value item={b.conditions} audit={audit}/></p>}</div>)}</div>, document.body)}</div>;
}
function Audit({ data }: {
    data: Item;
}) { return <><details><summary>完整词条数据</summary><pre>{JSON.stringify(data.entry, null, 2)}</pre></details><details><summary>来源与字段证据（{data.sources?.length || 0}）</summary><div className="source-table">{(data.sources || []).map((source: Item, i: number) => <details key={source.id || i}><summary>{source.id || '来源'} · {source.source_object || source.object || ''}</summary><pre>{JSON.stringify(source, null, 2)}</pre></details>)}</div></details><details><summary>活动依赖</summary><pre>{JSON.stringify(data.dependency_context, null, 2)}</pre></details></>; }
