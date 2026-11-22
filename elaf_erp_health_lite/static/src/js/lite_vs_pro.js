/** @odoo-module **/
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

export class ElafHcLiteVsPro extends Component {
    static template = "elaf_erp_health_lite.LiteVsPro";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ loading: true, error: false, data: null });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("elaf.health.scan", "get_lite_vs_pro_data", []);
        } catch (e) {
            this.state.error = (e && e.data && e.data.message) || (e && e.message) || _t("Unknown error");
        } finally {
            this.state.loading = false;
        }
    }

    get rows() {
        return [
            { label: _t("Built-in checks"), lite: "10", pro: "27" },
            { label: _t("Audited business modules"), lite: _t("One, fixed: chosen once, cannot be changed in Lite"), pro: _t("All modules") },
            { label: _t("Business areas per scan"), lite: _t("One"), pro: _t("One, or all areas in one cross-module scan") },
            { label: _t("Companies"), lite: _t("One"), pro: _t("Multi-company") },
            { label: _t("Completed scans"), lite: _t("Up to 3 in total"), pro: _t("Unlimited") },
            { label: _t("Health Score, findings, recommendations, affected records"), lite: true, pro: true },
            { label: _t("Score change versus the previous scan"), lite: true, pro: true },
            { label: _t("Threshold settings (days)"), lite: true, pro: true },
            { label: _t("Cross-module checks (delivered not invoiced, received not billed, price increases, valuation vs accounting, credit limits)"), lite: false, pro: true },
            { label: _t("Security and control diagnostics (admin rights, inactive users, shared logins, segregation of duties)"), lite: false, pro: true },
            { label: _t("Per-check thresholds"), lite: false, pro: true },
            { label: _t("Scheduled audits with alerts when the score drops"), lite: false, pro: true },
            { label: _t("Score trend and scan comparison (new / resolved / persisting)"), lite: false, pro: true },
            { label: _t("Financial exposure dashboard (estimates)"), lite: false, pro: true },
            { label: _t("PDF audit report"), lite: false, pro: true },
        ];
    }

    discoverPro() {
        return this.action.doAction({ type: "ir.actions.act_url", url: this.state.data.pro_url, target: "new" });
    }

    openDashboard() {
        return this.action.doAction("elaf_erp_health_lite.action_health_dashboard");
    }
}

registry.category("actions").add("elaf_hc_lite_vs_pro", ElafHcLiteVsPro);
