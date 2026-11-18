#!/usr/bin/env python3
"""Maintain the active Sandillon calendar and prepare the following year."""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import sys
import unicodedata
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from extract_calendar import download_pdf, extract

CALENDAR_PAGE = "https://sictom-chateauneuf.fr/calendrier-de-collecte/"
OUTPUT = Path("collectes.json")


def get_page(url: str) -> str:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; SandillonCalendarBot/1.0)"}
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read().decode("utf-8", errors="replace")


class PDFLinkParser(HTMLParser):
    """Collect PDF links together with the visible text of their anchor."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.current_href: str | None = None
        self.current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self.current_href = dict(attrs).get("href")
            self.current_text = []

    def handle_data(self, data: str) -> None:
        if self.current_href is not None:
            self.current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self.current_href is not None:
            self.links.append((self.current_href, " ".join(self.current_text)))
            self.current_href = None
            self.current_text = []


def normalize(value: str) -> str:
    value = html.unescape(value).casefold()
    return "".join(
        char for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )


def find_pdf(year: int) -> str:
    """Find the commune/year page first, then follow its PDF download link."""
    page_urls: list[str] = [CALENDAR_PAGE]

    # WordPress search API: it returns the intermediate commune calendar page,
    # even when the main calendar listing is filtered or loaded dynamically.
    search_url = (
        "https://sictom-chateauneuf.fr/wp-json/wp/v2/search?"
        + urllib.parse.urlencode({"search": f"Sandillon calendrier {year}", "per_page": 100})
    )
    try:
        results = json.loads(get_page(search_url))
        for item in results:
            url = item.get("url")
            title = item.get("title", {}).get("rendered", "") if isinstance(item.get("title"), dict) else item.get("title", "")
            searchable = normalize(f"{url or ''} {title or ''}")
            if url and "sandillon" in searchable and ("calendrier" in searchable or "collecte" in searchable):
                page_urls.append(url)
    except Exception as error:
        print(f"Recherche WordPress API indisponible : {error}")

    # Sitemap fallback: locate matching Sandillon calendar pages without
    # depending on the exact PDF filename or on the main page's visible cards.
    try:
        sitemap_root = ET.fromstring(get_page("https://sictom-chateauneuf.fr/wp-sitemap.xml"))
        sitemap_locs = [
            node.text.strip()
            for node in sitemap_root.iter()
            if node.tag.endswith("loc") and node.text
        ]
        for sitemap_url in sitemap_locs:
            if "post" not in sitemap_url and "page" not in sitemap_url:
                continue
            try:
                root = ET.fromstring(get_page(sitemap_url))
                for node in root.iter():
                    if not node.tag.endswith("loc") or not node.text:
                        continue
                    url = node.text.strip()
                    slug = normalize(url)
                    if "sandillon" in slug and ("calendrier" in slug or "collecte" in slug):
                        page_urls.append(url)
            except Exception as error:
                print(f"Sitemap ignoré ({sitemap_url}) : {error}")
    except Exception as error:
        print(f"Sitemap WordPress indisponible : {error}")

    # Also inspect links present directly on the official calendar listing.
    unique_pages = list(dict.fromkeys(page_urls))
    pdf_matches: list[tuple[int, str]] = []
    for page_url in unique_pages:
        try:
            page_html = get_page(page_url)
            page_text = normalize(re.sub(r"<[^>]+>", " ", page_html))
            if page_url != CALENDAR_PAGE:
                if "sandillon" not in page_text or str(year) not in page_text:
                    continue
                if not any(term in page_text for term in ("calendrier", "collecte")):
                    continue

            parser = PDFLinkParser()
            parser.feed(page_html)
            for href, label in parser.links:
                absolute = urljoin(page_url, html.unescape(href))
                if not urlsplit(absolute).path.casefold().endswith(".pdf"):
                    continue
                searchable = normalize(f"{absolute} {label} {page_text}")
                if "sandillon" not in searchable or str(year) not in searchable:
                    continue
                if not any(term in searchable for term in ("calendrier", "collecte")):
                    continue
                filename = normalize(Path(urlsplit(absolute).path).name)
                score = sum(term in filename for term in ("sandillon", "calendrier", str(year)))
                if page_url != CALENDAR_PAGE:
                    score += 3
                pdf_matches.append((score, absolute))
        except Exception as error:
            print(f"Page ignorée ({page_url}) : {error}")

    if pdf_matches:
        pdf_matches.sort(key=lambda item: item[0], reverse=True)
        chosen = pdf_matches[0][1]
        print(f"PDF {year} détecté après recherche des pages Sandillon : {chosen}")
        return chosen

    # Final fallback for known SICTOM file naming conventions.
    candidates = [
        f"https://sictom-chateauneuf.fr/wp-content/uploads/{year - 1}/11/SANDILLON_-Calendrier-de-collecte-{year}.pdf",
        f"https://sictom-chateauneuf.fr/wp-content/uploads/{year - 1}/11/SANDILLON-Calendrier-de-collectes-{year}.pdf",
    ]
    for candidate in candidates:
        temp = Path("candidate-check.pdf")
        try:
            download_pdf(candidate, temp)
            print(f"PDF {year} trouvé via l'adresse de secours : {candidate}")
            return candidate
        except Exception:
            pass
        finally:
            temp.unlink(missing_ok=True)

    raise RuntimeError(f"Calendrier PDF de Sandillon {year} introuvable sur le site officiel.")


def archive_path(year: int) -> Path:
    return Path(f"collectes-{year}.json")


def read_valid_calendar(path: Path, year: int) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("commune") != "Sandillon" or payload.get("annee") != year:
        raise RuntimeError(f"{path} ne contient pas un calendrier Sandillon valide pour {year}.")
    for key in ("ordures_menageres", "tri_selectif"):
        dates = payload.get(key)
        if not isinstance(dates, list) or not dates:
            raise RuntimeError(f"{path}: liste {key} absente ou vide.")
    return payload


def build_calendar(year: int) -> dict:
    pdf_url = find_pdf(year)
    pdf_path = Path(f"calendar-source-{year}.pdf")
    try:
        download_pdf(pdf_url, pdf_path)
        dates = extract(pdf_path, year)
        return {
            "commune": "Sandillon",
            "annee": year,
            "source": pdf_url,
            "consigne": "Sortir les poubelles la veille. Passage du camion l'après-midi.",
            **dates,
        }
    finally:
        pdf_path.unlink(missing_ok=True)


def save_calendar(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    # Use French local time so the active calendar changes at local New Year.
    current_year = dt.datetime.now(ZoneInfo("Europe/Paris")).year
    next_year = current_year + 1

    for year in (current_year, next_year):
        archive = archive_path(year)
        try:
            payload = build_calendar(year)
            save_calendar(archive, payload)
            print(
                f"Calendrier {year} validé : "
                f"{len(payload['ordures_menageres'])} collectes OM, "
                f"{len(payload['tri_selectif'])} collectes tri."
            )
        except Exception as error:
            if archive.exists():
                # Keep the last validated copy if the SICTOM site is temporarily unavailable
                # or has not published the following year's PDF yet.
                read_valid_calendar(archive, year)
                print(f"Calendrier {year} conservé (source non disponible : {error}).")
            elif year == current_year:
                raise RuntimeError(f"Calendrier actif {year} indisponible : {error}") from error
            else:
                print(f"Calendrier suivant {year} pas encore publié : {error}")

    active_archive = archive_path(current_year)
    active_payload = read_valid_calendar(active_archive, current_year)
    save_calendar(OUTPUT, active_payload)
    print(f"Calendrier actif publié : {current_year}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERREUR : publication interrompue : {error}", file=sys.stderr)
        raise SystemExit(1)
