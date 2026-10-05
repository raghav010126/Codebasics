# Ingestion report

Total chunks: **257** from 12 documents.

| Collection | Document | Chunks | text | table | code | heading |
|---|---|---|---|---|---|---|
| general | code_of_conduct.pdf | 16 | 16 | 0 | 0 | 0 |
| general | general_faqs.pdf | 26 | 26 | 0 | 0 | 0 |
| general | leave_policy.pdf | 12 | 11 | 1 | 0 | 0 |
| general | staff_handbook.pdf | 22 | 21 | 0 | 1 | 0 |
| clinical | diagnostic_reference.pdf | 12 | 2 | 10 | 0 | 0 |
| clinical | drug_formulary.pdf | 16 | 4 | 12 | 0 | 0 |
| clinical | treatment_protocols.pdf | 34 | 25 | 9 | 0 | 0 |
| nursing | icu_nursing_procedures.pdf | 28 | 24 | 4 | 0 | 0 |
| nursing | infection_control.pdf | 16 | 13 | 3 | 0 | 0 |
| billing | billing_codes.pdf | 16 | 6 | 10 | 0 | 0 |
| billing | claim_submission_guide.md | 28 | 18 | 6 | 4 | 0 |
| equipment | equipment_manual.pdf | 31 | 21 | 10 | 0 | 0 |

## Sample text chunk (heading path carried into the embedded text)

Embedded text:
```
Code of Conduct & Ethics Policy
1. Professional Conduct
Zero tolerance
MediAssist maintains zero tolerance for verbal or physical abuse directed at patients, visitors or colleagues. Substantiated abuse is treated as gross misconduct.
```
Metadata:
```json
{
  "source_document": "code_of_conduct.pdf",
  "collection": "general",
  "access_roles": [
    "doctor",
    "nurse",
    "billing_executive",
    "technician",
    "admin"
  ],
  "section_title": "Zero tolerance",
  "heading_path": [
    "1. Professional Conduct",
    "Zero tolerance"
  ],
  "chunk_type": "text",
  "page_numbers": [
    2
  ],
  "doc_title": "Code of Conduct & Ethics Policy",
  "doc_ref": "COMP-COC-003",
  "chunk_index": 2
}
```

## Sample table chunk (Markdown table, device name carried in the path)

Embedded text:
```
Equipment Operation & Maintenance Manual
A. Patient Monitoring System - MediAssist BM-500
Alarm parameter defaults and adjustable ranges
| Parameter     | Default Low   | Default High   | Adjustable Range   |
|---------------|---------------|----------------|--------------------|
| SpO₂          | 90%           | 100%           | 80-100%            |
| Heart Rate    | 50 bpm        | 120 bpm        | 30-250 bpm         |
| NIBP Systolic | 90 mmHg       | 160 mmHg       | 60-240 mmHg        |
| Temperature   | 35.5 °C       | 38.5 °C        | 34-42 °C           |
```
Metadata:
```json
{
  "source_document": "equipment_manual.pdf",
  "collection": "equipment",
  "access_roles": [
    "technician",
    "admin"
  ],
  "section_title": "Alarm parameter defaults and adjustable ranges",
  "heading_path": [
    "A. Patient Monitoring System - MediAssist BM-500",
    "Alarm parameter defaults and adjustable ranges"
  ],
  "chunk_type": "table",
  "page_numbers": [
    3
  ],
  "doc_title": "Equipment Operation & Maintenance Manual",
  "doc_ref": "BME-EQManual-012",
  "chunk_index": 4
}
```
