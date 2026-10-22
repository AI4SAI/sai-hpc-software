"""Explicitly bind a new acceptance controller to an unchanged compiled delivery."""
import json
from pathlib import Path

from md_tracking import fingerprint
from remote_controller import safe_name
from source_cache import checksum


def capture(root, control, build_run, delivery, reason):
    """Record both controller identities; never rewrite the compiled recipe."""
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1024:
        raise ValueError('acceptance controller recovery requires an explicit reason')
    root, control = Path(root), Path(control)
    build_run = safe_name(build_run)
    request_file = root / 'runs' / build_run / 'request.json'
    if request_file.resolve() != request_file or not request_file.is_file():
        raise ValueError('untrusted original build request')
    request = json.loads(request_file.read_text())
    original = Path(request['controller'])
    for path in (original, control):
        if (path.resolve() != path or not path.is_dir()
                or not path.is_relative_to(root / 'controller') or path == root / 'controller'):
            raise ValueError('acceptance recovery requires separate canonical controller snapshots')
    if (request.get('run_id') != build_run or request.get('delivery') != delivery
            or fingerprint(original) != delivery['recipe_sha256']):
        raise ValueError('original compiled controller or delivery changed')
    if original == control:
        raise ValueError('acceptance recovery requires a new controller snapshot')
    return dict(schema=1, build_run=build_run, build_controller=str(original),
                build_recipe_sha256=delivery['recipe_sha256'], build_request_sha256=checksum(request_file),
                acceptance_controller=str(control), acceptance_recipe_sha256=fingerprint(control),
                reason=reason)


def validate(binding, root, control, build_run, delivery):
    if not isinstance(binding, dict) or binding != capture(root, control, build_run, delivery, binding.get('reason')):
        raise ValueError('acceptance controller recovery binding changed')
    return Path(binding['build_controller'])
