/** @odoo-module **/
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { ElafHcGauge } from "./gauge";

export class ElafHcDashboard extends Component {
    static template = "elaf_erp_health_lite.Dashboard";
    static components = { ElafHcGauge };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({
            loading: true,
            running: false,
            error: false,
            data: null,
            scope: "",
        });
        // Idempotency key of the next "Run scan" request (S5-H1): a double
        // click or a network retry sends the same key, so the server returns
        // the first scan instead of running and counting a second one.
        this.requestKey = this.newRequestKey();
        onWillStart(() => this.load());
    }

    newRequestKey() {
        const rnd = window.crypto && window.crypto.randomUUID
            ? window.crypto.randomUUID()
            : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
        return `hc-${rnd}`;
    }

    async load() {
        this.state.loading = true;
        this.state.error = false;
        try {
            const data = await this.orm.call("elaf.health.scan", "get_dashboard_data", [this.getFilters()]);
            this.state.data = data;
            if (!this.state.scope || !data.scopes.find((s) => s.key === this.state.scope)) {
                this.state.scope = (data.latest && data.scopes.find((s) => s.key === data.latest.scope))
                    ? data.latest.scope
                    : (data.scopes[0] && data.scopes[0].key) || "";
            }
        } catch (e) {
            this.state.error = (e && e.data && e.data.message) || (e && e.message) || _t("Unknown error");
        } finally {
            this.state.loading = false;
        }
    }

    getFilters() {
        return {};
    }

    // ----------------------------------------------------------------- helpers
    get data() {
        return this.state.data;
    }
    get isLite() {
        return this.data && this.data.edition === "lite";
    }
    get usage() {
        return this.data && this.data.usage;
    }
    get usageClass() {
        const u = this.usage;
        if (!u || !u.caps_enabled) {
            return "";
        }
        if (u.exhausted) {
            return "bad";
        }
        return u.pct >= 80 ? "warn" : "";
    }
    get maxSeverity() {
        const rows = (this.data && this.data.severity) || [];
        return Math.max(1, ...rows.map((r) => r.records));
    }
    get lockedFeatures() {
        return [
            { key: "cross", label: _t("Cross-module & security audits (all areas in one scan)") },
            { key: "checks", label: _t("17 additional checks (27 in total)") },
            { key: "trend", label: _t("Score trend & scan comparison") },
            { key: "schedule", label: _t("Scheduled audits with alerts") },
            { key: "exposure", label: _t("Financial exposure dashboard") },
            { key: "report", label: _t("PDF audit report") },
        ];
    }
    fmtDate(value) {
        if (!value) {
            return "";
        }
        try {
            return formatDateTime(deserializeDateTime(value));
        } catch {
            return value;
        }
    }
    fmtDelta(delta) {
        if (delta === null || delta === undefined) {
            return "";
        }
        const v = Math.round(delta * 10) / 10;
        return (v > 0 ? "+" : "") + v;
    }
    deltaClass(delta) {
        if (!delta) {
            return "";
        }
        return delta > 0 ? "up" : "down";
    }
    kpiClass(value, warnAt = 1) {
        return value >= warnAt ? "is-bad" : "is-ok";
    }

    // ----------------------------------------------------------------- actions
    async runScan() {
        if (this.state.running || !this.state.scope) {
            return;
        }
        if (this.isLite && this.usage && this.usage.exhausted) {
            this.notification.add(
                _t("All 3 Lite scans are used. Your scans and findings stay available. ERP Health Check Pro runs unlimited scans."),
                { type: "warning" }
            );
            return this.openLiteVsPro();
        }
        this.state.running = true;
        try {
            const res = await this.orm.call("elaf.health.scan", "action_dashboard_run", [
                this.state.scope,
                this.requestKey,
            ]);
            // the request was answered: the next click is a new request
            this.requestKey = this.newRequestKey();
            if (res.redirect) {
                this.notification.add(res.message, { type: "warning" });
                return this.openLiteVsPro();
            }
            if (res.state === "failed") {
                this.notification.add(_t("The scan failed and was not counted: %s", res.error || ""), {
                    type: "danger",
                    sticky: true,
                });
            } else if (res.score !== null && res.score !== undefined) {
                this.notification.add(_t("Scan completed. Health Score: %s", res.score), { type: "success" });
                this.milestones(res);
            } else {
                this.notification.add(_t("Scan completed."), { type: "success" });
                this.milestones(res);
            }
            await this.load();
        } catch (e) {
            const msg = (e && e.data && e.data.message) || (e && e.message) || _t("Unknown error");
            this.notification.add(msg, { type: "danger", sticky: true });
        } finally {
            this.state.running = false;
        }
    }

    milestones(res) {
        const u = res.usage;
        if (!u || !u.caps_enabled) {
            return;
        }
        if (res.first_success) {
            this.notification.add(
                _t("First scan done. Fix a problem, then run a second scan of the same area to measure the improvement."),
                { type: "info", title: _t("Lite edition") }
            );
        }
        if (u.exhausted) {
            this.notification.add(
                _t("You have used all %s Lite scans. Everything stays viewable; Pro adds unlimited and scheduled scans.", u.limit),
                { type: "warning", title: _t("Lite edition") }
            );
        } else if (u.pct >= 80) {
            this.notification.add(_t("%s Lite scan(s) left.", u.remaining), { type: "warning", title: _t("Lite edition") });
        }
    }

    async bindScope() {
        if (!this.state.scope || this.state.running) {
            return;
        }
        this.state.running = true;
        try {
            this.state.data = await this.orm.call("elaf.health.scan", "action_bind_lite_scope", [this.state.scope]);
            this.notification.add(_t("Audited module saved. Lite scans now audit this module."), { type: "success" });
        } catch (e) {
            const msg = (e && e.data && e.data.message) || (e && e.message) || _t("Unknown error");
            this.notification.add(msg, { type: "danger", sticky: true });
        } finally {
            this.state.running = false;
        }
    }

    lockedClick(feature) {
        this.notification.add(_t("\"%s\" is part of ERP Health Check Pro.", feature.label), {
            type: "info",
            title: _t("Pro feature"),
        });
        return this.openLiteVsPro();
    }

    openLiteVsPro() {
        return this.action.doAction("elaf_erp_health_lite.action_health_lite_vs_pro");
    }

    openScan(id) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "elaf.health.scan",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openScans(domain = []) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Health Scans"),
            res_model: "elaf.health.scan",
            views: [[false, "list"], [false, "form"]],
            domain: [["company_id", "=", this.data.company.id], ...domain],
            target: "current",
        });
    }

    openFindings(extraDomain = [], name = _t("Findings")) {
        const latest = this.data.latest;
        if (!latest) {
            return;
        }
        return this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: "elaf.health.finding",
            views: [[false, "list"], [false, "form"]],
            domain: [["scan_id", "=", latest.id], ...extraDomain],
            context: { create: false },
            target: "current",
        });
    }

    openByResult(result) {
        const names = { fail: _t("Problems found"), pass: _t("Checks passed"), na: _t("Not applicable") };
        return this.openFindings([["result", "=", result]], names[result]);
    }

    get runTitle() {
        return this.isLite && this.usage && this.usage.exhausted
            ? _t("All Lite scans are used: opens the Lite vs Pro page")
            : _t("Run a health scan of the selected area");
    }

    openFinding(id) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "elaf.health.finding",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    async openRecords(findingId) {
        try {
            const action = await this.orm.call("elaf.health.finding", "action_view_records", [[findingId]]);
            return this.action.doAction(action);
        } catch (e) {
            const msg = (e && e.data && e.data.message) || (e && e.message) || _t("Unknown error");
            this.notification.add(msg, { type: "danger" });
        }
    }
}

registry.category("actions").add("elaf_hc_dashboard", ElafHcDashboard);
