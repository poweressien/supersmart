# Entity Intelligence

A polished local browser app for scanning people, companies, domains, phone numbers, emails, URLs, and numeric values using public search data.

## Features
- Single-value search workflow
- Bulk scan mode for many numbers or values
- Category-aware detection
- Aggressive search toggle
- Smarter result filtering
- Stronger source ranking
- Person/company extraction hints
- Recent scan history
- JSON / CSV / Excel export
- Works without API keys using public search fallback
- Desktop app launcher + packaging script

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

## Package the desktop app
```bash
python build_app.py
```

This creates a packaged binary in the `dist/` folder for the current operating system.

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
