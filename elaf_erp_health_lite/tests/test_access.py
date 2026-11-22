"""Data access of ERP Health checks (security review HC-01..HC-04).

Decision: Health users without accounting rights must not see accounting
figures; the same holds for any check revealing data from an app the user
cannot read. Such checks are not run for them (reported as not applicable,
"Requires accounting access"), and findings computed by someone else are
hidden from them by a record rule.
"""
from unittest.mock import patch

import odoo.modules.module
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from .common import HealthCommon


@tagged('elaf_health', 'at_install', '-post_install')
class TestHealthDataAccess(HealthCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.accountant = new_test_user(
            cls.env, login='hc_accountant',
            groups='base.group_user,elaf_erp_health_lite.group_health_user,account.group_account_readonly',
            company_id=cls.company.id, company_ids=[cls.company.id])
        cls.seller = new_test_user(
            cls.env, login='hc_seller',
            groups='base.group_user,elaf_erp_health_lite.group_health_user,'
                   'sales_team.group_sale_salesman_all_leads',
            company_id=cls.company.id, company_ids=[cls.company.id])

    def _finding(self, scan, code):
        return scan.finding_ids.sudo().filtered(lambda f: f.check_code == code)

    def _denied(self, user):
        # same environment as the engine (runner's context, sudo)
        return self.Check.with_user(user).sudo()._hc_denied_checks(user)

    def test_accountant_runs_accounting_checks(self):
        partner = self.make_partner('Access Debtor')
        inv = self.make_invoice('out_invoice', partner, 555.0, 60)
        scan = self.Scan.with_user(self.accountant).create({'scope': 'accounting'})
        scan.action_run()
        self.assertEqual(scan.state, 'done')
        f = self._finding(scan, 'acc_overdue_customer_invoices')
        self.assertEqual(f.result, 'fail')
        vals = f.with_user(self.accountant).read(['exposure', 'res_ids'])[0]
        self.assertGreaterEqual(vals['exposure'], 555.0)
        self.assertIn(inv.id, f._get_res_ids())
        self.assertGreaterEqual(scan.with_user(self.accountant).total_exposure, 555.0)
        # a Health user without accounting rights cannot read that finding,
        # nor see its amount in the scan total or the dashboard
        with self.assertRaises(AccessError):
            f.with_user(self.hc_user).read(['exposure', 'res_ids'])
        Finding = self.env['elaf.health.finding'].with_user(self.hc_user)
        self.assertFalse(Finding.search([('scan_id', '=', scan.id), ('result', '!=', 'na')]))
        self.assertEqual(scan.with_user(self.hc_user).total_exposure, 0.0)
        data = self.Scan.with_user(self.hc_user).get_dashboard_data({'scope': 'accounting'})
        self.assertEqual(data['latest']['id'], scan.id)
        self.assertFalse(data['top_findings'])

    def test_non_accountant_scan_skips_accounting_checks(self):
        """R2 (S5-H1, PO ruling): a scan in which no check can run for the
        user is refused up front (it used to complete with every check "not
        applicable" and consume a Lite allowance). No data is read or shown."""
        partner = self.make_partner('Access Debtor 2')
        self.make_invoice('out_invoice', partner, 444.0, 60)
        scan = self.Scan.with_user(self.hc_user).create({'scope': 'accounting'})
        denied = self._denied(self.hc_user)
        lite_codes = {'acc_overdue_customer_invoices', 'acc_overdue_vendor_bills'}
        self.assertTrue(lite_codes <= set(denied))
        with self.assertRaises(UserError) as ctx:
            with self.env.cr.savepoint():
                scan.action_run()
        for code in lite_codes:
            self.assertIn(denied[code], str(ctx.exception))
        self.env.invalidate_all()
        self.assertEqual(scan.state, 'draft')
        self.assertFalse(scan.sudo().finding_ids)
        self.assertEqual(self.Usage._get_row('completed_scans').used, 0)
        self.assertEqual(self.Check.with_context(lang='en_US')._hc_access_group_label('accounting'), 'Requires accounting access')

    def test_sales_checks_need_sales_read_access(self):
        scan = self.Scan.with_user(self.hc_user).create({'scope': 'sales'})
        denied = self._denied(self.hc_user)
        lite_codes = {'sale_stale_quotation', 'sale_below_cost'}
        self.assertTrue(lite_codes <= set(denied))
        # R2 (S5-H1): refused up front instead of a scan of "not applicable" rows
        with self.assertRaises(UserError):
            with self.env.cr.savepoint():
                scan.action_run()
        self.env.invalidate_all()
        self.assertEqual(scan.state, 'draft')
        scan2 = self.Scan.with_user(self.seller).create({'scope': 'sales'})
        scan2.action_run()
        self.assertFalse(set(self._denied(self.seller)) & lite_codes)
        for f in scan2.finding_ids.filtered(lambda f: f.check_code in lite_codes):
            self.assertIn(f.result, ('pass', 'fail'))
        self.assertTrue(scan2.score_available)

    def test_master_data_open_to_health_users(self):
        denied = self.Check.sudo()._hc_denied_checks(self.hc_user)
        self.assertNotIn('md_duplicate_partners', denied)
        self.assertNotIn('md_partner_no_contact', denied)
        self.assertIn('acc_overdue_customer_invoices', denied)
        self.assertNotIn('acc_overdue_customer_invoices', self.Check.sudo()._hc_denied_checks(self.accountant))
        # the administrator (Invoicing administrator) keeps accounting access
        self.assertNotIn('acc_overdue_customer_invoices',
                         self.Check.sudo()._hc_denied_checks(self.env.ref('base.user_admin')))

    def test_check_definition_locked(self):
        check = self.check('sale_stale_quotation')
        for vals in ({'scope': 'master_data'}, {'code': 'x'}, {'res_model': 'res.partner'},
                     {'param_key': 'overdue_days'}):
            with self.assertRaises(AccessError):
                check.with_user(self.hc_manager).write(vals)
        check.with_user(self.hc_manager).write({'weight': 3.0})
        self.assertEqual(check.scope, 'sales')

    def test_engine_key_needs_superuser(self):
        scan = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        with self.assertRaises(AccessError):
            scan.with_user(self.hc_user).with_context(elaf_hc_engine=True).write({'score': 99.0})
        with self.assertRaises(AccessError):
            self.Scan.with_user(self.hc_user).with_context(elaf_hc_engine=True).create(
                {'scope': 'sales', 'state': 'done', 'score': 100.0})


@tagged('elaf_health', 'post_install', '-at_install')
class TestDeniedLogTransaction(HealthCommon):
    """The production path of the usage denial log (second transaction)."""

    def test_denied_log_written_in_second_transaction(self):
        marker = 'hc_denied_log_probe'
        admin = self.env.ref('base.user_admin')
        with patch.object(odoo.modules.module, 'current_test', False):
            # hc_user only exists in this test transaction: logged without user
            self.Usage.with_user(self.hc_user)._log_denied(marker)
            self.Usage.with_user(admin)._log_denied(marker)
        with self.env.registry.cursor() as cr2:
            cr2.execute("SELECT user_id FROM elaf_health_usage_log "
                        "WHERE key = %s AND event = 'denied' ORDER BY id", (marker,))
            rows = cr2.fetchall()
            cr2.execute("DELETE FROM elaf_health_usage_log WHERE key = %s", (marker,))
        self.assertEqual(rows, [(None,), (admin.id,)])
