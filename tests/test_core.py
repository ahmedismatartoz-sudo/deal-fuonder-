import unittest, json
from datetime import datetime, timezone, timedelta
from deal_finder.models import Listing
from deal_finder.storage import Store
from deal_finder.pricing import estimate
NOW = datetime.now(timezone.utc)
def row(i=0, **changes):
    data = dict(source='manual', source_id=str(i), url=f'https://example.com/car/{i}', make='Fiat', model='Panda', generation='319', trim='1.2 69cv', fuel='petrol', transmission='manual', year=2020, mileage_km=50000, price_eur=10000, province='Milano', seller_type='private', condition='undamaged', observed_at=(NOW-timedelta(days=1)).isoformat(), vehicle_id=f'vehicle-{i}')
    data.update(changes)
    return data
class Tests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(Listing.parse(row(make=' FIAT ')).make, 'fiat')
        for changes in ({'price_eur': -1}, {'price_eur': True}, {'active':'yes'}, {'observed_at':NOW.replace(tzinfo=None).isoformat()}, {'url':'file:///x'}):
            with self.assertRaises(ValueError): Listing.parse(row(**changes))
    def test_history_idempotency(self):
        s=Store(':memory:'); content=json.dumps([row()])
        self.assertEqual(s.import_text(content)['inserted'],1)
        self.assertEqual(s.import_text(content)['duplicates'],1)
        s.import_text(json.dumps([row(price_eur=9000,observed_at=NOW.isoformat())]))
        self.assertEqual(len(s.listings()),2); s.close()
    def test_atomic_conflict(self):
        s=Store(':memory:')
        with self.assertRaises(ValueError): s.import_text(json.dumps([row(1),row(2,price_eur=-1)]))
        self.assertEqual(s.listings(),[])
        s.import_text(json.dumps([row()]))
        with self.assertRaises(ValueError): s.import_text(json.dumps([row(1),row(price_eur=8000)]))
        self.assertEqual(len(s.listings()),1); s.close()
    def test_csv(self):
        import csv,io
        out=io.StringIO(); w=csv.DictWriter(out,fieldnames=row().keys()); w.writeheader(); w.writerow(row())
        s=Store(':memory:'); self.assertEqual(s.import_text(out.getvalue(),'csv')['inserted'],1); s.close()
    def test_benchmark(self):
        target=Listing.parse(row(99)); items=[Listing.parse(row(i,price_eur=10000+i*100)) for i in range(8)]
        r=estimate(target,items,as_of=NOW)
        self.assertEqual(r['benchmark_eur'],10300)
        self.assertEqual(r['status'],'benchmark_available')
        self.assertEqual(estimate(target,items[:7],as_of=NOW)['status'],'insufficient_data')
    def test_exclusions(self):
        target=Listing.parse(row(99)); items=[Listing.parse(row(i)) for i in range(8)]
        items += [Listing.parse(row(20,model='500')),Listing.parse(row(21,condition='damaged')),Listing.parse(row(22,observed_at=(NOW-timedelta(days=31)).isoformat())),Listing.parse(row(23,vehicle_id='vehicle-0')),target]
        self.assertEqual(estimate(target,items,as_of=NOW)['comparable_count'],8)
        items = [x for x in items if x.source_id != '23']
        items.append(Listing.parse(row(0,active=False,observed_at=NOW.isoformat())))
        self.assertEqual(estimate(target,items,as_of=NOW)['status'],'insufficient_data')
    def test_backtest_and_dispersion(self):
        target=Listing.parse(row(99)); items=[Listing.parse(row(i)) for i in range(8)]
        self.assertEqual(estimate(target,items,as_of=NOW-timedelta(days=2))['comparable_count'],0)
        items=[Listing.parse(row(i,price_eur=5000 if i<4 else 20000)) for i in range(8)]
        self.assertEqual(estimate(target,items,as_of=NOW)['status'],'insufficient_data')
