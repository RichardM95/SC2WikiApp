"""Preserve legacy Other article as safe ordered plain-text content."""
from html.parser import HTMLParser
from pathlib import Path
import hashlib
import re
import shutil
from urllib.parse import unquote,urlsplit

class Node:
    def __init__(self,tag='',attrs=()):self.tag=tag;self.attrs=dict(attrs);self.children=[]
    def text(self):return ''.join(c.text() if isinstance(c,Node) else c for c in self.children)

class Parser(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.root=Node();self.stack=[self.root]
    def handle_starttag(self,tag,attrs):
        node=Node(tag,attrs);self.stack[-1].children.append(node)
        if tag not in ('img','br','meta','link','input','hr'):self.stack.append(node)
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,0,-1):
            if self.stack[i].tag==tag:self.stack=self.stack[:i];break
    def handle_data(self,text):self.stack[-1].children.append(text)

def parse_other(legacy,output):
    legacy=Path(legacy).resolve();output=Path(output);source=legacy/'other.html'
    if not source.is_file():return {'version':1,'title':'坠毁系统','blocks':[{'type':'paragraph','text':'历史专题资料未提供。'}],'source':{}}
    raw=source.read_bytes();parser=Parser();parser.feed(raw.decode('utf-8-sig'))
    blocks=[];title='坠毁系统';section_evidence=False
    def clean(node):return re.sub(r'\s+',' ',node.text()).strip()
    def nodes(node,tag):
        found=[]
        for c in node.children:
            if isinstance(c,Node):
                if c.tag==tag:found.append(c)
                else:found.extend(nodes(c,tag))
        return found
    def contains_source(node):
        return 'src' in node.attrs.get('class','').split() or any(contains_source(c) for c in node.children if isinstance(c,Node))
    def player_text(node):
        if 'src' in node.attrs.get('class','').split():return '相关对象'
        return ''.join(player_text(c) if isinstance(c,Node) else c for c in node.children)
    def visit(node,in_main=False,hidden=False):
        nonlocal title,section_evidence
        if node.tag in ('script','style','head'):return
        in_main=in_main or node.tag=='main'
        if not in_main:
            for child in node.children:
                if isinstance(child,Node):visit(child,in_main,hidden)
            return
        classes=node.attrs.get('class','').split();hidden=hidden or 'source-list' in classes
        block=None
        if node.tag in ('h1','h2','h3'):
            text=clean(node)
            if node.tag=='h1':title=text;return
            section_evidence=text=='来源'
            block={'type':'heading','level':int(node.tag[1]),'text':text}
        elif node.tag=='p':
            if '返回百科首页' in clean(node):return
            block={'type':'paragraph','text':clean(node)}
        elif node.tag in ('ul','ol'):
            block={'type':'list','items':[clean(n) for n in nodes(node,'li')]}
            if contains_source(node):block['evidence']=True
        elif node.tag=='table':
            rows=nodes(node,'tr');headers=[];data=[];evidence_columns=set()
            for row in rows:
                hs=[clean(n) for n in nodes(row,'th')];cells=[clean(n) for n in nodes(row,'td')]
                for i,cell in enumerate(nodes(row,'td')):
                    if contains_source(cell):evidence_columns.add(i)
                if hs:headers=hs
                elif cells:data.append(cells)
            block={'type':'table','headers':headers,'rows':data}
            if evidence_columns:block['evidenceColumns']=sorted(evidence_columns)
        elif node.tag=='img':
            ref=urlsplit(node.attrs.get('src',''));path=(legacy/unquote(ref.path)).resolve()
            if ref.scheme or ref.netloc or not path.is_relative_to(legacy) or not path.is_file():raise ValueError('Other image escapes legacy source')
            if path.suffix.lower() not in ('.png','.jpg','.jpeg','.webp','.gif','.svg'):raise ValueError('Unsupported Other image')
            # SVG can contain executable scripts; do not copy it into an update.
            if path.suffix.lower()=='.svg':raise ValueError('Other SVG requires raster conversion')
            dest='assets/images/other-'+hashlib.sha256(path.read_bytes()).hexdigest()[:20]+path.suffix.lower()
            (output/dest).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,output/dest)
            block={'type':'image','src':dest,'alt':node.attrs.get('alt','')}
        elif node.tag=='div' and ('ttl' in classes or 'dsc' in classes):block={'type':'paragraph','text':clean(node)}
        elif node.tag=='summary' and hidden:block={'type':'heading','level':2,'text':clean(node)}
        if block:
            if block['type']=='paragraph' and contains_source(node):
                block['originalText']=block['text']
                block['text']=re.sub(r'\s+',' ',player_text(node)).strip()
            if hidden or section_evidence:block['evidence']=True
            blocks.append(block);return
        for child in node.children:
            if isinstance(child,Node):visit(child,in_main,hidden)
    visit(parser.root)
    return {'version':1,'title':title,'blocks':blocks,'source':{'sha256':hashlib.sha256(raw).hexdigest()}}
