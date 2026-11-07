import importlib.util
from pathlib import Path
import tempfile
import unittest
from native_harness_api.errors import ChannelError

path = Path(__file__).resolve().parents[1] / "examples" / "stable_operation_client.py"
spec = importlib.util.spec_from_file_location("stable_operation_example", path)
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


class ClientExampleTests(unittest.TestCase):
    def test_operation_survives_another_invocation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operation.json"
            first = example.stable_key(path)
            self.assertEqual(first, example.stable_key(path))
            self.assertEqual(set(__import__("json").loads(path.read_text())), {"operation_key"})

    def test_incomplete_operation_file_does_not_silently_create_new_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operation.json"
            path.write_text("")
            with self.assertRaises(ChannelError):
                example.stable_key(path)
            self.assertEqual(path.read_text(), "")

    def test_new_intent_uses_another_file(self):
        with tempfile.TemporaryDirectory() as directory:
            first = example.stable_key(Path(directory) / "first.json")
            second = example.stable_key(Path(directory) / "second.json")
            self.assertNotEqual(first, second)
