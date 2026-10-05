undefined
    def test_collection_catalogue_and_promotion_routes(self):
        from test_archive import event, page
        from test_core import row
        self.assertEqual(self.client.get('/collection/sources').status_code,200)
        self.assertEqual(self.client.post('/collection/pages',json=page([event()])).status_code,202)
        self.assertEqual(self.client.get('/collection/runs/export/first').json()['pages'],1)
        self.assertEqual(len(self.client.get('/catalogue?city=milano').json()['items']),1)
        self.assertEqual(self.client.get('/catalogue?min_price=999').status_code,422)
        self.assertEqual(self.client.get('/catalogue/export/a/history').status_code,200)
        self.assertEqual(self.client.post('/catalogue/export/a/promote',json={'batch_id':'incomplete'}).status_code,422)
        full=event('99',payload=dict(row(99),price_kind='total'))
        self.assertEqual(self.client.post('/collection/pages',json=page([full],run='full')).status_code,202)
        self.assertEqual(self.client.post('/catalogue/export/99/promote',json={'batch_id':'promoted'}).status_code,202)
        preview=self.client.post('/publication/preview',json={'batch_id':'promoted'})
        self.assertEqual(preview.status_code,200)
        self.assertEqual(preview.json()['items'],[])
        with patch.dict(os.environ,{'DEAL_FINDER_API_TOKEN':'test-token'}):
            self.assertEqual(self.client.get('/catalogue').status_code,401)
            self.assertEqual(self.client.post('/collection/pages',json=page([])).status_code,401)
