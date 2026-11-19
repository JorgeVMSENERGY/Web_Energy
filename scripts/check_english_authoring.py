"""Extra authoring regressions. Install requirements-english.txt and run directly."""
import unittest
from build_english import Translator, javascript


class AuthoringTests(unittest.TestCase):
    def test_javascript_retains_regex_literals_and_filter_values(self):
        tr = Translator({'Ingeniería': 'Engineering', 'Solicitud inválida.': 'Invalid request.'})
        source = "var pattern = /['\"]/g; var filter = 'Ingeniería'; if (filter === 'Ingeniería') alert('Solicitud inválida.');"
        result = javascript(source, tr)
        self.assertIn("var pattern = /['\"]/g", result)
        self.assertIn("var filter = 'Ingeniería'", result)
        self.assertIn("filter === 'Ingeniería'", result)
        self.assertIn('alert("Invalid request.")', result)

    def test_template_routes_keep_interpolations_executable(self):
        tr = Translator({}, collect=True)
        result = javascript('var image = `assets/img/${name}.webp`;', tr)
        self.assertEqual(result, 'var image = `../assets/img/${name}.webp`;')


if __name__ == '__main__':
    unittest.main()
