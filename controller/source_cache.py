#!/usr/bin/env python3
"""Content-addressed, resumable source archive chunks for the SAI home cache."""
import hashlib, json, pathlib, sys
CHUNK=8*1024*1024
def pack(source, out):
    source, out = pathlib.Path(source), pathlib.Path(out); out.mkdir(parents=True, exist_ok=True)
    chunks=[]; index=0
    with source.open('rb') as f:
        while data:=f.read(CHUNK):
            digest=hashlib.sha256(data).hexdigest(); name=f'{digest}.chunk'; p=out/name
            if not p.exists(): p.write_bytes(data)
            chunks.append({'name':name,'size':len(data) }); index+=1
    (out/'manifest.json').write_text(json.dumps({'version':1,'size':source.stat().st_size,'chunks':chunks},sort_keys=True)+'\n')
def main():
    if len(sys.argv)!=3: raise SystemExit('usage: source_cache.py ARCHIVE CACHE_DIR')
    pack(sys.argv[1],sys.argv[2])
if __name__=='__main__': main()
