"""Offline regressions for language routing and preservation of form/filter contracts."""
import json
from pathlib import Path
import unittest

from build_english import Translator, javascript, json_data, route, transform_html, preserve_numbers, human, esprima


class EnglishTests(unittest.TestCase):
    def translator(self):
        return Translator({'Hola mundo': 'Hello world', 'Nombre': 'Name',
                           'Selecciona una opción': 'Choose an option',
                           'Ingeniería': 'Engineering', 'Solicitud inválida.': 'Invalid request.'})

    def test_html_translates_labels_without_changing_form_contract(self):
        source = '<label for="nombre">Nombre</label><input name="nombre" id="nombre" placeholder="Nombre"><select name="especialidad"><option>Ingeniería</option></select>'
        result = transform_html(source, self.translator())
        self.assertIn('name="nombre" id="nombre" placeholder="Name"', result)
        self.assertIn('<option value="Ingeniería">Engineering</option>', result)

    def test_entities_are_translated_as_one_phrase(self):
        result = transform_html('<p> Hola&nbsp;mundo </p>', self.translator())
        self.assertEqual(result, '<p> Hello world </p>')

    def test_numbers_and_contact_identifiers_are_protected(self):
        self.assertEqual(preserve_numbers('12 000 BTU/h = 3.517 kW', '12,000 BTU/h = 3,517 kW'), '12,000 BTU/h = 3.517 kW')
        self.assertEqual(preserve_numbers('junio 2025', 'June 2016'), 'June 2025')
        self.assertFalse(human('ventas@vmsenergy.com'))
        self.assertFalse(human('vmsenergy.com'))
        self.assertFalse(human('I² × R = P'))
        self.assertEqual(preserve_numbers('+52 33 3813 0739 Guadalajara', '+52 33,3813,0739 Guadalajara'), '+52 33 3813 0739 Guadalajara')

    def test_routes_remain_in_english_and_assets_are_shared(self):
        self.assertEqual(route('nosotros.html#inicio'), 'nosotros.html#inicio')
        self.assertEqual(route('https://vmsenergy.com/soluciones_oil_gas.html#inicio'), 'oil-gas-solutions.html#inicio')
        self.assertEqual(route('assets/img/logo.webp'), '../assets/img/logo.webp')
        self.assertEqual(route('tokens.css'), '../tokens.css')
        self.assertEqual(route('assets/data/landing-projects.json'), 'assets/data/landing-projects.json')
        self.assertEqual(route('mailto:ventas@vmsenergy.com'), 'mailto:ventas@vmsenergy.com')

    def test_runtime_data_preserves_filter_keys_and_values(self):
        tr = Translator({}, collect=True)
        source = {'Ingeniería': {'sector': 'Industrial', 'title': 'Hola mundo', 'img': 'assets/img/logo.webp'}}
        result = json_data(source, tr)
        self.assertIn('Ingeniería', result)
        self.assertEqual(result['Ingeniería']['title'], 'Hola mundo')
        self.assertEqual(result['Ingeniería']['img'], '../assets/img/logo.webp')
        self.assertIn('Hola mundo', tr.strings)

    def test_all_pages_have_english_counterparts(self):
        from build_english import ROOT, PAGES, english_name
        for page in PAGES:
            with self.subTest(page=page.name):
                counterpart = ROOT / 'en' / english_name(page.name)
                self.assertTrue(counterpart.is_file())
                text = counterpart.read_text(encoding='utf-8')
                self.assertRegex(text, r'<html\b[^>]*lang="en"')
                self.assertIn('src="translations.js"', text)
                self.assertIn('hreflang="es-MX"', text)
                self.assertTrue('src="vms-language.js"' in page.read_text(encoding='utf-8') or
                                (page.name in {'catalogo-proyectos.html', 'proyectos-vms.html'} and
                                 'vms-header.js' in page.read_text(encoding='utf-8')))


if __name__ == '__main__':
    unittest.main()
