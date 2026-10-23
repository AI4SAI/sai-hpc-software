#!/usr/bin/env python3
"""Check the final SIF's LibXC HOST API against pre-existing DFT repository data.

Preparation freezes raw references, input-derived exclusions and tolerances before
the candidate library is opened. Verification belongs in a separately staged
run of the unchanged final SIF. This script never submits a cluster job and
does not grant or modify CP2K build, science, publication or installation status.
"""
import argparse
import ctypes as C
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tarfile

ARCHIVE_SHA256 = '3915fac94416e4c415534223ea492ad2663f928acf27e98662c861b094a6c306'
SOURCES = {
    'gga_x_pbe': (101, '25136ea8b025c51c1ea818b040503e2cb0717b41d1ca72311fab42dd1e59da5e'),
    'gga_c_pbe': (130, '75193580a06223c04acfc3080171c5880ecf485230295e458463fb80b70eb6c8'),
}
PARAMETERS = {
    'gga_x_pbe': {'_kappa': 0.8040, '_mu': 0.2195149727645171},
    'gga_c_pbe': {'_beta': 0.06672455060314922, '_gamma': 0.031090690869654895034,
                  '_B': 1.0, '_tscale': 1.0},
}
INPUTS = ('rhoa', 'rhob', 'sigmaaa', 'sigmaab', 'sigmabb')
OUTPUTS = ('zk', 'vrhoa', 'vrhob', 'vsigmaaa', 'vsigmaab', 'vsigmabb')
NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?'
ASSIGNMENT = re.compile(r'([A-Za-z][A-Za-z0-9]*)\s*=\s*(' + NUMBER + r')')
HOST = 1 << 19
DEVICE = 1 << 18
POLICY = {
    'reference_decimal_units': 10,
    'floating_roundoff_ulps': 128,
    'zero_reference_absolute': '1e-12',
    'formula': '10 * last printed decimal unit + 128 * binary64 epsilon * abs(reference); '
               'exact printed zero uses absolute 1e-12',
    'polarized_selection': 'both spin densities positive; sigmaaa and sigmabb nonnegative; '
                           'sigmaab**2 <= sigmaaa*sigmabb, evaluated with exact Decimal inputs',
    'unpolarized_selection': 'polarized selection plus rhoa == rhob and sigmaaa == sigmaab == sigmabb',
    'unpolarized_mapping': 'rho=rhoa+rhob; sigma=sigmaaa+2*sigmaab+sigmabb; '
                           'vrho=(vrhoa+vrhob)/2; vsigma=(vsigmaaa+vsigmaab+vsigmabb)/4',
    'energy_mapping': 'DF repository zk is energy per volume; LibXC exc is per particle; compare rho*exc',
    'scope': 'LibXC 7.1.2 HOST GGA_X_PBE and GGA_C_PBE values and first derivatives only',
    'original_supplement_failure_preserved': True,
    'original_builtin_host_or_host_device_tolerances_changed': False,
}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')


def parse_reference(path):
    lines = Path(path).read_text().splitlines()
    require(lines[:2] == ['# Reference data from the Density functional repository',
                         '# http://www.cse.clrc.ac.uk/qcg/dft/'], 'unexpected reference attribution')
    records = []
    current = None
    for line_number, line in enumerate(lines, 1):
        fields = ASSIGNMENT.findall(line)
        if not fields:
            continue
        if fields[0][0] == 'rhoa':
            current = dict(index=len(records) + 1, source_line=line_number, values={})
            records.append(current)
        require(current is not None, 'reference values precede density')
        for name, value in fields:
            require(name not in current['values'], 'duplicate reference field')
            require(Decimal(value).is_finite(), 'nonfinite reference value')
            current['values'][name] = value
    require(len(records) == 23, 'expected all 23 pre-existing records')
    for record in records:
        require(set(INPUTS + OUTPUTS) <= record['values'].keys(), 'incomplete reference record')
    return records


def selection(record):
    data = {key: Decimal(record['values'][key]) for key in INPUTS}
    reasons = []
    if data['rhoa'] <= 0 or data['rhob'] <= 0:
        reasons.append('zero spin density endpoint: LibXC screens/floors individual spin densities')
    if data['sigmaaa'] < 0 or data['sigmabb'] < 0:
        reasons.append('negative squared gradient')
    if data['sigmaab'] ** 2 > data['sigmaaa'] * data['sigmabb']:
        reasons.append('nonphysical spin gradients: Cauchy-Schwarz inequality is violated')
    symmetric = data['rhoa'] == data['rhob'] and data['sigmaaa'] == data['sigmaab'] == data['sigmabb']
    return dict(index=record['index'], source_line=record['source_line'],
                inputs={key: record['values'][key] for key in INPUTS},
                polarized=not reasons, polarized_exclusion_reasons=reasons,
                unpolarized=not reasons and symmetric,
                unpolarized_exclusion_reasons=reasons + ([] if symmetric else ['not spin-symmetric density and gradient']))


def protocol_for(directory):
    functions = {}
    for name, (number, expected) in SOURCES.items():
        path = directory / (name + '.data')
        require(path.is_file() and not path.is_symlink() and sha(path) == expected,
                'raw reference differs: ' + name)
        records = parse_reference(path)
        selected = [selection(record) for record in records]
        require(sum(row['polarized'] for row in selected) == 16, 'polarized selection changed')
        require(sum(row['unpolarized'] for row in selected) == 11, 'unpolarized selection changed')
        functions[name] = dict(functional_id=number, file=path.name, sha256=expected,
                               archive_member='libxc-7.1.2/testsuite/df_repo/' + path.name,
                               records=selected, expected_default_parameters=PARAMETERS[name])
    return dict(schema=1, source_archive_sha256=ARCHIVE_SHA256, source_version='7.1.2',
                reference_origin='Density functional repository; http://www.cse.clrc.ac.uk/qcg/dft/',
                policy=POLICY, functionals=functions)


def prepare(archive, destination):
    require(sha(archive) == ARCHIVE_SHA256, 'unexpected LibXC source archive')
    destination.mkdir()
    with tarfile.open(archive, 'r:bz2') as source:
        for name, (_, expected) in SOURCES.items():
            member = source.getmember('libxc-7.1.2/testsuite/df_repo/' + name + '.data')
            require(member.isfile(), 'reference is not a regular archive member')
            content = source.extractfile(member).read()
            require(hashlib.sha256(content).hexdigest() == expected, 'archive reference differs')
            with (destination / (name + '.data')).open('xb') as output:
                output.write(content)
    protocol = destination / 'protocol.json'
    write_json(protocol, protocol_for(destination))
    print(json.dumps(dict(protocol=str(protocol), protocol_sha256=sha(protocol)), sort_keys=True))


def reference_bound(token):
    value = Decimal(token)
    if not value:
        return Decimal(POLICY['zero_reference_absolute'])
    unit = Decimal(1).scaleb(value.as_tuple().exponent)
    floating = Decimal(POLICY['floating_roundoff_ulps']) * Decimal.from_float(sys.float_info.epsilon) * abs(value)
    return Decimal(POLICY['reference_decimal_units']) * unit + floating


def comparison(name, actual, expected, tolerance):
    require(math.isfinite(actual), 'nonfinite candidate ' + name)
    delta = abs(Decimal.from_float(actual) - expected)
    return dict(quantity=name, actual=actual, expected=str(expected), tolerance=str(tolerance),
                absolute_error=str(delta), passed=delta <= tolerance)


def bind(library, name, arguments, returns):
    function = getattr(library, name)
    function.argtypes = arguments
    function.restype = returns
    return function


class Library:
    """Opaque handles and signatures from the pinned LibXC 7.1.2 xc.h."""
    def __init__(self, path):
        self.path = path
        self.handle = C.CDLL(str(path), mode=os.RTLD_NOW | os.RTLD_LOCAL)
        pointer, integer = C.c_void_p, C.c_int
        real_pointer = C.POINTER(C.c_double)
        self.functions = {}
        signatures = {
            'xc_version': ([C.POINTER(integer)] * 3, None),
            'xc_func_alloc': ([], pointer),
            'xc_func_init_flags': ([pointer, integer, integer, integer], integer),
            'xc_func_get_info': ([pointer], pointer),
            'xc_func_info_get_flags': ([pointer], integer),
            'xc_func_info_get_number': ([pointer], integer),
            'xc_func_info_get_n_ext_params': ([pointer], integer),
            'xc_func_info_get_ext_params_name': ([pointer, integer], C.c_char_p),
            'xc_func_get_ext_params': ([pointer, real_pointer], None),
            'xc_gga_exc_vxc': ([pointer, C.c_size_t] + [real_pointer] * 5, None),
            'xc_func_end': ([pointer], None),
            'xc_func_free': ([pointer], None),
        }
        for name, (arguments, returns) in signatures.items():
            self.functions[name] = bind(self.handle, name, arguments, returns)
        class DlInfo(C.Structure):
            _fields_ = [('filename', C.c_char_p), ('base', pointer),
                        ('symbol_name', C.c_char_p), ('symbol_address', pointer)]
        dl = C.CDLL('libdl.so.2')
        dladdr = bind(dl, 'dladdr', [pointer, C.POINTER(DlInfo)], integer)
        self.providers = {}
        for name, function in self.functions.items():
            info = DlInfo()
            require(dladdr(C.cast(function, pointer), C.byref(info)) != 0 and info.filename,
                    'cannot identify symbol provider: ' + name)
            provider = Path(os.fsdecode(info.filename)).resolve(strict=True)
            require(provider == path, 'symbol came from another library: ' + name)
            self.providers[name] = str(provider)
        version = [integer(), integer(), integer()]
        self.functions['xc_version'](*(C.byref(item) for item in version))
        self.version = [item.value for item in version]
        require(self.version == [7, 1, 2], 'unexpected LibXC version')

    def evaluate(self, name, spin, records):
        f = self.functions
        number = SOURCES[name][0]
        handle = f['xc_func_alloc']()
        require(handle, 'LibXC allocation failed')
        initialized = False
        try:
            require(f['xc_func_init_flags'](handle, number, spin, HOST) == 0, 'LibXC initialization failed')
            initialized = True
            info = f['xc_func_get_info'](handle)
            require(info, 'missing LibXC functional information')
            require(f['xc_func_info_get_number'](info) == number, 'wrong functional initialized')
            flags = f['xc_func_info_get_flags'](info)
            require(flags & (HOST | DEVICE) == HOST, 'functional did not select the HOST backend')
            count = f['xc_func_info_get_n_ext_params'](info)
            require(count == len(PARAMETERS[name]), 'unexpected parameter count')
            parameters = (C.c_double * count)()
            f['xc_func_get_ext_params'](handle, parameters)
            actual_parameters = {f['xc_func_info_get_ext_params_name'](info, i).decode(): parameters[i]
                                 for i in range(count)}
            require(actual_parameters == PARAMETERS[name], 'functional defaults differ from pinned source')
            results = []
            for record in records:
                chosen = selection(record)
                if not chosen['unpolarized' if spin == 1 else 'polarized']:
                    continue
                values = record['values']
                d = {key: Decimal(value) for key, value in values.items()}
                density = d['rhoa'] + d['rhob']
                if spin == 2:
                    rho_values = [d['rhoa'], d['rhob']]
                    sigma_values = [d['sigmaaa'], d['sigmaab'], d['sigmabb']]
                else:
                    rho_values = [density]
                    sigma_values = [d['sigmaaa'] + 2 * d['sigmaab'] + d['sigmabb']]
                rho = (C.c_double * len(rho_values))(*(float(x) for x in rho_values))
                sigma = (C.c_double * len(sigma_values))(*(float(x) for x in sigma_values))
                exc = (C.c_double * 1)(math.nan)
                vrho = (C.c_double * len(rho_values))(*([math.nan] * len(rho_values)))
                vsigma = (C.c_double * len(sigma_values))(*([math.nan] * len(sigma_values)))
                f['xc_gga_exc_vxc'](handle, 1, rho, sigma, exc, vrho, vsigma)
                checks = [comparison('zk_energy_density', float(density) * exc[0], d['zk'], reference_bound(values['zk']))]
                if spin == 2:
                    for key, actual in zip(OUTPUTS[1:], [*vrho, *vsigma]):
                        checks.append(comparison(key, actual, d[key], reference_bound(values[key])))
                else:
                    for key, actual, members, divisor in [
                            ('vrho', vrho[0], ('vrhoa', 'vrhob'), 2),
                            ('vsigma', vsigma[0], ('vsigmaaa', 'vsigmaab', 'vsigmabb'), 4)]:
                        expected = sum(d[field] for field in members) / divisor
                        bound = sum(reference_bound(values[field]) for field in members) / divisor
                        checks.append(comparison(key, actual, expected, bound))
                results.append(dict(reference_index=record['index'], source_line=record['source_line'],
                                    inputs={key: values[key] for key in INPUTS},
                                    exc_per_particle=exc[0], checks=checks,
                                    passed=all(check['passed'] for check in checks)))
            require(len(results) == (11 if spin == 1 else 16), 'missing reference evaluations')
            return dict(nspin=spin, flags=flags, parameters=actual_parameters, records=results,
                        passed=all(result['passed'] for result in results))
        finally:
            if initialized:
                f['xc_func_end'](handle)
            f['xc_func_free'](handle)


def verify(args):
    # Freeze and check the independent protocol before loading candidate code.
    protocol_path = args.protocol.resolve(strict=True)
    require(sha(protocol_path) == args.expected_protocol_sha256, 'protocol hash differs')
    protocol = json.loads(protocol_path.read_text())
    require(protocol == protocol_for(protocol_path.parent), 'protocol/reference selection changed')
    records = {name: parse_reference(protocol_path.parent / (name + '.data')) for name in SOURCES}
    require(args.prefix.is_absolute() and args.prefix.resolve(strict=True) == args.prefix,
            'install prefix must be canonical')
    library = args.library.resolve(strict=True)
    require(library.is_file() and library.is_relative_to(args.prefix), 'library is outside the delivery prefix')
    allowed = (library.parent in (args.prefix / 'lib', args.prefix / 'lib64')
               and library.name.startswith('libcp2k.so')) or (
                   library.parent == args.prefix / 'dependencies/libxc/lib'
                   and library.name.startswith('libxc.so'))
    require(allowed, 'expected private libxc.so or static LibXC provider libcp2k.so')
    require(sha(library) == args.expected_library_sha256, 'library checksum differs before load')
    require(not args.output.exists(), 'result file already exists')
    report = dict(schema=1, status='failed', started_at=datetime.now(timezone.utc).isoformat(),
                  protocol_sha256=args.expected_protocol_sha256, protocol=protocol,
                  script_sha256=sha(Path(__file__).resolve()),
                  library=str(library), library_sha256=args.expected_library_sha256,
                  install_prefix=str(args.prefix), scientific_verified=False,
                  original_supplement_failure_preserved=True,
                  scope=POLICY['scope'], source_archive_sha256=ARCHIVE_SHA256)
    try:
        candidate = Library(library)
        report.update(version=candidate.version, symbol_providers=candidate.providers,
                      backend='HOST', functionals={})
        for name in SOURCES:
            report['functionals'][name] = [candidate.evaluate(name, spin, records[name]) for spin in (2, 1)]
        require(sha(library) == args.expected_library_sha256, 'library checksum changed after evaluation')
        require(sha(protocol_path) == args.expected_protocol_sha256, 'protocol checksum changed after evaluation')
        passed = all(result['passed'] for modes in report['functionals'].values() for result in modes)
        report['numerical_reference_verified'] = passed
        report['status'] = 'passed' if passed else 'failed'
    except Exception as error:
        report['error'] = str(error)
        report['numerical_reference_verified'] = False
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    write_json(args.output, report)
    print(json.dumps({key: report[key] for key in ('status', 'numerical_reference_verified', 'library',
                                                 'protocol_sha256')}, sort_keys=True))
    return 0 if report['status'] == 'passed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--archive', type=Path, required=True)
    prep.add_argument('--output-directory', type=Path, required=True)
    check = commands.add_parser('verify')
    check.add_argument('--library', type=Path, required=True)
    check.add_argument('--expected-library-sha256', required=True)
    check.add_argument('--prefix', type=Path, required=True)
    check.add_argument('--protocol', type=Path, required=True)
    check.add_argument('--expected-protocol-sha256', required=True)
    check.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args.archive, args.output_directory)
        return 0
    return verify(args)


if __name__ == '__main__':
    raise SystemExit(main())
