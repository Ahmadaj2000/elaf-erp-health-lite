"""R2 (S5-H3): Lite is bound to ONE business module.

Databases that already completed Lite scans of several modules are bound to
the module of their FIRST completed scan (of the bound company). Scans and
findings are kept as they are; later Lite scans can only audit that module.
"""
import logging

_logger = logging.getLogger(__name__)

USAGE_KEY_SCANS = 'completed_scans'
LITE_SCOPES = ('sales', 'purchase', 'inventory', 'accounting', 'master_data')


def migrate(cr, version):
    if not version:
        return
    cr.execute("SELECT id, bound_company_id FROM elaf_health_usage "
               "WHERE key = %s AND bound_scope IS NULL", (USAGE_KEY_SCANS,))
    row = cr.fetchone()
    if not row:
        return
    usage_id, company_id = row
    cr.execute("""
        SELECT scope FROM elaf_health_scan
         WHERE state = 'done' AND scope IN %s
           AND (%s::int IS NULL OR company_id = %s::int)
      ORDER BY date ASC NULLS LAST, id ASC
         LIMIT 1
    """, (LITE_SCOPES, company_id, company_id))
    first = cr.fetchone()
    if not first:
        return
    cr.execute("UPDATE elaf_health_usage SET bound_scope = %s WHERE id = %s", (first[0], usage_id))
    _logger.info("ERP Health Check Lite bound to the module of its first completed scan: %s", first[0])
