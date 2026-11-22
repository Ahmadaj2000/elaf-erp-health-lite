import json

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import HealthCommon


@tagged('elaf_health', 'at_install', '-post_install')
class TestLiteScan(HealthCommon):

    def test_score_formula_hand_checked(self):
        """Health Score = 100 x (1 - sum(penalty) / sum(max penalty))."""
        scan = self.Scan.create({'scope': 'master_data'})
        dup = self.check('md_duplicate_partners')        # medium (2) x weight 1.5 = max 3, saturation 10
        contact = self.check('md_partner_no_contact')    # low (1) x weight 1 = max 1, saturation 25
        quotes = self.check('sale_stale_quotation')      # used as a not-applicable check here
        results = [
            (dup, {'result': 'fail', 'count': 5, 'ids': [1, 2, 3, 4, 5], 'exposure': 0.0}),
            (contact, {'result': 'pass', 'count': 0, 'ids': []}),
            (quotes, {'result': 'na', 'na_reason': 'test'}),
        ]
        scan.sudo().with_context(elaf_hc_engine=True)._hc_store_results(results)
        # penalty = 3 x min(1, 5/10) = 1.5 ; max = 3 + 1 = 4 ; score = 100 x (1 - 1.5/4) = 62.5
        self.assertEqual(scan.state, 'done')
        self.assertAlmostEqual(scan.score, 62.5, places=1)
        self.assertEqual(scan.band, 'at_risk')
        self.assertEqual(scan.problem_count, 1)
        self.assertEqual(scan.check_count, 2)
        self.assertEqual(scan.pass_count, 1)
        self.assertEqual(scan.na_count, 1)
        self.assertEqual(scan.affected_count, 5)
        finding = scan.finding_ids.filtered(lambda f: f.result == 'fail')
        self.assertAlmostEqual(finding.penalty, 1.5, places=4)
        self.assertEqual(json.loads(finding.res_ids), [1, 2, 3, 4, 5])
        self.assertTrue(finding.recommendation)

        # saturation: 20 affected (> 10) gives the full penalty 3 -> score 25.0
        scan2 = self.Scan.create({'scope': 'master_data'})
        scan2.sudo().with_context(elaf_hc_engine=True)._hc_store_results([
            (dup, {'result': 'fail', 'count': 20, 'ids': list(range(1, 21))}),
            (contact, {'result': 'pass', 'count': 0, 'ids': []}),
        ])
        self.assertAlmostEqual(scan2.score, 25.0, places=1)
        # delta vs the previous scan of the same scope: 25 - 62.5
        self.assertEqual(scan2.previous_scan_id, scan)
        self.assertAlmostEqual(scan2.score_delta, -37.5, places=1)

    def test_bands(self):
        from odoo.addons.elaf_erp_health_lite.models.health_scan import score_band
        self.assertEqual(score_band(100), 'healthy')
        self.assertEqual(score_band(85), 'healthy')
        self.assertEqual(score_band(84.9), 'attention')
        self.assertEqual(score_band(65), 'attention')
        self.assertEqual(score_band(64.9), 'at_risk')

    def test_no_applicable_check_gives_no_score(self):
        scan = self.Scan.create({'scope': 'master_data'})
        scan.sudo().with_context(elaf_hc_engine=True)._hc_store_results([
            (self.check('md_duplicate_partners'), {'result': 'na', 'na_reason': 'x'})])
        self.assertFalse(scan.score_available)
        self.assertFalse(scan.band)

    def test_run_scan_end_to_end_and_rescan_improves(self):
        """Real scan, inspect records, fix the problem, rescan: the score improves."""
        a = self.make_partner('Duplicate Foods LLC')
        b = self.make_partner('duplicate foods llc')
        c = self.make_partner('Twin Supplies', vat='US-99-1234567')
        d = self.make_partner('Twin Supplies Branch', vat='US991234567')
        scan1 = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        scan1.action_run()
        self.assertEqual(scan1.state, 'done')
        self.assertEqual(scan1.user_id, self.hc_user)
        dup1 = scan1.finding_ids.filtered(lambda f: f.check_code == 'md_duplicate_partners')
        self.assertEqual(dup1.result, 'fail')
        self.assertTrue({a.id, b.id, c.id, d.id} <= set(json.loads(dup1.res_ids)))
        # inspect affected records: the action opens the real partners
        action = dup1.with_user(self.hc_user).action_view_records()
        self.assertEqual(action['res_model'], 'res.partner')
        opened = self.env['res.partner'].with_context(active_test=False).search(action['domain'])
        self.assertTrue((a | b | c | d) <= opened)
        # only master data checks ran, only Lite checks
        self.assertEqual(set(scan1.finding_ids.mapped('scope')), {'master_data'})
        # fix: archive the duplicates
        (b | d).action_archive()
        scan2 = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        scan2.action_run()
        dup2 = scan2.finding_ids.filtered(lambda f: f.check_code == 'md_duplicate_partners')
        self.assertEqual(dup2.count, dup1.count - 4)
        self.assertGreater(scan2.score, scan1.score)
        self.assertEqual(scan2.previous_scan_id, scan1)
        self.assertAlmostEqual(scan2.score_delta, scan2.score - scan1.score, places=1)

    def test_not_applicable_when_module_missing(self):
        check = self.check('md_duplicate_partners')
        check.sudo().required_modules = 'module_that_does_not_exist'
        scan = self.Scan.create({'scope': 'master_data'})
        res = check._hc_evaluate(scan, self.installed)
        self.assertEqual(res['result'], 'na')
        self.assertIn('module_that_does_not_exist', res['na_reason'])

    def test_lite_scope_restriction(self):
        Edition = self.env['elaf.health.edition']
        self.assertFalse(Edition._elaf_health_is_pro())
        for scope in ('all', 'cross', 'security'):
            with self.assertRaises(UserError):
                Edition._lite_enforce_scope(scope)
        # the scope selection of Lite only offers the 5 business areas
        keys = [o['key'] for o in self.Scan._hc_scope_options()]
        self.assertEqual(keys, ['sales', 'purchase', 'inventory', 'accounting', 'master_data'])
        with self.assertRaises(UserError):
            self.Scan.action_dashboard_run('all')

    def test_lite_runs_only_lite_checks(self):
        extra = self.Check.sudo().create({
            'code': 'md_product_no_ref', 'name': 'Injected check', 'scope': 'master_data',
            'severity': 'low', 'res_model': 'product.product'})
        scan = self.Scan.create({'scope': 'master_data'})
        self.assertNotIn(extra, scan._hc_checks_to_run())
        self.assertLessEqual(len(scan._hc_checks_to_run()), 10)

    def test_archived_check_not_run(self):
        self.check('md_partner_no_contact').sudo().active = False
        scan = self.Scan.create({'scope': 'master_data'})
        scan.action_run()
        self.assertEqual(scan.finding_ids.mapped('check_code'), ['md_duplicate_partners'])

    def test_results_cannot_be_tampered(self):
        scan = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        scan.action_run()
        with self.assertRaises(AccessError):
            scan.with_user(self.hc_user).write({'score': 100})
        with self.assertRaises(AccessError):
            self.Scan.with_user(self.hc_user).create({'scope': 'sales', 'score': 99, 'state': 'done'})
        with self.assertRaises(UserError):
            scan.with_user(self.hc_user).write({'scope': 'sales'})

    def test_dashboard_data(self):
        data = self.Scan.with_user(self.hc_user).get_dashboard_data({})
        self.assertEqual(data['edition'], 'lite')
        self.assertFalse(data['latest'])
        res = self.Scan.with_user(self.hc_user).action_dashboard_run('master_data')
        self.assertEqual(res['state'], 'done')
        self.assertTrue(res['first_success'])
        data = self.Scan.with_user(self.hc_user).get_dashboard_data({})
        self.assertEqual(data['latest']['id'], res['scan_id'])
        self.assertEqual(data['usage']['used'], 1)
        self.assertEqual(len(data['severity']), 4)
        page = self.Scan.with_user(self.hc_user).get_lite_vs_pro_data()
        self.assertEqual(page['value']['scans'], 1)
        self.assertIn('Sales', page['value']['not_audited_scopes'])
        self.assertEqual(page['pro_url'], 'https://apps.odoo.com/apps/modules/18.0/elaf_erp_health')
