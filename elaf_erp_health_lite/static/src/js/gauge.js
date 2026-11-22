/** @odoo-module **/
import { Component } from "@odoo/owl";

/** Semicircular SVG score gauge (0-100). Pure SVG, no chart library. */
export class ElafHcGauge extends Component {
    static template = "elaf_erp_health_lite.Gauge";
    static props = {
        score: { type: Number, optional: true },
        band: { type: String, optional: true },
        label: { type: String, optional: true },
        available: { type: Boolean, optional: true },
    };
    get offset() {
        const s = Math.max(0, Math.min(100, this.props.score || 0));
        return 100 - s;
    }
    get scoreText() {
        return this.props.available === false ? "–" : (Math.round((this.props.score || 0) * 10) / 10).toString();
    }
}
