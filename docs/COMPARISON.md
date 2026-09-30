# Alternatives and comparison

How this project relates to existing ways of catching duplicate payments, overpayments and edited receipts.
**Documented facts** come from the cited vendor pages and knowledge-base articles (retrieved October 2026).
**Interpretation** is marked as such. Pricing is omitted: none of these vendors publish it on the pages reviewed.

---

## 1. How the problem is handled today

| Approach | Examples | When it acts |
|---|---|---|
| ERP built-in duplicate check | SAP duplicate invoice check | At invoice entry |
| AP automation platforms with duplicate prevention | Medius | During invoice processing |
| AI spend / expense audit | AppZen, Oversight | Before reimbursement or payment |
| Document-fraud detection | Inscribe, Resistant AI | When a document is submitted (mostly lending/onboarding) |
| Recovery audit | PRGX | **After** payment, to recover money already paid |
| Manual review / spreadsheets | — | Sample-based, periodic |

---

## 2. Comparison matrix

| | This project | SAP duplicate check | AppZen | Oversight | Medius | Inscribe / Resistant AI | PRGX |
|---|---|---|---|---|---|---|---|
| **Category** | Open-source engine + app | ERP control | AI expense/spend audit | AI payables/spend audit | AP automation | Document fraud detection | Recovery audit services |
| **Duplicate detection** | Exact + fuzzy (reformatted numbers, name variants, date shifts) | Exact match on key fields [1] | Yes, including different receipt forms of one transaction [2] | Duplicate payment audit [5] | ML-based duplicate prevention [6] | Not the focus | Identifies duplicates after payment [4] |
| **Overpayment vs vendor norm** | Rule + anomaly model per vendor | No | Not verified | Not verified | Not verified | No | Overpayments identified in audit [4] |
| **Edited-image detection** | Weak classical model (test ROC-AUC 0.735) | No | Not verified | Not verified | Not verified | Core capability: pixel, metadata, structure analysis [3] | No |
| **Inputs** | Photo, scan, PDF, manual, Excel/CSV | ERP entries | Expense reports, receipts, cards, invoices [2] | Payables/spend data | Invoices | Documents | AP transaction data [4] |
| **Deployment** | Local Python app; data stays on the machine | Inside SAP | SaaS | SaaS | SaaS | SaaS | Service |
| **Explanations** | Plain-language reasons + matching records | Warning/error message | Not verified | Exception workflow [5] | Not verified | Not verified | Audit findings |
| **Scale / maturity** | Prototype; single company; no auth | Production | Production | Production | Production | Production | Established service, 30 countries [4] |

"Not verified" = no reliable public source found in this review; it does **not** mean the product lacks the capability.

---

## 3. Documented facts

1. **SAP duplicate invoice check** compares key fields of an incoming invoice with posted/parked documents: company
   code, vendor, reference (invoice) number, invoice date and currency; amount can be added as a criterion
   ([invoicedataextraction.com](https://invoicedataextraction.com/blog/sap-duplicate-invoice-check),
   [SAP Community](https://answers.sap.com/t5/enterprise-resource-planning-blogs-by-sap/found-a-duplicate-mm-invoice-here-s-how-to-troubleshoot-the-reason/ba-p/13563986)).
2. **AppZen** cross-references expense reports against historical data to detect duplicate receipts before
   reimbursement, including duplicates across expense reports, corporate cards and invoices, and cases where one
   transaction appears as different receipt forms; it lists 40+ T&E audit models
   ([AppZen: duplicate receipts](https://www.appzen.com/blog/duplicate-receipts),
   [AppZen: expense audit](https://appzen.com/expense-audit)).
3. **Inscribe and Resistant AI** are document-fraud platforms; typical layers include pixel-level analysis
   (alterations, splicing, compression artifacts, fonts), metadata, structure and cross-document checks; Resistant AI
   states it analyses documents "over 500 ways" ([Inscribe vs Resistant AI](https://www.inscribe.ai/fraud-detection/inscribe-vs-resistant-ai),
   [Resistant AI](https://info.resistant.ai/ppc-lp-fraud-detection-solutions)).
4. **PRGX** runs recovery audits that review AP transaction data to find overpayments and duplicate payments and
   recover them from suppliers, across 30 countries ([PRGX AP Profit Recoveries](https://www.prgx.com/wp-content/uploads/2023/04/PRGX_AP_Profit_Recoveries.pdf)).
5. **Oversight** automates duplicate-payment audit in payables with exception workflows
   ([Oversight case study](https://www.oversight.com/case-study/f200-utility-company)).
6. **Medius** positions ML-based identification and prevention of duplicate invoices inside AP automation
   ([Medius](https://medius.com/blog/how-to-prevent-duplicate-payments-in-accounts-payable)).

---

## 4. Interpretation: where this project differs

- **Tolerant matching vs exact keys.** An exact-key check (fact 1) cannot, by construction, match `SAP-2024-123456`
  with `SAP2024123456`, or "Acme Corp" with "ACME CORPORATION LLC" on a different date. That gap is what this project's
  fuzzy matcher targets. Commercial AI audit tools (facts 2, 5, 6) also go beyond exact keys.
- **Pre-payment and local.** It checks before payment (unlike recovery audit, fact 4) and runs entirely on the
  company's machine with no external calls, which matters to teams that cannot send invoices to a SaaS vendor.
- **One tool for records and images**, with a stated, measured weakness on the image side. Dedicated document-fraud
  platforms (fact 3) are far more thorough on images; this project should not be positioned against them on forgery.
- **Transparent and free to inspect.** Rules, thresholds and model evaluations are in the repository.
- **Not comparable on maturity:** no login, no multi-company support, no ERP connectors, no SLA. It is a prototype /
  pilot tool, not a replacement for an enterprise platform.
