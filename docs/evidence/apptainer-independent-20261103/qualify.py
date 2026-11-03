import subprocess,json,os,importlib.util
from pathlib import Path
root=Path('/org/org-open-sai/proj-sai-hpc-software')
work=root/'diagnostics/apptainer-independent-20261103'
handoff=work/'handoff';prefix=work/'installed/apptainer-sai/1.4.4'
spec=importlib.util.spec_from_file_location('deploy',handoff/'install.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
try:
 m.runtime_check(prefix,work/'native-check',root/'containers/base/minimal-v1.sif')
except RuntimeError as error:
 print('NATIVE_CHECK_FAILED',str(error),flush=True)
else:
 print('NATIVE_CHECK_PASSED',flush=True)
command=['/opt/devtools/apptainer/1.4.4/bin/apptainer','exec','--fakeroot','--ignore-fakeroot-command','--no-home','--no-mount','home,cwd','--pwd','/','--bind','/usr,/sbin,/lib,/lib64,/opt,'+str(root)+',/etc/passwd,/etc/group,/etc/subuid,/etc/subgid,/etc/nsswitch.conf',str(root/'containers/base/minimal-v1.sif'),'/usr/bin/python3','-B',str(handoff/'verify.py'),'--apptainer',str(prefix/'bin/apptainer'),'--site-config',str(prefix/'etc/apptainer/apptainer.conf'),'--work',str(work/'qualified-relocated')]
env=dict(os.environ,TMPDIR=str(work/'runtime'),APPTAINER_TMPDIR=str(work/'runtime'),APPTAINER_CACHEDIR=str(work/'cache'))
raise SystemExit(subprocess.run(command,env=env).returncode)
