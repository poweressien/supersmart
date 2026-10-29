# Entity Intelligence

A browser-based public intelligence app for scanning people, companies, domains, phone numbers, emails, URLs, and numeric values.

## Features
- Search input box
- Category selector
- Aggressive search mode
- Overview dashboard
- Who/what clues
- Where it appears
- Likely usage
- Country/region/owner hints
- Risk score
- Download JSON or CSV

## Supported inputs
- phone number
- domain
- email
- username
- person/company
- URL
- any random numeric value

## Run locally
```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

## Notes
- This app is for public, lawful, and ethical research only.
- API keys are optional. The app will still work using public search fallback sources.
