"""
URL definitions for enterprise catalog API version 1.
"""
from django.urls import path, re_path
from rest_framework.routers import DefaultRouter

from enterprise_catalog.apps.api.v1.views.catalog_csv import CatalogCsvView
from enterprise_catalog.apps.api.v1.views.catalog_csv_data import (
    CatalogCsvDataView,
)
from enterprise_catalog.apps.api.v1.views.catalog_workbook import (
    CatalogWorkbookView,
)
from enterprise_catalog.apps.api.v1.views.default_catalog_results import (
    DefaultCatalogResultsView,
)
from enterprise_catalog.apps.api.v1.views.distinct_catalog_queries import (
    DistinctCatalogQueriesView,
)
from enterprise_catalog.apps.api.v1.views.enterprise_catalog_contains_content_items import (
    EnterpriseCatalogContainsContentItems,
)
from enterprise_catalog.apps.api.v1.views.enterprise_catalog_crud import (
    EnterpriseCatalogCRUDViewSet,
)
from enterprise_catalog.apps.api.v1.views.enterprise_catalog_diff import (
    EnterpriseCatalogDiff,
)
from enterprise_catalog.apps.api.v1.views.enterprise_catalog_get_content_metadata import (
    EnterpriseCatalogGetContentMetadata,
)
from enterprise_catalog.apps.api.v1.views.enterprise_catalog_refresh_data_from_discovery import (
    EnterpriseCatalogRefreshDataFromDiscovery,
)
from enterprise_catalog.apps.api.v1.views.enterprise_customer import (
    EnterpriseCustomerViewSet,
)


app_name = 'v1'

router = DefaultRouter()
router.register(r'enterprise-catalogs', EnterpriseCatalogCRUDViewSet, basename='enterprise-catalog')
router.register(r'enterprise-catalogs', EnterpriseCatalogContainsContentItems, basename='enterprise-catalog')
router.register(r'enterprise-customer', EnterpriseCustomerViewSet, basename='enterprise-customer')

urlpatterns = [
    path('enterprise-catalogs/catalog_csv_data', CatalogCsvDataView.as_view(),
         name='catalog-csv-data'
         ),
    path('enterprise-catalogs/default_course_set', DefaultCatalogResultsView.as_view(),
         name='default-course-set'
         ),
    path('enterprise-catalogs/catalog_csv', CatalogCsvView.as_view(),
         name='catalog-csv'
         ),
    path('enterprise-catalogs/catalog_workbook', CatalogWorkbookView.as_view(),
         name='catalog-workbook'
         ),
    re_path(
        r'^enterprise-catalogs/(?P<uuid>[\S]+)/get_content_metadata',
        EnterpriseCatalogGetContentMetadata.as_view({'get': 'get'}),
        name='get-content-metadata'
    ),
    re_path(
        r'^enterprise-catalogs/(?P<uuid>[\S]+)/generate_diff',
        EnterpriseCatalogDiff.as_view({'post': 'post'}),
        name='generate-catalog-diff'
    ),
    re_path(
        r'^enterprise-catalogs/(?P<uuid>[\S]+)/refresh_metadata',
        EnterpriseCatalogRefreshDataFromDiscovery.as_view({'post': 'post'}),
        name='update-enterprise-catalog'
    ),
    path('distinct-catalog-queries/', DistinctCatalogQueriesView.as_view(),
         name='distinct-catalog-queries'
         ),
]

urlpatterns += router.urls
