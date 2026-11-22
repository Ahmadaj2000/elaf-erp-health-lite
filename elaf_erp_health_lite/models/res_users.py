from odoo import models


class ResUsers(models.Model):
    _inherit = 'res.users'

    def _elaf_hc_denied_check_codes(self):
        """Codes of the ERP Health checks whose findings this user may not see
        (they reveal data from apps the user cannot read, e.g. accounting).
        Used by the record rule on elaf.health.finding (HC-04 / HC-21); the
        rule domain is cached per user and recomputed when groups or access
        rights change."""
        self.ensure_one()
        return sorted(self.env['elaf.health.check'].sudo()._hc_denied_checks(self))
