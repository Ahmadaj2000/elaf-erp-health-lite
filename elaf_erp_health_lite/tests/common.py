from datetime import timedelta

from odoo import Command, fields
from odoo.tests import new_test_user
from odoo.tests.common import TransactionCase


class HealthCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Check = cls.env['elaf.health.check']
        cls.Scan = cls.env['elaf.health.scan']
        cls.Usage = cls.env['elaf.health.usage']
        cls.hc_user = new_test_user(
            cls.env, login='hc_user', groups='base.group_user,elaf_erp_health_lite.group_health_user',
            company_id=cls.company.id, company_ids=[cls.company.id])
        cls.hc_manager = new_test_user(
            cls.env, login='hc_manager', groups='base.group_user,elaf_erp_health_lite.group_health_manager',
            company_id=cls.company.id, company_ids=[cls.company.id])
        cls.plain_user = new_test_user(cls.env, login='hc_plain', groups='base.group_user')
        cls.installed = cls.Check._installed_modules()
        cls.now = fields.Datetime.now()
        cls.today = fields.Date.context_today(cls.Check)

    # ---------------------------------------------------------------- helpers
    def check(self, code):
        return self.Check.with_context(active_test=False).search([('code', '=', code)], limit=1)

    def evaluate(self, code, company=None):
        """Evaluate one check for ``company`` through the real engine entry point."""
        check = self.check(code)
        self.assertTrue(check, "check %s missing" % code)
        scan = self.Scan.create({'scope': check.scope, 'company_id': (company or self.company).id})
        return check.with_company(scan.company_id)._hc_evaluate(scan, self.installed)

    def ids_of(self, code, company=None):
        res = self.evaluate(code, company)
        return set(res.get('ids') or []), res

    def make_product(self, name, cost=10.0, price=20.0, storable=True, **kw):
        # Odoo 17: the product type is set through detailed_type
        # ('product' = storable, 'consu', 'service'); 'type' is computed from it.
        ptype = kw.pop('type', None) or ('product' if storable else 'consu')
        vals = {'name': name, 'detailed_type': ptype, 'standard_price': cost, 'list_price': price}
        vals.update(kw)
        return self.env['product.product'].create(vals)

    def make_partner(self, name, **kw):
        vals = {'name': name, 'email': '%s@example.com' % name.lower().replace(' ', '.'),
                'phone': '+1 555 0100'}
        vals.update(kw)
        return self.env['res.partner'].create(vals)

    def stock_location(self, company=None):
        wh = self.env['stock.warehouse'].search([('company_id', '=', (company or self.company).id)], limit=1)
        return wh.lot_stock_id

    def make_invoice(self, move_type, partner, amount, due_days_ago, post=True):
        due = self.today - timedelta(days=due_days_ago)
        move = self.env['account.move'].create({
            'move_type': move_type,
            'partner_id': partner.id,
            'invoice_date': due - timedelta(days=1),
            'invoice_date_due': due,
            'invoice_line_ids': [Command.create({
                'name': 'Synthetic line', 'quantity': 1, 'price_unit': amount, 'tax_ids': [],
            })],
        })
        if post:
            move.action_post()
        return move
