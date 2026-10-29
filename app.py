import json
import os
import re
from io import BytesIO
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import pandas as pd
import phonenumbers
import requests
import streamlit as st
import whois
import wikipediaapi
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
from dotenv import load_dotenv

load_dotenv()

APP_HISTORY_PATH = os.path.join(os.path.dirname(__file__), "history.json")
APP_STORAGE_KEY = "entity_intelligence_history_v1"


def get_secret(name: str, default: str = "") -> str:
    try:
        if hasattr(st, "secrets"):
            secrets = st.secrets
            if isinstance(secrets, dict) and name in secrets:
                value = secrets.get(name)
                if value not in (None, ""):
                    return str(value).strip()
    except Exception:
        pass
    value = os.getenv(name)
    if value not in (None, ""):
        return str(value).strip()
    return default

SERPAPI_KEY = get_secret("SERPAPI_API_KEY")
GOOGLE_CSE_KEY = get_secret("GOOGLE_CSE_KEY")
GOOGLE_CSE_CX = get_secret("GOOGLE_CSE_CX")
NUMVERIFY_KEY = get_secret("NUMVERIFY_API_KEY")

st.set_page_config(page_title="Entity Intelligence", page_icon="🔎", layout="wide")

SOURCE_WEIGHTS = {
    "linkedin": 1.55,
    "social": 1.25,
    "institution": 1.4,
    "search": 1.12,
    "general": 0.9,
}

STOP_WORDS = {
    "the", "this", "that", "search", "results", "page", "website", "about",
    "contact", "support", "home", "login", "sign", "up", "news", "business",
    "company", "site", "official", "welcome", "help", "services", "products",
    "pricing", "profile", "directory", "find", "people", "team", "solutions",
    "platform", "media", "blog", "newsroom", "research", "portal"
}

GENERIC_TITLE_TOKENS = {
    "home", "homepage", "index", "login", "sign in", "contact us", "contact",
    "support", "help", "faq", "about us", "about", "services", "products",
    "pricing", "news", "blog", "search", "results", "directory"
}


def normalize_whitespace(value: str) -> str:
    return (value or "").strip()


def is_person_like_name(value: str) -> bool:
    cleaned = normalize_whitespace(value)
    if not cleaned or cleaned.isdigit():
        return False
    if re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", cleaned):
        return False
    if "http" in cleaned.lower() or "." in cleaned:
        return False
    words = cleaned.split()
    if len(words) == 1 and len(words[0]) <= 2:
        return False
    if len(words) <= 4 and any(ch.isalpha() for ch in cleaned):
        return True
    return False


def looks_like_company(value: str) -> bool:
    text = normalize_whitespace(value).lower()
    if not text:
        return False
    company_markers = [
        "inc", "ltd", "llc", "corp", "group", "company", "co.", "co ", "holdings",
        "partners", "systems", "solutions", "technologies", "labs", "studio", "agency",
        "network", "consulting", "capital", "foundation", "association", "university"
    ]
    if any(marker in text for marker in company_markers):
        return True
    if re.search(r"\b(?:[A-Z][a-z]+\s+){1,3}(?:Inc|LLC|Ltd|Corp|Group|Co|Holding)\b", value):
        return True
    return False


def classify_query_label(query: str, kind: str) -> str:
    value = normalize_whitespace(query)
    if kind in ["email", "phone", "domain", "url", "number", "username"]:
        return kind
    if is_person_like_name(value):
        return "person"
    if looks_like_company(value):
        return "company"
    if value.count(" ") >= 2:
        return "company"
    return "entity"


def detect_type(raw: str) -> str:
    value = normalize_whitespace(raw)
    if not value:
        return "unknown"
    if re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value):
        return "email"
    if re.search(r"(?:\+?\d[\d\s().-]{7,}\d)", value):
        return "phone"
    if re.match(r"^(https?://|www\.)", value, re.I):
        return "url"
    if re.match(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}$", value, re.I):
        return "domain"
    if value.startswith("@") or re.match(r"^[A-Za-z0-9_.-]{2,}$", value):
        return "username"
    if value.isdigit() and len(value) >= 4:
        return "number"
    if is_person_like_name(value):
        return "person_or_company"
    if looks_like_company(value):
        return "person_or_company"
    if value.count(" ") >= 1:
        return "person_or_company"
    return "keyword"


def clean_email(raw: str) -> str:
    value = normalize_whitespace(raw).lower()
    if not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value):
        raise ValueError("Invalid email format.")
    return value


def clean_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) < 7:
        raise ValueError("Phone number too short.")
    return digits


def clean_domain(raw: str) -> str:
    value = normalize_whitespace(raw).lower().replace("https://", "").replace("http://", "").replace("www.", "")
    value = value.split("/")[0].split("?")[0]
    return value


def clean_url(raw: str) -> str:
    value = normalize_whitespace(raw)
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:60] or "result"


def load_history() -> List[Dict[str, Any]]:
    history: List[Dict[str, Any]] = []
    try:
        if "history_cache" in st.session_state and isinstance(st.session_state["history_cache"], list):
            history = st.session_state["history_cache"]
            if history:
                return history
    except Exception:
        pass

    try:
        if os.path.exists(APP_HISTORY_PATH):
            with open(APP_HISTORY_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
                if isinstance(data, list):
                    history = data
                    st.session_state["history_cache"] = history
                    return history
    except Exception:
        pass

    try:
        stored = st.session_state.get("browser_history")
        if isinstance(stored, list):
            history = stored
            st.session_state["history_cache"] = history
            return history
    except Exception:
        pass

    return []


def save_history(history: List[Dict[str, Any]]) -> None:
    normalized = history[:20]
    st.session_state["history_cache"] = normalized
    try:
        with open(APP_HISTORY_PATH, "w", encoding="utf-8") as handle:
            json.dump(normalized, handle, indent=2, ensure_ascii=False)
    except Exception:
        pass

    try:
        st.session_state["browser_history"] = normalized
    except Exception:
        pass


def add_history_item(value: str, category: str) -> None:
    history = load_history()
    new_item = {"value": value, "category": category}
    history = [item for item in history if str(item.get("value", "")).lower() != str(value).lower()]
    history.insert(0, new_item)
    save_history(history)


def extract_domain_from_link(link: str) -> str:
    try:
        host = urlparse(link).netloc.lower()
        return host.replace("www.", "")
    except Exception:
        return ""


def get_source_label(domain: str) -> str:
    d = (domain or "").lower()
    if "linkedin" in d:
        return "linkedin"
    if "facebook" in d or "instagram" in d:
        return "social"
    if "twitter" in d or "x.com" in d:
        return "social"
    if "google" in d:
        return "search"
    if any(token in d for token in ["gov", "edu", "org"]):
        return "institution"
    return "general"


def get_source_weight(domain: str) -> float:
    return SOURCE_WEIGHTS.get(get_source_label(domain), 0.9)


def strip_generic_title(title: str) -> str:
    value = normalize_whitespace(title)
    lowered = value.lower()
    for token in GENERIC_TITLE_TOKENS:
        if lowered == token or lowered.endswith(token):
            return ""
    return value


def compact_title(title: str) -> str:
    value = strip_generic_title(title)
    if not value:
        return ""
    value = re.sub(r"[-–|:—].*$", "", value).strip()
    value = re.sub(r"\s+", " ", value)
    return value


def extract_entity_names(text: str) -> List[str]:
    cleaned = re.sub(r"https?://\S+", " ", text)
    cleaned = re.sub(r"\b(?:www\.|com|net|org|io|co|gov|edu)\b", " ", cleaned, flags=re.I)
    pattern = r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}|[A-Z]{2,})\b"
    titles = re.findall(pattern, cleaned)
    names: List[str] = []
    seen = set()
    for item in titles:
        normalized = item.strip()
        if len(normalized) < 2:
            continue
        lowered = normalized.lower()
        if lowered in STOP_WORDS:
            continue
        if normalized not in seen:
            names.append(normalized)
            seen.add(normalized)
    return names[:8]


def query_tokens(query: str) -> List[str]:
    value = normalize_whitespace(query).lower()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return [token for token in value.split() if len(token) > 1]


def safe_domain_root(domain: str) -> str:
    clean = clean_domain(domain)
    if not clean:
        return ""
    root = clean.split(".")[0]
    return root.strip("-")


def score_title_match(title: str, query: str) -> int:
    text = normalize_whitespace(title).lower()
    q = normalize_whitespace(query).lower()
    if not text or not q:
        return 0
    score = 0
    if q in text:
        score += 30
    tokens = query_tokens(q)
    matched = sum(1 for token in tokens if token in text)
    score += matched * 10
    return score


def explain_result(item: Dict[str, Any], query: str) -> List[str]:
    reasons: List[str] = []
    title = (item.get("title") or "").lower()
    snippet = (item.get("snippet") or "").lower()
    domain = (item.get("domain") or "").lower()
    source = (item.get("source") or "general").lower()
    q = normalize_whitespace(query).lower()

    if q and q in (title + " " + snippet):
        reasons.append("exact match")
    if q and q in title:
        reasons.append("title overlap")
    if q and q in domain:
        reasons.append("domain match")
    if any(token in domain for token in ["linkedin", "edu", "gov", "org"]):
        reasons.append("high-trust domain")
    if source in {"linkedin", "institution"}:
        reasons.append("strong source trust")
    if any(token in title for token in ["contact", "support", "sales", "customer", "help", "pricing", "about", "team"]):
        reasons.append("contact signal")
    if item.get("score", 0) >= 80:
        reasons.append("strong relevance")
    if item.get("score", 0) < 25:
        reasons.append("weak match")
    if not reasons:
        reasons.append("public reference")
    return reasons[:5]


def rank_result(item: Dict[str, str], query: str) -> Dict[str, Any]:
    title = (item.get("title") or "").strip()
    snippet = (item.get("snippet") or "").strip()
    link = item.get("link") or ""
    domain = extract_domain_from_link(link)
    text = f"{title} {snippet}".lower()
    q = normalize_whitespace(query).lower()
    base_score = 0
    source_weight = get_source_weight(domain)

    if q and q in text:
        base_score += 30
    if q and q in title.lower():
        base_score += 22
    if q and q in domain.lower():
        base_score += 18

    tokens = query_tokens(q)
    if tokens:
        title_hits = sum(1 for token in tokens if token in title.lower())
        snippet_hits = sum(1 for token in tokens if token in snippet.lower())
        domain_hits = sum(1 for token in tokens if token in domain.lower())
        base_score += title_hits * 10 + snippet_hits * 7 + domain_hits * 5

    if domain:
        base_score += 10
    if "linkedin.com" in domain:
        base_score += 18
    if "facebook.com" in domain:
        base_score += 12
    if "google" in domain:
        base_score += 4
    if any(token in domain for token in ["gov", "edu", "org"]):
        base_score += 8

    if any(k in text for k in ["support", "contact", "customer", "sales", "pricing", "business", "company", "account", "invoice"]):
        base_score += 8
    if any(k in title.lower() for k in ["contact", "support", "sales", "help", "customer", "pricing", "official", "team"]):
        base_score += 10
    if title:
        base_score += 6
    if snippet:
        base_score += 4

    clean_title = compact_title(title)
    if not clean_title and ("home" in title.lower() or "login" in title.lower() or "support" in title.lower()):
        base_score -= 12

    if q and any(phrase in text for phrase in ["login", "sign in", "register", "download pdf"]):
        base_score -= 10

    # Public-source extraction signals
    if "email" in title.lower() or "email" in snippet.lower():
        base_score += 12
    if "contact" in title.lower() and "page" in title.lower():
        base_score += 7
    if "linkedin" in domain:
        base_score += 11

    score = int(base_score * source_weight)
    item_record = {
        "title": title,
        "link": link,
        "snippet": snippet,
        "domain": domain,
        "score": max(0, score),
        "source": get_source_label(domain),
        "entity_names": extract_entity_names(f"{title} {snippet}"),
        "owner_hint": safe_domain_root(domain),
    }
    item_record["reasons"] = explain_result(item_record, query)
    return item_record


def dedupe_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique: List[Dict[str, Any]] = []
    seen = set()
    for item in results:
        title = compact_title(item.get("title", ""))
        link = item.get("link", "")
        domain = extract_domain_from_link(link)
        key = (title.lower(), link.lower(), domain.lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def prepare_ranked_results(results: List[Dict[str, str]], query: str) -> List[Dict[str, Any]]:
    ranked = [rank_result(item, query) for item in dedupe_results(results)]
    ranked = [item for item in ranked if item.get("title") or item.get("link") or item.get("snippet")]
    ranked.sort(key=lambda x: x.get("score", 0), reverse=True)
    return ranked


def filter_ranked_results(items: List[Dict[str, Any]], min_score: int = 0, domain_filter: str = "all", source_filter: str = "all") -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    for item in items:
        if item.get("score", 0) < min_score:
            continue
        if domain_filter and domain_filter != "all" and item.get("domain", "").lower() != domain_filter.lower():
            continue
        if source_filter and source_filter != "all" and item.get("source", "").lower() != source_filter.lower():
            continue
        filtered.append(item)
    return filtered


def http_get_json(url: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    try:
        response = requests.get(url, params=params, headers=headers, timeout=20)
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


def http_get_text(url: str, headers: Optional[Dict[str, str]] = None) -> Optional[str]:
    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        return response.text
    except Exception:
        return None


def serpapi_search(query: str, limit: int = 8) -> List[Dict[str, str]]:
    if not SERPAPI_KEY:
        return []
    params = {"engine": "google", "q": query, "api_key": SERPAPI_KEY, "num": limit}
    data = http_get_json("https://serpapi.com/search", params=params)
    if not data:
        return []
    results: List[Dict[str, str]] = []
    for item in data.get("organic_results", [])[:limit]:
        title = item.get("title") or "Untitled"
        link = item.get("link") or ""
        snippet = item.get("snippet") or ""
        if title or link or snippet:
            results.append({"title": title, "link": link, "snippet": snippet})
    return dedupe_results(results)


def google_cse_search(query: str, limit: int = 8) -> List[Dict[str, str]]:
    if not (GOOGLE_CSE_KEY and GOOGLE_CSE_CX):
        return []
    params = {"key": GOOGLE_CSE_KEY, "cx": GOOGLE_CSE_CX, "q": query, "num": limit}
    data = http_get_json("https://www.googleapis.com/customsearch/v1", params=params)
    if not data:
        return []
    results: List[Dict[str, str]] = []
    for item in data.get("items", [])[:limit]:
        title = item.get("title") or "Untitled"
        link = item.get("link") or ""
        snippet = item.get("snippet") or ""
        if title or link or snippet:
            results.append({"title": title, "link": link, "snippet": snippet})
    return dedupe_results(results)


def duckduckgo_search(query: str, limit: int = 8) -> List[Dict[str, str]]:
    try:
        with DDGS() as ddgs:
            items = list(ddgs.text(query, max_results=limit))
            results: List[Dict[str, str]] = []
            for item in items:
                title = item.get("title") or "Untitled"
                link = item.get("href") or ""
                snippet = item.get("body") or ""
                if title or link or snippet:
                    results.append({"title": title, "link": link, "snippet": snippet})
            return dedupe_results(results)
    except Exception:
        return []


def build_search_queries(query: str, kind: str) -> List[str]:
    q = normalize_whitespace(query)
    cleaned = re.sub(r"\s+", " ", q)
    base_queries = [f'"{cleaned}"', f"{cleaned}", f'"{cleaned}" "contact"', f'"{cleaned}" "support"', f'"{cleaned}" "company"']
    if kind == "phone":
        digits = re.sub(r"\D", "", q)
        base_queries = [f'"{digits}"', f"{digits}", f'"{digits}" "phone"', f'"{digits}" "business"', f'"{digits}" "support"', f'"{digits}" "sales"']
    elif kind == "number":
        digits = re.sub(r"\D", "", q)
        base_queries = [f'"{digits}"', f"{digits}", f'"{digits}" "reference"', f'"{digits}" "invoice"', f'"{digits}" "account"', f'"{digits}" "customer"']
    elif kind == "domain":
        domain = clean_domain(q)
        base_queries = [f'"{domain}"', f"{domain}", f'"{domain}" "company"', f'"{domain}" "contact"', f'"{domain}" "site:linkedin.com"', f'"{domain}" "about"']
    elif kind == "email":
        email = clean_email(q)
        local, domain_part = email.split("@", 1)
        base_queries = [f'"{email}"', f"{email}", f'"{local}" "{domain_part}"', f'"{email}" "contact"', f'"{email}" "support"', f'"{local}" "company"']
    elif kind == "url":
        url = clean_url(q)
        base_queries = [f'"{url}"', f"{url}", f'"{url}" "contact"', f'"{url}" "about"', f'"{url}" "company"', f'"{url}" "team"']
    elif kind in ["person_or_company", "keyword"]:
        base_queries = [f'"{cleaned}"', f"{cleaned}", f'"{cleaned}" "company"', f'"{cleaned}" "site:linkedin.com"', f'"{cleaned}" "contact"', f'"{cleaned}" "team"']
    seen = set(); out: List[str] = []
    for item in base_queries:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out[:8]


def search_all(query: str, kind: str = "all", limit: int = 10, aggressive: bool = False) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    seen = set()
    queries = build_search_queries(query, kind)
    sources = [serpapi_search, google_cse_search, duckduckgo_search]
    for query_text in queries:
        for source in sources:
            source_limit = max(limit, 6) if aggressive else limit
            for item in source(query_text, limit=source_limit):
                key = (item.get("title"), item.get("link"))
                if key not in seen and item.get("link"):
                    seen.add(key)
                    results.append(item)
    return dedupe_results(results)[: max(limit * 3, 25) if aggressive else limit * 3]


def guess_owner(results: List[Dict[str, str]]) -> List[str]:
    owners = []
    seen = set()
    for item in results:
        title = compact_title(item.get("title", ""))
        if not title:
            continue
        cleaned = re.sub(r"[-–|:—].*$", "", title).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if cleaned and len(cleaned) > 2 and cleaned not in seen:
            owners.append(cleaned)
            seen.add(cleaned)
    if not owners:
        for item in results:
            domain = extract_domain_from_link(item.get("link", ""))
            root = safe_domain_root(domain)
            if root and root not in seen:
                owners.append(root)
                seen.add(root)
    return owners[:8]


def guess_usage(results: List[Dict[str, str]]) -> List[str]:
    all_text = " ".join((item.get("title", "") + " " + item.get("snippet", "")).lower() for item in results)
    buckets = {
        "Business": ["business", "company", "office", "firm", "enterprise", "service"],
        "Sales": ["sales", "quote", "price", "pricing", "demo", "lead"],
        "Support": ["support", "helpdesk", "customer support", "tech support"],
        "Bookings": ["booking", "appointment", "reservation", "schedule"],
        "Recruiting": ["hiring", "jobs", "careers", "recruitment"],
        "Marketing": ["marketing", "ad campaign", "advertising", "promotion"],
        "Real Estate": ["real estate", "property", "listing", "agent"],
        "Delivery": ["delivery", "courier", "shipment", "dispatch"],
        "Security": ["security", "alarm", "guard", "surveillance"],
        "Medical": ["clinic", "doctor", "hospital", "medical", "pharmacy"],
        "Finance": ["bank", "finance", "loan", "payment", "billing"],
        "Personal": ["personal", "private", "family", "home"],
        "Messaging": ["whatsapp", "sms", "text message", "telegram", "signal"],
        "Call Center": ["call center", "customer care", "hotline"],
        "Invoice": ["invoice", "receipt", "payment", "order", "bill"],
        "Account": ["account", "client id", "customer id", "reference id", "tracking"],
    }
    matches = []
    for label, keywords in buckets.items():
        if any(k in all_text for k in keywords):
            matches.append(label)
    return matches[:6] if matches else ["Unknown"]


def parse_phone_details(raw_phone: str) -> Dict[str, Any]:
    digits = clean_phone(raw_phone)
    parsed = phonenumbers.parse(digits, None)
    region = phonenumbers.region_code_for_number(parsed)
    country_name = phonenumbers.geocoder.description_for_number(parsed, "en") or "Unknown"
    type_map = {
        phonenumbers.PhoneNumberType.FIXED_LINE: "Fixed line",
        phonenumbers.PhoneNumberType.MOBILE: "Mobile",
        phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "Fixed line or mobile",
        phonenumbers.PhoneNumberType.TOLL_FREE: "Toll free",
        phonenumbers.PhoneNumberType.PREMIUM_RATE: "Premium rate",
        phonenumbers.PhoneNumberType.SHARED_COST: "Shared cost",
        phonenumbers.PhoneNumberType.VOIP: "VoIP",
        phonenumbers.PhoneNumberType.PERSONAL_NUMBER: "Personal number",
        phonenumbers.PhoneNumberType.UAN: "UAN",
        phonenumbers.PhoneNumberType.VOICEMAIL: "Voicemail",
        phonenumbers.PhoneNumberType.UNKNOWN: "Unknown",
    }
    number_type = type_map.get(phonenumbers.number_type(parsed), "Unknown")
    e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    national = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL)
    return {
        "raw": raw_phone,
        "digits": digits,
        "international": e164,
        "national": national,
        "country": country_name,
        "region": region or "Unknown",
        "type": number_type,
        "is_possible": phonenumbers.is_possible_number(parsed),
        "is_valid": phonenumbers.is_valid_number(parsed),
    }


def numverify_lookup(phone_e164: str) -> Dict[str, Any]:
    key = NUMVERIFY_KEY
    if not key:
        return {"status": "disabled", "note": "NUMVERIFY_API_KEY not configured"}
    params = {"access_key": key, "number": phone_e164, "format": 1}
    data = http_get_json("http://apilayer.net/api/validate", params=params)
    if not data:
        return {"status": "error", "note": "NumVerify request failed"}
    return {
        "status": "ok",
        "valid": data.get("valid"),
        "number": data.get("number"),
        "international_format": data.get("international_format"),
        "local_format": data.get("local_format"),
        "country_prefix": data.get("country_prefix"),
        "country_code": data.get("country_code"),
        "country_name": data.get("country_name"),
        "location": data.get("location"),
        "carrier": data.get("carrier"),
        "line_type": data.get("line_type"),
    }


def get_domain_info(domain: str) -> Dict[str, Any]:
    domain = clean_domain(domain)
    try:
        info = whois.whois(domain)
    except Exception:
        info = {}
    return {"domain": domain, "whois": info}


def get_related_domains(domain: str, limit: int = 15) -> Dict[str, Any]:
    domain = clean_domain(domain)
    data = http_get_json(f"https://crt.sh/?q=%25.{domain}&output=json")
    if not data:
        return {"status": "error", "note": "crt.sh lookup failed"}
    items: List[str] = []
    seen = set()
    for entry in data[:limit]:
        value = entry.get("common_name") or entry.get("name_value") or ""
        for row in str(value).splitlines():
            row = row.strip()
            if row and row.endswith(domain):
                row = row.lower()
                if row not in seen:
                    seen.add(row)
                    items.append(row)
    return {"status": "ok", "domain": domain, "related_domains": items[:limit], "count": len(items)}


def website_fingerprint(url: str) -> Dict[str, Any]:
    url = clean_url(url)
    page = http_get_text(url)
    if not page:
        return {"status": "error", "url": url, "note": "Page request failed"}
    soup = BeautifulSoup(page, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    meta = {}
    for tag in soup.find_all("meta"):
        key = tag.get("name") or tag.get("property")
        if key:
            meta[key.lower()] = tag.get("content", "")
    favicon = ""
    icon_tag = soup.find("link", rel=lambda x: x and "icon" in str(x).lower())
    if icon_tag:
        favicon = icon_tag.get("href") or ""
    text = (title + " " + " ".join(meta.values()) + " " + soup.get_text(" ", strip=True)).lower()
    tech_hints = []
    checks = {
        "WordPress": ["wp-content", "wordpress", "wp-json"],
        "Shopify": ["shopify", "cdn.shopify.com"],
        "Cloudflare": ["cloudflare", "cf-chl"],
        "React": ["react", "next.js"],
        "Node": ["node.js", "express"],
        "Bootstrap": ["bootstrap"],
        "Laravel": ["laravel", "livewire"],
    }
    for name, tokens in checks.items():
        if any(token in text for token in tokens):
            tech_hints.append(name)
    return {"status": "ok", "url": url, "title": title, "meta_tags": meta, "favicon": favicon, "tech_hints": tech_hints[:10], "description": meta.get("description", "")}


def search_wikipedia(keyword: str) -> Dict[str, Any]:
    wiki = wikipediaapi.Wikipedia(language="en")
    page = wiki.page(keyword)
    if page.exists():
        return {"title": page.title, "summary": page.summary[:1200], "url": page.fullurl}
    return {"status": "not_found"}


def build_number_queries(value: str) -> List[str]:
    digits = re.sub(r"\D", "", value)
    queries = [
        f'"{value}"',
        f'"{digits}"',
        f'"{digits}" "phone"',
        f'"{digits}" "account"',
        f'"{digits}" "order"',
        f'"{digits}" "invoice"',
        f'"{digits}" "reference"',
        f'"{digits}" "customer"',
        f'"{digits}" "support"',
        f'"{digits}" "tracking"',
        f'"{digits}" "id"',
        f'"{digits}" "code"',
    ]
    seen = set(); out: List[str] = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out[:12]


def build_risk_brief(raw: str, kind: str) -> Dict[str, Any]:
    score = 0
    signals = []
    value = normalize_whitespace(raw)
    if kind == "number":
        digits = re.sub(r"\D", "", value)
        if len(digits) >= 8:
            score += 3; signals.append("Long numeric value")
        if len(digits) in [10, 11]:
            score += 5; signals.append("Looks like a phone number")
        if len(digits) >= 12:
            score += 3; signals.append("Could be an ID/reference/account number")
    elif kind in ["email", "domain", "username"]:
        if "@" in value:
            score += 3; signals.append("Email-like format")
        if ".co" in value.lower():
            score += 4; signals.append("Short TLD / uncommon domain pattern")
    elif kind == "url":
        score += 4; signals.append("URL structure")
    elif kind == "phone":
        score += 4; signals.append("Phone-like structure")
    elif kind in ["person_or_company", "keyword"]:
        if len(value.split()) >= 2:
            score += 2; signals.append("Multi-word search")
    if score >= 12:
        level = "high"
    elif score >= 7:
        level = "medium"
    else:
        level = "low"
    return {"risk_level": level, "score": score, "signals": signals[:10]}


def get_search_provider_status() -> Dict[str, Any]:
    return {
        "serpapi": bool(SERPAPI_KEY),
        "google_cse": bool(GOOGLE_CSE_KEY and GOOGLE_CSE_CX),
        "duckduckgo": True,
        "numverify": bool(NUMVERIFY_KEY),
    }


def normalize_kind(category: str, raw_value: str) -> str:
    mapping = {"person": "person_or_company", "company": "person_or_company"}
    if category == "all":
        return detect_type(raw_value)
    return mapping.get(category, category)


def search_quality_score(results: List[Dict[str, Any]], query: str) -> int:
    if not results:
        return 0
    average_score = sum(item.get("score", 0) for item in results) / max(len(results), 1)
    return int(average_score)


def fallback_search_variants(query: str, kind: str) -> List[str]:
    value = normalize_whitespace(query)
    variants = [value]
    if kind == "email":
        if "@" in value:
            local, domain = value.split("@", 1)
            variants.extend([local, domain, f'"{local}" "{domain}"', f'{domain} "{local}"'])
    elif kind == "phone":
        digits = re.sub(r"\D", "", value)
        variants.extend([digits, f'"{digits}" phone', f'"{digits}" customer support'])
    elif kind == "domain":
        domain = clean_domain(value)
        variants.extend([domain, f'"{domain}" official', f'"{domain}" contact'])
    elif kind == "url":
        url = clean_url(value)
        variants.extend([url, f'"{url}" company', f'"{url}" contact'])
    elif kind in ["person_or_company", "keyword"]:
        tokens = [part for part in re.split(r"\s+", value) if part]
        if len(tokens) > 1:
            variants.append(" ".join(tokens[:2]))
        variants.extend([f'"{value}" official', f'"{value}" company', f'"{value}" linkedin'])
    deduped = []
    seen = set()
    for item in variants:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped[:10]


def run_search_with_fallback(query: str, kind: str, limit: int = 10, aggressive: bool = False) -> List[Dict[str, str]]:
    primary = search_all(query, kind=kind, limit=limit, aggressive=aggressive)
    quality = search_quality_score(primary, query)
    if len(primary) >= 3 and quality >= 30:
        return primary

    for variant in fallback_search_variants(query, kind):
        if variant == query:
            continue
        extra = search_all(variant, kind=kind, limit=max(6, limit), aggressive=aggressive)
        if extra:
            primary.extend(extra)
        if len(primary) >= 8 and search_quality_score(primary, query) >= 30:
            break

    deduped: List[Dict[str, str]] = []
    seen = set()
    for item in primary:
        key = (item.get("title"), item.get("link"))
        if key not in seen and item.get("link"):
            seen.add(key)
            deduped.append(item)
    return deduped[: max(limit * 3, 25)]


def build_public_profile(query: str, kind: str, results: List[Dict[str, Any]]) -> Dict[str, Any]:
    entity_candidates = []
    for item in results:
        entity_candidates.extend(item.get("entity_names", []))
    deduped = list(dict.fromkeys([c for c in entity_candidates if c and len(c) > 2]))[:8]

    source_counts: Dict[str, int] = {}
    domains: Dict[str, int] = {}
    for item in results:
        source = item.get("source", "general")
        source_counts[source] = source_counts.get(source, 0) + 1
        domain = item.get("domain", "")
        if domain:
            domains[domain] = domains.get(domain, 0) + 1

    top_domains = [name for name, _ in sorted(domains.items(), key=lambda x: x[1], reverse=True)[:5]]
    dominant_source = max(source_counts.items(), key=lambda x: x[1], default=("general", 0))[0]
    average_score = sum(item.get("score", 0) for item in results) / max(len(results), 1)
    confidence = "high" if len(results) >= 4 and average_score >= 50 else "medium" if len(results) >= 2 and average_score >= 25 else "low"

    return {
        "entity_category": classify_query_label(query, kind),
        "top_entities": deduped,
        "top_domains": top_domains,
        "dominant_source": dominant_source,
        "average_score": round(average_score, 1),
        "confidence": confidence,
    }


def build_intelligence_profile(query: str, kind: str, results: List[Dict[str, Any]], likely_owner: List[str], likely_usage: List[str]) -> Dict[str, Any]:
    profile = build_public_profile(query, kind, results)
    entity_candidates = []
    for item in results:
        entity_candidates.extend(item.get("entity_names", []))
    for owner in likely_owner:
        entity_candidates.append(owner)
    entity_candidates = [e for e in entity_candidates if e and len(e) > 2]
    deduped = list(dict.fromkeys(entity_candidates))[:8]

    domain_counts: Dict[str, int] = {}
    for item in results:
        domain = item.get("domain", "")
        if domain:
            domain_counts[domain] = domain_counts.get(domain, 0) + 1
    top_domains = sorted(domain_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    score_value = max((item.get("score", 0) for item in results), default=0)
    average_score = sum(item.get("score", 0) for item in results) / max(len(results), 1)
    confidence_label = "high" if score_value >= 80 and len(results) >= 4 else "medium" if score_value >= 40 and len(results) >= 2 else "low"

    likely_name = deduped[0] if deduped else query
    likely_company = deduped[1] if len(deduped) > 1 else likely_name

    narrative = (
        f"Public-source evidence points most strongly to '{likely_name}' and suggests '{likely_company}' as the most likely public identity or brand anchor. "
        f"The strongest references cluster around {', '.join([domain for domain, _ in top_domains][:3] or ['public web records'])}. "
        f"The most likely use case appears to be {likely_usage[0] if likely_usage else 'Unknown'}, with a {confidence_label} confidence based on source diversity, match strength, and result ranking."
    )

    profile.update({
        "top_entities": deduped,
        "top_domains": [d for d, _ in top_domains],
        "likely_name": likely_name,
        "likely_company": likely_company,
        "likely_use_case": likely_usage[0] if likely_usage else "Unknown",
        "confidence": confidence_label,
        "average_score": round(average_score, 1),
        "narrative": narrative,
        "explanation": [
            f"Strongest ranked match score: {score_value}",
            f"Average result score: {round(average_score, 1)}",
            f"Source profile: {profile.get('dominant_source', 'general')}",
            f"Top domains: {', '.join([d for d, _ in top_domains] or ['none detected'])}",
        ],
        "notes": [
            "This is public-source intelligence only and should not be treated as private identity verification.",
            "The model rewards trusted domains, query overlap, and strong public references while down-weighting generic pages.",
        ],
    })
    return profile


def run_scan(raw: str, category: str = "all", aggressive: bool = False) -> Dict[str, Any]:
    value = normalize_whitespace(raw)
    kind = normalize_kind(category, value)
    result: Dict[str, Any] = {"input": value, "type": kind, "risk": build_risk_brief(value, kind), "provider_status": get_search_provider_status()}

    if kind == "email":
        email = clean_email(value)
        result["email"] = email
        raw_results = run_search_with_fallback(f'"{email}"', kind="email", limit=10, aggressive=aggressive)
        result["search_results"] = raw_results
        result["likely_owner"] = guess_owner(raw_results)
        result["likely_usage"] = guess_usage(raw_results)
        result["wikipedia"] = search_wikipedia(email.split("@")[-1])

    elif kind == "phone":
        phone = clean_phone(value)
        parsed = parse_phone_details(phone)
        result["parsed"] = parsed
        result["numverify"] = numverify_lookup(parsed["international"])
        raw_results = run_search_with_fallback(f'"{phone}"', kind="phone", limit=12, aggressive=aggressive)
        result["search_results"] = raw_results
        result["likely_owner"] = guess_owner(raw_results)
        result["likely_usage"] = guess_usage(raw_results)
        result["country"] = parsed.get("country")
        result["region"] = parsed.get("region")

    elif kind == "domain":
        domain = clean_domain(value)
        result["domain"] = domain
        result["domain_info"] = get_domain_info(domain)
        result["related_domains"] = get_related_domains(domain)
        raw_results = run_search_with_fallback(f'"{domain}"', kind="domain", limit=12, aggressive=aggressive)
        result["search_results"] = raw_results
        result["likely_owner"] = guess_owner(raw_results)
        result["likely_usage"] = guess_usage(raw_results)

    elif kind == "url":
        url = clean_url(value)
        result["url"] = url
        result["url_details"] = {"host": urlparse(url).netloc.lower(), "path": urlparse(url).path, "query": urlparse(url).query}
        result["website_fingerprint"] = website_fingerprint(url)
        raw_results = run_search_with_fallback(f'"{url}"', kind="url", limit=12, aggressive=aggressive)
        result["search_results"] = raw_results
        result["likely_owner"] = guess_owner(raw_results)
        result["likely_usage"] = guess_usage(raw_results)

    elif kind == "username":
        username = value.lstrip("@")
        result["username"] = username
        raw_results = run_search_with_fallback(f'"{username}"', kind="username", limit=12, aggressive=aggressive)
        result["search_results"] = raw_results
        result["likely_owner"] = guess_owner(raw_results)
        result["likely_usage"] = guess_usage(raw_results)

    elif kind == "number":
        digits = re.sub(r"\D", "", value)
        result["number_details"] = {
            "digits": digits,
            "length": len(digits),
            "looks_like_phone": len(digits) in [10, 11],
            "looks_like_reference_id": len(digits) >= 8 and len(digits) not in [10, 11],
        }
        if len(digits) in [10, 11]:
            parsed = parse_phone_details(digits)
            result["parsed_phone"] = parsed
            result["numverify"] = numverify_lookup(parsed["international"])
            result["country"] = parsed.get("country")
            result["region"] = parsed.get("region")
        all_results = []
        for q in build_number_queries(digits):
            all_results.extend(run_search_with_fallback(q, kind="number", limit=5, aggressive=aggressive))
        result["search_results"] = all_results[:60]
        result["likely_owner"] = guess_owner(result["search_results"])
        result["likely_usage"] = guess_usage(result["search_results"])

    elif kind in ["person_or_company", "keyword"]:
        raw_results = run_search_with_fallback(f'"{value}"', kind="person_or_company", limit=12, aggressive=aggressive)
        result["search_results"] = raw_results
        result["likely_owner"] = guess_owner(raw_results)
        result["likely_usage"] = guess_usage(raw_results)
        if kind == "person_or_company":
            result["wikipedia"] = search_wikipedia(value)

    else:
        result["search_results"] = []
        result["likely_owner"] = []
        result["likely_usage"] = ["Unknown"]

    ranked = prepare_ranked_results(result["search_results"], value)
    result["ranked_results"] = ranked
    result["summary"] = build_intelligence_profile(value, kind, ranked, result["likely_owner"], result["likely_usage"])
    result["public_profile"] = build_public_profile(value, kind, ranked)
    confidence_value = max((item.get("score", 0) for item in ranked), default=0)
    if len(ranked) >= 5 and confidence_value >= 80:
        confidence = "high"
    elif len(ranked) >= 2 and confidence_value >= 40:
        confidence = "medium"
    else:
        confidence = "low"
    result["summary"]["confidence"] = confidence
    result["public_profile"]["confidence"] = confidence
    return result


def render_mobile_css() -> None:
    st.markdown(
        """
        <style>
        .stApp { padding-bottom: 2rem; }
        div[data-testid="stSidebar"] { background: #0f172a; }
        .result-card { border:1px solid #2d3b53; border-radius:14px; padding:14px 16px; margin-bottom:12px; background:linear-gradient(180deg,#111827,#0f172a); box-shadow:0 2px 10px rgba(15,23,4,0.18); }
        .result-chip { display:inline-block; padding:4px 8px; border-radius:999px; margin:4px 6px 0 0; font-size:11px; background:#1e293b; color:#dbeafe; }
        @media (max-width: 768px) {
            .block-container { padding: 0.8rem 0.75rem 1.5rem !important; }
            h1 { font-size: 2rem !important; }
            h2 { font-size: 1.4rem !important; }
            h3 { font-size: 1.1rem !important; }
            .stButton>button { width: 100%; }
            .stTextInput input, .stTextArea textarea, .stSelectbox div, .stNumberInput input {
                font-size: 1rem !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_summary_cards(data: Dict[str, Any]) -> None:
    st.subheader("Overview")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Type", str(data.get("type", "unknown")).title())
    c2.metric("Risk", str(data.get("risk", {}).get("risk_level", "low")).title())
    c3.metric("Public results", str(len(data.get("search_results", []))))
    c4.metric("Confidence", str(data.get("summary", {}).get("confidence", "low")).title())

    if data.get("type") == "phone":
        st.caption(f"Country: {data.get('country', 'Unknown')} | Region: {data.get('region', 'Unknown')}")
    elif data.get("type") == "domain":
        st.caption(f"Domain: {data.get('domain', '')}")
    elif data.get("type") == "number":
        st.caption(f"Numeric length: {data.get('number_details', {}).get('length', 'unknown')}")

    provider_status = data.get("provider_status", {})
    available = [name for name, enabled in provider_status.items() if enabled]
    if available:
        st.caption(f"Active search providers: {', '.join(available)}")


def render_result_card(item: Dict[str, Any]) -> None:
    title = item.get("title", "Untitled")
    link = item.get("link", "")
    snippet = item.get("snippet", "")
    domain = item.get("domain", "")
    score = item.get("score", 0)
    entity_names = item.get("entity_names", [])
    reasons = item.get("reasons", [])
    chips = "".join(f'<span class="result-chip">{reason}</span>' for reason in reasons)
    with st.container():
        st.markdown(
            f"""
            <div class="result-card">
                <div style="display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:8px; flex-wrap:wrap;">
                    <div style="font-size:12px; color:#93c5fd;">{domain}</div>
                    <div style="font-size:12px; color:#cbd5e1; background:#1e293b; padding:4px 8px; border-radius:999px;">score {score}</div>
                </div>
                <div style="font-size:20px; font-weight:600; margin-bottom:8px;"><a href="{link}" target="_blank" style="color:#f8fafc; text-decoration:none;">{title}</a></div>
                <div style="font-size:13px; color:#cbd5e1; margin-bottom:8px;">entity hints: {', '.join(entity_names) if entity_names else 'n/a'}</div>
                <div style="margin-bottom:8px;">{chips}</div>
                <div style="font-size:14px; color:#dbeafe; line-height:1.6;">{snippet}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_intelligence_profile(data: Dict[str, Any]) -> None:
    st.subheader("Intelligence profile")
    profile = data.get("summary", {})
    if not profile:
        st.info("Not enough data to generate an intelligence profile.")
        return

    st.info(profile.get("narrative", "No summary available."))

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Likely identity**")
        st.code(profile.get("likely_name", "Unknown"))
        st.markdown("**Likely company**")
        st.code(profile.get("likely_company", "Unknown"))
    with col2:
        st.markdown("**Likely use case**")
        st.code(profile.get("likely_use_case", "Unknown"))
        st.markdown("**Confidence**")
        st.code(str(profile.get("confidence", "low")).title())

    st.markdown("**Entity category**")
    st.code(profile.get("entity_category", "entity"))

    st.markdown("**Top entities**")
    st.code(", ".join(profile.get("top_entities", [])) if profile.get("top_entities") else "No meaningful entities found.")

    st.markdown("**Top domains**")
    st.code(", ".join(profile.get("top_domains", [])) if profile.get("top_domains") else "No domains found.")

    st.markdown("**Source distribution**")
    st.json(profile.get("source_distribution", {}))

    for note in profile.get("notes", []):
        st.caption(note)


def render_who_what_panel(data: Dict[str, Any]) -> None:
    st.subheader("Who / what is this?")
    summary = data.get("summary", {})
    if summary.get("narrative"):
        st.markdown(summary["narrative"])
    if data.get("likely_owner"):
        st.markdown("Likely owner or brand clues:")
        st.code(", ".join(data["likely_owner"]))
    else:
        st.info("No strong owner clues were found in public results.")
    if data.get("wikipedia"):
        st.markdown("Wikipedia context:")
        st.write(data["wikipedia"].get("title"))
        st.write(data["wikipedia"].get("summary", "")[:1000])
        st.caption(data["wikipedia"].get("url", ""))


def render_usage_panel(data: Dict[str, Any]) -> None:
    st.subheader("What is it likely used for?")
    st.code(", ".join(data.get("likely_usage", ["Unknown"])))


def render_hints_panel(data: Dict[str, Any]) -> None:
    st.subheader("Country / region / owner hints")
    if data.get("type") == "phone":
        st.json({"country": data.get("country"), "region": data.get("region"), "parsed": data.get("parsed")})
        if data.get("numverify"):
            st.json(data["numverify"])
    elif data.get("type") == "domain":
        st.json({"domain_info": data.get("domain_info"), "related_domains": data.get("related_domains")})
    elif data.get("type") == "url":
        st.json({"url_details": data.get("url_details"), "website_fingerprint": data.get("website_fingerprint")})
    elif data.get("type") == "number":
        st.json({"number_details": data.get("number_details"), "parsed_phone": data.get("parsed_phone")})
    else:
        st.json({"owner_hints": data.get("likely_owner", []), "usage_hints": data.get("likely_usage", [])})


def render_risk_panel(data: Dict[str, Any]) -> None:
    st.subheader("Risk score")
    st.code(json.dumps(data.get("risk", {}), indent=2))


def render_raw_metadata(data: Dict[str, Any]) -> None:
    with st.expander("Raw metadata / debug output", expanded=False):
        st.json(data)


def render_downloads(data: Dict[str, Any], input_value: str) -> None:
    json_bytes = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    csv_lines = []
    for item in data.get("ranked_results", data.get("search_results", [])):
        csv_lines.append({
            "title": item.get("title", ""),
            "link": item.get("link", ""),
            "snippet": item.get("snippet", ""),
            "domain": item.get("domain", ""),
            "score": item.get("score", 0),
            "source": item.get("source", ""),
            "reasons": ", ".join(item.get("reasons", [])),
        })
    df = pd.DataFrame(csv_lines)
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    excel_buffer = BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="results")
    excel_bytes = excel_buffer.getvalue()

    st.markdown("### Download results")
    st.download_button("Download JSON", data=json_bytes, file_name=f"{slugify(input_value)}.json", mime="application/json")
    st.download_button("Download CSV", data=csv_bytes, file_name=f"{slugify(input_value)}.csv", mime="text/csv")
    st.download_button("Download Excel", data=excel_bytes, file_name=f"{slugify(input_value)}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def render_history_sidebar() -> None:
    history = load_history()
    if not history:
        return
    st.sidebar.subheader("Recent scans")
    for index, item in enumerate(history[:8]):
        value = item.get("value", "")
        if st.sidebar.button(value, key=f"recent_{index}"):
            st.session_state["search_input"] = value
            st.session_state["category"] = item.get("category", "all")
            st.rerun()


def render_bulk_scan_ui() -> None:
    st.subheader("Bulk scan")
    bulk_text = st.text_area("Paste values (one per line)", height=220, placeholder="+1 555 123 4567\nuser@example.com\nexample.com\nApple Inc.")
    bulk_category = st.selectbox("Bulk category", ["all", "phone", "email", "domain", "url", "number"], index=0)
    aggressive = st.toggle("Aggressive bulk search", value=False)
    if st.button("Run bulk scan"):
        entries = [line.strip() for line in bulk_text.splitlines() if line.strip()]
        if not entries:
            st.warning("Paste at least one value to scan.")
            return
        results = []
        for entry in entries:
            try:
                result = run_scan(entry, category=bulk_category, aggressive=aggressive)
                results.append({
                    "input": entry,
                    "type": result.get("type"),
                    "risk": result.get("risk"),
                    "likely_owner": result.get("likely_owner", []),
                    "likely_usage": result.get("likely_usage", []),
                    "summary": result.get("summary"),
                })
            except Exception as exc:
                results.append({"input": entry, "error": str(exc)})
        st.success(f"Finished bulk scan for {len(results)} record(s).")
        st.json(results)
        st.download_button("Download bulk JSON", data=json.dumps(results, indent=2, ensure_ascii=False).encode("utf-8"), file_name="bulk_results.json", mime="application/json")


def render_deep_scan_mode() -> None:
    st.subheader("Deep scan mode")
    deep_query = st.text_input("Deep scan target", key="deep_scan_target", placeholder="company name, domain, or email")
    deep_kind = st.selectbox("Deep scan type", ["all", "email", "domain", "phone", "person", "url"], index=0)
    if st.button("Run deep scan"):
        if not deep_query.strip():
            st.warning("Please provide a target for deep scan.")
            return
        with st.spinner("Running deeper public-source investigation..."):
            result = run_scan(deep_query, category=deep_kind, aggressive=True)
        st.json({
            "entity_type": result.get("type"),
            "public_profile": result.get("public_profile"),
            "summary": result.get("summary"),
            "likely_owner": result.get("likely_owner"),
            "likely_usage": result.get("likely_usage"),
            "top_results": result.get("ranked_results", [])[:5],
        })


def main() -> None:
    render_mobile_css()
    st.title("Entity Intelligence")
    st.caption("Public-source intelligence for people, companies, domains, phone numbers, emails, URLs, and numeric values.")
    render_history_sidebar()

    with st.sidebar:
        st.header("Search settings")
        category = st.selectbox("Category", ["all", "person", "phone", "domain", "email", "url", "number"], index=0, key="category")
        aggressive = st.toggle("Aggressive search", value=False)
        min_score = st.slider("Minimum relevance score", 0, 80, 0)
        source_filter = st.selectbox("Source filter", ["all", "linkedin", "social", "search", "institution", "general"], index=0)
        st.info("Public-only intelligence: this system does not access private account data or protected personal records.")

        st.markdown("### Search provider status")
        provider_status = get_search_provider_status()
        for name, enabled in provider_status.items():
            st.caption(f"{name}: {'enabled' if enabled else 'not configured'}")

    examples = [
        "+1 555 123 4567",
        "example.com",
        "user@example.com",
        "Apple Inc.",
        "https://www.microsoft.com",
    ]
    cols = st.columns(len(examples))
    for col, example in zip(cols, examples):
        if col.button(example, key=f"example_{slugify(example)}"):
            st.session_state["search_input"] = example
            st.rerun()

    search_input = st.text_input("Enter a value to scan", value=st.session_state.get("search_input", ""), key="search_input", placeholder="e.g. +1 555 123 4567, example.com, user@example.com, Apple Inc.")

    if st.button("Search"):
        if not search_input.strip():
            st.warning("Please enter a value.")
            return
        with st.spinner("Scanning public results..."):
            result = run_scan(search_input, category=category, aggressive=aggressive)
        add_history_item(search_input, category)

        ranked = prepare_ranked_results(result.get("search_results", []), search_input)
        filtered = filter_ranked_results(ranked, min_score=min_score, domain_filter="all", source_filter=source_filter)
        result["filtered_ranked_results"] = filtered

        render_summary_cards(result)
        st.markdown("---")
        render_intelligence_profile(result)
        st.markdown("---")
        render_who_what_panel(result)
        st.markdown("---")

        st.subheader("Where does it appear?")
        domain_options = ["all"] + sorted({item.get("domain", "") for item in filtered if item.get("domain")})
        domain_filter = st.selectbox("Filter by domain", domain_options, index=0)
        visible = filter_ranked_results(filtered, min_score=min_score, domain_filter=(domain_filter if domain_filter != "all" else "all"), source_filter=source_filter)
        if visible:
            for item in visible[:12]:
                render_result_card(item)
        else:
            st.info("No public matches matched the selected filters.")
        st.markdown("---")

        render_usage_panel(result)
        st.markdown("---")
        render_hints_panel(result)
        st.markdown("---")
        render_risk_panel(result)
        st.markdown("---")
        render_raw_metadata(result)
        st.markdown("---")
        render_downloads(result, search_input)

    render_deep_scan_mode()
    render_bulk_scan_ui()


if __name__ == "__main__":
    main()

