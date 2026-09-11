"""Validate native delivery modules with a real Tcl interpreter and mocked Lmod."""
import copy
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controller"))
from native_module import (render_native_fragment, render_native_selector, tcl,
                           validate_native_entry)
from release_contract import make_identity, allowed_partitions


def example(software="abacus", target="4v100-avx512", track="development"):
    identity = make_identity(software, track, "develop", "a" * 40, "3.11.0",
                             "b" * 64, target)
    command = {"abacus": "abacus", "cp2k": "cp2k.psmp", "lammps": "lmp",
               "gpumd": "gpumd", "deepmd-kit": "dp"}[software]
    return {"identity": identity, "commands": {command: "bin/" + command},
            "external_roots": ["/opt/devtools/openmpi", "/opt/modules/modulefiles/devtools"],
            "runtime": {"modules": ["openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto"],
                        "prepend": {"MODULEPATH": ["/opt/modules/modulefiles/devtools"],
                                    "LD_LIBRARY_PATH": ["/opt/devtools/openmpi/lib"]},
                        "set": {"OMP_NUM_THREADS": "2"}}}


class NativeValidationTests(unittest.TestCase):
    def test_identity_is_fully_recomputed(self):
        for key, value in (("install_prefix", "/opt/software/untrusted"),
                           ("source_sha", "c" * 40), ("cpu_arch", "native"),
                           ("partition", "DSPRHBM"), ("schema", True),
                           ("recipe_sha256", "0" * 64)):
            with self.subTest(key=key):
                entry = example()
                entry["identity"][key] = value
                for render in (lambda: render_native_fragment(entry),
                               lambda: render_native_selector(entry["identity"])):
                    with self.assertRaises(ValueError):
                        render()

    def test_strict_entry_and_runtime_schema(self):
        for modify in (lambda entry: entry.update(shell="source /control/env.sh"),
                       lambda entry: entry["runtime"].update(shell="env.sh"),
                       lambda entry: entry.pop("commands"),
                       lambda entry: entry["runtime"].pop("set")):
            entry = example()
            modify(entry)
            with self.assertRaises(ValueError):
                validate_native_entry(entry)
        entry = example()
        original = copy.deepcopy(entry)
        result = validate_native_entry(entry)
        result["runtime"]["prepend"]["LD_LIBRARY_PATH"].append("/usr/lib")
        self.assertEqual(entry, original)

    def test_cpu_and_gpu_software_partition_sets(self):
        for software in ("abacus", "cp2k", "deepmd-kit", "lammps", "gpumd"):
            selector = render_native_selector(example(software)["identity"])
            self.assertEqual("DSPRHBM" in selector, software in ("abacus", "cp2k"))
            for partition in ("4V100", "16V100", "8V100V0"):
                self.assertIn(partition, selector)
            self.assertEqual(len(allowed_partitions(software)), 4 if software in ("abacus", "cp2k") else 3)

    def test_runtime_paths_reject_build_host_paths_and_traversal(self):
        for path in ("/workspace/lib", "/control/lib", "/input/lib", "/home/stardust/snapshot/lib",
                     "/tmp/lib", "/opt/apps/unknown/lib", "/opt/devtools/openmpi-old/lib",
                     "/usr/../workspace", "/usr//lib", "/usr/lib/", "/usr/lib:/workspace",
                     "//usr/lib", "/usr/lib\nX", "/usr/lib\\injected", "relative/lib"):
            with self.subTest(path=path):
                entry = example()
                entry["runtime"]["prepend"]["LD_LIBRARY_PATH"] = [path]
                with self.assertRaises(ValueError):
                    render_native_fragment(entry)
        for path in ("/usr/lib", "/lib/x86_64-linux-gnu", "/lib64", "/opt/devtools/openmpi/lib"):
            entry = example()
            entry["runtime"]["prepend"]["LD_LIBRARY_PATH"] = [path]
            render_native_fragment(entry)

    def test_explicit_external_roots_and_other_delivery_prefixes(self):
        entry = example("lammps")
        deepmd = example("deepmd-kit")["identity"]["install_prefix"]
        entry["runtime"]["prepend"]["LD_LIBRARY_PATH"] = [deepmd + "/lib"]
        with self.assertRaises(ValueError):
            render_native_fragment(entry)
        self.assertIn(deepmd, render_native_fragment(entry, allowed_prefixes=[deepmd]))
        other_partition = example("deepmd-kit", "16v100-avx2")["identity"]["install_prefix"]
        entry["runtime"]["prepend"]["LD_LIBRARY_PATH"] = [other_partition + "/lib"]
        with self.assertRaises(ValueError):
            render_native_fragment(entry, allowed_prefixes=[deepmd, other_partition])
        for root in ("/", "/opt", "/opt/apps", "/opt/devtools", "/home/project", "/workspace", "/control"):
            with self.subTest(root=root):
                entry = example()
                entry["external_roots"].append(root)
                with self.assertRaises(ValueError):
                    render_native_fragment(entry)
                with self.assertRaises(ValueError):
                    render_native_fragment(example(), allowed_prefixes=[root])
        entry = example()
        entry["external_roots"].append("/opt/apps/plumed/2.10.1")
        entry["runtime"]["set"]["PLUMED_KERNEL"] = "/opt/apps/plumed/2.10.1/lib/libplumedKernel.so"
        render_native_fragment(entry)

    def test_no_arbitrary_environment_overrides_or_tcl_module_injection(self):
        for name in ("LD_PRELOAD", "LD_AUDIT", "LDPRELOAD", "BASH_ENV", "ENV", "PYTHONSTARTUP",
                     "PYTHONINSPECT", "PROMPT_COMMAND", "CUDA_VISIBLE_DEVICES", "SLURM_JOB_PARTITION",
                     "SAI_ABACUS_PREFIX", "PATH", "LD_LIBRARY_PATH", "DP_ARBITRARY", "TF_ARBITRARY",
                     "NCCL_DEBUG", "OMP_BAD", "OMP_NUM_THREADS;error injected"):
            with self.subTest(name=name):
                entry = example()
                entry["runtime"]["set"][name] = "1"
                with self.assertRaises(ValueError):
                    render_native_fragment(entry)
        for module in ("gcc", "gcc/13.3.0;error injected", "gcc/$bad", "gcc/[error injected]",
                       "gcc/13.3.0\nerror injected", "gcc/13.3.0 other", "-x/1", "../13.3.0",
                       "apptainer/1.4.4", "abacus/old"):
            with self.subTest(module=module):
                entry = example()
                entry["runtime"]["modules"] = [module]
                with self.assertRaises(ValueError):
                    render_native_fragment(entry)

    def test_bounded_scalar_settings_and_paths(self):
        entry = example("cp2k")
        entry["runtime"]["set"].update({"CP2K_DATA_DIR": entry["identity"]["install_prefix"] + "/share/cp2k/data",
                                         "OMP_PROC_BIND": "close", "OMP_PLACES": "cores",
                                         "DP_INFER_BATCH_SIZE": "auto:4096", "TF_CPP_MIN_LOG_LEVEL": "2"})
        render_native_fragment(entry)
        for name, value in (("OMP_NUM_THREADS", "0"), ("OMP_NUM_THREADS", "65537"),
                            ("OMP_NUM_THREADS", 2), ("OMP_NUM_THREADS", "1;error bad"),
                            ("OMP_PLACES", "[error bad]"), ("DP_INFER_BATCH_SIZE", "auto:0"),
                            ("CP2K_DATA_DIR", "/control/data"), ("TF_CPP_MIN_LOG_LEVEL", "4")):
            with self.subTest(name=name, value=value):
                invalid = copy.deepcopy(entry)
                invalid["runtime"]["set"][name] = value
                with self.assertRaises(ValueError):
                    render_native_fragment(invalid)

    def test_commands_are_packaged_executables_not_shell(self):
        for commands in ({}, {"run": "/bin/run"}, {"run": "bin/../run"},
                         {"run": "bin/./run"}, {"run": "bin/tool/run;error injected"},
                         {"abacus": "bin/not-abacus"}, {"run x": "bin/run x"}):
            with self.subTest(commands=commands):
                entry = example()
                entry["commands"] = commands
                with self.assertRaises(ValueError):
                    render_native_fragment(entry)
        entry = example("cp2k")
        entry["commands"] = {"cp2k.psmp": "bin/nested/cp2k.psmp"}
        self.assertIn("/bin/nested", render_native_fragment(entry))


@unittest.skipUnless(shutil.which("tclsh"), "real Tcl interpreter required")
class NativeTclTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.rootfs = self.root / "rootfs"

    def install(self, entry):
        prefix = self.rootfs / entry["identity"]["install_prefix"].lstrip("/")
        for relative in ("bin", "lib", "lib64", "include", "lib/pkgconfig", "share/man", "share/sai"):
            (prefix / relative).mkdir(parents=True, exist_ok=True)
        for relative in entry["commands"].values():
            command = prefix / relative
            command.parent.mkdir(parents=True, exist_ok=True)
            command.write_text("# scientific executable fixture\n")
            command.chmod(0o755)
        fragment = prefix / "share/sai/native-module.tcl"
        fragment.write_text(render_native_fragment(entry))
        identity = entry["identity"]
        selector = self.root / "modulefiles" / identity["software"] / identity["track"] / identity["build_id"]
        selector.parent.mkdir(parents=True, exist_ok=True)
        selector.write_text(render_native_selector(identity))
        return selector, fragment, prefix

    def evaluate(self, selector, *, partition="4V100", job="1234", mode="load", saved=None,
                 depends_on=True, after=""):
        environment = {name: value for name, value in os.environ.items()
                       if not name.startswith("SAI_") and not name.startswith("SLURM_")}
        environment["PATH"] = "/usr/bin:/bin"
        for name in ("LD_LIBRARY_PATH", "LIBRARY_PATH", "MODULEPATH", "CPATH", "CMAKE_PREFIX_PATH"):
            environment.pop(name, None)
        if partition is not None:
            environment["SLURM_JOB_PARTITION"] = partition
        if job is not None:
            environment["SLURM_JOB_ID"] = job
        environment.update(saved or {})
        # File/source mocks map only canonical /opt paths into a test rootfs.
        # Generated Tcl, mode logic, quoting, path checks and env operations are
        # otherwise interpreted exactly by tclsh, with no host /opt writes.
        script = [f"set test_rootfs {tcl(str(self.rootfs))}", f"set mode {tcl(mode)}", "set effects {}",
                  'proc mapped {path} {global test_rootfs; if {$path eq "/opt" || [string match "/opt/*" $path]} {return "$test_rootfs$path"}; return $path}',
                  'rename file original_file',
                  'proc file {operation args} {if {$operation in {exists isfile isdirectory executable type readlink}} {set args [lreplace $args 0 0 [mapped [lindex $args 0]]]}; return [uplevel 1 [list original_file $operation {*}$args]]}',
                  'rename source original_source',
                  'proc source {path} {return [uplevel 1 [list original_source [mapped $path]]]}',
                  'proc module-info {kind arg} {global mode; return [expr {$kind eq "mode" && $arg eq $mode}]}',
                  'proc module-whatis {args} {}', 'proc conflict {args} {}',
                  'proc module {args} {global effects; lappend effects [list MODULE {*}$args]}',
                  'proc prepend-path {name value} {global env mode effects; lappend effects [list PREPEND $name $value]; set items {}; if {[info exists env($name)]} {set items [split $env($name) :]}; set found [lsearch -exact $items $value]; if {$found >= 0} {set items [lreplace $items $found $found]}; if {$mode ne "remove"} {set items [linsert $items 0 $value]}; set env($name) [join $items :]}',
                  'proc setenv {name value} {global env mode effects; lappend effects [list SET $name $value]; if {$mode eq "remove"} {unset -nocomplain env($name)} else {set env($name) $value}}']
        if depends_on:
            script.append('proc depends-on {args} {global effects; lappend effects [list DEPENDS {*}$args]}')
        script += [f'if {{[catch {{source {tcl(str(selector))}}} reason]}} {{puts stderr $reason; exit 1}}',
                   after,
                   'foreach effect $effects {puts "EFFECT\t$effect"}',
                   'foreach name [lsort [array names env]] {if {[string match "SAI_*" $name] || $name in {PATH LD_LIBRARY_PATH CP2K_DATA_DIR OMP_NUM_THREADS}} {puts "STATE\t$name\t$env($name)"}}']
        return subprocess.run([shutil.which("tclsh")], input="\n".join(script), text=True,
                              capture_output=True, env=environment)

    def states(self, result):
        return dict(line.split("\t", 2)[1:] for line in result.stdout.splitlines() if line.startswith("STATE\t"))

    def test_native_load_no_container_and_app_paths_override_dependencies(self):
        entry = example()
        selector, _, _ = self.install(entry)
        result = self.evaluate(selector)
        self.assertEqual(result.returncode, 0, result.stderr)
        states = self.states(result)
        prefix = entry["identity"]["install_prefix"]
        self.assertEqual(states["SAI_ABACUS_PREFIX"], prefix)
        self.assertEqual(states["SAI_ABACUS_NATIVE_PARTITION"], "4V100")
        self.assertEqual(states["PATH"].split(":")[0], prefix + "/bin")
        self.assertEqual(states["LD_LIBRARY_PATH"].split(":"), [prefix + "/lib", prefix + "/lib64", "/opt/devtools/openmpi/lib"])
        self.assertIn("DEPENDS openmpi/", result.stdout)
        self.assertNotIn("apptainer", selector.read_text().lower())
        self.assertNotIn("controller", selector.read_text().lower())
        fallback = self.evaluate(selector, depends_on=False)
        self.assertEqual(fallback.returncode, 0, fallback.stderr)
        self.assertIn("MODULE load openmpi/", fallback.stdout)

    def test_load_allocation_required_but_display_and_help_have_no_effects(self):
        selector, _, _ = self.install(example())
        for partition, job in ((None, None), ("4V100", None), (None, "123"), ("4V100", "bad"),
                               ("unknown", "123"), ("4V100,DSPRHBM", "123")):
            with self.subTest(partition=partition, job=job):
                self.assertNotEqual(self.evaluate(selector, partition=partition, job=job).returncode, 0)
        # No filesystem or dependency load is needed even after removing payload.
        shutil.rmtree(self.rootfs)
        for mode in ("display", "help", "whatis", "test"):
            result = self.evaluate(selector, partition=None, job=None, mode=mode)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("EFFECT", result.stdout)
        result = self.evaluate(selector, partition=None, job=None, mode="help", after="ModulesHelp")
        self.assertIn("expected prefix /opt/software/", result.stderr)

    def test_missing_canonical_prefix_and_symlink_fragment_or_parent_fail(self):
        selector, fragment, prefix = self.install(example())
        fragment.unlink()
        self.assertNotEqual(self.evaluate(selector).returncode, 0)
        outside = self.root / "outside-fragment"
        outside.write_text('error "untrusted source executed"\n')
        fragment.symlink_to(outside)
        result = self.evaluate(selector)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlinks", result.stderr)
        self.assertNotIn("untrusted source executed", result.stderr)
        fragment.unlink()
        fragment.write_text(render_native_fragment(example()))
        moved = self.root / "moved-prefix"
        prefix.rename(moved)
        prefix.symlink_to(moved, target_is_directory=True)
        result = self.evaluate(selector)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlinks", result.stderr)
        prefix.unlink()
        self.assertNotEqual(self.evaluate(selector).returncode, 0)

    def test_partitions_cannot_mix_and_remove_uses_saved_partition(self):
        cpu, gpu = example("cp2k", "dsprhbm"), example("cp2k", "16v100-avx2")
        cpu["runtime"]["modules"] = ["gcc/13.3.0"]
        gpu["runtime"]["modules"].append("nvmplibs/26.7-tmp")
        cpu_selector, _, _ = self.install(cpu)
        gpu_selector, _, _ = self.install(gpu)
        self.assertEqual(render_native_selector(cpu["identity"]), render_native_selector(gpu["identity"]))
        for partition, entry in (("DSPRHBM", cpu), ("16V100", gpu)):
            result = self.evaluate(cpu_selector, partition=partition)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.states(result)["SAI_CP2K_PREFIX"], entry["identity"]["install_prefix"])
            self.assertEqual("nvmplibs/26.7-tmp" in result.stdout, partition == "16V100")
        saved = {"SAI_CP2K_PREFIX": cpu["identity"]["install_prefix"], "SAI_CP2K_NATIVE_PARTITION": "DSPRHBM"}
        removed = self.evaluate(gpu_selector, partition="16V100", mode="remove", saved=saved)
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertIn("DEPENDS gcc/13.3.0", removed.stdout)
        self.assertNotIn("nvmplibs/", removed.stdout)
        self.assertNotIn("SAI_CP2K_PREFIX", self.states(removed))
        mixed = self.evaluate(gpu_selector, partition="16V100", saved=saved)
        self.assertNotEqual(mixed.returncode, 0)
        self.assertIn("another native partition", mixed.stderr)
        self.assertNotEqual(self.evaluate(cpu_selector, mode="remove", saved={}).returncode, 0)
        saved["SAI_CP2K_NATIVE_PARTITION"] = "../../4V100"
        self.assertNotEqual(self.evaluate(cpu_selector, mode="remove", saved=saved).returncode, 0)

    def test_gpu_only_selector_and_direct_fragment_reject_cpu(self):
        selector, fragment, _ = self.install(example("gpumd"))
        self.assertNotEqual(self.evaluate(selector, partition="DSPRHBM").returncode, 0)
        self.assertNotEqual(self.evaluate(fragment, partition="DSPRHBM").returncode, 0)
        self.assertNotEqual(self.evaluate(fragment, partition=None, job=None).returncode, 0)
        self.assertEqual(self.evaluate(fragment, partition=None, job=None, mode="display").returncode, 0)

    def test_another_build_cannot_replace_or_unload_loaded_build(self):
        old, new = example(), example()
        new["identity"] = make_identity("abacus", "development", "develop", "c" * 40,
                                        "3.11.0", "b" * 64, "4v100-avx512")
        old_selector, _, _ = self.install(old)
        new_selector, _, _ = self.install(new)
        saved = {"SAI_ABACUS_NATIVE_PARTITION": "4V100",
                 "SAI_ABACUS_PREFIX": old["identity"]["install_prefix"]}
        self.assertNotEqual(self.evaluate(new_selector, saved=saved).returncode, 0)
        self.assertNotEqual(self.evaluate(new_selector, mode="remove", saved=saved).returncode, 0)
        self.assertEqual(self.evaluate(old_selector, mode="remove", partition=None,
                                       job=None, saved=saved).returncode, 0)

    def test_runtime_quoted_paths_are_literal_and_never_executed(self):
        entry = example("cp2k")
        root = '/opt/apps/plumed/space $not_a_variable [error injected] "quotes";not-a-command'
        entry["external_roots"].append(root)
        entry["runtime"]["set"]["PLUMED_KERNEL"] = root + "/lib/libplumedKernel.so"
        entry["runtime"]["set"]["CP2K_DATA_DIR"] = root + "/data"
        selector, _, _ = self.install(entry)
        result = self.evaluate(selector)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.states(result)["CP2K_DATA_DIR"], root + "/data")
        literal = '$not_a_variable [error injected] ; "quotes" \\ slash\nnewline\tend'
        result = subprocess.run([shutil.which("tclsh")], input="puts -nonewline " + tcl(literal),
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, literal)


if __name__ == "__main__":
    unittest.main()
