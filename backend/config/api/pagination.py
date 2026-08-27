"""
One pagination style, everywhere.

Page-number, not cursor. Cursor pagination is the better default for a feed
that grows at the head, and it was the tempting choice — but the two things
students actually do here are *search a listing set* and *page through a
landlord's own records*, and both want a page count and the ability to jump.
A cursor gives neither.

The place cursors would earn their keep is the review list on a busy property,
where new reviews arrive while a reader pages. That is a real drift, and the
answer is that a review list is short enough to not be paged deeply. If a
property ever accumulates enough reviews for drift to matter, this decision is
worth revisiting **for that endpoint alone** — with the ADR to say why it
differs.
"""

from __future__ import annotations

from collections import OrderedDict

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class StandardPagination(PageNumberPagination):
    """The default for every list endpoint.

    `count` is included deliberately, despite costing a second query: a UI that
    cannot say "20 of 340" has to say "20", and a student comparing two
    searches needs the denominator to know which one narrowed anything.
    """

    page_size = 20
    page_size_query_param = "page_size"
    #: Capped, because `page_size` is caller-supplied and an uncapped one is a
    #: one-parameter denial of service on any endpoint with a join.
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response(
            OrderedDict(
                [
                    ("count", self.page.paginator.count),
                    ("page", self.page.number),
                    ("page_size", self.get_page_size(self.request)),
                    ("total_pages", self.page.paginator.num_pages),
                    ("next", self.get_next_link()),
                    ("previous", self.get_previous_link()),
                    ("results", data),
                ]
            )
        )

    def get_paginated_response_schema(self, schema):
        """Keep the generated TypeScript honest about the envelope."""
        return {
            "type": "object",
            "required": ["count", "page", "page_size", "total_pages", "results"],
            "properties": {
                "count": {"type": "integer", "example": 340},
                "page": {"type": "integer", "example": 1},
                "page_size": {"type": "integer", "example": 20},
                "total_pages": {"type": "integer", "example": 17},
                "next": {"type": "string", "nullable": True, "format": "uri"},
                "previous": {"type": "string", "nullable": True, "format": "uri"},
                "results": schema,
            },
        }
