from django.conf import settings
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import connection
from wagtail.models.sites import get_site_for_hostname
from wagtail.search.index import class_is_indexed
from wagtail.search.models import Query

from .settings import grapple_settings
from .types.structures import BasePaginatedType, PaginationType


def resolve_site(hostname):
    """
    Looks up a Site record from a hostname.

    If two site records exist with the same hostname, you must specify a port
    to disambiguate the by appending a colon followed by the port number to the
    end of the hostname.

    For example:

    >>> resolve_site("wagtail.org")
    >>> resolve_site("wagtail.org:443")

    May raise one of the following exceptions:
     - Site.DoesNotExist: If the site is not found
     - Site.MultipleObjectsReturned: If multiple sites are found for a given hostname

    :param hostname: The hostname of the site to look up
    :type hostname: str
    """
    # Optionally allow querying by port
    h, p = None, None
    if ":" in hostname:
        (h, p) = hostname.split(":", 1)
    else:
        h = hostname
    return get_site_for_hostname(h, p)


def _sliced_queryset(qs, limit=None, offset=None):
    offset = int(offset or 0)
    # default
    limit = int(limit or grapple_settings.PAGE_SIZE)
    # maximum
    limit = min(limit, grapple_settings.MAX_PAGE_SIZE)
    return qs[offset : limit + offset]


def resolve_queryset(
    qs,
    info,
    limit=None,
    offset=None,
    search_query=None,
    id=None,
    order=None,
    collection=None,
    **kwargs,
):
    """
    Add limit, offset and search capabilities to the query. This contains
    argument names used by
    :class:`~grapple.types.structures.QuerySetList`.
    :param qs: The query set to be modified.
    :param info: The Graphene info object.
    :param limit: Limit number of objects in the QuerySet.
    :type limit: int
    :param id: Filter by the primary key.
    :type id: int
    :param offset: Omit a number of objects from the beginning of the query set
    :type offset: int
    :param search_query: Using Wagtail search, exclude objects that do not match
                         the search query.
    :type search_query: str
    :param order: Order the query set using the Django QuerySet order_by format.
    :type order: str
    :param collection: Use Wagtail's collection id to filter images or documents
    :type collection: int
    """

    if id is not None:
        qs = qs.filter(pk=id)
    else:
        qs = qs.all()

    order_by_relevance = True
    if order is not None:
        qs = qs.order_by(*(x.strip() for x in order.split(",")))
        order_by_relevance = False

    if collection is not None:
        try:
            qs.model._meta.get_field("collection")
        except LookupError:
            pass
        else:
            qs = qs.filter(collection=collection)

    if id is None and search_query:
        # Check if the queryset is searchable using Wagtail search.
        if not class_is_indexed(qs.model):
            raise TypeError("This data type is not searchable by Wagtail.")

        if grapple_settings.ADD_SEARCH_HIT:
            query = Query.get(search_query)
            query.add_hit()

        qs = qs.search(search_query, order_by_relevance=order_by_relevance)
        if connection.vendor != "sqlite":
            qs = qs.annotate_score("search_score")

    return _sliced_queryset(qs, limit, offset)


def get_paginated_result(qs, page, per_page):
    """
    Returns a paginated result.
    """
    paginator = Paginator(qs, per_page)

    try:
        # If the page exists and the page is an int
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        # If the page is not an int; show the first page
        page_obj = paginator.page(1)
    except EmptyPage:
        # If the page is out of range (too high most likely)
        # Then return the last page
        page_obj = paginator.page(paginator.num_pages)

    return BasePaginatedType(
        items=page_obj.object_list,
        pagination=PaginationType(
            total=paginator.count,
            count=len(page_obj.object_list),
            per_page=per_page,
            current_page=page_obj.number,
            prev_page=page_obj.previous_page_number()
            if page_obj.has_previous()
            else None,
            next_page=page_obj.next_page_number() if page_obj.has_next() else None,
            total_pages=paginator.num_pages,
        ),
    )


def resolve_paginated_queryset(
    qs, info, page=None, per_page=None, search_query=None, id=None, order=None, **kwargs
):
    """
    Add page, per_page and search capabilities to the query. This contains
    argument names used by
    :function:`~grapple.types.structures.PaginatedQuerySet`.
    :param qs: The query set to be modified.
    :param info: The Graphene info object.
    :param page: Page of resulting objects to return from the QuerySet.
    :type page: int
    :param id: Filter by the primary key.
    :type id: int
    :param per_page: The maximum number of items to include on a page.
    :type per_page: int
    :param search_query: Using Wagtail search, exclude objects that do not match
                         the search query.
    :type search_query: str
    :param order: Order the query set using the Django QuerySet order_by format.
    :type order: str
    """
    page = int(page or 1)
    per_page = min(
        int(per_page or grapple_settings.PAGE_SIZE), grapple_settings.MAX_PAGE_SIZE
    )

    if id is not None:
        qs = qs.filter(pk=id)
    else:
        qs = qs.all()

    # order_by_relevance will always take precedence over an existing order_by in the Postgres backend
    # we need to set it to False if we want to specify our own order_by.
    order_by_relevance = True
    if order is not None:
        qs = qs.order_by(*(x.strip() for x in order.split(",")))
        order_by_relevance = False

    if id is None and search_query:
        # Check if the queryset is searchable using Wagtail search.
        if not class_is_indexed(qs.model):
            raise TypeError("This data type is not searchable by Wagtail.")

        if grapple_settings.ADD_SEARCH_HIT:
            query = Query.get(search_query)
            query.add_hit()

        qs = qs.search(search_query, order_by_relevance=order_by_relevance)
        if connection.vendor != "sqlite":
            qs = qs.annotate_score("search_score")

    return get_paginated_result(qs, page, per_page)


def get_media_item_url(cls):
    url = ""
    if hasattr(cls, "url"):
        url = cls.url
    elif hasattr(cls, "file"):
        url = cls.file.url

    if url[0] == "/":
        return settings.BASE_URL + url
    return url
