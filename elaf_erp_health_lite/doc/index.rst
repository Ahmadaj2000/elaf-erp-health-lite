=====================
ERP Health Check Lite
=====================

Scan one business area of your Odoo database for hidden data and process risks, get a 0-100
Health Score, corrective recommendations and one-click access to the affected records.

.. contents:: Contents
   :local:
   :depth: 1

Overview
========

A **scan** runs the checks of one business area (scope) on your real records. The scopes are
Sales, Purchase, Inventory, Accounting and Master Data. Each check that finds a problem creates a
**finding** with:

* its severity,
* the number of affected records,
* a recommendation,
* a **View affected records** button.

Lite contains 10 checks:

========================================  ============  ========  ===========================
Check                                     Scope         Severity  Needs
========================================  ============  ========  ===========================
Stale quotations                          Sales         Medium    Sales
Sales order lines sold below cost         Sales         High      Sales
Purchase orders not received past date    Purchase      High      Purchase and Inventory
Stale requests for quotation              Purchase      Low       Purchase
Negative on-hand stock                    Inventory     Critical  Inventory
Dead stock (no movement)                  Inventory     Medium    Inventory
Customer invoices overdue                 Accounting    Critical  Invoicing
Vendor bills overdue                      Accounting    High      Invoicing
Duplicate partners (name or Tax ID)       Master Data   Medium    (none)
Partners missing e-mail or phone          Master Data   Low       (none)
========================================  ============  ========  ===========================

A check whose application is not installed is reported as **not applicable**. Its explanation
names the missing module, and the check is left out of the score.

Health Score
------------

* The score runs from 0 to 100. It is calculated as
  ``100 x (1 - sum of penalties / sum of maximum penalties)``, over the applicable checks.
* The penalty of a check is ``weight x severity factor x min(1, count / saturation count)``.
* The severity factors are: low 1, medium 2, high 3, critical 4.
* The score falls into one of three bands:

  * **Healthy**: 85 and above.
  * **Attention**: 65 to 84.
  * **At risk**: below 65.
* The *Methodology* tab of each scan shows the formula.
* The dashboard shows the change versus the previous completed scan of the same scope.
* The score and every total (problems, affected records, checks) are computed **for each reader**,
  over the findings that reader may see. Results of checks that need access rights the reader does
  not have (for example accounting) are hidden from that reader and left out of the reader's score;
  the scan form shows how many checks are hidden.

Installation
============

1. Install **ERP Health Check Lite**. It needs only the Base, Discuss and Product modules.
2. Install the applications you want to audit (Sales, Purchase, Inventory, Invoicing).
3. Odoo Online is not supported, because it does not run third-party Python modules.

Compatibility: Odoo 17.0 Community Edition (tested). Odoo 18.0 and 19.0 are distributed as
separate packages.

Configuration
=============

* **Users**: in *Settings > Users*, set the *ERP Health Check* access to **User** or **Manager**.
* **Thresholds**: set them in *ERP Health > Configuration > Settings*. The defaults are:

  * 30 days for stale quotations,
  * 30 days for stale RFQs,
  * 180 days for dead stock,
  * 30 days for overdue customer invoices.

  Vendor bills count as overdue from their due date.
* **Audited module (Lite)**: managers can choose the one business module Lite audits under
  *ERP Health > Configuration > Settings > Choose your audited module*, or with **Set as audited
  module** on the dashboard. Without a choice, the first completed scan sets it. It cannot be
  changed afterwards in Lite.
* **Checks**: managers can review them in *ERP Health > Configuration > Checks*. Managers can
  adjust severity, weight and saturation count, or deactivate a check. The code, scope, model and
  threshold parameter of a check are fixed by the application.

Usage
=====

1. Open *ERP Health > Dashboard*, select a scope and click **Run scan**. You can also create a scan
   under *ERP Health > Scans*. In Lite, once the audited module is set, the dashboard shows it and
   only offers that module.
2. Review the gauge, findings by severity, corrective recommendations and recent scans.
3. Open a finding and click **View affected records**. The records open with your own access rights.
4. Correct the data, then scan the same scope again to see the score change.

Scan results cannot be edited. Scans only read business data.

When a scan is refused
----------------------

Before running, the scan works out which checks of the scope can run for you:

* If no check can run (all need access rights you do not have, or the applications they need are
  not installed), the scan is refused with a message that names the missing access or modules.
* In Lite, if **any** check of the scope needs access rights you do not have, the scan is refused
  and names the missing access. A completed Lite scan is therefore always a complete scan of the
  audited module. Ask an administrator for the access, or ask a user who has it to run the scan.

A refused scan is not run and is not counted. A draft or failed scan can be cancelled with
**Cancel Scan**; a cancelled scan never runs and is not counted.

Lite edition limits
===================

* One company per database: the first company that completes a scan. Other companies get a
  clear message. Viewing existing scans is never blocked.
* One audited business module (Sales, Purchase, Inventory, Accounting or Master Data). It is
  chosen once by a manager in Settings or set by the first completed scan, and it **cannot be
  changed in Lite**. Scans of another module are refused and not counted. Pro removes this
  binding. Databases upgraded from an earlier version that already scanned several modules are
  bound to the module of their first completed scan; their existing scans stay viewable.
* Manual scans only.
* 10 checks.
* Up to 3 completed scans in total.

  * A scan that is refused (see above), cancelled or fails does not count.
  * A double click or a repeated request on **Run scan** returns the first scan; it is counted
    once.
  * Deleting scans does not give them back.
  * After the third scan, all scans and findings remain viewable, and the **Run scan** button
    opens the Lite vs Pro page.

The usage counter is shown on the dashboard and under *ERP Health > Configuration > Lite Usage*.
It is a local record in your database. There is no activation key and no remote check.

The **Discover Pro** button on *ERP Health > Lite vs Pro* opens the Pro listing on the Odoo Apps
store in your browser. It works once the Pro listing is published. No data is sent.

Security and permissions
========================

* **User**: run scans, view scans and findings.
* **Manager**: also settings, checks and the usage log, and can delete scans.
* Findings and usage records are read-only for users.
* Multi-company record rules apply to scans and findings.
* Checks only run on, and findings only show, data that you may read in Odoo itself:

  =========================================  ==================================================
  Checks                                     Access needed (besides ERP Health User)
  =========================================  ==================================================
  Stale quotations, sold below cost          Read access to sales orders (Sales user)
  Purchase checks                            Read access to purchase orders (Purchase user)
  Negative stock                             Read access to stock (all internal users)
  Dead stock                                 Read access to stock moves (Inventory user)
  Overdue customer invoices / vendor bills   **Accounting access**: Invoicing, Accounting
                                             read-only, or any higher accounting group
  Master data checks                         Read access to contacts (all internal users)
  =========================================  ==================================================

  If you run a scan without the access a check needs, the check is not run. It is listed as
  **not applicable** with the reason (for example *Requires accounting access*), it is left out
  of the score, and the scan's chatter names the checks that were not run. The scan still
  completes (and counts as one of the 3 Lite scans), so choose a scope you have access to.
* Findings of a check that found a problem or passed are hidden from users who lack its access,
  even when another user ran the scan: they do not see its records, counts per check, affected
  record ids or estimated exposure. The scan's totals (number of problems and affected records)
  are kept as computed at scan time.
* Scan results can only be written by the scan engine, and "Run by" is always the user who
  ran the scan.
* All data stays in your Odoo database. The module makes no external network calls and sends
  no e-mail.

FAQ
===

**Does a scan change my data?**
  No. It only reads business records and stores its own results.

**Why is a check "not applicable"?**
  The application it needs is not installed, for example Purchase for the RFQ checks, or you do
  not have the access rights the check needs (for example accounting access for the overdue
  invoice checks). The explanation on the result says which.

**Can I compare scores between databases?**
  Not directly. The score depends on the checks that apply to your installed applications.

Known limitations
=================

* Partner checks look at all partners, not only customers and vendors.
* Thresholds are global per database, not per check.
* Not available on Odoo Online.

Support
=======

Community support, best effort: info@elaf-development.com
