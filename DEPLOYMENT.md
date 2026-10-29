# Supersmart deployment notes

This project is intentionally a frontend-only application. It runs as a single Streamlit app and does not require a backend service.

## Recommended deployment options

### 1) Streamlit Cloud (recommended)

Use Streamlit Cloud for the easiest no-backend deployment.

1. Push the repo to GitHub.
2. Open Streamlit Cloud and select the repository.
3. Set the app path to `app.py`.
4. Add the required secrets in the app settings:
   - `SERPAPI_API_KEY`
   - `GOOGLE_CSE_KEY`
   - `GOOGLE_CSE_CX`
   - `NUMVERIFY_API_KEY`
5. Deploy the app.

The app is designed to degrade gracefully when keys are missing. Search still works through public fallback sources, although quality may be reduced without API access.

### 2) Hugging Face Spaces

This app can also run in a Python Space without a backend.

1. Create a new Space from the repo.
2. Use the `Streamlit` SDK template.
3. Add the same environment variables through the Space settings.
4. Set the main app file to `app.py`.

## Secrets handling

Use either:
- Streamlit Cloud secrets (`.streamlit/secrets.toml` in the deployed environment)
- local `.env` files during development

A sample file is included at `.streamlit/secrets.toml.example` and `.env.example`.

## No-backend persistence

The app uses a browser-side local storage fallback for recent-scan history when filesystem persistence is unavailable (such as cloud hosting). This avoids needing a backend while still preserving recent searches in the browser.

Important note:
- `history.json` is used for local development and is optional.
- In cloud deployments, browser-local storage is preferred because the filesystem is not reliable across sessions.

## Operational notes

- This app reaches out to public web sources directly from the client runtime.
- API keys are optional but improve results quality.
- Keep usage legal, ethical, and limited to public-source data and permitted queries.

## Local run

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```
