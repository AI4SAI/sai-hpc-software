#!/usr/bin/env python3
import argparse, json, re, sys
from pathlib import PurePosixPath

NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
def fail(msg): raise ValueError(msg)
def rel(v, field):
    if not isinstance(v, str) or not v or v.startswith('~') or '\\' in v: fail(f'{field} must be relative')
    p = PurePosixPath(v)
    if p.is_absolute() or '..' in p.parts: fail(f'{field} escapes workspace')
    return str(p)
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('plan'); ap.add_argument('--profiles', default='profiles/cluster.json'); ap.add_argument('--output', required=True); a=ap.parse_args()
    raw=json.load(open(a.plan)); cluster=json.load(open(a.profiles))
    if raw.get('version') != 1 or not NAME.fullmatch(raw.get('software','')): fail('invalid version or software')
    src=raw.get('source',{}); sha=src.get('sha','')
    if not re.fullmatch(r'[0-9a-fA-F]{40}', sha): fail('source.sha must be a full SHA')
    out={'version':1,'software':raw['software'],'source':{'repository':src.get('repository'),'sha':sha.lower()},'targets':[]}; names=set()
    for i,t in enumerate(raw.get('targets', [])):
        n=t.get('name'); part=t.get('partition')
        if not isinstance(n,str) or not NAME.fullmatch(n) or n in names: fail(f'target {i} name invalid or duplicate')
        if part not in cluster['partitions']: fail(f'target {n} has unknown partition')
        lim=cluster['partitions'][part]
        for k,d in [('nodes',1),('cpus_per_node',1),('gpus_per_node',0)]:
            v=t.get(k,d)
            if not isinstance(v,int) or v<0 or v>(lim['max_nodes'] if k=='nodes' else lim['max_cpus_per_node'] if k=='cpus_per_node' else lim['max_gpus_per_node']): fail(f'target {n}.{k} exceeds profile')
        if not isinstance(t.get('command'),list) or not t['command'] or any(not isinstance(x,str) or '\0' in x for x in t['command']): fail(f'target {n}.command invalid')
        names.add(n); out['targets'].append({**t,'install_prefix':rel(t.get('install_prefix',''),f'target {n}.install_prefix')})
    if not out['targets']: fail('targets must not be empty')
    json.dump(out,open(a.output,'w'),indent=2); open(a.output,'a').write('\n')
if __name__=='__main__':
    try: main()
    except (OSError,ValueError,KeyError,json.JSONDecodeError) as e: print(f'plan validation failed: {e}',file=sys.stderr); raise SystemExit(2)
