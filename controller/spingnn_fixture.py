"""Export the release's deterministic six-atom SOC qualification model.

This is a numerical qualification fixture (seed 1793), not a trained potential.
Run only in the private export environment; inference uses the installed venv.
"""
from pathlib import Path
import hashlib,json,os,runpy,sys
import numpy as np

run=Path(sys.argv[1]).resolve()
src=run/'sources/spingnn2'
runpy.run_path(str(src/'_spingnn_source.py'))['activate_source_tree'](src)
sys.path.insert(0,str(src/'tests_native'))
import torch,e3nn
from test_contract_oracle import CASES,_config,_model,_eager
from test_lammps_pair import POS,SPIN,TYPES,_cutoff
from nequip.utils._global_options import global_options_context
from spingnn2.export import export_model
from spingnn2.reference import ReferenceModel

out=run/'acceptance';out.mkdir(exist_ok=True)
case=next(x for x in CASES if x.name=='soc-u2')
cfg=_config(case,'float64')
with global_options_context(cfg):
    eager_model=_model(case,cfg)
    manifest,binary=export_model(eager_model,out/'model',source_root=src)
    ref=ReferenceModel.load(manifest)
    delta=POS[None,:,:]-POS[:,None,:]
    shifts=-np.rint(delta/16.)
    distances=np.linalg.norm(delta+16.*shifts,axis=-1)
    i,j=np.nonzero((distances<_cutoff(ref)) & (distances>0))
    graph=dict(pos=POS.astype('float64'),spin=SPIN.astype('float64'),atom_types=TYPES.astype('int64'),
      edge_index=np.array([i,j]),cell=(np.eye(3)*16.)[None],pbc=np.ones((1,3),dtype=bool),
      edge_cell_shift=shifts[i,j],batch=np.zeros(len(POS),dtype='int64'))
    truth=ref.evaluate(graph)
    eager=_eager(eager_model,graph)
    fields=['total_energy','atomic_energy','forces','spin_forces','spin_forces_vert','virial']
    errors={k:float(np.max(np.abs(truth[k]-eager[k]))) for k in fields}
    assert max(errors.values())<1e-10,errors
    np.savez(out/'graph.npz',**graph)
    np.savez(out/'reference.npz',**{k:truth[k] for k in fields})
    np.savez(out/'eager.npz',**{k:eager[k] for k in fields})
    np.savez(out/'graph-rotated.npz',**{**graph,'spin':np.roll(graph['spin'],1,axis=1)})
    shifted=ref.evaluate({**graph,'spin':np.roll(graph['spin'],1,axis=1)})
    np.savez(out/'reference-rotated.npz',**{k:shifted[k] for k in fields})
    assert np.max(np.abs(truth['forces']))>1e-8
    assert np.max(np.abs(truth['spin_forces']))>1e-8
    response=float(shifted['total_energy'].item()-truth['total_energy'].item())
    assert abs(response)>1e-8,response
    data=dict(fixture='upstream soc-u2 / test_lammps_pair six-atom periodic geometry',seed=1793,
        trained_potential=False,release=os.environ['SAI_SPINGNN_VERSION'],source_commit=os.environ['SAI_SPINGNN_SOURCE_SHA'],
        torch=torch.__version__,e3nn=e3nn.__version__,oracle_vs_eager_max_abs=errors,
        energy_ev=float(truth['total_energy'].item()),spin_rotation_energy_change_ev=response,
        hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()})
    (out/'fixture.json').write_text(json.dumps(data,indent=2)+'\n')
    (out/'config.json').write_text(json.dumps(cfg,indent=2,default=str)+'\n')
    print(json.dumps(data,indent=2))
