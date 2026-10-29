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

SERPAPI_KEY = os.getenv("SERPAPI_API_KEY")
GOOGLE_CSE_KEY = os.getenv("GOOGLE_CSE_KEY")
GOOGLE_CSE_CX = os.getenv("GOOGLE_CSE_CX")
NUMVERIFY_KEY = os.getenv("NUMVERIFY_API_KEY")
HISTORY_PATH = os.path.join(os.path.dirname(__file__), "history.json")

st.set_page_config(page_title="Entity Intelligence", page_icon="🔎", layout="wide")


def normalize_whitespace(value: str) -> str:
    return (value or "").strip()


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
    if re.fullmatch(r"\d+", value) and len(value) >= 4:
        return "number"
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
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:60] or "result"


def load_history() -> List[Dict[str, Any]]:
    try:
        if os.path.exists(HISTORY_PATH):
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
    except Exception:
        pass
    return []


def save_history(history: List[Dict[str, Any]]) -> None:
    try:
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history[:20], f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def add_history_item(value: str, category: str) -> None:
    history = load_history()
    new_item = {"value": value, "category": category}
    history = [item for item in history if str(item.get("value", "")).lower() != value.lower()]
    history.insert(0, new_item)
    save_history(history)


def extract_domain_from_link(link: str) -> str:
    try:
        host = urlparse(link).netloc.lower()
        return host.replace("www.", "")
    except Exception:
        return ""


def get_source_label(domain: str) -> str:
    d = domain.lower()
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


def extract_entity_names(text: str) -> List[str]:
    cleaned = re.sub(r"https?://\S+", " ", text)
    cleaned = re.sub(r"\b(?:www\.|com|net|org|io|co|gov|edu)\b", " ", cleaned, flags=re.I)
    titles = re.findall(r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}|[A-Z]{2,})\b", cleaned)
    stop = {
        "The", "This", "That", "Results", "Search", "Page", "Website", "About", "Contact",
        "Support", "Home", "Login", "Sign", "Up", "Business", "Company", "Site", "News"
    }
    names = []
    seen = set()
    for item in titles:
        if item in stop or len(item) < 2:
            continue
        if item not in seen:
            names.append(item)
            seen.add(item)
    return names[:8]


def rank_result(item: Dict[str, str], query: str) -> Dict[str, Any]:
    title = (item.get("title") or "").strip()
    snippet = (item.get("snippet") or "").strip()
    link = item.get("link") or ""
    domain = extract_domain_from_link(link)
    text = f"{title} {snippet}".lower()
    q = normalize_whitespace(query).lower()
    score = 0

    if q and q in text:
        score += 30
    if q and q in title.lower():
        score += 20
    if q and q in domain.lower():
        score += 15
    if domain:
        score += 10
    if "linkedin.com" in domain:
        score += 15
    if "facebook.com" in domain:
        score += 10
    if any(k in text for k in ["support", "contact", "customer", "sales", "pricing", "business", "company", "account", "invoice"]):
        score += 8
    if any(k in title.lower() for k in ["contact", "support", "sales", "help", "customer", "pricing"]):
        score += 12
    if title:
        score += 5
    if snippet:
        score += 4

    return {
        "title": title,
        "link": link,
        "snippet": snippet,
        "domain": domain,
        "score": score,
        "source": get_source_label(domain),
        "entity_names": extract_entity_names(f"{title} {snippet}"),
    }


def prepare_ranked_results(results: List[Dict[str, str]], query: str) -> List[Dict[str, Any]]:
    ranked = [rank_result(item, query) for item in results]
    ranked = [item for item in ranked if item.get("title") or item.get("link") or item.get("snippet")]
    ranked.sort(key=lambda x: x.get("score", 0), reverse=True)
    return ranked


def filter_ranked_results(items: List[Dict[str, Any]], min_score: int = 0, domain_filter: str = "all", source_filter: str = "all") -> List[Dict[str, Any]]:
    filtered = []
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
        resp = requests.get(url, params=params, headers=headers, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def http_get_text(url: str, headers: Optional[Dict[str, str]] = None) -> Optional[str]:
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        return resp.text
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
    return results


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
    return results


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
            return results
    except Exception:
        return []


def build_search_queries(query: str, kind: str) -> List[str]:
    q = normalize_whitespace(query)
    cleaned = re.sub(r"\s+", " ", q)
    queries = [f'"{cleaned}"', f'"{cleaned}" "contact"', f'"{cleaned}" "support"', f'"{cleaned}" "company"']
    if kind == "phone":
        digits = re.sub(r"\D", "", q)
        queries = [f'"{digits}"', f'"{digits}" "phone"', f'"{digits}" "business"', f'"{digits}" "support"']
    elif kind == "number":
        digits = re.sub(r"\D", "", q)
        queries = [f'"{digits}"', f'"{digits}" "reference"', f'"{digits}" "invoice"', f'"{digits}" "account"']
    elif kind == "domain":
        domain = clean_domain(q)
        queries = [f'"{domain}"', f'"{domain}" "company"', f'"{domain}" "contact"', f'"{domain}" "site:linkedin.com"']
    elif kind == "email":
        email = clean_email(q)
        queries = [f'"{email}"', f'"{email}" "contact"', f'"{email}" "support"', f'"{email}" "domain"']
    elif kind == "url":
        url = clean_url(q)
        queries = [f'"{url}"', f'"{url}" "contact"', f'"{url}" "about"', f'"{url}" "company"']
    elif kind in ["person_or_company", "keyword"]:
        queries = [f'"{cleaned}"', f'"{cleaned}" "company"', f'"{cleaned}" "site:linkedin.com"', f'"{cleaned}" "contact"']
    return queries[:6]


def search_all(query: str, kind: str = "all", limit: int = 10, aggressive: bool = False) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    seen = set()
    queries = build_search_queries(query, kind)
    source_calls = [serpapi_search, google_cse_search, duckduckgo_search]
    for query_text in queries:
        for source in source_calls:
            source_limit = max(limit, 6) if aggressive else limit
            for item in source(query_text, limit=source_limit):
                key = (item.get("title"), item.get("link"))
                if key not in seen and item.get("link"):
                    seen.add(key)
                    results.append(item)
    return results[: max(limit * 3, 25) if aggressive else limit * 3]


def guess_owner(results: List[Dict[str, str]]) -> List[str]:
    owners = []
    seen = set()
    for item in results:
        title = (item.get("title") or "").strip()
        cleaned = re.sub(r"[-–|:—].*$", "", title).strip()
        if cleaned and len(cleaned) > 2 and cleaned not in seen:
            owners.append(cleaned)
            seen.add(cleaned)
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
    key = os.getenv("NUMVERIFY_API_KEY")
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
    seen = set(); out = []
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


def run_scan(raw: str, category: str = "all", aggressive: bool = False) -> Dict[str, Any]:
    value = normalize_whitespace(raw)
    kind = detect_type(value) if category == "all" else category
    result: Dict[str, Any] = {"input": value, "type": kind, "risk": build_risk_brief(value, kind)}

    if kind == "email":
        email = clean_email(value)
        result["email"] = email
        query = f'"{email}"'
        raw_results = search_all(query, kind="email", limit=10, aggressive=aggressive)
        result["search_results"] = raw_results
        result["likely_owner"] = guess_owner(raw_results)
        result["likely_usage"] = guess_usage(raw_results)
        result["wikipedia"] = search_wikipedia(email.split("@")[-1])

    elif kind == "phone":
        phone = clean_phone(value)
        parsed = parse_phone_details(phone)
        result["parsed"] = parsed
        result["numverify"] = numverify_lookup(parsed["international"])
        raw_results = search_all(f'"{phone}"', kind="phone", limit=12, aggressive=aggressive)
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
        raw_results = search_all(f'"{domain}"', kind="domain", limit=12, aggressive=aggressive)
        result["search_results"] = raw_results
        result["likely_owner"] = guess_owner(raw_results)
        result["likely_usage"] = guess_usage(raw_results)

    elif kind == "url":
        url = clean_url(value)
        result["url"] = url
        result["url_details"] = {"host": urlparse(url).netloc.lower(), "path": urlparse(url).path, "query": urlparse(url).query}
        result["website_fingerprint"] = website_fingerprint(url)
        raw_results = search_all(f'"{url}"', kind="url", limit=12, aggressive=aggressive)
        result["search_results"] = raw_results
        result["likely_owner"] = guess_owner(raw_results)
        result["likely_usage"] = guess_usage(raw_results)

    elif kind == "username":
        username = value.lstrip("@")
        result["username"] = username
        raw_results = search_all(f'"{username}"', kind="username", limit=12, aggressive=aggressive)
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
            all_results.extend(search_all(q, kind="number", limit=5, aggressive=aggressive))
        result["search_results"] = all_results[:60]
        result["likely_owner"] = guess_owner(result["search_results"])
        result["likely_usage"] = guess_usage(result["search_results"])

    elif kind == "person_or_company":
        raw_results = search_all(f'"{value}"', kind="person_or_company", limit=12, aggressive=aggressive)
        result["search_results"] = raw_results
        result["likely_owner"] = guess_owner(raw_results)
        result["likely_usage"] = guess_usage(raw_results)
        result["wikipedia"] = search_wikipedia(value)

    elif kind == "keyword":
        raw_results = search_all(f'"{value}"', kind="keyword", limit=12, aggressive=aggressive)
        result["search_results"] = raw_results
        result["likely_owner"] = guess_owner(raw_results)
        result["likely_usage"] = guess_usage(raw_results)
    else:
        result["search_results"] = []
        result["likely_owner"] = []
        result["likely_usage"] = ["Unknown"]

    ranked = prepare_ranked_results(result["search_results"], value)
    result["ranked_results"] = ranked
    result["summary"] = {
        "top_domains": sorted({item.get("domain", "") for item in ranked if item.get("domain")})[:10],
        "confidence": "high" if len(ranked) >= 3 and max((item.get("score", 0) for item in ranked), default=0) >= 30 else "medium" if ranked else "low",
        "entities": list(dict.fromkeys([name for item in ranked for name in item.get("entity_names", [])]))[:8],
    }
    return result


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


def render_result_card(item: Dict[str, Any]) -> None:
    title = item.get("title", "Untitled")
    link = item.get("link", "")
    snippet = item.get("snippet", "")
    domain = item.get("domain", "")
    score = item.get("score", 0)
    entity_names = item.get("entity_names", [])
    with st.container():
        st.markdown(
            f"""
            <div style="border:1px solid #2d3b53; border-radius:14px; padding:14px 16px; margin-bottom:12px; background:linear-gradient(180deg,#111827,#0f172a); box-shadow:0 2px 10px rgba(15,23,42,0.35);">
                <div style="display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:8px; flex-wrap:wrap;">
                    <div style="font-size:12px; color:#93c5fd;">{domain}</div>
                    <div style="font-size:12px; color:#cbd5e1; background:#1e293b; padding:4px 8px; border-radius:999px;">score {score}</div>
                </div>
                <div style="font-size:20px; font-weight:600; margin-bottom:8px;"><a href="{link}" target="_blank" style="color:#f8fafc; text-decoration:none;">{title}</a></div>
                <div style="font-size:13px; color:#cbd5e1; margin-bottom:8px;">entity hints: {', '.join(entity_names) if entity_names else 'n/a'}</div>
                <div style="font-size:14px; color:#dbeafe; line-height:1.6;">{snippet}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_who_what_panel(data: Dict[str, Any]) -> None:
    st.subheader("Who / what is this?")
    summary_entities = data.get("summary", {}).get("entities", [])
    if summary_entities:
        st.code(", ".join(summary_entities))
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
        label = f"{item.get('value', 'unknown')}"
        if st.sidebar.button(label, key=f"recent_{index}"):
            st.session_state["search_input"] = item.get("value", "")
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
        bulk_df = pd.DataFrame(results)
        st.download_button("Download bulk JSON", data=json.dumps(results, indent=2, ensure_ascii=False).encode("utf-8"), file_name="bulk_results.json", mime="application/json")


def main() -> None:
    st.title("Entity Intelligence")
    st.caption("Search and analyze public data across people, companies, domains, phone numbers, emails, URLs, and numeric values.")
    render_history_sidebar()

    with st.sidebar:
        st.header("Search settings")
        category = st.selectbox("Category", ["all", "person", "phone", "domain", "email", "url", "number"], index=0)
        aggressive = st.toggle("Aggressive search", value=False)
        min_score = st.slider("Minimum relevance score", 0, 80, 0)
        source_filter = st.selectbox("Source filter", ["all", "linkedin", "social", "search", "institution", "general"], index=0)
        st.info("Use aggressive mode to search more deeply and rank more public results.")

    search_input = st.text_input("Enter a value to scan", value=st.session_state.get("search_input", ""), key="search_input", placeholder="e.g. +1 555 123 4567, example.com, user@example.com, Apple Inc.")

    if st.button("Search"):
        if not search_input.strip():
            st.warning("Please enter a value.")
            return
        with st.spinner("Scanning public results..."):
            result = run_scan(search_input, category=category, aggressive=aggressive)
        add_history_item(search_input, category)

        ranked = result.get("ranked_results", [])
        filtered = filter_ranked_results(ranked, min_score=min_score, domain_filter="all", source_filter=source_filter)
        result["filtered_ranked_results"] = filtered

        render_summary_cards(result)
        st.markdown("---")
        render_who_what_panel(result)
        st.markdown("---")

        st.subheader("Where does it appear?")
        domain_options = ["all"] + sorted({item.get("domain", "") for item in filtered if item.get("domain")})
        domain_filter = st.selectbox("Filter by domain", domain_options, index=0)
        if domain_filter != "all":
            visible = filter_ranked_results(filtered, min_score=min_score, domain_filter=domain_filter, source_filter=source_filter)
        else:
            visible = filter_ranked_results(filtered, min_score=min_score, domain_filter="all", source_filter=source_filter)
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

    render_bulk_scan_ui()


if __name__ == "__main__":
    main()
