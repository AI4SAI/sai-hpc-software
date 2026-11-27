"""Select supported software/partition jobs for the shared daily workflow."""
import json
import os
from release_contract import SOFTWARE, TRACKS


def plan(software='all', tracks=','.join(TRACKS), targets='all'):
    if software not in ('all', *SOFTWARE):
        raise ValueError('unknown software')
    channels = tracks.split(',')
    available = tuple(dict.fromkeys(target for row in SOFTWARE.values() for target in row['targets']))
    selected = list(available) if targets == 'all' else targets.split(',')
    for values, allowed in ((channels, TRACKS), (selected, available)):
        if not values or len(set(values)) != len(values) or any(v not in allowed for v in values):
            raise ValueError('invalid or duplicate tracks/targets')
    result = {}
    for name in SOFTWARE:
        enabled = software in ('all', name)
        supported = [t for t in selected if t in SOFTWARE[name]['targets']] if enabled else []
        result[name.replace('-', '_')] = ','.join(supported)
    if not any(result.values()):
        raise ValueError('no selected software supports the requested targets')
    return result


if __name__ == '__main__':
    result = plan(os.environ['SOFTWARE'], os.environ['TRACKS'], os.environ['TARGETS'])
    with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
        for key, value in result.items():
            stream.write(f'{key}={value}\n')
    print(json.dumps(result, indent=2))
