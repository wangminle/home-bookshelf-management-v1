from __future__ import annotations

import concurrent.futures
import logging
import time

from app.config import settings
from app.services.metadata.base import BookMetadata, MetadataProvider
from app.services.metadata.google_books import GoogleBooksProvider
from app.services.metadata.nlc import NLCProvider
from app.services.metadata.openlibrary import OpenLibraryProvider
from app.utils.book_helpers import isbn10_to_isbn13, normalize_isbn

logger = logging.getLogger(__name__)

# 元数据链总超时，避免中文 ISBN 串行阻塞超设计验收（约 10 秒入库）
METADATA_CHAIN_DEADLINE_SEC = 12.0
# BUG-150：单批并行 provider 查询的额外宽限。
# fetch_metadata 把同一阶段的多个 provider 并行发起，取最先返回的命中；
# 每个底层请求已有 timeout（http.py 默认 15s），这里给整批一个硬上限，
# 防止最慢的 provider 拖住整体（deadline 同时约束串行阶段切换）。
_PROVIDER_BATCH_TIMEOUT_SEC = 8.0


def is_chinese_isbn(isbn: str | None) -> bool:
    normalized = normalize_isbn(isbn)
    if not normalized:
        return False
    isbn13 = normalized if len(normalized) == 13 else isbn10_to_isbn13(normalized)
    return isbn13.startswith("9787")


def _build_providers() -> dict[str, MetadataProvider]:
    user_agent = settings.metadata_user_agent
    return {
        "google_books": GoogleBooksProvider(api_key=settings.google_books_api_key, user_agent=user_agent),
        "nlc": NLCProvider(user_agent=user_agent),
        "openlibrary": OpenLibraryProvider(user_agent=user_agent),
    }


def get_primary_provider_names(*, chinese: bool) -> list[str]:
    if chinese:
        return ["nlc", "google_books"]
    return ["google_books"]


def get_auxiliary_provider_names() -> list[str]:
    return ["openlibrary"]


def get_search_fallback_provider_names(*, chinese: bool) -> list[str]:
    if chinese:
        return ["google_books", "nlc", "openlibrary"]
    return ["google_books", "openlibrary"]


def _safe_call(provider: MetadataProvider, method_name: str, *args, **kwargs) -> BookMetadata | None:
    try:
        method = getattr(provider, method_name)
        return method(*args, **kwargs)
    except Exception as exc:
        logger.warning("元数据 provider %s.%s 异常: %s", provider.name, method_name, exc, exc_info=False)
        return None


def _race_providers(
    providers: dict[str, MetadataProvider],
    names: list[str],
    method_name: str,
    *args,
    deadline: float,
    **kwargs,
) -> BookMetadata | None:
    """并行发起多个 provider 的同一方法调用，返回最先命中的结果。

    BUG-150：原串行实现里中文 ISBN 要依次等 nlc→google_books→openlibrary，
    最慢的 provider 决定总耗时（实测 60s 级）。并行后取最快命中返回，
    其余任务由 as_completed 自然丢弃；底层 HTTP 各自有 timeout 兜底。

    仍受 deadline 约束：若剩余时间不足，直接放弃本批。
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not names:
        return None
    batch_timeout = min(_PROVIDER_BATCH_TIMEOUT_SEC, remaining)

    # 不使用 with 上下文：退出 with 会隐式执行 shutdown(wait=True)，
    # 即使已命中或超时仍会阻塞到最慢的 provider 返回，使批次超时与
    # "最先命中返回"形同虚设。改为手动管理执行器，命中/超时后立即
    # shutdown(wait=False, cancel_futures=True)，放弃未完成任务。
    # 底层 HTTP 各自有 timeout 兜底，孤儿线程会在请求超时后自行结束。
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(names))
    future_to_name = {
        executor.submit(_safe_call, providers[name], method_name, *args, **kwargs): name
        for name in names
    }
    try:
        for future in concurrent.futures.as_completed(future_to_name, timeout=batch_timeout):
            result = future.result()
            if result:
                return result
    except concurrent.futures.TimeoutError:
        logger.warning(
            "元数据 provider 批次超时（%ss），已查 %s",
            batch_timeout,
            ",".join(names),
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return None


def fetch_metadata(
    isbn: str | None = None,
    title: str | None = None,
    author: str | None = None,
) -> BookMetadata | None:
    providers = _build_providers()
    normalized_isbn = normalize_isbn(isbn)
    chinese = is_chinese_isbn(normalized_isbn)
    deadline = time.monotonic() + METADATA_CHAIN_DEADLINE_SEC

    if normalized_isbn:
        # BUG-150：primary 与 auxiliary 两批之间仍是顺序关系（先精准 ISBN 命中，再辅助），
        # 但每批内部并行。两批都受 deadline 约束。
        primary_names = get_primary_provider_names(chinese=chinese)
        result = _race_providers(
            providers, primary_names, "fetch_by_isbn", normalized_isbn, deadline=deadline
        )
        if result:
            return result

        if time.monotonic() < deadline:
            aux_names = get_auxiliary_provider_names()
            result = _race_providers(
                providers, aux_names, "fetch_by_isbn", normalized_isbn, deadline=deadline
            )
            if result:
                return result

    if title and title.strip():
        if time.monotonic() >= deadline:
            logger.warning("元数据链超时，中止书名搜索（title=%s）", title.strip()[:80])
            return None
        fallback_names = get_search_fallback_provider_names(chinese=chinese or _looks_chinese(title))
        result = _race_providers(
            providers, fallback_names, "search", title.strip(), author, deadline=deadline
        )
        if result:
            return result

    return None


def _looks_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)