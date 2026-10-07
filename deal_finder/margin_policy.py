"""Required conservative net profit after all evidenced costs."""
from math import ceil


def minimum_net_margin_eur(purchase_eur):
    if type(purchase_eur) is not int or purchase_eur<=0:
        raise ValueError('Positive whole-EUR purchase price required')
    if purchase_eur<6000:return 2000
    if purchase_eur<10000:return 3000
    if purchase_eur<15000:return 4000
    return 5000+1000*max(0,ceil((purchase_eur-20000)/5000))


def policy():
    return dict(version='tiered-net-margin-v1',basis='net_after_all_costs',
        unknown_costs_are_zero=False,resale_reference='lowest_comparable_asking_price_with_stress',
        tiers=[dict(min_purchase_eur=1,max_purchase_eur=5999,minimum_net_eur=2000),
               dict(min_purchase_eur=6000,max_purchase_eur=9999,minimum_net_eur=3000),
               dict(min_purchase_eur=10000,max_purchase_eur=14999,minimum_net_eur=4000),
               dict(min_purchase_eur=15000,max_purchase_eur=20000,minimum_net_eur=5000)],
        above_20000='Add 1000 EUR net for each further 5000 EUR purchase band')
