#!/usr/bin/env python3
import argparse, hashlib, json, pathlib, subprocess
PARTS=8
def run(*a): return subprocess.run(a,check=True,text=True,capture_output=True).stdout.strip()
def pack(repo, commit, output):
    repo, output=pathlib.Path(repo),pathlib.Path(output); output.mkdir(parents=True,exist_ok=True)
    commit=run('git','-C',str(repo),'rev-parse',commit); bundle=output/'source.bundle'
    revs=run('git','-C',str(repo),'rev-list',commit)
    subprocess.run(['git','-C',str(repo),'bundle','create',str(bundle),'--stdin'],input=revs+'\n',text=True,check=True)
    data=bundle.read_bytes(); step=(len(data)+PARTS-1)//PARTS
    for i in range(PARTS): (output/f'source.bundle.part.{i:02d}').write_bytes(data[i*step:(i+1)*step])
    (output/'manifest.json').write_text(json.dumps({'version':1,'commit':commit,'sha256':hashlib.sha256(data).hexdigest(),'size':len(data),'parts':PARTS})+'\n')
def assemble(cache):
    cache=pathlib.Path(cache); m=json.loads((cache/'manifest.json').read_text()); tmp=cache/'.source.bundle.tmp'
    with tmp.open('wb') as out:
        for i in range(m['parts']): out.write((cache/f'source.bundle.part.{i:02d}').read_bytes())
    if hashlib.sha256(tmp.read_bytes()).hexdigest()!=m['sha256']: raise SystemExit('bundle checksum mismatch')
    tmp.replace(cache/'source.bundle')
def main():
    p=argparse.ArgumentParser(); s=p.add_subparsers(dest='op',required=True); a=s.add_parser('pack'); a.add_argument('repo'); a.add_argument('commit'); a.add_argument('output'); b=s.add_parser('assemble'); b.add_argument('cache'); x=p.parse_args(); pack(x.repo,x.commit,x.output) if x.op=='pack' else assemble(x.cache)
if __name__=='__main__': main()
