# Entity Intelligence

A polished local browser app for scanning people, companies, domains, phone numbers, emails, URLs, and numeric values using public search data.

## Features
- Single-value search workflow
- Bulk scan mode for many numbers or values
- Category-aware detection
- Aggressive search toggle
- Risk score and owner/use-case hints
- Result cards + domain filters
- JSON/CSV export
- Works without API keys using public search fallback
- Desktop app launcher script for local use

## Run the web app
```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Run the desktop app
```bash
python desktop_app.py
```

## Run bulk scan from a file
```bash
python bulk_scan.py --input values.txt --output results.json
```

## Input file format for bulk scan
Each line can be one value:
```text
+1 555 123 4567
user@example.com
example.com
Apple Inc.
```

## Notes
- API keys are optional.
- The app will still work with public search fallback sources if no key is configured.
- This project is intended for lawful, ethical, and public-source investigation only.
