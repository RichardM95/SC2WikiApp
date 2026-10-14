export function contentUrl(pack,path){if(!path)return '';if(!/^[A-Za-z0-9_-]{1,100}$/.test(pack))throw new Error('内容包标识无效。');const normalized=path.replace(/^\//,'');if(normalized.split('/').some(p=>p==='..'||p==='.')||normalized.includes('\\')||normalized.includes('://'))throw new Error('内容路径无效。');return '/__content/'+encodeURIComponent(pack)+'/'+normalized;}
export function consumeSessionLines(buffer){const events=[];let pos;while((pos=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,pos);buffer=buffer.slice(pos+1);if(line.trim())events.push(JSON.parse(line));}if(buffer.length>1048576)throw new Error('服务消息过长。');return {events,rest:buffer};}
export function visibleColumns(block,audit){return (block.headers||[]).map((_,index)=>index).filter(index=>audit||!block.evidenceColumns?.includes(index));}

export function contentRevision(pack){return typeof pack==='string'&&pack.length?pack.slice(-12):'无';}
export function otherParagraph(block,audit){return audit?(block.originalText??block.text):block.text;}
