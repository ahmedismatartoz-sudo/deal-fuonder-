import unittest
from deal_finder.margin_policy import minimum_net_margin_eur


class MarginPolicyTests(unittest.TestCase):
    def test_boundary_prices_and_progressive_margins(self):
        for purchase,net in ((1,2000),(4999,2000),(5000,3000),(5999,3000),(6000,3000),
                             (9999,3000),(10000,4000),(14999,4000),(15000,5000),
                             (20000,5000),(20001,6000),(25000,6000),(25001,7000)):
            with self.subTest(purchase=purchase):
                self.assertEqual(minimum_net_margin_eur(purchase),net)

    def test_unknown_or_invalid_purchase_cannot_select_a_margin(self):
        for purchase in (None,True,0,-1,6000.0,'6000'):
            with self.subTest(purchase=purchase),self.assertRaises(ValueError):
                minimum_net_margin_eur(purchase)
