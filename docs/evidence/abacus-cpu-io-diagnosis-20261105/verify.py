from pathlib import Path
import json,tarfile,hashlib,re
root=Path(__file__).resolve().parent
m=json.loads((root/'manifest.json').read_text());assert hashlib.sha256((root/'raw.tar.gz').read_bytes()).hexdigest()==m['archive']['sha256']
rank_checks=0
with tarfile.open(root/'raw.tar.gz') as tar:
 for member in tar.getmembers():
  b=tar.extractfile(member).read();assert hashlib.sha256(b).hexdigest()==m['files'][member.name]['sha256'];assert len(b)==m['files'][member.name]['size']
 print('archive',len(m['files']),'files',m['archive'])
 for tag in ('mmap-abba','session-abba-profile'):
  allocation=tar.extractfile(tag+'/allocation.txt').read().decode();print(tag,[s.strip() for s in allocation.splitlines() if 'CPU_IDs' in s]);cpus={}
  for host,lo,hi in re.findall(r'Nodes=(\S+) CPU_IDs=(\d+)-(\d+)',allocation):
   match=re.fullmatch(r'(.*)\[(\d+)-(\d+)\]',host)
   hosts=[host] if not match else [match[1]+str(i).zfill(len(match[2])) for i in range(int(match[2]),int(match[3])+1)]
   for h in hosts:cpus[h]=set(range(int(lo),int(hi)+1))
  for mem in tar.getmembers():
   if mem.name.startswith(tag+'/') and '/ranks/rank-' in mem.name and mem.name.endswith('.tsv'):
    rank,host,binary,gpu,affinity,sha,mpi,threads=tar.extractfile(mem).read().decode().strip('\n').split('\t');allowed=set()
    for tok in affinity.split(','):
     bounds=tok.split('-');allowed.update(range(int(bounds[0]),int(bounds[-1])+1))
    assert allowed <= cpus[host],(mem.name,host,allowed,cpus[host]);assert len(allowed)==2;assert threads=='2';assert sha=='1f387572bacfc8e7838ff57b35bcab98db43fa51e79b76697ab501ba99b3b842';rank_checks+=1
 print('rank_affinities_hashes_pass',rank_checks)
refs=json.loads((root.parents[2]/'deployment/abacus-3.11.0-beta12/native-reference.json').read_text())['partitions']['DSPRHBM']
inputs=json.loads((root/'input-digests.json').read_text());rows=json.loads((root/'results.json').read_text());errors=[];input_checks=0;cases=0
for run,records in rows.items():
 for row in records:
  assert row['returncode']==0
  if row['case']=='probe':continue
  cases+=1;ref=refs['cases'][row['case']];err=abs(row['energy_ev']-ref['energy_ev']);errors.append(err);assert err<=1e-5
  base=run+f"/{row['seq']:02}-{row['mode']}-{row['case']}/"
  for path,spec in ref['inputs'].items():
   assert inputs[base+path]['sha256']==spec['sha256'],base+path;input_checks+=1
print('science',cases,'max_error',max(errors),'input_hash_checks',input_checks)
slow=json.loads((root/'slow-syscalls.json').read_text())
for run,records in slow.items():
 print('\nTRACE',run,'long syscall sum',sum(r['seconds'] for r in records),'count',len(records))
 for row in sorted(records,key=lambda r:r['seconds'],reverse=True)[:8]:print(row)
verification=dict(archive_hash_verified=True,archive_files_verified=len(m['files']),rank_affinity_and_binary_hash_checks=rank_checks,scientific_cases=cases,input_sha256_checks=input_checks,max_reference_energy_error_ev=max(errors),tolerance_ev=1e-5,diagnostic_allocated_core_hours=16*(468+2+483)/3600,diagnostic_total_cpu_hours=(1490.655+.845+1664.122)/3600,diagnostic_slurm_wall_seconds=468+2+483)
(root/'verification.json').write_text(json.dumps(verification,indent=2)+'\n')
