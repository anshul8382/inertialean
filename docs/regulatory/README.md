# Regulatory reference (SEBI)

This folder is the **authoritative project location for SEBI compliance circulars and related documents** referenced by **`.cursor/rules/sebi-regulatory.mdc`**.

## Current contents

SEBI circulars and related files have been uploaded. See **[INDEX.md](INDEX.md)** for the list of documents in this folder.

**Key documents include:**
- **Master Circular for Investment Advisers..pdf** — primary IA regulatory reference
- **SEBI - IA ADVERTISEMTN CODE OF CODUCT.pdf** — IA advertisement code of conduct
- **BASL - IA_Code_of_Advt.pdf** — BASL IA code of advertisement
- **Annexxure_A.pdf**, **BASL_Checklist_of_Advertisement_Approval.xlsx**, **Advt Code PPT _Final.pptx**
- Additional circulars/amendments as timestamp-named PDFs (listed in INDEX.md)

## Process and reports for the regulator

Process-related instructions derived from these documents are in **`docs/SEBI_PROCESS_INSTRUCTIONS.md`**. When developing features, follow that doc and **`.cursor/rules/sebi-process.mdc`** so that the process stays in sync with guidelines and **reports remain ready to be shared with the regulator**.

## Adding more documents

- Copy SEBI/IA circulars, amendments, or FAQs into this folder.
- Update **INDEX.md** with the new filename and a short description so the regulatory agent can reference it.
- If the new document affects process (e.g. record-keeping, disclosures), update **`docs/SEBI_PROCESS_INSTRUCTIONS.md`** as needed.
