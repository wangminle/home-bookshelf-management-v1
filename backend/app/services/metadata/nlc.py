from __future__ import annotations

import re
import urllib.parse

from bs4 import BeautifulSoup

from app.services.metadata.base import BookMetadata, MetadataProvider
from app.services.metadata.http import get_text
from app.utils.book_helpers import isbn10_to_isbn13, normalize_isbn

BASE_URL = "https://opac.nlc.cn/F"
SEARCH_URL_ISBN = (
    BASE_URL
    + "?func=find-b&find_code=ISB&request={isbn}&local_base=NLC01"
    + "&filter_code_1=WLN&filter_request_1=&filter_code_2=WYR&filter_request_2="
    + "&filter_code_3=WYR&filter_request_3=&filter_code_4=WFM&filter_request_4=&filter_code_5=WSL&filter_request_5="
)
SEARCH_URL_TITLE = (
    BASE_URL
    + "?func=find-b&find_code=WTP&request={title}&local_base=NLC01"
    + "&filter_code_1=WLN&filter_request_1=&filter_code_2=WYR&filter_request_2="
    + "&filter_code_3=WYR&filter_request_3=&filter_code_4=WFM&filter_request_4=&filter_code_5=WSL&filter_request_5="
)


class NLCProvider(MetadataProvider):
    name = "nlc"

    def __init__(self, user_agent: str | None = None):
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

    def fetch_by_isbn(self, isbn: str) -> BookMetadata | None:
        normalized = normalize_isbn(isbn)
        if not normalized:
            return None

        isbn13 = normalized if len(normalized) == 13 else isbn10_to_isbn13(normalized)
        html = get_text(SEARCH_URL_ISBN.format(isbn=isbn13), user_agent=self.user_agent)
        if not html:
            return None

        parsed = self._parse_detail_page(html, isbn13=isbn13, isbn10=normalized if len(normalized) == 10 else None)
        if parsed:
            return parsed

        for _, detail_url in self._parse_search_list(html)[:3]:
            detail_html = get_text(self._abs_url(detail_url), user_agent=self.user_agent)
            if not detail_html:
                continue
            parsed = self._parse_detail_page(detail_html, isbn13=isbn13, isbn10=normalized if len(normalized) == 10 else None)
            if parsed:
                return parsed
        return None

    def search(self, title: str, author: str | None = None) -> BookMetadata | None:
        query = title.strip()
        if author and author.strip():
            query = f"{query} {author.strip()}"

        encoded = urllib.parse.quote(query)
        html = get_text(SEARCH_URL_TITLE.format(title=encoded), user_agent=self.user_agent)
        if not html:
            return None

        for _, detail_url in self._parse_search_list(html)[:3]:
            detail_html = get_text(self._abs_url(detail_url), user_agent=self.user_agent)
            if not detail_html:
                continue
            parsed = self._parse_detail_page(detail_html)
            if parsed:
                return parsed
        return None

    def _parse_search_list(self, html: str) -> list[tuple[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        results: list[tuple[str, str]] = []
        for element in soup.find_all("div", class_="itemtitle"):
            link = element.find("a")
            if not link or not link.get("href"):
                continue
            results.append((element.get_text(strip=True), link["href"]))
        return results

    def _parse_detail_page(
        self,
        html: str,
        *,
        isbn13: str | None = None,
        isbn10: str | None = None,
    ) -> BookMetadata | None:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", attrs={"id": "td"})
        if not table:
            return None

        data: dict[str, str] = {}
        prev_key = ""
        prev_value = ""
        for row in table.find_all("tr"):
            cells = row.find_all("td", class_="td1")
            if len(cells) != 2:
                continue
            key = cells[0].get_text(strip=True).replace("\xa0", " ")
            value = cells[1].get_text(strip=True).replace("\xa0", " ")
            if not key and not value:
                continue
            if key:
                data[key] = value
                prev_key, prev_value = key, value
            elif prev_key:
                data[prev_key] = "\n".join([prev_value, value]).strip()

        title = self._clean_title(data.get("题名与责任") or "")
        if not title:
            return None

        authors = self._clean_authors(data.get("著者") or "")
        publisher, publish_date = self._parse_publish_info(data.get("出版项") or "", data.get("通用数据") or "")
        web_isbn = self._parse_isbn_from_html(html) or isbn13
        if web_isbn and len(web_isbn) == 10:
            isbn10 = web_isbn
            web_isbn = isbn10_to_isbn13(web_isbn)
        elif web_isbn:
            isbn13 = web_isbn

        clc_code = data.get("中图分类号")
        subject = data.get("主题")
        extra: dict[str, str] = {}
        if clc_code:
            extra["clc_code"] = clc_code
        if subject:
            extra["subject"] = subject

        return BookMetadata(
            title=title,
            isbn13=isbn13 or web_isbn,
            isbn10=isbn10,
            authors=authors,
            publisher=publisher,
            publish_date=publish_date,
            language="zh_CN",
            category=clc_code or subject,
            summary=data.get("内容提要"),
            extra=extra or None,
            source=self.name,
        )

    def _clean_title(self, title: str) -> str:
        pattern = r"([\u4e00-\u9fa5a-zA-Z0-9]+(?:[\u4e00-\u9fa5a-zA-Z0-9\s]+)?)(?=\s\[[\u4e00-\u9fa5]{2}\])"
        match = re.search(pattern, title)
        return match.group(1) if match else title.strip()

    def _clean_authors(self, raw: str) -> list[str]:
        authors: list[str] = []
        author_pattern = re.compile(r"^(.*?)\s+(?:著|编)")
        for entry in raw.split("\n"):
            entry = entry.strip()
            if not entry:
                continue
            match = author_pattern.match(entry)
            authors.append(match.group(1).strip() if match else entry)
        return authors

    def _parse_publish_info(self, publish_item: str, general_data: str) -> tuple[str | None, str | None]:
        year_match = re.search(r"\d{9}(\d{4})", general_data)
        publish_date = year_match.group(1) if year_match else None
        if not publish_date:
            fallback = re.search(r"(\d{4})", publish_item)
            publish_date = fallback.group(1) if fallback else None

        right = publish_item.split(":", 1)[1].strip() if ":" in publish_item else publish_item.strip()
        year_pos = re.search(r"\d{4}", right)
        if year_pos:
            publisher = right[: year_pos.start()].strip(" ,·-;:，")
        else:
            publisher = right.split(",", 1)[0].strip(" ,·-;:，")
        publisher = publisher or None
        return publisher, publish_date

    def _parse_isbn_from_html(self, html: str) -> str | None:
        match = re.search(r"ISBN:\s*([\d\-Xx]+)", html)
        if not match:
            return None
        return normalize_isbn(match.group(1))

    def _abs_url(self, href: str) -> str:
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return f"http://opac.nlc.cn{href}"
        return f"{BASE_URL.rstrip('/')}/{href.lstrip('/')}"
