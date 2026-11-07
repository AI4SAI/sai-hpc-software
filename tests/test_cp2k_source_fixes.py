"""Guard source repair against duplicate insertion and unrelated source edits."""
import hashlib
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import cp2k_source_fixes as fixes


class ELPASetupFixTests(unittest.TestCase):
    def test_repair_is_idempotent_and_records_exact_source_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source/src/fm/cp_fm_elpa.F'
            source.parent.mkdir(parents=True)
            original = 'SUBROUTINE test\n' + fixes.DECLARATION + fixes.ANCHOR + 'END SUBROUTINE\n'
            source.write_text(original)
            record = fixes.fix(root / 'source', root / 'metadata')
            self.assertTrue(record['applied'])
            self.assertEqual(record['before_sha256'], hashlib.sha256(original.encode()).hexdigest())
            changed = source.read_bytes()
            self.assertEqual(changed.count(b'elpa_obj%setup_gpu()'), 1)
            again = fixes.fix(root / 'source', root / 'metadata')
            self.assertFalse(again['applied'])
            self.assertEqual(source.read_bytes(), changed)

    def test_unknown_source_is_preserved_and_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source/src/fm/cp_fm_elpa.F'
            source.parent.mkdir(parents=True)
            source.write_text('unexpected changed source\n')
            with self.assertRaisesRegex(ValueError, 'unrecognized'):
                fixes.fix(root / 'source', root / 'metadata')
            self.assertEqual(source.read_text(), 'unexpected changed source\n')
            self.assertFalse((root / 'metadata').exists())


class NCCLProgressFixTests(unittest.TestCase):
    def test_real_release_source_repair_and_repeated_application(self):
        fixture = Path(__file__).parent / 'fixtures/cp2k_cusolver_v2026_2.c.gz'
        original = gzip.decompress(fixture.read_bytes())
        self.assertEqual(hashlib.sha256(original).hexdigest(),
                         '379cda72ac8c18e18f30ffd019b37ae0e8216572c40f5e6ad2fbceb6decd4ad8')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source/src/fm/cp_fm_cusolver.c'
            source.parent.mkdir(parents=True)
            source.write_bytes(original)
            record = fixes.fix_nccl_progress(root / 'source', root / 'metadata')
            self.assertTrue(record['applied'])
            repaired = source.read_bytes()
            self.assertEqual(hashlib.sha256(repaired).hexdigest(),
                             'eb22082437dbee127fedd77a8f7e2f7cb05444b507b2a4be29843cc1fa9ceff3')
            self.assertEqual(repaired.count(b'MPI_Barrier(comm);'), 6)
            self.assertEqual(json.loads((root / 'metadata/nccl-mpi-progress-fix.json').read_text()), record)
            self.assertFalse(fixes.fix_nccl_progress(root / 'source', root / 'metadata')['applied'])
            self.assertEqual(source.read_bytes(), repaired)

    def test_shared_context_source_repairs_only_initialization_and_is_idempotent(self):
        fixture = Path(__file__).parent / 'fixtures/cp2k_cusolver_0761fd1b.c.gz'
        original = gzip.decompress(fixture.read_bytes())
        self.assertEqual(hashlib.sha256(original).hexdigest(),
                         '9f68fe666f1f1c9da411c8bd6dd7810dd8572c39c486efbe4afb4a3c15e14b31')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source/src/fm/cp_fm_cusolver.c'
            source.parent.mkdir(parents=True)
            source.write_bytes(original)
            record = fixes.fix_nccl_progress(root / 'source', root / 'metadata')
            self.assertTrue(record['applied'])
            self.assertEqual(record['bootstrap_sites'], 1)
            repaired = source.read_bytes()
            self.assertEqual(hashlib.sha256(repaired).hexdigest(),
                             '6fa681c45d5dce92eeb33a7047a851d5bcc5146133e4e50d48804a30f1fa7b40')
            self.assertEqual(repaired.count(b'MPI_Barrier(ctx->comm);'), 1)
            self.assertIn(b'MPI_Barrier(ctx->comm);\n'
                          b'  NCCL_CHECK(ncclCommInitRank(&ctx->nccl_comm, nranks, nccl_id, rank));',
                          repaired)
            self.assertLess(repaired.index(b'    return ctx;'),
                            repaired.index(b'MPI_Barrier(ctx->comm);'))
            self.assertEqual(json.loads((root / 'metadata/nccl-mpi-progress-fix.json').read_text()), record)
            self.assertFalse(fixes.fix_nccl_progress(root / 'source', root / 'metadata')['applied'])
            self.assertEqual(source.read_bytes(), repaired)

    def test_changed_shared_context_source_is_not_silently_patched(self):
        fixture = Path(__file__).parent / 'fixtures/cp2k_cusolver_0761fd1b.c.gz'
        original = gzip.decompress(fixture.read_bytes())
        changed = original.replace(b'  MPI_Comm_dup(comm, &ctx->comm);',
                                   b'  ctx->comm = comm;')
        self.assertNotEqual(changed, original)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source/src/fm/cp_fm_cusolver.c'
            source.parent.mkdir(parents=True)
            source.write_bytes(changed)
            with self.assertRaisesRegex(ValueError, hashlib.sha256(changed).hexdigest()):
                fixes.fix_nccl_progress(root / 'source', root / 'metadata')
            self.assertEqual(source.read_bytes(), changed)
            self.assertFalse((root / 'metadata').exists())

    def test_changed_upstream_source_is_preserved_and_requires_review(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source/src/fm/cp_fm_cusolver.c'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'changed upstream cuSOLVER implementation\n')
            with self.assertRaisesRegex(ValueError, 'unrecognized'):
                fixes.fix_nccl_progress(root / 'source', root / 'metadata')
            self.assertEqual(source.read_bytes(), b'changed upstream cuSOLVER implementation\n')
            self.assertFalse((root / 'metadata').exists())
