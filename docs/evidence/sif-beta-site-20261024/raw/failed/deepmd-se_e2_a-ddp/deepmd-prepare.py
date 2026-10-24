"""Small launch/training fixture; this is not a trained scientific potential."""
import json
from pathlib import Path
import numpy as np

fixture = json.loads(Path('fixture.json').read_text())
data = Path('data/set.000')
data.mkdir(parents=True)
np.savetxt('data/type.raw', fixture['atom_types'], fmt='%d')
Path('data/type_map.raw').write_text('O\nH\n')
for name, values in {
    'coord': fixture['coordinates'], 'box': fixture['box'],
    'energy': [fixture['reference']['energy']], 'force': fixture['reference']['forces'],
}.items():
    array = np.asarray(values, dtype=np.float64).reshape(1, -1)
    np.save(data / (name + '.npy'), np.repeat(array, 4, axis=0))
configuration = {
    'model': {'type_map':['O','H'],
        'descriptor': {'type':'se_e2_a','sel':[4,8], 'rcut_smth':3.5, 'rcut':4.0,
                       'neuron':[8,8], 'resnet_dt':False, 'axis_neuron':4, 'type_one_side':True, 'seed':1},
        'fitting_net': {'neuron':[8,8], 'resnet_dt':True, 'seed':1}, 'data_stat_nbatch':1},
    'learning_rate': {'type':'exp','decay_steps':2,'start_lr':0.001,'stop_lr':0.0001},
    'loss': {'type':'ener','start_pref_e':1,'limit_pref_e':1,'start_pref_f':1,'limit_pref_f':1},
    'training': {'training_data':{'systems':['./data'],'batch_size':1},
                 'validation_data':{'systems':['./data'],'batch_size':1,'numb_btch':1},
                 'numb_steps':2,'seed':10,'disp_file':'lcurve.out','disp_freq':1,'save_freq':1},
}
Path('input.json').write_text(json.dumps(configuration, indent=2) + '\n')
