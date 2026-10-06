import unittest
from deal_finder.brightdata import event
from deal_finder.collection_geography import published_location, registry
from test_brightdata import car, NOW


class CollectionGeographyTests(unittest.TestCase):
    def test_nearby_cities_and_provinces_are_preserved(self):
        cases = [('Bergamo, Italia', 'Bergamo', 'BG'), ('Brescia, Italia', 'Brescia', 'BS'),
                 ('Monza, Monza e Brianza', 'Monza', 'MB'), ('Sesto San Giovanni, Italia', 'Sesto San Giovanni', 'MI'),
                 ('Vigevano, Pavia', 'Vigevano', 'PV'), ('Gallarate, Varese', 'Gallarate', 'VA'),
                 ('Novara, Piemonte', 'Novara', 'NO'), ('Rho (MI), Lombardia', 'Rho', 'MI'),
                 ('Lecco, Italia', 'Lecco', 'LC'), ('Crema, Cremona', 'Crema', 'CR'),
                 ('Lodi, Italia', 'Lodi', 'LO'), ('Cantù, Como', 'Cantù', 'CO')]
        for value, city, province in cases:
            with self.subTest(value=value):
                row = dict(car(), location=value)
                payload = event(row, NOW)['payload']
                self.assertEqual((payload['city'], payload['province']), (city, province))
                self.assertEqual(payload['original']['location'], value)
                self.assertEqual(payload['price_kind'], 'unknown')

    def test_all_included_municipalities_resolve_with_their_province(self):
        for city, province in registry()[0]['municipalities']:
            location = published_location(city + ', ' + province)
            self.assertEqual((location['city'], location['province']), (city, province))

    def test_unknown_outside_and_conflicting_locations_block(self):
        for value in ('Roma, Italia', 'Torino, Piemonte', 'Milano Marittima',
                      'Bergamo, Roma', 'Brescia (MI)', 'Novara, Lombardia',
                      'Bergamo, Veneto', 'Milano, BG, MI', None, ''):
            with self.subTest(value=value), self.assertRaises(ValueError):
                published_location(value)

    def test_ambiguous_national_names_require_a_province(self):
        with self.assertRaises(ValueError):
            published_location('Castro, Italia')
        self.assertEqual(published_location('Castro, Bergamo')['province'], 'BG')
        with self.assertRaises(ValueError):
            published_location('Castro, Lecce')

    def test_geography_does_not_relax_other_intake_checks(self):
        for change in ({'country_code': 'US'}, {'currency': 'USD'},
                       {'initial_price': 20000}, {'is_sold': True},
                       {'breadcrumbs': None}, {'product_id': '999'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                event(dict(car(), location='Brescia, Italia', **change), NOW)
