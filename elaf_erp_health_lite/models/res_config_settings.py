from odoo import api, fields, models

from .health_check import SCOPES
from .limits import USAGE_KEY_SCANS


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    elaf_hc_stale_quote_days = fields.Integer(
        string='Stale quotation age (days)', default=30,
        config_parameter='elaf_erp_health.stale_quote_days')
    elaf_hc_stale_rfq_days = fields.Integer(
        string='Stale RFQ age (days)', default=30,
        config_parameter='elaf_erp_health.stale_rfq_days')
    elaf_hc_dead_stock_days = fields.Integer(
        string='Dead stock: no movement for (days)', default=180,
        config_parameter='elaf_erp_health.dead_stock_days')
    elaf_hc_overdue_days = fields.Integer(
        string='Customer invoices overdue by more than (days)', default=30,
        config_parameter='elaf_erp_health.overdue_days')
    # S5-H3: "Choose your audited module" (Lite, managers only, once)
    elaf_hc_lite_scope = fields.Selection(
        SCOPES, string='Audited module (Lite)',
        help="The Lite edition audits one business module. Once chosen here, or set by the first "
             "completed scan, it cannot be changed in Lite. Pro audits every module.")
    elaf_hc_lite_scope_locked = fields.Boolean(compute='_compute_elaf_hc_edition')
    elaf_hc_is_pro = fields.Boolean(compute='_compute_elaf_hc_edition')

    def _compute_elaf_hc_edition(self):
        is_pro = self.env['elaf.health.edition']._elaf_health_is_pro()
        bound = self.env['elaf.health.usage']._get_row(USAGE_KEY_SCANS).bound_scope
        for settings in self:
            settings.elaf_hc_is_pro = is_pro
            settings.elaf_hc_lite_scope_locked = bool(bound)

    @api.model
    def get_values(self):
        res = super().get_values()
        res['elaf_hc_lite_scope'] = self.env['elaf.health.usage']._get_row(USAGE_KEY_SCANS).bound_scope or False
        return res

    def set_values(self):
        super().set_values()
        Usage = self.env['elaf.health.usage']
        if self.env['elaf.health.edition']._elaf_health_is_pro():
            return
        bound = Usage._get_row(USAGE_KEY_SCANS).bound_scope
        for settings in self:
            if settings.elaf_hc_lite_scope and settings.elaf_hc_lite_scope != bound:
                Usage._bind_scope(settings.elaf_hc_lite_scope)
