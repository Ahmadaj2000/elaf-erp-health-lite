from datetime import timedelta

from odoo import Command
from odoo.tests import tagged

from .common import HealthCommon


@tagged('elaf_health', 'at_install', '-post_install')
class TestLiteChecks(HealthCommon):
    """Each Lite check detects its synthetic problem, does not flag clean
    records, and returns hand-checked counts and exposures."""

    def test_00_lite_has_exactly_10_checks(self):
        checks = self.Check.with_context(active_test=False).search([])
        self.assertEqual(len(checks), 10)
        self.assertEqual(set(checks.mapped('scope')),
                         {'sales', 'purchase', 'inventory', 'accounting', 'master_data'})

    # ------------------------------------------------------------- Sales
    def test_sale_stale_quotation(self):
        before, _r = self.ids_of('sale_stale_quotation')
        partner = self.make_partner('Stale Customer')
        product = self.make_product('Quote Widget')
        line = [Command.create({'product_id': product.id, 'product_uom_qty': 1, 'price_unit': 50})]
        old = self.env['sale.order'].create({
            'partner_id': partner.id, 'order_line': line, 'date_order': self.now - timedelta(days=45)})
        fresh = self.env['sale.order'].create({
            'partner_id': partner.id, 'order_line': [Command.create({
                'product_id': product.id, 'product_uom_qty': 1, 'price_unit': 50})],
            'date_order': self.now - timedelta(days=5)})
        after, res = self.ids_of('sale_stale_quotation')
        self.assertEqual(after - before, {old.id})
        self.assertNotIn(fresh.id, after)
        self.assertEqual(res['result'], 'fail')
        # threshold from Settings is honoured
        self.env['ir.config_parameter'].set_param('elaf_erp_health.stale_quote_days', 60)
        after60, _r = self.ids_of('sale_stale_quotation')
        self.assertNotIn(old.id, after60)

    def test_sale_below_cost(self):
        before, _r = self.ids_of('sale_below_cost')
        partner = self.make_partner('Margin Customer')
        product = self.make_product('Cost Widget', cost=10.0, price=20.0)
        so = self.env['sale.order'].create({
            'partner_id': partner.id,
            'order_line': [
                Command.create({'product_id': product.id, 'product_uom_qty': 2, 'price_unit': 6.0, 'tax_ids': []}),
                Command.create({'product_id': product.id, 'product_uom_qty': 1, 'price_unit': 15.0, 'tax_ids': []}),
            ]})
        so.action_confirm()
        bad_line, good_line = so.order_line.sorted('price_unit')
        after, res = self.ids_of('sale_below_cost')
        self.assertEqual(after - before, {bad_line.id})
        self.assertNotIn(good_line.id, after)
        # hand check: (10 - 6) x 2 = 8
        self.assertAlmostEqual(res['exposure'] - (_r.get('exposure') or 0.0), 8.0, places=2)

    # ------------------------------------------------------------- Purchase
    def test_purchase_late_receipt(self):
        before, _r = self.ids_of('purchase_late_receipt')
        vendor = self.make_partner('Late Vendor')
        product = self.make_product('Late Part')

        def po(planned):
            order = self.env['purchase.order'].create({
                'partner_id': vendor.id,
                'order_line': [Command.create({'product_id': product.id, 'product_qty': 5,
                                               'price_unit': 10, 'date_planned': planned})]})
            order.button_confirm()
            return order
        late = po(self.now - timedelta(days=10))
        future = po(self.now + timedelta(days=10))
        received = po(self.now - timedelta(days=10))
        received.picking_ids.move_ids.quantity = 5
        received.picking_ids.button_validate()
        self.assertEqual(received.receipt_status, 'full')
        after, _res = self.ids_of('purchase_late_receipt')
        self.assertEqual(after - before, {late.id})
        self.assertNotIn(future.id, after)
        self.assertNotIn(received.id, after)

    def test_purchase_stale_rfq(self):
        before, _r = self.ids_of('purchase_stale_rfq')
        vendor = self.make_partner('RFQ Vendor')
        product = self.make_product('RFQ Part')
        line = {'product_id': product.id, 'product_qty': 1, 'price_unit': 5}
        old = self.env['purchase.order'].create({
            'partner_id': vendor.id, 'date_order': self.now - timedelta(days=40),
            'order_line': [Command.create(line)]})
        new = self.env['purchase.order'].create({
            'partner_id': vendor.id, 'order_line': [Command.create(line)]})
        after, _res = self.ids_of('purchase_stale_rfq')
        self.assertEqual(after - before, {old.id})
        self.assertNotIn(new.id, after)

    # ------------------------------------------------------------- Inventory
    def test_stock_negative_quant(self):
        before, r0 = self.ids_of('stock_negative_quant')
        loc = self.stock_location()
        neg = self.make_product('Negative Item', cost=4.0)
        pos = self.make_product('Positive Item', cost=4.0)
        Quant = self.env['stock.quant']
        Quant._update_available_quantity(neg, loc, -5)
        Quant._update_available_quantity(pos, loc, 5)
        after, res = self.ids_of('stock_negative_quant')
        new = Quant.browse(list(after - before))
        self.assertEqual(new.product_id, neg)
        self.assertNotIn(pos, Quant.browse(list(after)).product_id)
        # hand check: |-5| x 4 = 20
        self.assertAlmostEqual(res['exposure'] - (r0.get('exposure') or 0.0), 20.0, places=2)

    def test_stock_dead_stock(self):
        before, r0 = self.ids_of('stock_dead_stock')
        loc = self.stock_location()
        dead = self.make_product('Dead Item', cost=3.0)
        alive = self.make_product('Alive Item', cost=3.0)
        Quant = self.env['stock.quant']
        # dead: stock present, no done move at all
        Quant._update_available_quantity(dead, loc, 10)
        # alive: stock set by an inventory adjustment today (a done move)
        Quant.with_context(inventory_mode=True).create({
            'product_id': alive.id, 'location_id': loc.id, 'inventory_quantity': 7,
        }).action_apply_inventory()
        after, res = self.ids_of('stock_dead_stock')
        self.assertEqual(after - before, {dead.id})
        self.assertNotIn(alive.id, after)
        # hand check: 10 x 3 = 30
        self.assertAlmostEqual(res['exposure'] - (r0.get('exposure') or 0.0), 30.0, places=2)

    # ------------------------------------------------------------- Accounting
    def test_acc_overdue_customer_invoices(self):
        before, r0 = self.ids_of('acc_overdue_customer_invoices')
        customer = self.make_partner('Late Payer')
        overdue = self.make_invoice('out_invoice', customer, 100.0, 40)
        recent = self.make_invoice('out_invoice', customer, 70.0, 10)
        draft = self.make_invoice('out_invoice', customer, 55.0, 60, post=False)
        self.assertEqual(overdue.invoice_date_due, self.today - timedelta(days=40))
        after, res = self.ids_of('acc_overdue_customer_invoices')
        self.assertEqual(after - before, {overdue.id})
        self.assertFalse({recent.id, draft.id} & after)
        self.assertAlmostEqual(res['exposure'] - (r0.get('exposure') or 0.0), 100.0, places=2)

    def test_acc_overdue_vendor_bills(self):
        before, r0 = self.ids_of('acc_overdue_vendor_bills')
        vendor = self.make_partner('Bill Vendor')
        overdue = self.make_invoice('in_invoice', vendor, 250.0, 1)
        not_due = self.make_invoice('in_invoice', vendor, 90.0, -5)
        after, res = self.ids_of('acc_overdue_vendor_bills')
        self.assertEqual(after - before, {overdue.id})
        self.assertNotIn(not_due.id, after)
        self.assertAlmostEqual(res['exposure'] - (r0.get('exposure') or 0.0), 250.0, places=2)

    # ------------------------------------------------------------- Master data
    def test_md_duplicate_partners(self):
        before, _r = self.ids_of('md_duplicate_partners')
        a = self.make_partner('Acme Trading LLC')
        b = self.make_partner('ACME  trading, llc')
        c = self.make_partner('Northwind One', vat='DE123456789')
        d = self.make_partner('Northwind Two', vat='de 123 456 789')
        unique = self.make_partner('Unique Supplier Ltd')
        child = self.env['res.partner'].create({'name': 'Acme Trading LLC', 'parent_id': unique.id})
        after, res = self.ids_of('md_duplicate_partners')
        self.assertEqual(after - before, {a.id, b.id, c.id, d.id})
        self.assertNotIn(unique.id, after)
        self.assertNotIn(child.id, after)
        self.assertEqual(res['count'], len(after))

    def test_md_partner_no_contact(self):
        before, _r = self.ids_of('md_partner_no_contact')
        missing_mail = self.make_partner('No Mail Co', email=False)
        missing_phone = self.make_partner('No Phone Co', phone=False)
        complete = self.make_partner('Complete Co')
        after, _res = self.ids_of('md_partner_no_contact')
        self.assertEqual(after - before, {missing_mail.id, missing_phone.id})
        self.assertNotIn(complete.id, after)
        self.assertNotIn(self.company.partner_id.id, after)

    # ------------------------------------------------------------- Company isolation
    def test_checks_only_see_scan_company(self):
        other = self.env['res.company'].create({'name': 'Other Co (test)'})
        before, _r = self.ids_of('md_duplicate_partners')
        self.make_partner('Twin Holding', company_id=other.id)
        self.make_partner('Twin Holding', company_id=other.id)
        after, _r = self.ids_of('md_duplicate_partners')
        self.assertEqual(after, before, "partners of another company must not be flagged")
        other_ids, _r = self.ids_of('md_duplicate_partners', company=other)
        self.assertEqual(len(other_ids - before), 2)
