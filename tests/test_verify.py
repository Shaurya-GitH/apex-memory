"""Focused regressions for the verification harness, with no Docker or MCP server."""
import argparse
import asyncio
import importlib.machinery
import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from apex_memory.backends import open_backend
from apex_memory.store import Store


def load_verify():
    path = pathlib.Path(__file__).resolve().parents[1] / 'scripts' / 'verify'
    loader = importlib.machinery.SourceFileLoader('verify_harness', str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class VerifyTest(unittest.TestCase):
    def test_store_move_detects_corrupted_section_body(self):
        v = load_verify()
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt), tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                for sub in ('home', 'db'):
                    (root / sub).mkdir()
                st = {'dir': tmp, 'backend': 'sqlite'}
                url = v.url_for(st, 'seed')
                v.seed(url)
                j = v.Journey(st, 'store-admin', 'store-admin-1')
                self.addCleanup(j.transcript.close)
                j.url = url
                original_cli = j.cli

                def cli(*args, **kwargs):
                    out = original_cli(*args, **kwargs)
                    if corrupt and args[0] == 'import' and kwargs.get('url', '').endswith('_moved.db') and out.returncode == 0:
                        db = open_backend(kwargs['url'])
                        try:
                            db.execute("UPDATE sections SET body = 'Corrupted tokens.' WHERE note = 'atlas/api' AND heading = 'Auth'")
                        finally:
                            db.close()
                    return out

                async def read(tool, **args):
                    self.assertEqual(tool, 'read')
                    s = Store(url, v.OWNER)
                    try:
                        return s.read(**args)
                    finally:
                        s.db.close()

                with mock.patch.object(j, 'cli', side_effect=cli):
                    asyncio.run(v.store_admin(j, read, None))
                failed = [c['check'] for c in j.checks if not c['ok']]
                if corrupt:
                    self.assertEqual(failed, ['rows: same contents in sections'])
                else:
                    self.assertEqual(failed, [])
                j.transcript.close()

    def test_cleanup_keeps_a_replacement_container_with_the_same_name(self):
        v = load_verify()
        with tempfile.TemporaryDirectory() as tmp:
            original, replacement = 'a' * 64, 'b' * 64
            containers = {}
            name = 'apex-memory-verify-cleanup-test'
            def docker(cmd, **kwargs):
                if cmd[1] == 'run':
                    containers[name] = original
                elif cmd[1] == 'rm':
                    for key, cid in list(containers.items()):
                        if cmd[-1] in (key, cid):
                            del containers[key]
                return subprocess.CompletedProcess(cmd, 0, stdout=original + '\n', stderr='')

            args = argparse.Namespace(run='cleanup-test', backend='postgres')
            with mock.patch.object(v, 'VERIFY', pathlib.Path(tmp)), \
                 mock.patch.object(v, 'free_port', return_value=54321), \
                 mock.patch.object(v, '_wait_pg', side_effect=RuntimeError('stop before connecting')), \
                 mock.patch.object(v.subprocess, 'run', side_effect=docker):
                with self.assertRaisesRegex(RuntimeError, 'stop before connecting'):
                    v.launch(args)
                containers[name] = replacement  # original was removed; someone reused its name
                v.cleanup(args)
                v.cleanup(args)
                self.assertEqual(containers, {name: replacement})
                saved = json.loads((pathlib.Path(tmp) / args.run / 'state.json').read_text())
                self.assertEqual(saved['container_id'], original)
