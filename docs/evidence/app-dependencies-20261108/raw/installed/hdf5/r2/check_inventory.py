import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from devtools_install import verify
run=Path(sys.argv[2]); prefix=sys.argv[3]
rows=json.loads((run/'inventory.json').read_text())
verify(prefix,rows)
(run/'results/inventory-passed.json').write_text(json.dumps(dict(passed=True,prefix=prefix,entries=len(rows),bytes=sum(r.get('size',0) for r in rows)),sort_keys=True)+'\n')
print('PHYSICAL_HDF5_INVENTORY_PASS',len(rows))
