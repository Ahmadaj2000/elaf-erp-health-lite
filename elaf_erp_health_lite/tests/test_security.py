from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from .common import HealthCommon


@tagged('elaf_health', 'at_install', '-post_install')
class TestLiteSecurity(HealthCommon):

    def test_user_runs_scans_but_cannot_configure(self):
        check = self.check('md_duplicate_partners')
        self.assertTrue(check.with_user(self.hc_user).read(['name']))
        with self.assertRaises(AccessError):
            check.with_user(self.hc_user).write({'active': False})
        check.with_user(self.hc_manager).write({'weight': 2.0, 'severity': 'high'})
        self.assertEqual(check.severity, 'high')
        with self.assertRaises(AccessError):
            self.Check.with_user(self.hc_manager).create({
                'code': 'x_new', 'name': 'x', 'scope': 'sales', 'severity': 'low'})
        scan = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        scan.action_run()
        self.assertEqual(scan.state, 'done')
        with self.assertRaises(AccessError):
            scan.with_user(self.hc_user).unlink()
        with self.assertRaises(AccessError):
            scan.finding_ids[:1].with_user(self.hc_user).write({'count': 0})
        # settings parameters are not writable by a plain health user
        with self.assertRaises(AccessError):
            self.env['ir.config_parameter'].with_user(self.hc_user).set_param(
                'elaf_erp_health.overdue_days', 1)

    def test_non_member_cannot_access(self):
        with self.assertRaises(AccessError):
            self.Scan.with_user(self.plain_user).search([])
        with self.assertRaises(AccessError):
            self.Scan.with_user(self.plain_user).action_dashboard_run('master_data')

    def test_multi_company_record_rules(self):
        other = self.env['res.company'].create({'name': 'Rule Co (test)'})
        user_b = new_test_user(
            self.env, login='hc_user_b', groups='base.group_user,elaf_erp_health_lite.group_health_user',
            company_id=other.id, company_ids=[other.id])
        scan_a = self.Scan.with_user(self.hc_user).create({'scope': 'master_data'})
        scan_a.action_run()
        visible_b = self.Scan.with_user(user_b).search([])
        self.assertNotIn(scan_a, visible_b)
        self.assertFalse(self.env['elaf.health.finding'].with_user(user_b).search(
            [('scan_id', '=', scan_a.id)]))
        with self.assertRaises(AccessError):
            scan_a.with_user(user_b).read(['score'])
        # dashboard of company B shows nothing of company A
        data = self.Scan.with_user(user_b).get_dashboard_data({})
        self.assertFalse(data['latest'])
        self.assertEqual(data['scan_total'], 0)
