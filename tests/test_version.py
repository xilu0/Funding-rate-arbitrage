import unittest
from src.version import __version__, __app_name__, get_diagnostics, format_version_text

class TestVersion(unittest.TestCase):
    def test_version_string(self):
        self.assertTrue(isinstance(__version__, str))
        self.assertGreater(len(__version__), 0)

    def test_get_diagnostics(self):
        diag = get_diagnostics()
        self.assertIn("version", diag)
        self.assertIn("python_version", diag)
        self.assertIn("gopass", diag)
        self.assertIn("dependencies", diag)
        self.assertEqual(diag["app_name"], __app_name__)

    def test_format_version_text(self):
        text = format_version_text()
        self.assertIn(__version__, text)
        self.assertIn("Python:", text)


if __name__ == "__main__":
    unittest.main()
