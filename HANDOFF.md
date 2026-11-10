# Lio FDE Case Study — Customer Improvements

## Overview

Implemented the three requested customer improvements and the optional approval rule:

- **Quote extraction:** Extract structured line items from PDFs, including scanned pages within mixed text/image documents, with validation against printed totals and mandatory user review.
- **Catalog suggestions:** Suggest tenant-specific negotiated products, with strict checks before applying catalog prices.
- **Booking rules:** Classify articles and apply customer-specific accounting rules, with human review for uncertain cases.
- **Auto-approval:** Automatically approve eligible Acme requests below $2,000, while preserving manual review and auditability.

See [README.md](https://github.com/Mertavelli/lio-fde-application-case-study/blob/practice/customer-improvements/README.md) for setup, demo accounts, and test commands, and [MANUAL_TEST.md](https://github.com/Mertavelli/lio-fde-application-case-study/blob/practice/customer-improvements/MANUAL_TEST.md) for a reproducible browser walkthrough.

## Key Engineering Decisions

**1. Separate AI interpretation from business decisions**

AI identifies article types and extracts quote data. Deterministic server-side rules control accounting groups, catalog prices, and approval eligibility.

Explicit article wording with recognized product attributes is handled deterministically; unfamiliar qualifiers and unresolved descriptions go to a small model. Mentioning toner or cable ties within a service or tool description is insufficient for a deterministic product classification. Uncertain classifications and model failures require buyer/admin review rather than silently proceeding.

**2. Prioritize correctness in financial workflows**

- Extracted quote data is validated against printed totals and source evidence.
- Pages without selectable text are OCRed individually in document order, with at most ten OCR pages. If required OCR fails, parsing fails rather than returning a partial quote.
- Users review the original PDF before submitting.
- Exact normalized catalog descriptions are matched before lexical filtering, so short names and dimensions are not lost. Unit, quantity and tenant checks still apply.
- Catalog prices can only be applied to same-supplier, exact-description, matching-unit USD articles with quantity one. The server revalidates eligibility and the current catalog price.
- Approval eligibility is recalculated after edits, and manually decided requests remain locked. Closed requests cannot be edited; an explicit, logged reopening is required first.
- Automatic approval checks request and line amounts conservatively; discounted offers may still require buyer review.
- Tenant isolation, role checks, and audit logging are enforced server-side.
- Migration preserves customized customer rules.

**3. Keep the solution proportionate to the problem**

The existing FastAPI, Next.js, and PostgreSQL stack supports the required functionality without additional infrastructure.

For the seeded 1,920-article catalog, in-memory matching and a bounded semantic shortlist serve the case-study scope. Larger deployments would benefit from indexed retrieval once performance requirements justify it.

## Acme-Specific Behavior

| Requirement | Implementation |
|---|---|
| Cable ties | Electrical Engineering |
| Printer toner | IT Hardware |
| Small software subscriptions | Office Supplies |
| Requests below $2,000 | Eligible for automatic approval after classification and amount checks |
| Requests at or above $2,000 | Buyer/admin approval required |
| Uncertain article classification | Buyer/admin review |

**Open assumption:** The brief does not define “small” software subscriptions. The demo uses a configurable threshold of at most $500 for the total request, not a monthly or annual subscription value. This needs customer confirmation before production use.

Mixed accounting groups require separate requests under the current request-level categorization model.

## Verification

- **57 backend tests passed** in the final local run.
- Next.js production build, lint, and type checking passed locally and in Docker.
- PostgreSQL migrations `0001`–`0007` were verified against a fresh database.
- All five supplied quotes were processed through the running API, with seller, gross total, and selected line counts matching the source documents in the observed runs.
- Article-kind classification passed 46/46 curated synthetic cases in a live run after the review fixes, including six related-tool and service counterexamples. This is a small curated evaluation, not a general accuracy estimate.
- Manual browser verification covered approval boundaries, catalog interactions, and resetting PDF confirmation after material edits.
- Live API checks confirmed rejection of an incorrect accounting group and automatic approval of an eligible request.
- Regression tests cover closed-request edits, short exact catalog matches, mixed PDF page order and OCR failures/limits, and product-versus-service classification.
- The rebuilt Docker/PostgreSQL stack also passed API checks for rejecting edits to closed requests, explicit reopening and approval recalculation, both reported exact catalog matches, and mixed-document OCR. The temporary request was deleted.

These are observed local results, not guarantees of production accuracy. Three upstream Starlette deprecation warnings remain.

## Known Limitations & Next Steps

- **Quote extraction:** Additional real-world document layouts and scanned quotes require evaluation. Pages mixing selectable text and image-only content within the same page can still require manual correction; full-page OCR is triggered for pages without selectable text.
- **Catalog matching:** Comparable products are not necessarily interchangeable; semantic suggestions require user verification. An observed suggestion matched a living plant wall to preserved moss art, demonstrating this limitation.
- **Pricing:** Currency conversion, pack conversion, and broader discount/tax semantics are outside the current scope.
- **Classification:** Unknown article types require manual review.
- **Scale:** Retrieval performance and concurrent workloads have not been load-tested.
- **Production readiness:** Demo credentials must be replaced and deployment security reviewed.

## Review Guide

The `main` branch contains the supplied starter application. Compare `practice/customer-improvements` against `main` to review the implementation.

For a reproducible demonstration, follow [MANUAL_TEST.md](https://github.com/Mertavelli/lio-fde-application-case-study/blob/practice/customer-improvements/MANUAL_TEST.md).
