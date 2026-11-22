from unittest.mock import patch

import psycopg2

from odoo import sql_db
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.elaf_erp_health_lite.models import limits
from odoo.addons.elaf_erp_health_lite.models.health_scan import ElafHealthScan
from .common import HealthCommon


@tagged('elaf_health', 'at_install', '-post_install')
class TestLiteUsage(HealthCommon):
    """Freemium limits (docs/FREEMIUM_SPEC.md section A.7)."""

    def used(self):
        return self.Usage._get_row(limits.USAGE_KEY_SCANS).used

    def run_scan(self, user=None, scope='master_data', company=None):
        Scan = self.Scan.with_user(user or self.hc_user)
        vals = {'scope': scope}
        if company:
            vals['company_id'] = company.id
        scan = Scan.create(vals)
        scan.action_run()
        return scan

    def test_counting_and_limit(self):
        self.assertTrue(limits.ELAF_USAGE_CAPS_ENABLED)
        start = self.used()
        self.assertEqual(start, 0)
        s1 = self.run_scan()
        self.assertEqual(self.used(), 1)
        # R2 (S5-H3): Lite is bound to the module of the first completed scan,
        # so the three allowed scans all audit that module.
        self.run_scan()
        self.run_scan()
        self.assertEqual(self.used(), 3)
        status = self.Usage._get_status()
        self.assertTrue(status['exhausted'])
        self.assertEqual(status['remaining'], 0)
        self.assertEqual(status['pct'], 100.0)
        # 4th scan denied, nothing consumed
        scan4 = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        with self.assertRaises(UserError):
            scan4.action_run()
        self.assertEqual(self.used(), 3)
        # dashboard: Run scan opens the comparison page instead
        res = self.Scan.with_user(self.hc_user).action_dashboard_run('master_data')
        self.assertEqual(res.get('redirect'), 'lite_vs_pro')
        # history stays readable after exhaustion
        data = self.Scan.with_user(self.hc_user).get_dashboard_data({})
        self.assertEqual(len([s for s in data['recent_scans'] if s['state'] == 'done']), 3)
        finding = s1.with_user(self.hc_user).finding_ids[:1]
        self.assertTrue(finding.read(['name', 'result']))
        # in-flight work after exhaustion: opening affected records still works
        failing = s1.finding_ids.filtered(lambda f: f.result == 'fail')[:1]
        if failing:
            self.assertEqual(failing.with_user(self.hc_user).action_view_records()['type'],
                             'ir.actions.act_window')
        log = self.env['elaf.health.usage.log'].search([('key', '=', limits.USAGE_KEY_SCANS)])
        self.assertEqual(len(log.filtered(lambda l: l.event == 'consumed')), 3)

    def test_monotonic_deleting_scans_does_not_refund(self):
        scan = self.run_scan()
        self.assertEqual(self.used(), 1)
        scan.with_user(self.hc_manager).unlink()
        self.assertEqual(self.used(), 1)

    @mute_logger('odoo.addons.elaf_erp_health_lite.models.health_scan')
    def test_crashed_scan_does_not_consume(self):
        with patch.object(ElafHealthScan, '_hc_execute_checks', side_effect=RuntimeError('boom')):
            scan = self.run_scan()
        self.assertEqual(scan.state, 'failed')
        self.assertIn('boom', scan.error_message)
        self.assertEqual(self.used(), 0)
        # rerun the same scan once it works: consumed once
        scan.with_user(self.hc_user).action_run()
        self.assertEqual(scan.state, 'done')
        self.assertEqual(self.used(), 1)

    def test_failure_after_counter_step_rolls_back(self):
        """An exception after the consume step rolls back the counter too."""
        scan = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        with patch.object(ElafHealthScan, '_hc_after_done', side_effect=RuntimeError('late failure')):
            with self.assertRaises(RuntimeError):
                with self.env.cr.savepoint():
                    scan.action_run()
        self.env.invalidate_all()
        self.assertEqual(self.used(), 0)
        self.assertEqual(scan.state, 'draft')

    def test_duplicate_run_does_not_double_consume(self):
        scan = self.run_scan()
        self.assertEqual(self.used(), 1)
        scan.with_user(self.hc_user).action_run()
        scan.with_user(self.hc_user).action_run()
        self.assertEqual(self.used(), 1)

    def test_usage_rows_protected(self):
        row = self.Usage._get_row(limits.USAGE_KEY_SCANS)
        for user in (self.hc_user, self.hc_manager):
            self.assertTrue(row.with_user(user).read(['used']))
            with self.assertRaises(AccessError):
                row.with_user(user).write({'used': 0})
            with self.assertRaises(AccessError):
                row.with_user(user).unlink()
            with self.assertRaises(AccessError):
                self.Usage.with_user(user).create({'key': 'x'})
        # even sudo / admin cannot change it without the internal flag
        with self.assertRaises(AccessError):
            row.sudo().write({'used': 0})
        with self.assertRaises(AccessError):
            row.sudo().unlink()
        log = self.env['elaf.health.usage.log']
        with self.assertRaises(AccessError):
            log.sudo().create({'key': 'x', 'event': 'consumed'})
        with self.assertRaises(AccessError):
            self.plain_user and row.with_user(self.plain_user).read(['used'])

    def test_second_company_refused(self):
        other = self.env['res.company'].create({'name': 'Second Co (test)'})
        self.hc_user.write({'company_ids': [(4, other.id)]})
        self.run_scan()
        bound = self.Usage._get_row(limits.USAGE_KEY_SCANS).bound_company_id
        self.assertEqual(bound, self.company)
        scan_b = self.Scan.with_user(self.hc_user).with_company(other).create(
            {'scope': 'master_data', 'company_id': other.id})
        with self.assertRaises(UserError):
            scan_b.with_company(other).action_run()
        self.assertEqual(self.used(), 1)
        # viewing the first company's data is never blocked
        data = self.Scan.with_user(self.hc_user).with_company(other).get_dashboard_data({})
        self.assertTrue(data['company_blocked'])

    def test_limit_sql_path(self):
        """The conditional UPDATE refuses once used >= limit (used directly)."""
        row = self.Usage._get_row(limits.USAGE_KEY_SCANS)
        self.env.cr.execute("UPDATE elaf_health_usage SET used = %s WHERE id = %s",
                            (limits.LITE_MAX_COMPLETED_SCANS, row.id))
        row.invalidate_recordset()
        scan = self.Scan.create({'scope': 'master_data'})
        with self.assertRaises(UserError):
            self.Usage._consume(limits.USAGE_KEY_SCANS, scan, self.company)
        self.assertEqual(self.used(), limits.LITE_MAX_COMPLETED_SCANS)


@tagged('elaf_health', 'post_install', '-at_install')
class TestLiteUsageLock(HealthCommon):

    @mute_logger('odoo.sql_db')
    def test_concurrent_consume_waits_for_row_lock(self):
        """A second connection holding the usage row lock blocks consumption
        (proves the UPDATE takes a row lock, so concurrent scans serialise)."""
        row = self.Usage._get_row(limits.USAGE_KEY_SCANS)
        # The row is created at install (post_init_hook) and committed.
        cr2 = sql_db.db_connect(self.env.cr.dbname).cursor()
        try:
            cr2.execute("SELECT id FROM elaf_health_usage WHERE key = %s FOR UPDATE NOWAIT",
                        (limits.USAGE_KEY_SCANS,))
            if not cr2.fetchone():
                self.skipTest("usage row not committed yet in this database")
            scan = self.Scan.create({'scope': 'master_data'})
            with self.assertRaises(psycopg2.errors.LockNotAvailable):
                with self.env.cr.savepoint():
                    self.env.cr.execute("SET LOCAL lock_timeout = '300ms'")
                    self.Usage._consume(limits.USAGE_KEY_SCANS, scan, self.company)
        finally:
            cr2.rollback()
            cr2.close()
        row.invalidate_recordset()
        self.assertEqual(row.used, self.Usage._get_row(limits.USAGE_KEY_SCANS).used)
