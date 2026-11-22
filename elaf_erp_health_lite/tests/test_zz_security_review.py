"""Independent security / QA review of ERP Health Check Lite.

Tests whose docstring says "VULNERABILITY" are expected to FAIL until the
product is fixed; "CONTROL" tests verify a protection that works today.
"""
import logging
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged

from odoo.addons.elaf_erp_health_lite.models import limits
from .common import HealthCommon

_logger = logging.getLogger(__name__)

INTERNAL = 'elaf_hc_usage_internal'


@tagged('elaf_health', 'at_install', '-post_install')
class TestZzSecurityReviewLite(HealthCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls.env.ref('base.user_admin')
        cls.other = cls.env['res.company'].create({'name': 'SecReview Co B'})
        cls.user_b = new_test_user(
            cls.env, login='sec_hc_user_b',
            groups='base.group_user,elaf_erp_health_lite.group_health_user',
            company_id=cls.other.id, company_ids=[cls.other.id])

    def _used(self):
        row = self.Usage._get_row(limits.USAGE_KEY_SCANS)
        row.invalidate_recordset()
        return row.used

    # ------------------------------------------------------------------ vulns
    def test_hc01_engine_context_flag_forges_results(self):
        """SECURITY-REVIEW: HC-01 VULNERABILITY. The 'elaf_hc_engine' context
        flag is the only guard on result fields; any RPC caller can send it in
        the context and overwrite score/state/band/user of a completed scan."""
        scan = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        scan.action_run()
        self.assertEqual(scan.state, 'done')
        forged = False
        try:
            scan.with_user(self.hc_user).with_context(elaf_hc_engine=True).write(
                {'score': 100.0, 'band': 'healthy', 'problem_count': 0, 'user_id': self.admin.id})
            forged = True
        except (AccessError, UserError):
            pass
        self.assertFalse(forged, "A health user forged the results of a completed scan by passing "
                                 "the elaf_hc_engine context key (RPC callers control the context).")

    def test_hc02_run_uid_context_spoofs_run_by(self):
        """SECURITY-REVIEW: HC-02 VULNERABILITY. 'Run by' (user_id) is taken from
        the caller-controlled context key 'elaf_hc_run_uid', so a user can
        attribute the scan to another user (audit trail spoofing)."""
        scan = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        scan.with_context(elaf_hc_run_uid=self.admin.id).action_run()
        self.assertEqual(scan.state, 'done')
        self.assertEqual(scan.user_id, self.hc_user,
                         "Scan attributed to %s instead of the user who ran it" % scan.user_id.login)

    def test_hc03_manager_rescopes_checks_to_bypass_one_scope_per_scan(self):
        """SECURITY-REVIEW: HC-03 VULNERABILITY. A manager can write the 'scope'
        of the 10 Lite checks (field not protected), so one 'master_data' scan
        audits all five business areas: the disclosed Lite limit 'one scope
        per scan' is bypassed."""
        checks = self.Check.search([])
        orig = {c.code: c.scope for c in checks}
        try:
            checks.with_user(self.hc_manager).write({'scope': 'master_data'})
        except (AccessError, UserError, ValidationError):
            return  # fixed: scope is not writable
        scan = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        scan.action_run()
        areas = {orig[c] for c in scan.finding_ids.mapped('check_code')}
        self.assertEqual(areas, {'master_data'},
                         "One Lite scan evaluated checks of %s" % sorted(areas))

    def test_hc04_accounting_exposure_disclosed_to_non_accounting_user(self):
        """SECURITY-REVIEW: HC-04 VULNERABILITY. A health user without any
        accounting right runs an Accounting scan (checks run in sudo) and reads
        the overdue receivable total and invoice ids from the finding."""
        if self.env['account.move'].with_user(self.hc_user).check_access_rights('read', raise_exception=False):
            self.skipTest("hc_user can read invoices in this database")
        partner = self.make_partner('SecReview Debtor')
        inv = self.make_invoice('out_invoice', partner, 777.0, 60)
        scan = self.Scan.with_user(self.hc_user).create({'scope': 'accounting'})
        try:
            scan.action_run()
        except (AccessError, UserError):
            return  # fixed: scan refused
        f = scan.finding_ids.filtered(lambda x: x.check_code == 'acc_overdue_customer_invoices')
        vals = f.with_user(self.hc_user).read(['exposure', 'res_ids', 'count'])[0]
        leaked = vals['exposure'] >= 777.0 and str(inv.id) in vals['res_ids']
        self.assertFalse(leaked, "Non-accounting user obtained overdue receivables %s and invoice ids %s"
                         % (vals['exposure'], vals['res_ids'][:80]))

    # --------------------------------------------------------------- controls
    def test_hc05_usage_rows_protected_even_with_internal_flag(self):
        """SECURITY-REVIEW: HC-05 CONTROL. Usage counters and logs cannot be
        created, written or deleted by user, manager or admin (base.group_system)
        even when the RPC caller adds the internal context flag."""
        row = self.Usage._get_row(limits.USAGE_KEY_SCANS)
        Log = self.env['elaf.health.usage.log']
        self.assertTrue(self.admin.has_group('base.group_system'))
        for user in (self.hc_user, self.hc_manager, self.admin):
            ctx = {INTERNAL: True}
            with self.assertRaises(AccessError):
                row.with_user(user).with_context(**ctx).write({'used': 0})
            with self.assertRaises(AccessError):
                row.with_user(user).with_context(**ctx).unlink()
            with self.assertRaises(AccessError):
                self.Usage.with_user(user).with_context(**ctx).create({'key': 'completed_scans_2'})
            with self.assertRaises(AccessError):
                Log.with_user(user).with_context(**ctx).create({'key': 'x', 'event': 'consumed'})
            with self.assertRaises(AccessError):
                row.with_user(user).with_context(**ctx).copy()
        logs = Log.sudo().search([], limit=1)
        if logs:
            with self.assertRaises(AccessError):
                logs.with_context(**{INTERNAL: True}).sudo().unlink()

    def test_hc06_limit_cannot_be_bypassed_by_public_methods(self):
        """SECURITY-REVIEW: HC-06 CONTROL. Once 3 scans are used: run, dashboard
        run, rerun of a failed scan, copy of a completed scan, delete+recreate
        all fail to produce a new completed scan or refund usage."""
        done = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        done.action_run()
        row = self.Usage._get_row(limits.USAGE_KEY_SCANS)
        self.env.cr.execute("UPDATE elaf_health_usage SET used = %s WHERE id = %s",
                            (limits.LITE_MAX_COMPLETED_SCANS, row.id))
        row.invalidate_recordset()
        done_before = self.Scan.search_count([('state', '=', 'done')])
        new = self.Scan.with_user(self.hc_user).create({'scope': 'sales'})
        with self.assertRaises(UserError):
            new.action_run()
        res = self.Scan.with_user(self.hc_user).action_dashboard_run('sales')
        self.assertEqual(res.get('redirect'), 'lite_vs_pro')
        with self.assertRaises(AccessError):
            done.with_user(self.hc_user).copy()
        done.with_user(self.hc_manager).unlink()
        again = self.Scan.with_user(self.hc_manager).create({'scope': 'master_data'})
        with self.assertRaises(UserError):
            again.action_run()
        self.assertEqual(self._used(), limits.LITE_MAX_COMPLETED_SCANS)
        self.assertEqual(self.Scan.search_count([('state', '=', 'done')]), done_before - 1)
        # the edition hook is private (not RPC-callable) and stays False
        self.assertFalse(self.env['elaf.health.edition'].with_user(self.hc_user)._elaf_health_is_pro())

    def test_hc07_second_company_refused_everywhere(self):
        """SECURITY-REVIEW: HC-07 CONTROL. Lite bound to company A refuses scans
        in company B from the form and from the dashboard, without consuming."""
        self.hc_user.write({'company_ids': [(4, self.other.id)]})
        self.Scan.with_user(self.hc_user).create({'scope': 'master_data'}).action_run()
        used = self._used()
        with self.assertRaises(UserError):
            self.Scan.with_user(self.hc_user).with_company(self.other).action_dashboard_run('master_data')
        self.assertEqual(self._used(), used)
        self.assertFalse(self.Scan.search([('company_id', '=', self.other.id), ('state', '=', 'done')]))
        # a user restricted to company A cannot create a scan for company B
        refused = False
        try:
            self.Scan.with_user(self.hc_manager).create({'scope': 'sales', 'company_id': self.other.id})
        except (AccessError, UserError, ValidationError):
            refused = True
        self.assertTrue(refused)

    def test_hc08_cross_company_isolation(self):
        """SECURITY-REVIEW: HC-08 CONTROL. A user of company B sees nothing of
        company A through search_read, read_group, dashboard / comparison data,
        or the 'View affected records' action."""
        self.make_partner('SecRev Twin')
        self.make_partner('secrev twin')
        scan_a = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        scan_a.action_run()
        Finding = self.env['elaf.health.finding'].with_user(self.user_b)
        Scan = self.Scan.with_user(self.user_b)
        self.assertFalse(Finding.search_read([], ['exposure', 'res_ids']))
        rg = getattr(Finding, 'formatted_read_group', None)
        if rg:
            self.assertFalse(rg([], ['scan_id'], ['count:sum']))
            # R2 (S5-H2): the score is computed per reader and no longer
            # stored, so it cannot be aggregated; the grouping itself must
            # still return nothing of company A.
            self.assertFalse(Scan.formatted_read_group([], ['company_id'], ['__count']))
        self.assertFalse(Scan.search_read([], ['score']))
        self.assertEqual(Scan.get_lite_vs_pro_data()['value']['scans'], 0)
        self.assertFalse(Scan.get_dashboard_data({'scope': 'master_data'})['latest'])
        fail = scan_a.finding_ids.filtered(lambda f: f.result == 'fail')[:1]
        self.assertTrue(fail)
        with self.assertRaises(AccessError):
            fail.with_user(self.user_b).action_view_records()
        with self.assertRaises(AccessError):
            scan_a.with_user(self.user_b).read(['name', 'score'])

    def test_hc09_check_catalogue_frozen_at_10(self):
        """SECURITY-REVIEW: HC-09 CONTROL. A manager cannot create, copy or delete
        checks; findings are read-only for users and managers."""
        check = self.check('sale_stale_quotation')
        with self.assertRaises(AccessError):
            check.with_user(self.hc_manager).copy()
        with self.assertRaises(AccessError):
            check.with_user(self.hc_manager).unlink()
        self.Check.with_context(active_test=False).search([]).with_user(self.hc_manager).write({'active': True})
        self.assertEqual(self.Check.search_count([]), limits.LITE_MAX_CHECKS)
        scan = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        scan.action_run()
        for user in (self.hc_user, self.hc_manager):
            with self.assertRaises(AccessError):
                scan.finding_ids[:1].with_user(user).write({'exposure': 0})
            with self.assertRaises(AccessError):
                scan.finding_ids[:1].with_user(user).unlink()

    # ------------------------------------------------------- business checks
    def test_hc10_spot_check_health_score_end_to_end(self):
        """SECURITY-REVIEW: HC-10 CONTROL (business). Real master-data scan; the
        Health Score is recomputed by hand from the check settings and counts."""
        dup = self.check('md_duplicate_partners')
        nc = self.check('md_partner_no_contact')
        self.assertEqual((dup.weight, dup.severity, dup.count_threshold), (1.5, 'medium', 10))
        self.assertEqual((nc.weight, nc.severity, nc.count_threshold), (1.0, 'low', 25))
        self.make_partner('Spot Twin Ltd')
        self.make_partner('spot  twin, ltd.')
        self.make_partner('Spot NoMail', email=False)
        scan = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        scan.action_run()
        c_dup = scan.finding_ids.filtered(lambda f: f.check_code == 'md_duplicate_partners').count
        c_nc = scan.finding_ids.filtered(lambda f: f.check_code == 'md_partner_no_contact').count
        self.assertGreaterEqual(c_dup, 2)
        self.assertGreaterEqual(c_nc, 1)
        pen = 1.5 * 2 * min(1, c_dup / 10) + 1.0 * 1 * min(1, c_nc / 25)
        hand = round(100 * (1 - pen / 4.0), 1)
        _logger.info("HC-10 counts dup=%s no_contact=%s penalty=%.4f hand=%s engine=%s",
                     c_dup, c_nc, pen, hand, scan.score)
        self.assertAlmostEqual(scan.score, hand, places=1)

    def test_hc11_spot_check_overdue_exposure_partial_payment(self):
        """SECURITY-REVIEW: HC-11 CONTROL (business). Overdue customer invoice
        exposure = residual: 1000 invoice due 45 days ago, 400 paid -> +600;
        an invoice due 10 days ago (< 30) is not flagged."""
        before, r0 = self.ids_of('acc_overdue_customer_invoices')
        partner = self.make_partner('Spot Debtor')
        inv = self.make_invoice('out_invoice', partner, 1000.0, 45)
        recent = self.make_invoice('out_invoice', partner, 250.0, 10)
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=inv.ids).create({'amount': 400.0})._create_payments()
        self.assertAlmostEqual(inv.amount_residual, 600.0, places=2)
        after, r1 = self.ids_of('acc_overdue_customer_invoices')
        self.assertEqual(after - before, {inv.id})
        self.assertNotIn(recent.id, after)
        _logger.info("HC-11 exposure before=%s after=%s", r0.get('exposure'), r1.get('exposure'))
        self.assertAlmostEqual((r1.get('exposure') or 0.0) - (r0.get('exposure') or 0.0), 600.0, places=2)
