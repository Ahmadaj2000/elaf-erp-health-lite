"""Remediation round 2 (PO QA of RC1): S5-H1, S5-H2, S5-H3.

S5-H1  An inaccessible, failed, cancelled, refused or duplicate scan never
       consumes a Lite allowance. Rule R2-H1 (partial access): a Lite scan
       is refused when ANY check of its scope is denied for the user.
S5-H2  Every total and score is computed for the reader, over the findings
       the reader may see.
S5-H3  Lite is bound to ONE business module (first completed scan, or a
       manager's explicit choice); it cannot be changed in Lite.
"""
import glob
import importlib.util
import os
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.modules.module import get_module_path
from odoo.tests import new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.elaf_erp_health_lite.models import limits
from odoo.addons.elaf_erp_health_lite.models.health_check import ElafHealthCheck
from odoo.addons.elaf_erp_health_lite.models.health_scan import ElafHealthScan, compute_score
from .common import HealthCommon


@tagged('elaf_health', 'at_install', '-post_install')
class TestR2Remediation(HealthCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.accountant = new_test_user(
            cls.env, login='r2_accountant',
            groups='base.group_user,elaf_erp_health_lite.group_health_user,account.group_account_readonly',
            company_id=cls.company.id, company_ids=[cls.company.id])
        cls.admin = cls.env.ref('base.user_admin')

    # ------------------------------------------------------------ helpers
    def used(self):
        row = self.Usage._get_row(limits.USAGE_KEY_SCANS)
        row.invalidate_recordset()
        return row.used

    def bound_scope(self):
        row = self.Usage._get_row(limits.USAGE_KEY_SCANS)
        row.invalidate_recordset()
        return row.bound_scope

    def refused(self, scan, needle=None):
        """action_run must raise UserError without running or counting."""
        used = self.used()
        with self.assertRaises(UserError) as ctx:
            with self.env.cr.savepoint():
                scan.action_run()
        self.env.invalidate_all()
        self.assertEqual(self.used(), used, "a refused scan consumed a Lite allowance")
        self.assertEqual(scan.state, 'draft')
        self.assertFalse(scan.sudo().finding_ids)
        if needle:
            self.assertIn(needle, str(ctx.exception))
        return str(ctx.exception)

    # ============================================================ S5-H1
    def test_h1_all_checks_denied_refused_up_front(self):
        """Health user without accounting rights: no accounting check can
        run for them, the scan is refused before running, nothing counted."""
        scan = self.Scan.with_user(self.hc_user).create({'scope': 'accounting'})
        msg = self.refused(scan, 'Requires accounting access')
        self.assertIn('nothing was run or counted', msg)
        self.assertFalse(self.bound_scope(), "a refused scan must not bind the Lite module")

    def test_h1_no_applicable_check_refused(self):
        """Every check of the scope needs an app that is not installed:
        refused up front (previously: completed without score and counted)."""
        for code in ('md_duplicate_partners', 'md_partner_no_contact'):
            self.check(code).sudo().required_modules = 'module_that_does_not_exist'
        scan = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        self.refused(scan, 'module_that_does_not_exist')
        # same through the dashboard: refused, no scan left, nothing counted
        with self.assertRaises(UserError):
            with self.env.cr.savepoint():
                self.Scan.with_user(self.hc_user).action_dashboard_run('master_data', 'hc-r2-na')
        self.assertFalse(self.Scan.search([('request_key', '=', 'hc-r2-na')]))
        self.assertEqual(self.used(), 0)

    def test_h1_partial_access_refused_in_lite(self):
        """Rule R2-H1: one check of the scope is denied, the other allowed.
        Lite refuses the scan and names the missing access, so a consumed
        scan is always a complete scan of the bound module."""
        orig = ElafHealthCheck._hc_access_requirements

        def requirements(model):
            res = orig(model)
            res['md_duplicate_partners'] = ('accounting', 'res.partner')
            return res
        with patch.object(ElafHealthCheck, '_hc_access_requirements', requirements):
            plan = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})._hc_run_plan()
            self.assertEqual(plan['runnable'].mapped('code'), ['md_partner_no_contact'])
            self.assertEqual([c.code for c in plan['denied']], ['md_duplicate_partners'])
            scan = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
            msg = self.refused(scan, 'Requires accounting access')
            self.assertIn(self.check('md_duplicate_partners').name, msg)
            # a user with that access runs the complete scan and consumes once
            full = self.Scan.with_user(self.accountant).create({'scope': 'master_data'})
            full.action_run()
            self.assertEqual(full.state, 'done')
            self.assertEqual(set(full.sudo().finding_ids.mapped('check_code')),
                             {'md_duplicate_partners', 'md_partner_no_contact'})
            self.assertTrue(all(f.result != 'na' for f in full.sudo().finding_ids))
        self.assertEqual(self.used(), 1)

    def test_h1_cancelled_scan_never_runs_or_consumes(self):
        scan = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        scan.action_cancel()
        self.assertEqual(scan.state, 'cancel')
        with self.assertRaises(UserError):
            scan.action_run()
        self.assertEqual(self.used(), 0)
        self.assertFalse(self.bound_scope())
        # a completed scan cannot be cancelled; a failed one can
        done = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        done.action_run()
        with self.assertRaises(UserError):
            done.action_cancel()
        with mute_logger('odoo.addons.elaf_erp_health_lite.models.health_scan'):
            with patch.object(ElafHealthScan, '_hc_execute_checks', side_effect=RuntimeError('boom')):
                failed = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
                failed.action_run()
        self.assertEqual(failed.state, 'failed')
        failed.action_cancel()
        self.assertEqual(failed.state, 'cancel')
        self.assertEqual(self.used(), 1)
        # users cannot set the state themselves
        with self.assertRaises(AccessError):
            self.Scan.with_user(self.hc_user).create({'scope': 'master_data'}).write({'state': 'cancel'})

    @mute_logger('odoo.addons.elaf_erp_health_lite.models.health_scan')
    def test_h1_crash_neither_consumes_nor_binds(self):
        with patch.object(ElafHealthScan, '_hc_execute_checks', side_effect=RuntimeError('boom')):
            scan = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
            scan.action_run()
        self.assertEqual(scan.state, 'failed')
        self.assertEqual(self.used(), 0)
        self.assertFalse(self.bound_scope())

    def test_h1_dashboard_duplicate_request_counted_once(self):
        """Double click / network retry: the same request key returns the
        first scan; only one scan exists and one allowance is consumed."""
        Scan = self.Scan.with_user(self.hc_user)
        first = Scan.action_dashboard_run('master_data', 'hc-r2-dup-1')
        self.assertEqual(first['state'], 'done')
        again = Scan.action_dashboard_run('master_data', 'hc-r2-dup-1')
        self.assertTrue(again['duplicate'])
        self.assertEqual(again['scan_id'], first['scan_id'])
        self.assertEqual(self.Scan.search_count([('request_key', '=', 'hc-r2-dup-1')]), 1)
        self.assertEqual(self.used(), 1)
        # a new click (new key) is a new scan
        third = Scan.action_dashboard_run('master_data', 'hc-r2-dup-2')
        self.assertNotEqual(third['scan_id'], first['scan_id'])
        self.assertEqual(self.used(), 2)

    def test_h1_rerun_of_completed_scan_is_noop(self):
        scan = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        for _i in range(3):
            scan.action_run()
        self.assertEqual(self.used(), 1)
        self.assertEqual(len(self.env['elaf.health.usage.log'].search([('event', '=', 'consumed')])), 1)

    # ============================================================ S5-H2
    def test_h2_totals_follow_the_reader(self):
        """One accounting scan, two readers with different rights."""
        partner = self.make_partner('R2 Debtor')
        self.make_invoice('out_invoice', partner, 640.0, 60)
        self.make_invoice('in_invoice', partner, 120.0, 5)
        scan = self.Scan.with_user(self.accountant).create({'scope': 'accounting'})
        scan.action_run()
        self.assertEqual(scan.state, 'done')
        findings = scan.sudo().finding_ids
        self.assertEqual(len(findings), 2)
        expected_score, _ok = compute_score([{
            'result': f.result, 'penalty': f.penalty, 'max_penalty': f.max_penalty} for f in findings])

        acc = scan.with_user(self.accountant)
        self.assertTrue(acc.score_available)
        self.assertAlmostEqual(acc.score, expected_score, places=1)
        self.assertEqual(acc.problem_count, len(findings.filtered(lambda f: f.result == 'fail')))
        self.assertGreaterEqual(acc.problem_count, 2)
        self.assertEqual(acc.affected_count, sum(findings.filtered(lambda f: f.result == 'fail').mapped('count')))
        self.assertEqual(acc.hidden_count, 0)
        self.assertGreaterEqual(acc.total_exposure, 760.0)

        other = scan.with_user(self.hc_user)
        self.assertFalse(other.score_available)
        self.assertFalse(other.band)
        self.assertEqual((other.problem_count, other.affected_count, other.check_count, other.pass_count),
                         (0, 0, 0, 0))
        self.assertEqual(other.hidden_count, 2)
        self.assertEqual(other.total_exposure, 0.0)
        # read() through the ORM (what the list and form views call)
        vals = other.read(['score', 'problem_count', 'affected_count', 'hidden_count'])[0]
        self.assertEqual((vals['problem_count'], vals['affected_count'], vals['hidden_count']), (0, 0, 2))
        # dashboard and comparison page
        dash = self.Scan.with_user(self.hc_user).get_dashboard_data({})
        self.assertEqual(dash['latest']['id'], scan.id)
        self.assertEqual((dash['latest']['problem_count'], dash['latest']['affected_count']), (0, 0))
        self.assertFalse(dash['latest']['score_available'])
        self.assertEqual(dash['latest']['hidden_count'], 2)
        self.assertFalse(dash['top_findings'])
        self.assertEqual(sum(r['records'] for r in dash['severity']), 0)
        self.assertIsNone(dash['recent_scans'][0]['score'])
        dash_acc = self.Scan.with_user(self.accountant).get_dashboard_data({})
        self.assertEqual(dash_acc['latest']['problem_count'], acc.problem_count)
        self.assertEqual(dash_acc['recent_scans'][0]['score'], acc.score)
        page = self.Scan.with_user(self.hc_user).get_lite_vs_pro_data()['value']
        self.assertEqual((page['problems_found'], page['open_problems'], page['open_records']), (0, 0, 0))
        page_acc = self.Scan.with_user(self.accountant).get_lite_vs_pro_data()['value']
        self.assertEqual(page_acc['open_records'], acc.affected_count)
        # the search on the status uses the reader's own band
        if acc.band:
            self.assertIn(scan, self.Scan.with_user(self.accountant).search([('band', '=', acc.band)]))
            self.assertNotIn(scan, self.Scan.with_user(self.hc_user).search([('band', '=', acc.band)]))
        # the shared chatter carries no score
        bodies = ' '.join(scan.sudo().message_ids.mapped('body'))
        self.assertNotIn('%s' % acc.score, bodies)

    def test_h2_score_delta_per_reader(self):
        self.make_partner('R2 Twin')
        self.make_partner('r2 twin')
        s1 = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        s1.action_run()
        self.make_partner('R2 Twin B')
        self.make_partner('r2 twin b')
        s2 = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        s2.action_run()
        self.assertEqual(s2.previous_scan_id, s1)
        self.assertAlmostEqual(s2.score_delta, s2.score - s1.score, places=1)
        self.assertLessEqual(s2.score, s1.score)

    # ============================================================ S5-H3
    def test_h3_first_completed_scan_binds_the_module(self):
        seller = new_test_user(
            self.env, login='r2_seller',
            groups='base.group_user,elaf_erp_health_lite.group_health_user,sales_team.group_sale_salesman_all_leads',
            company_id=self.company.id, company_ids=[self.company.id])
        self.assertFalse(self.Scan.with_user(self.hc_user).get_dashboard_data({})['bound_scope'])
        self.Scan.with_user(self.hc_user).create({'scope': 'master_data'}).action_run()
        self.assertEqual(self.bound_scope(), 'master_data')
        # another module is refused, whoever runs it, without consuming
        scan = self.Scan.with_user(seller).create({'scope': 'sales'})
        self.refused(scan, 'cannot be changed in Lite')
        with self.assertRaises(UserError):
            with self.env.cr.savepoint():
                self.Scan.with_user(seller).action_dashboard_run('sales', 'hc-r2-bound')
        self.assertEqual(self.used(), 1)
        # the bound module keeps working
        self.Scan.with_user(self.hc_user).create({'scope': 'master_data'}).action_run()
        self.assertEqual(self.used(), 2)
        # the dashboard shows the bound module and only offers it
        data = self.Scan.with_user(self.hc_user).get_dashboard_data({})
        self.assertEqual(data['bound_scope'], 'master_data')
        self.assertEqual(data['bound_scope_label'], 'Master Data')
        self.assertEqual([o['key'] for o in data['scopes']], ['master_data'])
        self.assertFalse(data['can_bind'])
        page = self.Scan.with_user(self.hc_user).get_lite_vs_pro_data()
        self.assertEqual(page['bound_scope_label'], 'Master Data')
        # the binding cannot be changed, not even by a manager
        with self.assertRaises(UserError):
            self.Usage.with_user(self.hc_manager)._bind_scope('sales')
        self.assertEqual(self.bound_scope(), 'master_data')

    def test_h3_manager_chooses_the_audited_module(self):
        self.assertTrue(self.Scan.with_user(self.hc_manager).get_dashboard_data({})['can_bind'])
        self.assertFalse(self.Scan.with_user(self.hc_user).get_dashboard_data({})['can_bind'])
        with self.assertRaises(AccessError):
            self.Scan.with_user(self.hc_user).action_bind_lite_scope('inventory')
        with self.assertRaises(UserError):
            self.Scan.with_user(self.hc_manager).action_bind_lite_scope('all')
        data = self.Scan.with_user(self.hc_manager).action_bind_lite_scope('accounting')
        self.assertEqual(data['bound_scope'], 'accounting')
        self.assertEqual(self.bound_scope(), 'accounting')
        # choosing the same module again is harmless, another one is refused
        self.Usage.with_user(self.hc_manager)._bind_scope('accounting')
        with self.assertRaises(UserError):
            self.Usage.with_user(self.hc_manager)._bind_scope('master_data')
        # nothing was consumed by choosing; master data scans are now refused
        self.assertEqual(self.used(), 0)
        self.refused(self.Scan.with_user(self.accountant).create({'scope': 'master_data'}),
                     'cannot be changed in Lite')
        acc = self.Scan.with_user(self.accountant).create({'scope': 'accounting'})
        acc.action_run()
        self.assertEqual(acc.state, 'done')

    def test_h3_settings_choose_module_once(self):
        Settings = self.env['res.config.settings'].with_user(self.admin)
        self.assertFalse(Settings.default_get(['elaf_hc_lite_scope']).get('elaf_hc_lite_scope'))
        Settings.create({"elaf_hc_lite_scope": "inventory"}).set_values()
        self.assertEqual(self.bound_scope(), 'inventory')
        settings = Settings.create({})
        self.assertEqual(settings.elaf_hc_lite_scope, 'inventory')
        self.assertTrue(settings.elaf_hc_lite_scope_locked)
        with self.assertRaises(UserError):
            Settings.create({"elaf_hc_lite_scope": "sales"}).set_values()
        self.assertEqual(self.bound_scope(), 'inventory')

    def test_h3_concurrent_first_scans_cannot_bind_two_modules(self):
        """_consume refuses when another transaction bound another module meanwhile."""
        row = self.Usage._get_row(limits.USAGE_KEY_SCANS)
        self.env.cr.execute("UPDATE elaf_health_usage SET bound_scope = 'sales' WHERE id = %s", (row.id,))
        row.invalidate_recordset()
        scan = self.Scan.create({'scope': 'master_data'})
        with self.assertRaises(UserError):
            with self.env.cr.savepoint():
                self.Usage._consume(limits.USAGE_KEY_SCANS, scan, self.company, 'master_data')
        self.assertEqual(self.used(), 0)

    def test_h3_migration_binds_first_completed_scan(self):
        """Existing Lite databases that scanned several modules are bound to
        the module of their FIRST completed scan by the post-migration."""
        engine = {'elaf_hc_engine': True}
        scans = []
        for scope, check in (('sales', 'sale_stale_quotation'), ('master_data', 'md_duplicate_partners'),
                             ('accounting', 'acc_overdue_customer_invoices')):
            scan = self.Scan.create({'scope': scope})
            scan.sudo().with_context(**engine)._hc_store_results([(self.check(check), {'result': 'pass', 'count': 0, 'ids': []})])
            scans.append(scan)
        # older RC1 data: the first scan is the oldest one
        self.env.flush_all()
        self.env.cr.execute("UPDATE elaf_health_scan SET date = date - interval '3 days' WHERE id = %s",
                            (scans[0].id,))
        row = self.Usage._get_row(limits.USAGE_KEY_SCANS)
        self.env.cr.execute("UPDATE elaf_health_usage SET bound_scope = NULL, bound_company_id = %s WHERE id = %s",
                            (self.company.id, row.id))
        path = glob.glob(os.path.join(get_module_path('elaf_erp_health_lite'), 'migrations', '*', 'post-migrate.py'))
        self.assertEqual(len(path), 1)
        spec = importlib.util.spec_from_file_location('elaf_hc_r2_post_migrate', path[0])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.migrate(self.env.cr, '1.0.0')
        self.assertEqual(self.bound_scope(), 'sales')
        # an already bound database is left alone
        self.env.cr.execute("UPDATE elaf_health_usage SET bound_scope = 'accounting' WHERE id = %s", (row.id,))
        module.migrate(self.env.cr, '1.0.0')
        self.assertEqual(self.bound_scope(), 'accounting')
        # a fresh install (no version) does nothing
        self.env.cr.execute("UPDATE elaf_health_usage SET bound_scope = NULL WHERE id = %s", (row.id,))
        module.migrate(self.env.cr, None)
        self.assertFalse(self.bound_scope())
