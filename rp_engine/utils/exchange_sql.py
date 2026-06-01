"""Shared SQL fragments for exchange-domain queries.

``BOOKMARK_ANNOTATION_JOINS`` enriches an ``exchanges e`` row with its bookmark
name and annotation count. It is consumed by both the exchange router
(``_LIST_QUERY`` for list/edit) and ``ExchangeSearchService`` (search
enrichment), so it lives in a neutral leaf module below both layers — this
preserves the split-exchanges-router PoC-5 "one home, no duplication" intent now
that a service (not only routers) needs the fragment.
"""

from __future__ import annotations

BOOKMARK_ANNOTATION_JOINS = """
    LEFT JOIN exchange_bookmarks b
        ON b.rp_folder = e.rp_folder AND b.branch = e.branch
        AND b.exchange_number = e.exchange_number
    LEFT JOIN (
        SELECT rp_folder, branch, exchange_number, COUNT(*) AS acnt
        FROM exchange_annotations GROUP BY rp_folder, branch, exchange_number
    ) a ON a.rp_folder = e.rp_folder AND a.branch = e.branch
        AND a.exchange_number = e.exchange_number"""
