import copy
import json
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta
from operator import itemgetter
from unittest import mock

import ddt
import pytz
from django.conf import settings
from django.db import IntegrityError
from django.utils.text import slugify
from rest_framework import status
from rest_framework.reverse import reverse
from rest_framework.settings import api_settings
from six.moves.urllib.parse import quote_plus

from enterprise_catalog.apps.api.v1.tests.mixins import APITestMixin
from enterprise_catalog.apps.api.v1.utils import is_any_course_run_active
from enterprise_catalog.apps.catalog.constants import (
    COURSE,
    COURSE_RUN,
    PROGRAM,
)
from enterprise_catalog.apps.catalog.models import (
    CatalogQuery,
    ContentMetadata,
    ContentMetadataToQueries,
    EnterpriseCatalog,
)
from enterprise_catalog.apps.catalog.tests.factories import (
    CatalogQueryFactory,
    ContentMetadataFactory,
    EnterpriseCatalogFactory,
)
from enterprise_catalog.apps.catalog.utils import get_parent_content_key


@ddt.ddt
class EnterpriseCatalogDefaultCatalogResultsTests(APITestMixin):
    """
    Tests for the DefaultCatalogResultsView class
    """
    mock_algolia_hits = {'hits': [{
        'aggregation_key': 'course:MITx+18.01.2x',
        'key': 'MITx+18.01.2x',
        'language': 'English',
        'level_type': 'Intermediate',
        'content_type': 'course',
        'partners': [
            {'name': 'Massachusetts Institute of Technology',
             'logo_image_url': 'https://edx.org/image.png'}
        ],
        'programs': ['Professional Certificate'],
        'program_titles': ['Totally Awesome Program'],
        'short_description': 'description',
        'subjects': ['Math'],
        'skills': [{
            'name': 'Probability And Statistics',
            'description': 'description'
        }, {
            'name': 'Engineering Design Process',
            'description': 'description'
        }],
        'title': 'Calculus 1B: Integration',
        'marketing_url': 'edx.org/foo-bar',
        'first_enrollable_paid_seat_price': 100,
        'advertised_course_run': {
            'key': 'MITx/18.01.2x/3T2015',
            'pacing_type': 'instructor_paced',
            'start': '2015-09-08T00:00:00Z',
            'end': '2015-09-08T00:00:01Z',
            'upgrade_deadline': 32503680000.0,
        },
        'objectID': 'course-3543aa4e-3c64-4d9a-a343-5d5eda1dacf8-catalog-query-uuids-0'
    },
        {
        'aggregation_key': 'course:MITx+19',
        'key': 'MITx+19',
        'language': 'English',
        'level_type': 'Intermediate',
        'objectID': 'course-3543aa4e-3c64-4d9a-a343-5d5eda1dacf9-catalog-query-uuids-0'
    },
        {
        'aggregation_key': 'course:MITx+20',
        'language': 'English',
        'level_type': 'Intermediate',
        'objectID': 'course-3543aa4e-3c64-4d9a-a343-5d5eda1dacf7-catalog-query-uuids-0'
    }
    ]}

    def setUp(self):
        super().setUp()
        self.set_up_staff_user()

    def _get_contains_content_base_url(self):
        """
        Helper to construct the base url for the contains_content_items endpoint
        """
        return reverse('api:v1:default-course-set')

    def test_facet_validation(self):
        """
        Tests that the view validates Algolia facets provided by query params
        """
        url = self._get_contains_content_base_url()
        invalid_facets = 'invalid_facet=wrong&enterprise_catalog_query_titles=ayylmao'
        response = self.client.get(f'{url}?{invalid_facets}')
        assert response.status_code == 400
        assert response.json() == {'Error': "invalid facet(s): ['invalid_facet'] provided."}

    @mock.patch('enterprise_catalog.apps.api.v1.views.default_catalog_results.get_initialized_algolia_client')
    def test_valid_facet_validation(self, mock_algolia_client):
        """
        Tests a successful request with facets.
        """
        mock_algolia_client.return_value.algolia_index.search.side_effect = [self.mock_algolia_hits, {'hits': []}]
        url = self._get_contains_content_base_url()
        facets = 'enterprise_catalog_query_titles=foo&content_type=course'
        response = self.client.get(f'{url}?{facets}')
        assert response.status_code == 200

    def test_required_param_validation(self):
        """
        Tests that the view requires a provided catalog
        """
        url = self._get_contains_content_base_url()
        invalid_facets = 'bad=ayylmao'
        response = self.client.get(f'{url}?{invalid_facets}')
        assert response.status_code == 400
        assert response.json() == [
            'You must provide at least one of the following query parameters: enterprise_catalog_query_titles.'
        ]


@ddt.ddt
class EnterpriseCatalogCRUDViewSetTests(APITestMixin):
    """
    Tests for the EnterpriseCatalogCRUDViewSet
    """

    def setUp(self):
        super().setUp()
        self.set_up_staff()
        self.enterprise_catalog = EnterpriseCatalogFactory(
            enterprise_uuid=self.enterprise_uuid,
            enterprise_name=self.enterprise_name,
        )
        self.new_catalog_uuid = uuid.uuid4()
        self.new_catalog_data = {
            'uuid': self.new_catalog_uuid,
            'title': 'Test Title',
            'enterprise_customer': self.enterprise_uuid,
            'enterprise_customer_name': self.enterprise_name,
            'enabled_course_modes': '["verified"]',
            'publish_audit_enrollment_urls': True,
            'content_filter': '{"content_type":"course"}',
        }

    def _assert_correct_new_catalog_data(self, catalog_uuid):
        """
        Helper for verifying the data for a created/updated catalog
        """
        new_enterprise_catalog = EnterpriseCatalog.objects.get(uuid=catalog_uuid)
        self.assertEqual(new_enterprise_catalog.title, self.new_catalog_data['title'])
        self.assertEqual(new_enterprise_catalog.enabled_course_modes, ['verified'])
        self.assertEqual(
            new_enterprise_catalog.publish_audit_enrollment_urls,
            self.new_catalog_data['publish_audit_enrollment_urls'],
        )
        self.assertEqual(
            new_enterprise_catalog.catalog_query.content_filter,
            OrderedDict([('content_type', 'course')]),
        )

    def test_detail_unauthorized_catalog_learner(self):
        """
        Verify the viewset rejects catalog learners for the detail route
        """
        self.set_up_catalog_learner()
        url = reverse('api:v1:enterprise-catalog-detail', kwargs={'uuid': self.enterprise_catalog.uuid})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_patch_unauthorized_catalog_learner(self):
        """
        Verify the viewset rejects patch for catalog learners
        """
        self.set_up_catalog_learner()
        url = reverse('api:v1:enterprise-catalog-detail', kwargs={'uuid': self.enterprise_catalog.uuid})
        patch_data = {'title': 'Patch title'}
        response = self.client.patch(url, patch_data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_put_unauthorized_catalog_learner(self):
        """
        Verify the viewset rejects put for catalog learners
        """
        self.set_up_catalog_learner()
        url = reverse('api:v1:enterprise-catalog-detail', kwargs={'uuid': self.enterprise_catalog.uuid})
        response = self.client.put(url, self.new_catalog_data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_post_unauthorized_catalog_learner(self):
        """
        Verify the viewset rejects post for catalog learners
        """
        self.set_up_catalog_learner()
        url = reverse('api:v1:enterprise-catalog-list')
        response = self.client.post(url, self.new_catalog_data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @ddt.data(
        (False),
        (True),
    )
    def test_detail(self, is_implicit_check):
        """
        Verify the viewset returns the details for a single enterprise catalog
        """
        if is_implicit_check:
            self.remove_role_assignments()

        url = reverse('api:v1:enterprise-catalog-detail', kwargs={'uuid': self.enterprise_catalog.uuid})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertEqual(uuid.UUID(data['uuid']), self.enterprise_catalog.uuid)
        self.assertEqual(data['title'], self.enterprise_catalog.title)
        self.assertEqual(uuid.UUID(data['enterprise_customer']), self.enterprise_catalog.enterprise_uuid)

    def test_detail_unauthorized_non_catalog_admin(self):
        """
        Verify the viewset rejects users that are not catalog admins for the detail route
        """
        self.set_up_invalid_jwt_role()
        self.remove_role_assignments()
        url = reverse('api:v1:enterprise-catalog-detail', kwargs={'uuid': self.enterprise_catalog.uuid})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_detail_unauthorized_incorrect_jwt_context(self):
        """
        Verify the viewset rejects users that are catalog admins with an invalid
        context (i.e., enterprise uuid) for the detail route.
        """
        enterprise_catalog = EnterpriseCatalogFactory()
        self.remove_role_assignments()
        url = reverse('api:v1:enterprise-catalog-detail', kwargs={'uuid': enterprise_catalog.uuid})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @ddt.data(
        (False),
        (True),
    )
    def test_patch(self, is_implicit_check):
        """
        Verify the viewset handles patching an enterprise catalog
        """
        if is_implicit_check:
            self.remove_role_assignments()

        url = reverse('api:v1:enterprise-catalog-detail', kwargs={'uuid': self.enterprise_catalog.uuid})
        patch_data = {'title': 'Patch title'}
        response = self.client.patch(url, patch_data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Verify that only the data we specifically patched changed
        self.assertEqual(response.data['title'], patch_data['title'])
        patched_catalog = EnterpriseCatalog.objects.get(uuid=self.enterprise_catalog.uuid)
        self.assertEqual(patched_catalog.catalog_query, self.enterprise_catalog.catalog_query)
        self.assertEqual(patched_catalog.enterprise_uuid, self.enterprise_catalog.enterprise_uuid)
        self.assertEqual(patched_catalog.enabled_course_modes, self.enterprise_catalog.enabled_course_modes)
        self.assertEqual(
            patched_catalog.publish_audit_enrollment_urls,
            self.enterprise_catalog.publish_audit_enrollment_urls,
        )

    def test_patch_unauthorized_non_catalog_admin(self):
        """
        Verify the viewset rejects patch for users that are not catalog admins
        """
        self.set_up_invalid_jwt_role()
        self.remove_role_assignments()
        url = reverse('api:v1:enterprise-catalog-detail', kwargs={'uuid': self.enterprise_catalog.uuid})
        patch_data = {'title': 'Patch title'}
        response = self.client.patch(url, patch_data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_patch_unauthorized_incorrect_jwt_context(self):
        """
        Verify the viewset rejects patch for users that are catalog admins with an invalid
        context (i.e., enterprise uuid)
        """
        enterprise_catalog = EnterpriseCatalogFactory()
        self.remove_role_assignments()
        url = reverse('api:v1:enterprise-catalog-detail', kwargs={'uuid': enterprise_catalog.uuid})
        patch_data = {'title': 'Patch title'}
        response = self.client.patch(url, patch_data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @ddt.data(
        (False),
        (True),
    )
    def test_put(self, is_implicit_check):
        """
        Verify the viewset handles replacing an enterprise catalog
        """
        if is_implicit_check:
            self.remove_role_assignments()

        url = reverse('api:v1:enterprise-catalog-detail', kwargs={'uuid': self.enterprise_catalog.uuid})
        response = self.client.put(url, self.new_catalog_data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self._assert_correct_new_catalog_data(self.enterprise_catalog.uuid)  # The UUID should not have changed

    def test_put_unauthorized_non_catalog_admin(self):
        """
        Verify the viewset rejects put for users that are not catalog admins
        """
        self.set_up_invalid_jwt_role()
        self.remove_role_assignments()
        url = reverse('api:v1:enterprise-catalog-detail', kwargs={'uuid': self.enterprise_catalog.uuid})
        response = self.client.put(url, self.new_catalog_data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_put_unauthorized_incorrect_jwt_context(self):
        """
        Verify the viewset rejects put for users that are catalog admins with an invalid
        context (i.e., enterprise uuid)
        """
        enterprise_catalog = EnterpriseCatalogFactory()
        self.remove_role_assignments()
        url = reverse('api:v1:enterprise-catalog-detail', kwargs={'uuid': enterprise_catalog.uuid})
        response = self.client.put(url, self.new_catalog_data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @ddt.data(
        (False),
        (True),
    )
    def test_post(self, is_implicit_check):
        """
        Verify the viewset handles creating an enterprise catalog
        """
        if is_implicit_check:
            self.remove_role_assignments()

        url = reverse('api:v1:enterprise-catalog-list')
        response = self.client.post(url, self.new_catalog_data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self._assert_correct_new_catalog_data(self.new_catalog_uuid)

    def test_post_integrity_error(self):
        """
        Verify the viewset raises error when creating a duplicate enterprise catalog
        """
        url = reverse('api:v1:enterprise-catalog-list')
        self.client.post(url, self.new_catalog_data)
        with self.assertRaises(IntegrityError):
            self.client.post(url, self.new_catalog_data)
        # Note: we're hitting the endpoint twice here, but this task should
        # only be run once, as we should error from an integrity error the
        # second time through

    def test_post_unauthorized_non_catalog_admin(self):
        """
        Verify the viewset rejects post for users that are not catalog admins
        """
        self.set_up_invalid_jwt_role()
        self.remove_role_assignments()
        url = reverse('api:v1:enterprise-catalog-list')
        response = self.client.post(url, self.new_catalog_data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_post_unauthorized_incorrect_jwt_context(self):
        """
        Verify the viewset rejects post for users that are catalog admins with an invalid
        context (i.e., enterprise uuid)
        """
        catalog_data = {
            'uuid': self.new_catalog_uuid,
            'title': 'Test Title',
            'enterprise_customer': uuid.uuid4(),
            'enabled_course_modes': '["verified"]',
            'publish_audit_enrollment_urls': True,
            'content_filter': '{"content_type":"course"}',
        }
        self.remove_role_assignments()
        url = reverse('api:v1:enterprise-catalog-list')
        response = self.client.post(url, catalog_data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


@ddt.ddt
class EnterpriseCatalogCRUDViewSetListTests(APITestMixin):
    """
    Tests for the EnterpriseCatalogCRUDViewSet list endpoint.
    """

    def setUp(self):
        super().setUp()
        self.set_up_staff_user()
        self.enterprise_catalog = EnterpriseCatalogFactory(enterprise_uuid=self.enterprise_uuid)

    def test_list_for_superusers(self):
        """
        Verify the viewset returns a list of all enterprise catalogs for superusers
        """
        self.set_up_superuser()
        url = reverse('api:v1:enterprise-catalog-list')
        second_enterprise_catalog = EnterpriseCatalogFactory()
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 2)
        results = response.data['results']
        self.assertEqual(uuid.UUID(results[0]['uuid']), self.enterprise_catalog.uuid)
        self.assertEqual(uuid.UUID(results[1]['uuid']), second_enterprise_catalog.uuid)

    def test_empty_list_for_non_catalog_admin(self):
        """
        Verify the viewset returns an empty list for users that are staff but not catalog admins.
        """
        self.set_up_invalid_jwt_role()
        url = reverse('api:v1:enterprise-catalog-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)

    @ddt.data(
        False,
        True,
    )
    def test_one_catalog_for_catalog_admins(self, is_role_assigned_via_jwt):
        """
        Verify the viewset returns a single catalog (when multiple exist) for catalog admins of a certain enterprise.
        """
        if is_role_assigned_via_jwt:
            self.assign_catalog_admin_jwt_role()
        else:
            self.assign_catalog_admin_feature_role()

        # create an additional catalog from a different enterprise,
        # and make sure we don't see it in the response results.
        EnterpriseCatalogFactory(enterprise_uuid=uuid.uuid4())

        url = reverse('api:v1:enterprise-catalog-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        results = response.data['results']
        self.assertEqual(uuid.UUID(results[0]['uuid']), self.enterprise_catalog.uuid)

    @ddt.data(
        False,
        True,
    )
    def test_multiple_catalogs_for_catalog_admins(self, is_role_assigned_via_jwt):
        """
        Verify the viewset returns multiple catalogs for catalog admins of two different enterprises.
        """
        second_enterprise_catalog = EnterpriseCatalogFactory(enterprise_uuid=uuid.uuid4())

        if is_role_assigned_via_jwt:
            self.assign_catalog_admin_jwt_role(
                self.enterprise_uuid,
                second_enterprise_catalog.enterprise_uuid,
            )
        else:
            self.assign_catalog_admin_feature_role(enterprise_uuids=[
                self.enterprise_uuid,
                second_enterprise_catalog.enterprise_uuid,
            ])

        url = reverse('api:v1:enterprise-catalog-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 2)
        results = response.data['results']
        self.assertEqual(uuid.UUID(results[0]['uuid']), self.enterprise_catalog.uuid)
        self.assertEqual(uuid.UUID(results[1]['uuid']), second_enterprise_catalog.uuid)

    @ddt.data(
        False,
        True,
    )
    def test_every_catalog_for_catalog_admins(self, is_role_assigned_via_jwt):
        """
        Verify the viewset returns catalogs of all enterprises for admins with wildcard permission.
        """
        if is_role_assigned_via_jwt:
            self.assign_catalog_admin_jwt_role('*')
        else:
            # This will cause a feature role assignment to be created with a null enterprise UUID,
            # which is interpretted as having access to catalogs of ANY enterprise.
            self.assign_catalog_admin_feature_role(enterprise_uuids=[None])

        catalog_b = EnterpriseCatalogFactory(enterprise_uuid=uuid.uuid4())
        catalog_c = EnterpriseCatalogFactory(enterprise_uuid=uuid.uuid4())

        url = reverse('api:v1:enterprise-catalog-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 3)
        results = response.data['results']
        self.assertEqual(uuid.UUID(results[0]['uuid']), self.enterprise_catalog.uuid)
        self.assertEqual(uuid.UUID(results[1]['uuid']), catalog_b.uuid)
        self.assertEqual(uuid.UUID(results[2]['uuid']), catalog_c.uuid)

    def test_list_unauthorized_catalog_learner(self):
        """
        Verify the viewset rejects list for catalog learners
        """
        self.set_up_catalog_learner()
        url = reverse('api:v1:enterprise-catalog-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class EnterpriseCatalogCsvDataViewTests(APITestMixin):
    """
    Tests for the CatalogCsvDataView view.
    """
    mock_algolia_hits = {'hits': [{
        'aggregation_key': 'course:MITx+18.01.2x',
        'key': 'MITx+18.01.2x',
        'language': 'English',
        'level_type': 'Intermediate',
        'content_type': 'course',
        'partners': [
            {'name': 'Massachusetts Institute of Technology',
             'logo_image_url': 'https://edx.org/image.png'}
        ],
        'programs': ['Professional Certificate'],
        'program_titles': ['Totally Awesome Program'],
        'short_description': 'description',
        'subjects': ['Math'],
        'skills': [{
            'name': 'Probability And Statistics',
            'description': 'description'
        }, {
            'name': 'Engineering Design Process',
            'description': 'description'
        }],
        'title': 'Calculus 1B: Integration',
        'marketing_url': 'edx.org/foo-bar',
        'first_enrollable_paid_seat_price': 100,
        'advertised_course_run': {
            'key': 'MITx/18.01.2x/3T2015',
            'pacing_type': 'instructor_paced',
            'start': '2015-09-08T00:00:00Z',
            'end': '2015-09-08T00:00:01Z',
            'upgrade_deadline': 32503680000.0,
            'max_effort': 10,
            'min_effort': 1,
            'weeks_to_complete': 1,
        },
        'outcome': '<p>learn</p>',
        'prerequisites_raw': '<p>interest</p>',
        'objectID': 'course-3543aa4e-3c64-4d9a-a343-5d5eda1dacf8-catalog-query-uuids-0'
    },
        {
        'aggregation_key': 'course:MITx+19',
        'key': 'MITx+19',
        'language': 'English',
        'level_type': 'Intermediate',
        'objectID': 'course-3543aa4e-3c64-4d9a-a343-5d5eda1dacf9-catalog-query-uuids-0'
    },
        {
        'aggregation_key': 'course:MITx+20',
        'language': 'English',
        'level_type': 'Intermediate',
        'objectID': 'course-3543aa4e-3c64-4d9a-a343-5d5eda1dacf7-catalog-query-uuids-0'
    }
    ]}

    expected_result_data = 'Title,Partner Name,Start,End,Verified Upgrade Deadline,Program Type,Program Name,Pacing,' \
                           'Level,Price,Language,URL,Short Description,Subjects,Key,Short Key,Skills,Min Effort,' \
                           'Max Effort,Length,What You’ll Learn,Pre-requisites\r\nCalculus 1B: ' \
                           'Integration,Massachusetts Institute of Technology,2015-09-08,' \
                           '2015-09-08,3000-01-01,Professional Certificate,Totally ' \
                           'Awesome Program,instructor_paced,Intermediate,100,English,edx.org/foo-bar,description,' \
                           'Math,MITx/18.01.2x/3T2015,course:MITx+18.01.2x,"Probability And Statistics, ' \
                           'Engineering Design Process",1,10,1,learn,interest\r\n'

    def setUp(self):
        super().setUp()
        self.set_up_staff_user()

    def _get_contains_content_base_url(self):
        """
        Helper to construct the base url for the contains_content_items endpoint
        """
        return reverse('api:v1:catalog-csv-data')

    def _get_mock_algolia_hits_with_missing_values(self):
        mock_hits_missing_values = copy.deepcopy(self.mock_algolia_hits)
        mock_hits_missing_values['hits'][0]['advertised_course_run'].pop('upgrade_deadline')
        mock_hits_missing_values['hits'][0].pop('marketing_url')
        mock_hits_missing_values['hits'][0].pop('first_enrollable_paid_seat_price')
        mock_hits_missing_values['hits'][0]['advertised_course_run']['end'] = None
        return mock_hits_missing_values

    def test_facet_validation(self):
        """
        Tests that the view validates Algolia facets provided by query params
        """
        url = self._get_contains_content_base_url()
        invalid_facets = 'invalid_facet=wrong'
        response = self.client.get(f'{url}?{invalid_facets}')
        assert response.status_code == 400
        assert response.data == "Error: invalid facet(s): ['invalid_facet'] provided."

    @mock.patch('enterprise_catalog.apps.api.v1.views.catalog_csv_data.get_initialized_algolia_client')
    def test_valid_facet_validation(self, mock_algolia_client):
        """
        Tests a successful request with facets.
        """
        mock_algolia_client.return_value.algolia_index.search.side_effect = [self.mock_algolia_hits, {'hits': []}]
        url = self._get_contains_content_base_url()
        facets = 'language=English'
        response = self.client.get(f'{url}?{facets}')
        assert response.status_code == 200

        expected_response = {
            'csv_data': self.expected_result_data
        }
        assert response.data == expected_response

    @mock.patch('enterprise_catalog.apps.api.v1.views.catalog_csv_data.get_initialized_algolia_client')
    def test_csv_row_construction_handles_missing_values(self, mock_algolia_client):
        """
        Tests that the view properly handles situations where data is missing from the Algolia hit.
        """
        mock_side_effects = [self._get_mock_algolia_hits_with_missing_values(), {'hits': []}]
        mock_algolia_client.return_value.algolia_index.search.side_effect = mock_side_effects
        url = self._get_contains_content_base_url()
        facets = 'language=English'
        response = self.client.get(f'{url}?{facets}')
        assert response.status_code == 200
        excpected_csv_data = 'Title,Partner Name,Start,End,Verified Upgrade Deadline,Program Type,Program Name,' \
                             'Pacing,Level,Price,Language,URL,Short Description,Subjects,Key,Short Key,Skills,' \
                             'Min Effort,Max Effort,Length,What You’ll Learn,Pre-requisites\r\n' \
                             'Calculus 1B: Integration,Massachusetts Institute of Technology,2015-09-08,' \
                             ',,Professional Certificate,Totally Awesome ' \
                             'Program,instructor_paced,Intermediate,,English,,description,' \
                             'Math,MITx/18.01.2x/3T2015,course:MITx+18.01.2x,"Probability And Statistics, ' \
                             'Engineering Design Process",1,10,1,learn,interest\r\n'
        expected_response = {
            'csv_data': excpected_csv_data
        }
        assert response.data == expected_response


class EnterpriseCatalogWorkbookViewTests(APITestMixin):
    """
    Tests for the CatalogWorkbookView view.
    """
    mock_algolia_hits = {'hits': [{
        'aggregation_key': 'course:MITx+18.01.2x',
        'key': 'MITx+18.01.2x',
        'language': 'English',
        'level_type': 'Intermediate',
        'content_type': 'course',
        'partners': [
            {'name': 'Massachusetts Institute of Technology',
             'logo_image_url': 'https://edx.org/image.png'}
        ],
        'programs': ['Professional Certificate'],
        'program_titles': ['Totally Awesome Program'],
        'short_description': 'description',
        'subjects': ['Math'],
        'skills': [{
            'name': 'Probability And Statistics',
            'description': 'description'
        }, {
            'name': 'Engineering Design Process',
            'description': 'description'
        }],
        'title': 'Calculus 1B: Integration',
        'marketing_url': 'edx.org/foo-bar',
        'first_enrollable_paid_seat_price': 100,
        'advertised_course_run': {
            'key': 'MITx/18.01.2x/3T2015',
            'pacing_type': 'instructor_paced',
            'start': '2015-09-08T00:00:00Z',
            'end': '2015-09-08T00:00:01Z',
            'upgrade_deadline': 32503680000.0,
            'max_effort': 10,
            'min_effort': 1,
            'weeks_to_complete': 1,
        },
        'course_runs': [
            {
                'key': 'MITx/18.01.2x/3T2015',
                'pacing_type': 'instructor_paced',
                'start': '2015-09-08T00:00:00Z',
                'end': '2015-09-08T00:00:01Z',
                'upgrade_deadline': 32503680000.0,
                'max_effort': 10,
                'min_effort': 1,
                'weeks_to_complete': 1,
            }
        ],
        'outcome': '<p>learn</p>',
        'prerequisites_raw': '<p>interest</p>',
        'objectID': 'course-3543aa4e-3c64-4d9a-a343-5d5eda1dacf8-catalog-query-uuids-0'
    },
        {
        'aggregation_key': 'course:MITx+19',
        'key': 'MITx+19',
        'language': 'English',
        'level_type': 'Intermediate',
        'objectID': 'course-3543aa4e-3c64-4d9a-a343-5d5eda1dacf9-catalog-query-uuids-0'
    },
        {
        'aggregation_key': 'course:MITx+20',
        'language': 'English',
        'level_type': 'Intermediate',
        'objectID': 'course-3543aa4e-3c64-4d9a-a343-5d5eda1dacf7-catalog-query-uuids-0'
    }
    ]}

    def setUp(self):
        super().setUp()
        self.set_up_staff_user()

    def _get_contains_content_base_url(self):
        """
        Helper to construct the base url for the contains_content_items endpoint
        """
        return reverse('api:v1:catalog-workbook')

    @mock.patch('enterprise_catalog.apps.api.v1.views.catalog_workbook.get_initialized_algolia_client')
    def test_empty_results_error(self, mock_algolia_client):
        """
        Tests when algolia returns no hits.
        """
        mock_algolia_client.return_value.algolia_index.search.side_effect = [{'hits': []}]
        url = self._get_contains_content_base_url()
        facets = 'language=English'
        response = self.client.get(f'{url}?{facets}')
        assert response.status_code == 400

    @mock.patch('enterprise_catalog.apps.api.v1.views.catalog_workbook.get_initialized_algolia_client')
    def test_success(self, mock_algolia_client):
        """
        Tests basic, successful output.
        """
        mock_algolia_client.return_value.algolia_index.search.side_effect = [self.mock_algolia_hits, {'hits': []}]
        url = self._get_contains_content_base_url()
        facets = 'language=English'
        response = self.client.get(f'{url}?{facets}')
        assert response.status_code == 200


class EnterpriseCatalogContainsContentItemsTests(APITestMixin):
    """
    Tests on the contains_content_items on enterprise catalogs endpoint
    """

    def setUp(self):
        super().setUp()
        # Set up catalog.has_learner_access permissions
        self.set_up_catalog_learner()
        self.enterprise_catalog = EnterpriseCatalogFactory(enterprise_uuid=self.enterprise_uuid)

    def _get_contains_content_base_url(self, enterprise_catalog):
        """
        Helper to construct the base url for the contains_content_items endpoint
        """
        return reverse('api:v1:enterprise-catalog-contains-content-items', kwargs={'uuid': enterprise_catalog.uuid})

    def test_contains_content_items_no_params(self):
        """
        Verify the contains_content_items endpoint errors if no parameters are provided
        """
        response = self.client.get(self._get_contains_content_base_url(self.enterprise_catalog))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_contains_content_items_unauthorized_incorrect_jwt_context(self):
        """
        Verify the contains_content_items endpoint rejects users with an invalid JWT context (i.e., enterprise uuid)
        """
        enterprise_catalog = EnterpriseCatalogFactory()
        self.remove_role_assignments()
        url = self._get_contains_content_base_url(enterprise_catalog) + '?course_run_ids=fakeX'
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_contains_content_items_implicit_access(self):
        """
        Verify the contains_content_items endpoint responds with 200 OK for user with implicit JWT access
        """
        self.remove_role_assignments()
        url = self._get_contains_content_base_url(self.enterprise_catalog) + '?course_run_ids=fakeX'
        self.assert_correct_contains_response(url, False)

    def test_contains_content_items_no_catalog_query(self):
        """
        Verify the contains_content_items endpoint returns False if there is no associated catalog query
        """
        no_catalog_query_catalog = EnterpriseCatalogFactory(
            catalog_query=None,
            enterprise_uuid=self.enterprise_uuid,
        )
        url = self._get_contains_content_base_url(no_catalog_query_catalog) + '?program_uuids=test-uuid'
        self.assert_correct_contains_response(url, False)

    def test_contains_content_items_keys_in_catalog(self):
        """
        Verify the contains_content_items endpoint returns True if the keys are explicitly in the catalog
        """
        content_key = 'test-key'
        associated_metadata = ContentMetadataFactory(content_key=content_key)
        self.add_metadata_to_catalog(self.enterprise_catalog, [associated_metadata])

        url = self._get_contains_content_base_url(self.enterprise_catalog) + '?course_run_ids=' + content_key
        self.assert_correct_contains_response(url, True)

    def test_contains_content_items_parent_keys_in_catalog(self):
        """
        Verify the contains_content_items endpoint returns True if the parent's key is in the catalog
        """
        parent_metadata = ContentMetadataFactory(content_key='parent-key')
        associated_metadata = ContentMetadataFactory(
            content_key='child-key+101x',
            parent_content_key=parent_metadata.content_key
        )
        self.add_metadata_to_catalog(self.enterprise_catalog, [associated_metadata])

        query_string = '?course_run_ids=' + parent_metadata.content_key
        url = self._get_contains_content_base_url(self.enterprise_catalog) + query_string
        self.assert_correct_contains_response(url, True)

    def test_contains_content_items_course_run_keys_in_catalog(self):
        """
        Verify the contains_content_items endpoint returns True if a course run's key is in the catalog
        """
        content_key = 'course-content-key'
        course_run_content_key = 'course-run-content-key'
        associated_course_metadata = ContentMetadataFactory(
            content_key=content_key,
            json_metadata={
                'key': content_key,
                'course_runs': [{'key': course_run_content_key}],
            }
        )
        # create content metadata for course run associated with above course
        ContentMetadataFactory(content_key=course_run_content_key, parent_content_key=content_key)
        self.add_metadata_to_catalog(self.enterprise_catalog, [associated_course_metadata])

        url = self._get_contains_content_base_url(self.enterprise_catalog) + '?course_run_ids=' + course_run_content_key
        self.assert_correct_contains_response(url, True)

    def test_contains_content_items_keys_not_in_catalog(self):
        """
        Verify the contains_content_items endpoint returns False if neither it or its parent's keys are in the catalog
        """
        associated_metadata = ContentMetadataFactory(content_key='some-unrelated-key')
        self.add_metadata_to_catalog(self.enterprise_catalog, [associated_metadata])

        url = self._get_contains_content_base_url(self.enterprise_catalog) + '?course_run_ids=' + 'test-key'
        self.assert_correct_contains_response(url, False)


@ddt.ddt
class EnterpriseCatalogGetContentMetadataTests(APITestMixin):
    """
    Tests on the get_content_metadata endpoint
    """

    def setUp(self):
        super().setUp()
        # Set up catalog.has_learner_access permissions
        self.set_up_catalog_learner()
        self.enterprise_catalog = EnterpriseCatalogFactory(enterprise_uuid=self.enterprise_uuid)
        # Delete any existing ContentMetadata records.
        ContentMetadata.objects.all().delete()

    def _get_content_metadata_url(self, enterprise_catalog):
        """
        Helper to get the get_content_metadata endpoint url for a given catalog
        """
        return reverse('api:v1:get-content-metadata', kwargs={'uuid': enterprise_catalog.uuid})

    def _get_expected_json_metadata(self, content_metadata, learner_portal_enabled):
        """
        Helper to get the expected json_metadata from the passed in content_metadata instance
        """
        content_type = content_metadata.content_type
        json_metadata = content_metadata.json_metadata.copy()

        json_metadata['content_last_modified'] = content_metadata.modified.isoformat()[:-6] + 'Z'
        if learner_portal_enabled and content_type in (COURSE, COURSE_RUN):
            enrollment_url = '{}/{}/course/{}?{}utm_medium=enterprise&utm_source={}'
        else:
            enrollment_url = '{}/enterprise/{}/{}/{}/enroll/?catalog={}&utm_medium=enterprise&utm_source={}'
        marketing_url = '{}?utm_medium=enterprise&utm_source={}'
        xapi_activity_id = '{}/xapi/activities/{}/{}'

        if json_metadata.get('uuid'):
            json_metadata['uuid'] = str(json_metadata.get('uuid'))

        if json_metadata.get('marketing_url'):
            json_metadata['marketing_url'] = marketing_url.format(
                json_metadata['marketing_url'],
                slugify(self.enterprise_catalog.enterprise_name),
            )

        if content_type in (COURSE, COURSE_RUN):
            json_metadata['xapi_activity_id'] = xapi_activity_id.format(
                settings.LMS_BASE_URL,
                content_type,
                json_metadata.get('key'),
            )

        # course
        if content_type == COURSE:
            course_key = json_metadata.get('key')
            course_runs = json_metadata.get('course_runs') or []
            if learner_portal_enabled:
                course_enrollment_url = enrollment_url.format(
                    settings.ENTERPRISE_LEARNER_PORTAL_BASE_URL,
                    self.enterprise_slug,
                    course_key,
                    '',
                    slugify(self.enterprise_catalog.enterprise_name),
                )
                json_metadata['enrollment_url'] = course_enrollment_url
                for course_run in course_runs:
                    course_run_key = quote_plus(course_run.get('key'))
                    course_run_key_param = f'course_run_key={course_run_key}&'
                    course_run_enrollment_url = enrollment_url.format(
                        settings.ENTERPRISE_LEARNER_PORTAL_BASE_URL,
                        self.enterprise_slug,
                        course_key,
                        course_run_key_param,
                        slugify(self.enterprise_catalog.enterprise_name),
                    )
                    course_run.update({'enrollment_url': course_run_enrollment_url})
            else:
                course_enrollment_url = enrollment_url.format(
                    settings.LMS_BASE_URL,
                    self.enterprise_catalog.enterprise_uuid,
                    COURSE,
                    course_key,
                    self.enterprise_catalog.uuid,
                    slugify(self.enterprise_catalog.enterprise_name),
                )
                json_metadata['enrollment_url'] = course_enrollment_url
                for course_run in course_runs:
                    course_run_enrollment_url = enrollment_url.format(
                        settings.LMS_BASE_URL,
                        self.enterprise_catalog.enterprise_uuid,
                        COURSE,
                        course_run.get('key'),
                        self.enterprise_catalog.uuid,
                        slugify(self.enterprise_catalog.enterprise_name),
                    )
                    course_run.update({'enrollment_url': course_run_enrollment_url})

            json_metadata['course_runs'] = course_runs
            json_metadata['active'] = is_any_course_run_active(course_runs)

        # course run
        if content_type == COURSE_RUN:
            if learner_portal_enabled:
                course_key = get_parent_content_key(json_metadata)
                course_run_key = quote_plus(json_metadata.get('key'))
                course_run_key_param = f'course_run_key={course_run_key}&'
                course_run_enrollment_url = enrollment_url.format(
                    settings.ENTERPRISE_LEARNER_PORTAL_BASE_URL,
                    self.enterprise_slug,
                    course_key,
                    course_run_key_param,
                    slugify(self.enterprise_catalog.enterprise_name),
                )
                json_metadata['enrollment_url'] = course_run_enrollment_url
            else:
                course_run_enrollment_url = enrollment_url.format(
                    settings.LMS_BASE_URL,
                    self.enterprise_catalog.enterprise_uuid,
                    COURSE,
                    json_metadata.get('key'),
                    self.enterprise_catalog.uuid,
                    slugify(self.enterprise_catalog.enterprise_name),
                )
                json_metadata['enrollment_url'] = course_run_enrollment_url

        # program
        if content_type == PROGRAM:
            program_enrollment_url = enrollment_url.format(
                settings.LMS_BASE_URL,
                self.enterprise_catalog.enterprise_uuid,
                PROGRAM,
                json_metadata.get('key'),
                self.enterprise_catalog.uuid,
                slugify(self.enterprise_catalog.enterprise_name),
            )
            json_metadata['enrollment_url'] = program_enrollment_url

        return json_metadata

    def test_get_content_metadata_unauthorized_invalid_permissions(self):
        """
        Verify the get_content_metadata endpoint rejects users with invalid permissions
        """
        self.set_up_invalid_jwt_role()
        self.remove_role_assignments()
        url = self._get_content_metadata_url(self.enterprise_catalog)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_content_metadata_unauthorized_incorrect_jwt_context(self):
        """
        Verify the get_content_metadata endpoint rejects catalog learners
        with an incorrect JWT context (i.e., enterprise uuid)
        """
        enterprise_catalog = EnterpriseCatalogFactory()
        self.remove_role_assignments()
        url = self._get_content_metadata_url(enterprise_catalog)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_content_metadata_implicit_access(self):
        """
        Verify the get_content_metadata endpoint responds with 200 OK for
        user with implicit JWT access
        """
        self.remove_role_assignments()
        url = self._get_content_metadata_url(self.enterprise_catalog)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_get_content_metadata_no_catalog_query(self):
        """
        Verify the get_content_metadata endpoint returns no results if the catalog has no catalog query
        """
        no_catalog_query_catalog = EnterpriseCatalogFactory(
            catalog_query=None,
            enterprise_uuid=self.enterprise_uuid,
        )
        url = self._get_content_metadata_url(no_catalog_query_catalog)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['results'], [])

    @mock.patch('enterprise_catalog.apps.api_client.enterprise_cache.EnterpriseApiClient')
    @ddt.data(
        False,
        True
    )
    def test_get_content_metadata_content_filters(self, learner_portal_enabled, mock_api_client):
        """
        Test that the get_content_metadata view GET view will filter provided content_keys (up to a limit)
        """
        mock_api_client.return_value.get_enterprise_customer.return_value = {
            'slug': self.enterprise_slug,
            'enable_learner_portal': learner_portal_enabled,
            'modified': str(datetime.now().replace(tzinfo=pytz.UTC)),
        }
        ContentMetadataFactory.reset_sequence(10)
        metadata = ContentMetadataFactory.create_batch(api_settings.PAGE_SIZE)
        filtered_content_keys = []
        url = self._get_content_metadata_url(self.enterprise_catalog)
        for filter_content_key_index in range(int(api_settings.PAGE_SIZE / 2)):
            filtered_content_keys.append(metadata[filter_content_key_index].content_key)
            url += f"&content_keys={metadata[filter_content_key_index].content_key}"

        self.add_metadata_to_catalog(self.enterprise_catalog, metadata)
        response = self.client.get(
            url,
            {'content_keys': filtered_content_keys}
        )
        assert response.data.get('count') == int(api_settings.PAGE_SIZE / 2)
        for result in response.data.get('results'):
            assert result.get('key') in filtered_content_keys

    @mock.patch('enterprise_catalog.apps.api_client.enterprise_cache.EnterpriseApiClient')
    @ddt.data(
        False,
        True
    )
    def test_get_content_metadata(self, learner_portal_enabled, mock_api_client):
        """
        Verify the get_content_metadata endpoint returns all the metadata associated with a particular catalog
        """
        mock_api_client.return_value.get_enterprise_customer.return_value = {
            'slug': self.enterprise_slug,
            'enable_learner_portal': learner_portal_enabled,
            'modified': str(datetime.now().replace(tzinfo=pytz.UTC)),
        }
        # The ContentMetadataFactory creates content with keys that are generated using a string builder with a
        # factory sequence (index is appended onto each content key). The results are sorted by key which creates
        # an unexpected sorting of [key0, key1, key10, key2, ...] so the test fails on
        # self.assertEqual(actual_metadata, expected_metadata[:-1]). By resetting the factory sequence to start at
        # 10 we avoid that sorting issue.
        ContentMetadataFactory.reset_sequence(10)
        # Create enough metadata to force pagination
        metadata = ContentMetadataFactory.create_batch(api_settings.PAGE_SIZE + 1)
        self.add_metadata_to_catalog(self.enterprise_catalog, metadata)
        url = self._get_content_metadata_url(self.enterprise_catalog)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        self.assertEqual((response_data['count']), api_settings.PAGE_SIZE + 1)
        self.assertEqual(uuid.UUID(response_data['uuid']), self.enterprise_catalog.uuid)
        self.assertEqual(response_data['title'], self.enterprise_catalog.title)
        self.assertEqual(uuid.UUID(response_data['enterprise_customer']), self.enterprise_catalog.enterprise_uuid)

        second_page_response = self.client.get(response_data['next'])
        self.assertEqual(second_page_response.status_code, status.HTTP_200_OK)
        second_response_data = second_page_response.json()
        self.assertIsNone(second_response_data['next'])

        # Check that the union of both pages' data is equal to the whole set of metadata
        expected_metadata = sorted([
            self._get_expected_json_metadata(item, learner_portal_enabled)
            for item in metadata
        ], key=itemgetter('key'))
        actual_metadata = sorted(
            response_data['results'] + second_response_data['results'],
            key=itemgetter('key')
        )

        self.assertEqual(
            json.dumps(actual_metadata, sort_keys=True),
            json.dumps(expected_metadata, sort_keys=True),
        )

    @mock.patch('enterprise_catalog.apps.api_client.enterprise_cache.EnterpriseApiClient')
    @ddt.data(
        False,
        True
    )
    def test_get_content_metadata_traverse_pagination(self, learner_portal_enabled, mock_api_client):
        """
        Verify the get_content_metadata endpoint returns all metadata on one page if the traverse pagination query
        parameter is added.
        """
        mock_api_client.return_value.get_enterprise_customer.return_value = {
            'slug': self.enterprise_slug,
            'enable_learner_portal': learner_portal_enabled,
            'modified': str(datetime.now().replace(tzinfo=pytz.UTC)),
        }
        # Create enough metadata to force pagination (if the query parameter wasn't sent)
        metadata = ContentMetadataFactory.create_batch(api_settings.PAGE_SIZE + 1)
        self.add_metadata_to_catalog(self.enterprise_catalog, metadata)
        url = self._get_content_metadata_url(self.enterprise_catalog) + '?traverse_pagination=1'
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response_data = response.json()
        self.assertEqual((response_data['count']), api_settings.PAGE_SIZE + 1)
        self.assertEqual(uuid.UUID(response_data['uuid']), self.enterprise_catalog.uuid)
        self.assertEqual(response_data['title'], self.enterprise_catalog.title)
        self.assertEqual(uuid.UUID(response_data['enterprise_customer']), self.enterprise_catalog.enterprise_uuid)

        # Check that the page contains all the metadata
        expected_metadata = [self._get_expected_json_metadata(item, learner_portal_enabled) for item in metadata]
        actual_metadata = response_data['results']
        self.assertCountEqual(actual_metadata, expected_metadata)


class EnterpriseCatalogRefreshDataFromDiscoveryTests(APITestMixin):
    """
    Tests for the update catalog metadata view
    """

    def setUp(self):
        super().setUp()
        self.set_up_staff()
        self.catalog_query = CatalogQueryFactory()
        self.enterprise_catalog = EnterpriseCatalogFactory(
            enterprise_uuid=self.enterprise_uuid,
            catalog_query=self.catalog_query,
        )

    @mock.patch('enterprise_catalog.apps.api.v1.views.enterprise_catalog_refresh_data_from_discovery.chain')
    @mock.patch(
        'enterprise_catalog.apps.api.v1.views.enterprise_catalog_refresh_data_from_discovery.'
        'update_catalog_metadata_task'
    )
    @mock.patch(
        'enterprise_catalog.apps.api.v1.views.enterprise_catalog_refresh_data_from_discovery.'
        'update_full_content_metadata_task'
    )
    @mock.patch(
        'enterprise_catalog.apps.api.v1.views.enterprise_catalog_refresh_data_from_discovery.'
        'index_enterprise_catalog_in_algolia_task'
    )
    def test_refresh_catalog(
        self,
        mock_index_task,
        mock_update_full_metadata_task,
        mock_update_metadata_task,
        mock_chain,
    ):
        """
        Verify the refresh_metadata endpoint correctly calls the chain of updating/indexing tasks.
        """
        # Mock the submitted task id for proper rendering
        mock_chain().apply_async().task_id = 1
        # Reset the call count since it was called in the above mock
        mock_chain.reset_mock()

        url = reverse('api:v1:update-enterprise-catalog', kwargs={'uuid': self.enterprise_catalog.uuid})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Note that since we're mocking celery's chain, the return values from the previous task don't get passed to
        # the next one, although we do use that functionality in the real view
        mock_chain.assert_called_once_with(
            mock_update_metadata_task.si(self.catalog_query.id),
            mock_update_full_metadata_task.si(),
            mock_index_task.si(),
        )

    def test_refresh_catalog_on_get_returns_405_not_allowed(self):
        """
        Verify the refresh_metadata endpoint does not update the catalog metadata with a get request
        """
        url = reverse('api:v1:update-enterprise-catalog', kwargs={'uuid': self.enterprise_catalog.uuid})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_refresh_catalog_on_invalid_uuid_returns_400_bad_request(self):
        """
        Verify the refresh_metadata endpoint returns an HTTP_400_BAD_REQUEST status when passed an invalid ID
        """
        random_uuid = uuid.uuid4()
        url = reverse('api:v1:update-enterprise-catalog', kwargs={'uuid': random_uuid})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class EnterpriseCustomerViewSetTests(APITestMixin):
    """
    Tests for the EnterpriseCustomerViewSet
    """

    def setUp(self):
        super().setUp()
        # clean up any stale test objects
        CatalogQuery.objects.all().delete()
        ContentMetadata.objects.all().delete()
        EnterpriseCatalog.objects.all().delete()

        self.enterprise_catalog = EnterpriseCatalogFactory(enterprise_uuid=self.enterprise_uuid)

        # Set up catalog.has_learner_access permissions
        self.set_up_catalog_learner()

    def tearDown(self):
        super().tearDown()
        # clean up any stale test objects
        ContentMetadataToQueries.all_objects.all().hard_delete()
        CatalogQuery.objects.all().delete()
        ContentMetadata.objects.all().delete()
        EnterpriseCatalog.objects.all().delete()

    def _get_contains_content_base_url(self, enterprise_uuid=None):
        """
        Helper to construct the base url for the contains_content_items endpoint
        """
        return reverse(
            'api:v1:enterprise-customer-contains-content-items',
            kwargs={'enterprise_uuid': enterprise_uuid or self.enterprise_uuid},
        )

    def _get_generate_diff_base_url(self, enterprise_catalog_uuid=None):
        """
        Helper to construct the base url for the catalog `generate_diff` endpoint
        """
        return reverse(
            'api:v1:generate-catalog-diff',
            kwargs={'uuid': enterprise_catalog_uuid or self.enterprise_catalog.uuid},
        )

    def test_generate_diff_unauthorized_non_catalog_learner(self):
        """
        Verify the generate_diff endpoint rejects users that are not catalog learners
        """
        self.set_up_invalid_jwt_role()
        self.remove_role_assignments()
        url = self._get_generate_diff_base_url()
        response = self.client.post(url, content_type='application/json',)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_generate_diff_get_supports_up_to_max_content_keys(self):
        """
        Test that GET requests to generate_diff supports up to but not more than the max number of content keys.
        """
        content = ContentMetadataFactory()
        self.add_metadata_to_catalog(self.enterprise_catalog, [content])
        url = self._get_generate_diff_base_url() + '?content_keys=key'

        for key in range(150):
            url += f"&content_keys=key{key}"

        response = self.client.get(url)
        assert response.status_code == 400
        assert response.data == 'catalog_diff GET requests supports up to 100. If more content keys required, please ' \
                                'use a POST body.'

    @mock.patch('enterprise_catalog.apps.api_client.enterprise_cache.EnterpriseApiClient')
    def test_generate_diff_matched_modified_uses_content(self, mock_api_client):
        """
        Test that the generate_diff endpoint, when matching content keys, takes the content modified times into
        consideration when generating the matched key's `date_updated`.
        """
        now = self.enterprise_catalog.modified
        customer_modified = str(now - timedelta(hours=1))
        mock_api_client.return_value.get_enterprise_customer.return_value = {
            'slug': self.enterprise_slug,
            'enable_learner_portal': True,
            'modified': customer_modified,
        }
        content_modified = now + timedelta(hours=1)
        content = ContentMetadataFactory(modified=content_modified)

        self.add_metadata_to_catalog(self.enterprise_catalog, [content])
        url = self._get_generate_diff_base_url()
        response = self.client.post(
            url,
            data=json.dumps({"content_keys": [content.content_key]}),
            content_type='application/json',
        )
        assert response.data.get('items_found')[0].get('date_updated') == content_modified

    @mock.patch('enterprise_catalog.apps.api_client.enterprise_cache.EnterpriseApiClient')
    def test_generate_diff_matched_modified_uses_customer(self, mock_api_client):
        """
        Test that the generate_diff endpoint, when matching content keys, takes the customer's modified times into
        consideration when generating the matched key's `date_updated`.
        """
        now = self.enterprise_catalog.modified
        customer_modified = now + timedelta(hours=1)
        customer_modified_str = str(customer_modified)
        mock_api_client.return_value.get_enterprise_customer.return_value = {
            'slug': self.enterprise_slug,
            'enable_learner_portal': True,
            'modified': customer_modified_str,
        }
        content = ContentMetadataFactory(modified=now - timedelta(hours=1))

        self.add_metadata_to_catalog(self.enterprise_catalog, [content])
        url = self._get_generate_diff_base_url()
        response = self.client.post(
            url,
            data=json.dumps({"content_keys": [content.content_key]}),
            content_type='application/json',
        )
        assert response.data.get('items_found')[0].get('date_updated') == customer_modified

    @mock.patch('enterprise_catalog.apps.api_client.enterprise_cache.EnterpriseApiClient')
    def test_generate_diff_matched_modified_uses_catalog(self, mock_api_client):
        """
        Test that the generate_diff endpoint, when matching content keys, takes the catalog modified times into
        consideration when generating the matched key's `date_updated`.
        """
        now = self.enterprise_catalog.modified
        mock_api_client.return_value.get_enterprise_customer.return_value = {
            'slug': self.enterprise_slug,
            'enable_learner_portal': True,
            'modified': str(now - timedelta(hours=1)),
        }
        content = ContentMetadataFactory(modified=now - timedelta(hours=1))

        self.add_metadata_to_catalog(self.enterprise_catalog, [content])
        url = self._get_generate_diff_base_url()

        response = self.client.post(
            url,
            data=json.dumps({"content_keys": [content.content_key]}),
            content_type='application/json',
        )
        assert response.data.get('items_found')[0].get('date_updated') == now

    @mock.patch('enterprise_catalog.apps.api_client.enterprise_cache.EnterpriseApiClient')
    def test_generate_diff_get_parses_all_buckets(self, mock_api_client):
        """
        Test that GET requests to the generate_diff endpoint behave the same as POST requests.
        """
        mock_api_client.return_value.get_enterprise_customer.return_value = {
            'slug': self.enterprise_slug,
            'enable_learner_portal': True,
            'modified': str(datetime.now().replace(tzinfo=pytz.UTC)),
        }
        content = ContentMetadataFactory()
        content2 = ContentMetadataFactory()
        content3 = ContentMetadataFactory()
        content4 = ContentMetadataFactory()

        self.add_metadata_to_catalog(self.enterprise_catalog, [content, content2, content3, content4])
        url = self._get_generate_diff_base_url()
        response = self.client.post(
            url,
            data=json.dumps({"content_keys": [content.content_key, content2.content_key, "bad+key", "bad+key2"]}),
            content_type='application/json',
        )
        assert response.status_code == 200
        response_data = response.data

        for item in response_data.get('items_not_found'):
            assert item in [{'content_key': 'bad+key'}, {'content_key': 'bad+key2'}]

        for item in response_data.get('items_not_included'):
            assert item in [{'content_key': content3.content_key}, {'content_key': content4.content_key}]

        for item in response_data.get('items_found'):
            assert item in [
                {'content_key': content.content_key, 'date_updated': content.modified},
                {'content_key': content2.content_key, 'date_updated': content2.modified}
            ]

    @mock.patch('enterprise_catalog.apps.api_client.enterprise_cache.EnterpriseApiClient')
    def test_generate_diff_returns_whole_catalog_w_empty_key_list(self, mock_api_client):
        """
        Test that the generate_diff endpoint will return all content keys under the catalog not provided under the
        `items_not_included` bucket
        """
        mock_api_client.return_value.get_enterprise_customer.return_value = {
            'slug': self.enterprise_slug,
            'enable_learner_portal': True,
            'modified': str(datetime.now().replace(tzinfo=pytz.UTC)),
        }
        content = ContentMetadataFactory()
        self.add_metadata_to_catalog(self.enterprise_catalog, [content])
        url = self._get_generate_diff_base_url()
        response = self.client.post(url, content_type='application/json')
        assert response.data.get('items_not_included') == [{'content_key': content.content_key}]
        assert not response.data.get('items_not_found')
        assert not response.data.get('items_found')

    @mock.patch('enterprise_catalog.apps.api_client.enterprise_cache.EnterpriseApiClient')
    def test_generate_diff_returns_content_items_found(self, mock_api_client):
        """
        Test that the generate_diff endpoint will return under the `items_found` bucket all content keys within the
        catalog that were provided.
        """
        mock_api_client.return_value.get_enterprise_customer.return_value = {
            'slug': self.enterprise_slug,
            'enable_learner_portal': True,
            'modified': str(datetime.now().replace(tzinfo=pytz.UTC)),
        }
        content = ContentMetadataFactory()
        content2 = ContentMetadataFactory()
        self.add_metadata_to_catalog(self.enterprise_catalog, [content, content2])

        url = self._get_generate_diff_base_url()
        response = self.client.post(
            url,
            data=json.dumps({'content_keys': [content.content_key, content2.content_key]}),
            content_type='application/json'
        )
        for item in response.data.get('items_found'):
            assert item.get('content_key') in [content.content_key, content2.content_key]
        assert not response.data.get('items_not_found')
        assert not response.data.get('items_not_included')

    @mock.patch('enterprise_catalog.apps.api_client.enterprise_cache.EnterpriseApiClient')
    def test_generate_diff_returns_content_items_not_found(self, mock_api_client):
        """
        Test that the generate_diff endpoint will return all content keys provided that were not found under the catalog
        under the `items_not_found` bucket.
        """
        mock_api_client.return_value.get_enterprise_customer.return_value = {
            'slug': self.enterprise_slug,
            'enable_learner_portal': True,
            'modified': str(datetime.now()),
        }
        key = 'bad+key'
        key2 = 'bad+key2'
        url = self._get_generate_diff_base_url()
        response = self.client.post(
            url,
            data=json.dumps({'content_keys': [key, key2]}),
            content_type='application/json'
        )
        for item in response.data.get('items_not_found'):
            assert item in [{'content_key': key}, {'content_key': key2}]
        assert not response.data.get('items_found')
        assert not response.data.get('items_not_included')

    def test_contains_content_items_unauthorized_non_catalog_learner(self):
        """
        Verify the contains_content_items endpoint rejects users that are not catalog learners
        """
        self.set_up_invalid_jwt_role()
        self.remove_role_assignments()
        url = self._get_contains_content_base_url() + '?course_run_ids=fakeX'
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_contains_content_items_unauthorized_incorrect_jwt_context(self):
        """
        Verify the contains_content_items endpoint rejects users that are catalog learners
        with an incorrect JWT context (i.e., enterprise uuid)
        """
        self.remove_role_assignments()
        base_url = self._get_contains_content_base_url(enterprise_uuid=uuid.uuid4())
        url = base_url + '?course_run_ids=fakeX'
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_contains_content_items_implicit_access(self):
        """
        Verify the contains_content_items endpoint responds with 200 OK for
        user with implicit JWT access
        """
        self.remove_role_assignments()
        url = self._get_contains_content_base_url() + '?program_uuids=fakeX'
        self.assert_correct_contains_response(url, False)

    def test_contains_content_items_no_params(self):
        """
        Verify the contains_content_items endpoint errors if no parameters are provided
        """
        response = self.client.get(self._get_contains_content_base_url())
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_contains_content_items_not_in_catalogs(self):
        """
        Verify the contains_content_items endpoint returns False if the content is not in any associated catalog
        """
        self.add_metadata_to_catalog(self.enterprise_catalog, [ContentMetadataFactory()])

        url = self._get_contains_content_base_url() + '?program_uuids=this-is-not-the-uuid-youre-looking-for'
        self.assert_correct_contains_response(url, False)

    def test_contains_content_items_in_catalogs(self):
        """
        Verify the contains_content_items endpoint returns True if the content is in any associated catalog
        """
        content_metadata = ContentMetadataFactory()
        self.add_metadata_to_catalog(self.enterprise_catalog, [content_metadata])

        # Create a second catalog that has the content we're looking for
        content_key = 'fake-key+101x'
        second_catalog = EnterpriseCatalogFactory(enterprise_uuid=self.enterprise_uuid)
        relevant_content = ContentMetadataFactory(content_key=content_key)
        self.add_metadata_to_catalog(second_catalog, [relevant_content])

        url = self._get_contains_content_base_url() + '?course_run_ids=' + content_key
        self.assert_correct_contains_response(url, True)

    def test_no_catalog_list_given_without_get_catalogs_containing_specified_content_ids_query(self):
        """
        Verify that the contains_content_items endpoint does not return a list of catalogs without a querystring
        """
        content_metadata = ContentMetadataFactory()
        self.add_metadata_to_catalog(self.enterprise_catalog, [content_metadata])

        # Create a second catalog that has the content we're looking for
        content_key = 'fake-key+101x'
        second_catalog = EnterpriseCatalogFactory(enterprise_uuid=self.enterprise_uuid)
        relevant_content = ContentMetadataFactory(content_key=content_key)
        self.add_metadata_to_catalog(second_catalog, [relevant_content])
        url = self._get_contains_content_base_url() + '?course_run_ids=' + content_key
        response = self.client.get(url)
        assert 'catalog_list' not in response.json().keys()

    def test_contains_catalog_list_with_catalog_list_param(self):
        """
        Verify the contains_content_items endpoint returns a list of catalogs the course is in if the correct
        parameter is passed
        """
        content_metadata = ContentMetadataFactory()
        self.add_metadata_to_catalog(self.enterprise_catalog, [content_metadata])

        # Create a two catalogs that have the content we're looking for
        content_key = 'fake-key+101x'
        second_catalog = EnterpriseCatalogFactory(enterprise_uuid=self.enterprise_uuid)
        relevant_content = ContentMetadataFactory(content_key=content_key)
        self.add_metadata_to_catalog(second_catalog, [relevant_content])
        url = self._get_contains_content_base_url() + '?course_run_ids=' + content_key + \
            '&get_catalog_list=True'
        self.assert_correct_contains_response(url, True)

        response = self.client.get(url)
        catalog_list = response.json()['catalog_list']
        assert set(catalog_list) == {str(second_catalog.uuid)}

    def test_contains_catalog_list_with_content_ids_param(self):
        """
        Verify the contains_content_items endpoint returns a list of catalogs the course is in if the correct
        parameter is passed
        """
        content_metadata = ContentMetadataFactory()
        self.add_metadata_to_catalog(self.enterprise_catalog, [content_metadata])

        # Create a two catalogs that have the content we're looking for
        content_key = 'fake-key+101x'
        second_catalog = EnterpriseCatalogFactory(enterprise_uuid=self.enterprise_uuid)
        relevant_content = ContentMetadataFactory(content_key=content_key)
        self.add_metadata_to_catalog(second_catalog, [relevant_content])
        url = self._get_contains_content_base_url() + '?course_run_ids=' + content_key + \
            '&get_catalogs_containing_specified_content_ids=True'
        self.assert_correct_contains_response(url, True)

        response = self.client.get(url)
        catalog_list = response.json()['catalog_list']
        assert set(catalog_list) == {str(second_catalog.uuid)}

    def test_contains_catalog_list_parent_key(self):
        """
        Verify the contains_content_items endpoint returns a list of catalogs the course is in
        """
        content_metadata = ContentMetadataFactory()
        self.add_metadata_to_catalog(self.enterprise_catalog, [content_metadata])

        # Create a two catalogs that have the content we're looking for
        parent_content_key = 'fake-parent-key+105x'
        content_key = 'fake-key+101x'
        second_catalog = EnterpriseCatalogFactory(enterprise_uuid=self.enterprise_uuid)
        relevant_content = ContentMetadataFactory(content_key=content_key, parent_content_key=parent_content_key)
        self.add_metadata_to_catalog(second_catalog, [relevant_content])
        content_key_2 = 'fake-key+102x'
        third_catalog = EnterpriseCatalogFactory(enterprise_uuid=self.enterprise_uuid)
        relevant_content = ContentMetadataFactory(content_key=content_key_2, parent_content_key=parent_content_key)
        self.add_metadata_to_catalog(third_catalog, [relevant_content])

        url = self._get_contains_content_base_url() + '?course_run_ids=' + parent_content_key + \
            '&get_catalogs_containing_specified_content_ids=True'
        response = self.client.get(url).json()
        assert response['contains_content_items'] is True
        catalog_list = response['catalog_list']
        assert set(catalog_list) == {str(second_catalog.uuid), str(third_catalog.uuid)}

    def test_contains_catalog_list_content_items_not_in_catalog(self):
        """
        Verify the contains_content_items endpoint returns a list of catalogs the course is in for multiple catalogs
        """
        content_metadata = ContentMetadataFactory()
        self.add_metadata_to_catalog(self.enterprise_catalog, [content_metadata])

        content_key = 'fake-key+101x'

        url = self._get_contains_content_base_url() + '?course_run_ids=' + content_key + \
            '&get_catalogs_containing_specified_content_ids=True'
        response = self.client.get(url)
        catalog_list = response.json()['catalog_list']
        assert catalog_list == []


@ddt.ddt
class DistinctCatalogQueriesViewTests(APITestMixin):
    """
    Tests for the DistinctCatalogQueriesView.
    """
    url = reverse('api:v1:distinct-catalog-queries')

    def setUp(self):
        super().setUp()
        self.set_up_staff()
        self.catalog_query_one = CatalogQueryFactory()
        self.enterprise_catalog_one = EnterpriseCatalogFactory(
            enterprise_uuid=self.enterprise_uuid,
            catalog_query=self.catalog_query_one,
        )

    @ddt.data(
        False,
        True,
    )
    def test_catalogs_different_uuids(self, use_different_query):
        """
        Tests that two catalogs with different CatalogQueries will return
        2 distinct CatalogQuery IDs and two catalogs with the same
        CatalogQueries will return 1 distinct CatalogQuery ID.
        """
        if use_different_query:
            catalog_query_two = CatalogQueryFactory()
        else:
            catalog_query_two = self.catalog_query_one
        enterprise_catalog_two = EnterpriseCatalogFactory(
            enterprise_uuid=self.enterprise_uuid,
            catalog_query=catalog_query_two,
        )
        request_json = {
            'enterprise_catalog_uuids': [
                str(self.enterprise_catalog_one.uuid),
                str(enterprise_catalog_two.uuid),
            ]
        }
        response = self.client.post(
            self.url,
            data=json.dumps(request_json),
            content_type='application/json',
        ).json()

        if use_different_query:
            assert response['num_distinct_query_ids'] == 2
            assert str(catalog_query_two.id) in response['catalog_uuids_by_catalog_query_id']
        else:
            assert response['num_distinct_query_ids'] == 1
        assert str(self.catalog_query_one.id) in response['catalog_uuids_by_catalog_query_id']
