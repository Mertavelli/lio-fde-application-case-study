# Local browser test

Start or refresh the full stack from this directory:

```bash
docker compose up -d --build
docker compose ps
```

Open `http://localhost:3200` and sign in with the local demo accounts (`demo1234`): `requester@acme.com`, `buyer@acme.com`, `admin@acme.com`. API documentation is at `http://localhost:7200/docs`. Keep this stack on localhost; the accounts are demo credentials.

## Focused final check (about two minutes)

To verify that edits invalidate PDF review, refresh the app and upload a quote with at least two lines. Confirm the PDF review checkbox, then remove a line: the checkbox must clear and submission must require confirmation again. Recheck it and change the quoted total: it must clear again. No request needs to be submitted for this check.

## Quote to request

1. As `requester@acme.com`, open **New Request**. Upload `fixtures/quotes/QT-4120-Cust-14918.pdf`.
2. Compare the supplier, tax ID, two selected line items, and gross total **$1,686.14** with the PDF. Confirm the alternative vertical logo is not in the request. Read any warnings; edit an extracted field and confirm the form accepts the correction.
3. Review catalog options under a line. Use **Find comparable catalog items** for a deliberately different product wording, then inspect supplier, unit, quantity and price. A comparable item must not silently change the selected product or vendor. Only a same-supplier, exact-description, single-unit result offers **Use negotiated unit price**; after clicking, check that the PDF review and gross-total confirmation are required again.
4. Submit the corrected request. Open its detail page and confirm the PDF attachment and **Auto approved** status. If attachment upload failed, use **Attach PDF** there. Record the request ID so you can delete the test request as an admin later.
5. Upload a second offer after the first one: if the second extraction has no category, the previous category must clear. Change a price or quantity after confirming the PDF; the confirmation must reset. Double-click **Create Request** and verify that only one request is created.

## Approval boundary and roles

1. Create two manual Acme requests with one line described as **Cable ties**, booking group **Electrical Engineering**, quantity **1**, and matching unit, line, and request totals of **$1,999.99** and **$2,000.00**. The first must be **Auto approved**; the second must be **Pending**. A vague line such as **test** requires article review and stays Pending at either amount.
2. Edit the auto-approved request to **$2,000.00** as its creator. It must become **Pending**, and its activity history must show that buyer approval is required. Sign in as `buyer@acme.com`, approve it, and confirm it becomes **Approved**. A manually approved request must resist subsequent edits. A closed automatically approved request must also hide Edit; explicitly reopen it before changing financial fields.
3. Sign in as `admin@acme.com`, open **Organization → Settings**, inspect the $2,000 threshold and Acme category rules. Temporarily add and remove a unique test rule to verify the UI. Restore the original settings.
4. Check that an Acme requester cannot edit organization settings. If you use a Walmart demo account, its settings and catalog must be separate from Acme's.
5. With the OpenAI key temporarily absent or the model unavailable, create a request with an unfamiliar item description. It must stay pending with an article-review notice. A buyer/admin selects each article kind, enters a reason, and can then approve; a requester cannot resolve the review. Restore the key afterward.

## Other PDFs

Repeat the upload/review step for all remaining files in `fixtures/quotes/`. Expected gross totals are Verdeform **$1,413.10**, Hollis **$2,042.15**, Apple **$1,534.59**, and Hartwell **$18,402.50**. Verdeform's legal seller and EIN are OCR evidence and should be checked visually. `fixtures/scanned-quote.pdf` exercises image-only OCR; verify its text carefully.

This checklist provides a reproducible browser walkthrough. Completed verification and known limitations are summarized in [HANDOFF.md](HANDOFF.md).
