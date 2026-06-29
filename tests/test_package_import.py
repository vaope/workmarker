import unittest


class PackageImportTest(unittest.TestCase):
    def test_package_imports(self):
        import workeventagent

        self.assertEqual(workeventagent.__all__, [])


if __name__ == "__main__":
    unittest.main()
