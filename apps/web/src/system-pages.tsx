import React, { useState } from 'react';
import { api, useResource, contentUrl } from './service';
import { ErrorBox, Image } from './unit-page';
import { visibleColumns, contentRevision, otherParagraph } from './protocol.mjs';
export function UpdatePage({ changed }: {
    changed: () => void;
}) { const status = useResource<any>('/__app/update/status'); const [file, setFile] = useState<File | null>(null); const [staged, setStaged] = useState<any>(null); const [busy, setBusy] = useState(false); const [message, setMessage] = useState(''); const [error, setError] = useState(''); async function run(action: string) { setBusy(true); setError(''); setMessage(action === 'update/check' ? '正在校验并准备更新包…' : '正在安全切换数据版本…'); try {
    const result = await api(action, action === 'update/install' ? { staging_id: staged.staging_id } : {}, action === 'update/check' ? file || undefined : undefined);
    if (action === 'update/check') {
        setStaged(result);
        setMessage('更新包校验通过，请检查摘要后安装。');
    }
    else {
        setStaged(null);
        setFile(null);
        setMessage(action.endsWith('rollback') ? '已恢复上一版数据。' : '数据更新完成。');
        changed();
    }
    status.retry();
}
catch (e: any) {
    setError(e.message);
    setMessage('');
}
finally {
    setBusy(false);
} } return <section className="update-page"><div className="page-heading"><h1>数据更新</h1><p>导入本地完整数据更新包。程序和阵营配置会保留。</p></div><div className="metal-frame update-panel">{status.data && <dl className="mechanism"><dt>当前数据</dt><dd>{status.data.current?.batch}<br/><small>内容修订：{contentRevision(status.data.current?.pack_id)}</small></dd><dt>上一版</dt><dd>{status.data.previous?.batch || '无'}{status.data.previous && <><br/><small>内容修订：{contentRevision(status.data.previous.pack_id)}</small></>}</dd></dl>}{status.error && <ErrorBox error={status.error} retry={status.retry}/>}<label className="update-file">选择 ZIP 更新包<input type="file" accept=".zip,application/zip" disabled={busy} onChange={e => { setFile(e.target.files?.[0] || null); setStaged(null); setError(''); setMessage(''); }}/></label><div className="update-actions"><button disabled={!file || busy || status.data?.busy} onClick={() => run('update/check')}>校验更新包</button><button disabled={!staged || busy || status.data?.busy} onClick={() => run('update/install')}>安装已校验更新</button><button disabled={!status.data?.previous || busy || status.data?.busy} onClick={() => run('update/rollback')}>回退上一版</button></div>{staged && <div className="update-summary"><h2>待安装数据</h2><p>批次：{staged.batch}</p><p>内容修订：{contentRevision(staged.pack_id)}</p><p>词条：{staged.summary?.entries} · 文件：{staged.summary?.files}</p></div>}{message && <p role="status">{message}</p>}{error && <p role="alert" className="error-box">{error}</p>}<p className="muted">新版客户端请使用完整程序包。更新期间请等待完成后再退出。</p></div></section>; }
export function OtherPage({ pack, path, audit }: {
    pack: string;
    path: string;
    audit: boolean;
}) { const resource = useResource<any>(path ? contentUrl(pack, path) : null); if (resource.error)
    return <ErrorBox error={resource.error} retry={resource.retry}/>; if (!resource.data)
    return <p role="status">正在加载坠毁系统…</p>; const data = resource.data; return <article className="other-page"><div className="page-heading"><h1>{data.title || '坠毁系统'}</h1></div>{(data.blocks || []).filter((b: any) => audit || !b.evidence).map((b: any, i: number) => { if (b.type === 'heading') {
    const Tag = ('h' + Math.max(2, Math.min(3, b.level))) as 'h2' | 'h3';
    return <Tag key={i}>{b.text}</Tag>;
} if (b.type === 'paragraph')
    return <p key={i}>{otherParagraph(b, audit)}</p>; if (b.type === 'list')
    return <ul key={i}>{b.items.map((t: string, j: number) => <li key={j}>{t}</li>)}</ul>; if (b.type === 'table')
    return <div className="other-table metal-frame" key={i}><table><thead><tr>{visibleColumns(b, audit).map((j: number) => <th key={j}>{b.headers[j]}</th>)}</tr></thead><tbody>{b.rows.map((r: string[], j: number) => <tr key={j}>{visibleColumns(b, audit).map((k: number) => <td key={k}>{r[k] || ''}</td>)}</tr>)}</tbody></table></div>; if (b.type === 'image')
    return <Image key={i} src={b.src} name={b.alt || '坠毁系统图示'}/>; return null; })}</article>; }
