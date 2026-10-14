import React, { useCallback, useEffect, useState } from 'react';
export type Item = Record<string, any>;
export const PackContext = React.createContext('');
import { contentUrl, consumeSessionLines } from './protocol.mjs';
export { contentUrl };
const cache = new Map<string, any>();
export function clearContentCache() { cache.clear(); }
export function useResource<T = any>(path: string | null) { const [state, setState] = useState<{
    data: T | null;
    error: string;
    loading: boolean;
    key: string | null;
}>({ key: path, data: null, error: '', loading: !!path }); const [retry, setRetry] = useState(0); useEffect(() => { const ctrl = new AbortController(); let active = true; if (!path) {
    setState({ key: path, data: null, error: '', loading: false });
    return;
} setState({ key: path, data: cache.get(path) || null, error: '', loading: !cache.has(path) }); (async () => { try {
    let data = cache.get(path);
    if (!data) {
        const r = await fetch(path, { cache: 'no-cache', signal: ctrl.signal });
        if (!r.ok)
            throw new Error('无法加载百科内容，请重试。');
        data = await r.json();
        if (active)
            cache.set(path, data);
    }
    if (active)
        setState({ key: path, data, error: '', loading: false });
}
catch (e: any) {
    if (active && e.name !== 'AbortError')
        setState({ key: path, data: null, error: e.message || '服务已断开，请重新启动百科。', loading: false });
} })(); return () => { active = false; ctrl.abort(); }; }, [path, retry]); const reload = useCallback(() => { if (path)
    cache.delete(path); setRetry(v => v + 1); }, [path]); return { ...(state.key === path ? state : { key: path, data: null, error: '', loading: !!path }), retry: reload }; }
export async function api(path: string, body?: any, zip?: File) { const h = await fetch('/__app/health', { cache: 'no-cache' }); if (!h.ok)
    throw new Error('百科服务已停止，请重新启动百科。'); const health = await h.json(); const r = await fetch('/__app/' + path, { method: 'POST', headers: zip ? { 'Content-Type': 'application/zip', 'X-SCORE-Token': health.token } : { 'Content-Type': 'application/json' }, body: zip || JSON.stringify({ ...body, token: health.token }) }); const data = await r.json(); if (!r.ok)
    throw new Error(data.error || '操作失败，请重试。'); return data; }
export function useSession(onChanged: () => void) { const [state, setState] = useState('正在连接'); useEffect(() => { let stopped = false; let ctrl: AbortController | null = null; let timer: ReturnType<typeof setTimeout> | null = null; let hidden = false; const client = crypto.randomUUID(); async function connect() { ctrl?.abort(); ctrl = new AbortController(); try {
    const h = await fetch('/__app/health', { signal: ctrl.signal, cache: 'no-cache' });
    if (!h.ok)
        throw new Error();
    const health = await h.json();
    const r = await fetch('/__app/session', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token: health.token, client_id: client }), signal: ctrl.signal });
    if (!r.ok || !r.body)
        throw new Error();
    setState('运行中');
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (!stopped) {
        const row = await reader.read();
        if (row.done)
            throw new Error();
        buffer += decoder.decode(row.value, { stream: true });
        const { events, rest } = consumeSessionLines(buffer);
        buffer = rest;
        for (const event of events) {
            if (event.event === 'content-changed')
                onChanged();
        }
    }
}
catch (e: any) {
    if (stopped || hidden || e.name === 'AbortError')
        return;
    setState('服务已断开，请重新启动百科');
    timer = setTimeout(connect, 3000);
} } function hide() { hidden = true; ctrl?.abort(); if (timer)
    clearTimeout(timer); } function show() { hidden = false; connect(); } connect(); addEventListener('pagehide', hide); addEventListener('pageshow', show); return () => { stopped = true; ctrl?.abort(); if (timer)
    clearTimeout(timer); removeEventListener('pagehide', hide); removeEventListener('pageshow', show); }; }, [onChanged]); return state; }
export function getPreference(key: string, fallback: boolean) { try {
    return localStorage.getItem(key) === 'true' || (localStorage.getItem(key) == null && fallback);
}
catch {
    return fallback;
} }
export function setPreference(key: string, value: boolean) { try {
    localStorage.setItem(key, String(value));
}
catch { } }
