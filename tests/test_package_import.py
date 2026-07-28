import json
import unittest
from pathlib import Path


class PackageImportTest(unittest.TestCase):
    def test_package_imports(self):
        import workeventagent

        self.assertEqual(workeventagent.__all__, [])

    def test_python_and_client_release_versions_match(self):
        import workeventagent

        package = json.loads(Path("client/package.json").read_text(encoding="utf-8"))
        pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

        self.assertEqual(workeventagent.__version__, package["version"])
        self.assertIn(f'version = "{package["version"]}"', pyproject)


if __name__ == "__main__":
    unittest.main()
