"""Implementations of the 10 Lite checks.

Every method receives the scan and must only look at the scan company's data.
They run in sudo (the scan engine verified the user's ERP Health group first)
and use search/read_group/SQL aggregates, never per-record Python loops over
large tables.
"""
from datetime import timedelta

from odoo import api, fields, models


class ElafHealthCheckLite(models.Model):
    _inherit = 'elaf.health.check'

    # ------------------------------------------------------------------ Sales
    def _hc_check_sale_stale_quotation(self, scan):
        days = self._param_for_check('stale_quote_days')
        orders = self.env['sale.order'].sudo().search([
            ('company_id', '=', scan.company_id.id),
            ('state', 'in', ('draft', 'sent')),
            ('date_order', '<', self._hc_cutoff(days)),
        ])
        return {'model': 'sale.order', 'ids': orders.ids}

    def _hc_check_sale_below_cost(self, scan):
        """Confirmed order lines whose net unit price (company currency) is
        below the product cost. Only lines sold in the product's own unit are
        compared (other units would need a conversion and are ignored).
        Exposure (estimate) = sum((cost - net unit price) x ordered qty)."""
        cid = scan.company_id.id
        key = str(cid)
        self.env['sale.order.line'].flush_model()
        self.env['sale.order'].flush_model(['currency_rate', 'state', 'company_id'])
        self.env['product.product'].flush_model(['standard_price'])
        self.env.cr.execute("""
            SELECT sol.id,
                   sol.product_uom_qty,
                   sol.price_reduce_taxexcl / COALESCE(NULLIF(so.currency_rate, 0), 1) AS net_price,
                   COALESCE((pp.standard_price ->> %(key)s)::numeric, 0) AS cost
              FROM sale_order_line sol
              JOIN sale_order so ON so.id = sol.order_id
              JOIN product_product pp ON pp.id = sol.product_id
              JOIN product_template pt ON pt.id = pp.product_tmpl_id
             WHERE so.company_id = %(cid)s
               AND so.state = 'sale'
               AND sol.display_type IS NULL
               AND sol.is_downpayment IS NOT TRUE
               AND sol.product_uom_qty > 0
               AND sol.product_uom_id = pt.uom_id
               AND COALESCE((pp.standard_price ->> %(key)s)::numeric, 0) > 0
               AND sol.price_reduce_taxexcl / COALESCE(NULLIF(so.currency_rate, 0), 1)
                   < COALESCE((pp.standard_price ->> %(key)s)::numeric, 0) - 0.0001
        """, {'cid': cid, 'key': key})
        rows = self.env.cr.fetchall()
        exposure = sum(float(qty) * (float(cost) - float(price)) for _id, qty, price, cost in rows)
        return {'model': 'sale.order.line', 'ids': [r[0] for r in rows], 'exposure': exposure}

    # --------------------------------------------------------------- Purchase
    def _hc_check_purchase_late_receipt(self, scan):
        orders = self.env['purchase.order'].sudo().search([
            ('company_id', '=', scan.company_id.id),
            ('state', '=', 'purchase'),
            ('receipt_status', 'in', ('pending', 'partial')),
            ('date_planned', '<', fields.Datetime.now()),
        ])
        return {'model': 'purchase.order', 'ids': orders.ids}

    def _hc_check_purchase_stale_rfq(self, scan):
        days = self._param_for_check('stale_rfq_days')
        orders = self.env['purchase.order'].sudo().search([
            ('company_id', '=', scan.company_id.id),
            ('state', 'in', ('draft', 'sent')),
            ('date_order', '<', self._hc_cutoff(days)),
        ])
        return {'model': 'purchase.order', 'ids': orders.ids}

    # -------------------------------------------------------------- Inventory
    def _hc_check_stock_negative_quant(self, scan):
        """Exposure (estimate) = sum(|negative quantity| x product cost)."""
        company = scan.company_id
        Quant = self.env['stock.quant'].sudo()
        domain = [
            ('company_id', '=', company.id),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '<', 0),
        ]
        quants = Quant.search(domain)
        exposure = 0.0
        if quants:
            groups = Quant._read_group(domain, ['product_id'], ['quantity:sum'])
            costs = self._hc_product_costs(company, [p.id for p, _q in groups])
            exposure = sum(abs(qty) * costs.get(p.id, 0.0) for p, qty in groups)
        return {'model': 'stock.quant', 'ids': quants.ids, 'exposure': exposure}

    def _hc_check_stock_dead_stock(self, scan):
        """Products with positive on-hand stock and no done stock move in the
        last N days. Exposure (estimate) = sum(on-hand qty x product cost)."""
        company = scan.company_id
        days = self._param_for_check('dead_stock_days')
        cutoff = self._hc_cutoff(days)
        self.env['stock.quant'].flush_model()
        self.env['stock.move'].flush_model(['product_id', 'state', 'date', 'company_id'])
        self.env.cr.execute("""
            SELECT q.product_id, SUM(q.quantity)
              FROM stock_quant q
              JOIN stock_location l ON l.id = q.location_id
             WHERE l.usage = 'internal' AND q.company_id = %(cid)s
          GROUP BY q.product_id
            HAVING SUM(q.quantity) > 0
               AND NOT EXISTS (
                    SELECT 1 FROM stock_move m
                     WHERE m.product_id = q.product_id
                       AND m.state = 'done'
                       AND m.company_id = %(cid)s
                       AND m.date >= %(cutoff)s)
        """, {'cid': company.id, 'cutoff': cutoff})
        rows = self.env.cr.fetchall()
        costs = self._hc_product_costs(company, [r[0] for r in rows])
        exposure = sum(float(qty) * costs.get(pid, 0.0) for pid, qty in rows)
        return {'model': 'product.product', 'ids': [r[0] for r in rows], 'exposure': exposure}

    # ------------------------------------------------------------- Accounting
    def _overdue_moves(self, scan, move_type, days):
        Move = self.env['account.move'].sudo()
        due_before = fields.Date.context_today(self) - timedelta(days=days)
        domain = [
            ('company_id', '=', scan.company_id.id),
            ('move_type', '=', move_type),
            ('state', '=', 'posted'),
            ('payment_state', 'in', ('not_paid', 'partial')),
            ('invoice_date_due', '<', due_before),
        ]
        moves = Move.search(domain)
        exposure = 0.0
        if moves:
            [(total,)] = Move._read_group(domain, [], ['amount_residual_signed:sum'])
            exposure = abs(total or 0.0)
        return {'model': 'account.move', 'ids': moves.ids, 'exposure': exposure}

    def _hc_check_acc_overdue_customer_invoices(self, scan):
        """Exposure (estimate) = sum of the residual amounts (company currency)."""
        return self._overdue_moves(scan, 'out_invoice', self._param_for_check('overdue_days'))

    def _hc_check_acc_overdue_vendor_bills(self, scan):
        """Bills past their due date. Exposure (estimate) = residual amounts."""
        return self._overdue_moves(scan, 'in_invoice', 0)

    # ------------------------------------------------------------ Master data
    def _hc_check_md_duplicate_partners(self, scan):
        """Commercial partners (no parent) sharing the same normalised name
        (case, spaces and punctuation ignored) or the same normalised Tax ID."""
        self.env['res.partner'].flush_model(['name', 'vat', 'active', 'parent_id', 'company_id'])
        self.env.cr.execute(r"""
            WITH p AS (
                SELECT id,
                       btrim(regexp_replace(lower(name), '[[:punct:][:space:]]+', ' ', 'g')) AS nn,
                       upper(regexp_replace(COALESCE(vat, ''), '[^A-Za-z0-9]', '', 'g')) AS nv
                  FROM res_partner
                 WHERE active AND parent_id IS NULL AND name IS NOT NULL
                   AND (company_id IS NULL OR company_id = %(cid)s)
            )
            SELECT id FROM p
             WHERE (nn <> '' AND nn IN (SELECT nn FROM p WHERE nn <> '' GROUP BY nn HAVING COUNT(*) > 1))
                OR (length(nv) > 1 AND nv IN (SELECT nv FROM p WHERE length(nv) > 1
                                              GROUP BY nv HAVING COUNT(*) > 1))
          ORDER BY id
        """, {'cid': scan.company_id.id})
        ids = [r[0] for r in self.env.cr.fetchall()]
        return {'model': 'res.partner', 'ids': ids}

    def _hc_check_md_partner_no_contact(self, scan):
        """Commercial partners (excluding companies' and users' own partners)
        missing an e-mail or a phone number."""
        company_partners = self.env['res.company'].sudo().search([]).partner_id.ids
        partners = self.env['res.partner'].sudo().search([
            ('parent_id', '=', False),
            ('company_id', 'in', [False, scan.company_id.id]),
            ('id', 'not in', company_partners),
            ('user_ids', '=', False),
            '|', ('email', '=', False), ('phone', '=', False),
        ])
        return {'model': 'res.partner', 'ids': partners.ids}
