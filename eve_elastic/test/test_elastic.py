# -*- coding: utf-8 -*-

import eve
import elasticsearch
from unittest import TestCase
from datetime import datetime
from copy import deepcopy
from flask import json
from eve.utils import config, ParsedRequest, parse_request
from ..elastic import parse_date, Elastic, get_indices, get_es
from nose.tools import raises


DOMAIN = {
    'items': {
        'schema': {
            'uri': {'type': 'string', 'unique': True},
            'name': {'type': 'string'},
            'firstcreated': {'type': 'datetime'},
            'category': {
                'type': 'string',
                'mapping': {'type': 'string', 'index': 'not_analyzed'}
            },
            'dateline': {
                'type': 'dict',
                'schema': {
                    'place': {'type': 'string'},
                    'created': {'type': 'datetime'},
                    'extra': {'type': 'dict'},
                }
            },
            'place': {
                'type': 'list',
                'schema': {
                    'type': 'dict',
                    'schema': {
                        'name': {'type': 'string'},
                        'created': {'type': 'datetime'},
                    }
                }
            },
        },
        'datasource': {
            'backend': 'elastic',
            'projection': {'firstcreated': 1, 'name': 1},
            'default_sort': [('firstcreated', -1)]
        }
    },
    'published_items': {
        'schema': {
            'published': {'type': 'datetime'}
        },
        'datasource': {
            'backend': 'elastic',
            'source': 'items',
        },
    },
    'items_with_description': {
        'schema': {
            'uri': {'type': 'string', 'unique': True},
            'name': {'type': 'string'},
            'description': {'type': 'string'},
            'firstcreated': {'type': 'datetime'},
        },
        'datasource': {
            'backend': 'elastic',
            'projection': {'firstcreated': 1, 'name': 1},
            'default_sort': [('firstcreated', -1)],
            'elastic_filter': {'exists': {'field': 'description'}},
            'aggregations': {'type': {'terms': {'field': 'name'}}}
        }
    },
    'items_with_callback_filter': {
        'schema': {
            'uri': {'type': 'string', 'unique': True}
        },
        'datasource': {
            'backend': 'elastic',
            'elastic_filter_callback': lambda: {'term': {'uri': 'foo'}}
        }
    }
}


ELASTICSEARCH_SETTINGS = {
    'settings': {
        'analysis': {
            'analyzer': {
                'phrase_prefix_analyzer': {
                    'type': 'custom',
                    'tokenizer': 'keyword',
                    'filter': ['lowercase']
                }
            }
        }
    }
}


INDEX = 'elastic_tests'
DOC_TYPE = 'items'


class TestElastic(TestCase):

    def setUp(self):
        settings = {'DOMAIN': DOMAIN}
        settings['ELASTICSEARCH_URL'] = 'http://localhost:9200'
        settings['ELASTICSEARCH_INDEX'] = INDEX
        self.app = eve.Eve(settings=settings, data=Elastic)
        with self.app.app_context():
            for resource in self.app.config['DOMAIN']:
                self.app.data.remove(resource)

    def test_parse_date(self):
        date = parse_date('2013-11-06T07:56:01.414944+00:00')
        self.assertIsInstance(date, datetime)
        self.assertEqual('07:56+0000', date.strftime('%H:%M%z'))

    def test_parse_date_with_null(self):
        date = parse_date(None)
        self.assertIsNone(date)

    def test_put_mapping(self):
        elastic = Elastic(None)
        elastic.init_app(self.app)
        elastic.put_mapping(self.app)

        mapping = elastic.get_mapping(elastic.index)[elastic.index]
        items_mapping = mapping['mappings']['items']['properties']

        self.assertIn('firstcreated', items_mapping)
        self.assertEqual('date', items_mapping['firstcreated']['type'])

        self.assertIn(config.DATE_CREATED, items_mapping)
        self.assertIn(config.LAST_UPDATED, items_mapping)

        self.assertIn('uri', items_mapping)
        self.assertIn('category', items_mapping)

        self.assertIn('dateline', items_mapping)
        dateline_mapping = items_mapping['dateline']
        self.assertIn('created', dateline_mapping['properties'])
        self.assertEqual('date', dateline_mapping['properties']['created']['type'])

        self.assertIn('place', items_mapping)
        place_mapping = items_mapping['place']
        self.assertIn('created', place_mapping['properties'])
        self.assertEqual('date', place_mapping['properties']['created']['type'])

    def test_dates_are_parsed_on_fetch(self):
        with self.app.app_context():
            ids = self.app.data.insert('items', [{'uri': 'test', 'firstcreated': '2012-10-10T11:12:13+0000'}])
            self.app.data.update('published_items', ids[0], {'published': '2012-10-10T12:12:13+0000'})
            item = self.app.data.find_one('published_items', req=None, uri='test')
            self.assertIsInstance(item['firstcreated'], datetime)
            self.assertIsInstance(item['published'], datetime)

    def test_bulk_insert(self):
        with self.app.app_context():
            (count, _errors) = self.app.data.bulk_insert('items_with_description', [
                {'uri': 'u1', 'name': 'foo', 'firstcreated': '2012-01-01T11:12:13+0000'},
                {'uri': 'u2', 'name': 'foo', 'firstcreated': '2013-01-01T11:12:13+0000'},
                {'uri': 'u3', 'name': 'foo', 'description': 'test', 'firstcreated': '2013-01-01T11:12:13+0000'},
            ])
            self.assertEquals(3, count)
            self.assertEquals(0, len(_errors))

    def test_query_filter_with_filter_dsl_and_schema_filter(self):
        with self.app.app_context():
            self.app.data.insert('items_with_description', [
                {'uri': 'u1', 'name': 'foo', 'firstcreated': '2012-01-01T11:12:13+0000'},
                {'uri': 'u2', 'name': 'foo', 'firstcreated': '2013-01-01T11:12:13+0000'},
                {'uri': 'u3', 'name': 'foo', 'description': 'test', 'firstcreated': '2013-01-01T11:12:13+0000'},
            ])

        query_filter = {
            'term': {'name': 'foo'}
        }

        with self.app.app_context():
            req = ParsedRequest()
            req.args = {'filter': json.dumps(query_filter)}
            self.assertEqual(1, self.app.data.find('items_with_description', req, None).count())

        with self.app.app_context():
            req = ParsedRequest()
            req.args = {'q': 'bar', 'filter': json.dumps(query_filter)}
            self.assertEqual(0, self.app.data.find('items_with_description', req, None).count())

    def test_find_one_by_id(self):
        """elastic 1.0+ is using 'found' property instead of 'exists'"""
        with self.app.app_context():
            self.app.data.insert('items', [{'uri': 'test', config.ID_FIELD: 'testid'}])
            item = self.app.data.find_one('items', req=None, **{config.ID_FIELD: 'testid'})
            self.assertEqual('testid', item[config.ID_FIELD])

    def test_formating_fields(self):
        """when using elastic 1.0+ it puts all requested fields values into a list
        so instead of {"name": "test"} it returns {"name": ["test"]}"""
        with self.app.app_context():
            self.app.data.insert('items', [{'uri': 'test', 'name': 'test'}])
            item = self.app.data.find_one('items', req=None, uri='test')
            self.assertEqual('test', item['name'])

    def test_search_via_source_param(self):
        query = {'query': {'term': {'uri': 'foo'}}}
        with self.app.app_context():
            self.app.data.insert('items', [{'uri': 'foo', 'name': 'foo'}])
            self.app.data.insert('items', [{'uri': 'bar', 'name': 'bar'}])
            req = ParsedRequest()
            req.args = {'source': json.dumps(query)}
            res = self.app.data.find('items', req, None)
            self.assertEqual(1, res.count())

    def test_search_via_source_param_and_schema_filter(self):
        query = {'query': {'term': {'uri': 'foo'}}}
        with self.app.app_context():
            self.app.data.insert('items_with_description', [{'uri': 'foo', 'description': 'test', 'name': 'foo'}])
            self.app.data.insert('items_with_description', [{'uri': 'bar', 'name': 'bar'}])
            req = ParsedRequest()
            req.args = {'source': json.dumps(query)}
            res = self.app.data.find('items_with_description', req, None)
            self.assertEqual(1, res.count())

    def test_mapping_is_there_after_delete(self):
        with self.app.app_context():
            self.app.data.put_mapping(self.app)
            mapping = self.app.data.get_mapping(INDEX, DOC_TYPE)
            self.app.data.remove('items')
            self.assertEqual(mapping, self.app.data.get_mapping(INDEX, DOC_TYPE))

    def test_find_one_raw(self):
        with self.app.app_context():
            ids = self.app.data.insert('items', [{'uri': 'foo', 'name': 'foo'}])
            item = self.app.data.find_one_raw('items', ids[0])
            self.assertEqual(item['name'], 'foo')

    def test_is_empty(self):
        with self.app.app_context():
            self.assertTrue(self.app.data.is_empty('items'))
            self.app.data.insert('items', [{'uri': 'foo'}])
            self.assertFalse(self.app.data.is_empty('items'))

    def test_replace(self):
        with self.app.app_context():
            res = self.app.data.insert('items', [{'uri': 'foo'}])
            self.assertEqual(1, len(res))
            new_item = {'uri': 'bar'}
            res = self.app.data.replace('items', res.pop(), new_item)
            self.assertEqual(2, res['_version'])

    def test_sub_resource_lookup(self):
        with self.app.app_context():
            self.app.data.insert('items', [{'uri': 'foo', 'name': 'foo'}])
            req = ParsedRequest()
            req.args = {}
            self.assertEqual(1, self.app.data.find('items', req, {'name': 'foo'}).count())
            self.assertEqual(0, self.app.data.find('items', req, {'name': 'bar'}).count())

    def test_sub_resource_lookup_with_schema_filter(self):
        with self.app.app_context():
            self.app.data.insert('items_with_description', [{'uri': 'foo', 'description': 'test', 'name': 'foo'}])
            req = ParsedRequest()
            req.args = {}
            self.assertEqual(1, self.app.data.find('items_with_description', req, {'name': 'foo'}).count())
            self.assertEqual(0, self.app.data.find('items_with_description', req, {'name': 'bar'}).count())

    def test_resource_filter(self):
        with self.app.app_context():
            self.app.data.insert('items_with_description', [{'uri': 'foo', 'description': 'test'}, {'uri': 'bar'}])
            req = ParsedRequest()
            req.args = {}
            req.args['source'] = json.dumps({'query': {'filtered': {'filter': {'term': {'uri': 'bar'}}}}})
            self.assertEqual(0, self.app.data.find('items_with_description', req, None).count())

    def test_update(self):
        with self.app.app_context():
            ids = self.app.data.insert('items', [{'uri': 'foo'}])
            self.app.data.update('items', ids[0], {'uri': 'bar'})
            self.assertEqual(self.app.data.find_one('items', req=None, _id=ids[0])['uri'], 'bar')

    def test_remove_with_query(self):
        with self.app.app_context():
            self.app.data.insert('items', [{'uri': 'foo'}, {'uri': 'bar'}])
            self.app.data.remove('items', {'query': {'term': {'uri': 'bar'}}})
            req = ParsedRequest()
            req.args = {}
            self.assertEqual(1, self.app.data.find('items', req, None).count())

    def test_remove_non_existing_item(self):
        with self.app.app_context():
            self.assertEqual(self.app.data.remove('items', {'_id': 'notfound'}), None)

    @raises(elasticsearch.exceptions.ConnectionError)
    def test_it_can_use_configured_url(self):
        with self.app.app_context():
            self.app.config['ELASTICSEARCH_URL'] = 'http://localhost:9292'
            Elastic(self.app)

    def test_resource_aggregates(self):
        with self.app.app_context():
            self.app.data.insert('items_with_description', [{'uri': 'foo1', 'description': 'test', 'name': 'foo'}])
            self.app.data.insert('items_with_description', [{'uri': 'foo2', 'description': 'test1', 'name': 'foo'}])
            self.app.data.insert('items_with_description', [{'uri': 'foo3', 'description': 'test2', 'name': 'foo'}])
            self.app.data.insert('items_with_description', [{'uri': 'bar1', 'description': 'test3', 'name': 'bar'}])
            req = ParsedRequest()
            req.args = {}
            response = {}
            item1 = self.app.data.find('items_with_description', req, {'name': 'foo'})
            item2 = self.app.data.find('items_with_description', req, {'name': 'bar'})
            item1.extra(response)
            self.assertEqual(3, item1.count())
            self.assertEqual(1, item2.count())
            self.assertEqual(3, response['_aggregations']['type']['buckets'][0]['doc_count'])

    def test_put(self):
        with self.app.app_context():
            self.app.data.replace('items', 'newid', {'uri': 'foo', '_id': 'newid', '_type': 'x'})
            self.assertEqual('foo', self.app.data.find_one('items', None, _id='newid')['uri'])

    def test_args_filter(self):
        with self.app.app_context():
            self.app.data.insert('items', [{'uri': 'foo'}, {'uri': 'bar'}])
            req = ParsedRequest()
            req.args = {}
            req.args['filter'] = json.dumps({'term': {'uri': 'foo'}})
            self.assertEqual(1, self.app.data.find('items', req, None).count())

    def test_filters_with_aggregations(self):
        with self.app.app_context():
            self.app.data.insert('items_with_description', [
                {'uri': 'foo', 'name': 'test', 'description': 'a'},
                {'uri': 'bar', 'name': 'test', 'description': 'b'},
            ])

            req = ParsedRequest()
            res = {}
            cursor = self.app.data.find('items_with_description', req, {'uri': 'bar'})
            cursor.extra(res)
            self.assertEqual(1, cursor.count())
            self.assertIn({'key': 'test', 'doc_count': 1}, res['_aggregations']['type']['buckets'])
            self.assertEqual(1, res['_aggregations']['type']['buckets'][0]['doc_count'],)

    def test_filters_with_filtered_query(self):
        with self.app.app_context():
            self.app.data.insert('items', [
                {'uri': 'foo'},
                {'uri': 'bar'},
                {'uri': 'baz'},
            ])

            query = {'query': {'filtered': {'filter': {'and': [
                {'term': {'uri': 'foo'}},
                {'term': {'uri': 'bar'}},
            ]}}}}

            req = ParsedRequest()
            req.args = {'source': json.dumps(query)}
            cursor = self.app.data.find('items', req, None)
            self.assertEqual(0, cursor.count())

    def test_basic_search_query(self):
        with self.app.app_context():
            self.app.data.insert('items', [
                {'uri': 'foo'},
                {'uri': 'bar'}
            ])

        with self.app.test_request_context('/items/?q=foo'):
            req = parse_request('items')
            cursor = self.app.data.find('items', req, None)
            self.assertEquals(1, cursor.count())

    def test_phrase_search_query(self):
        with self.app.app_context():
            self.app.data.insert('items', [
                {'uri': 'foo bar'},
                {'uri': 'some text'}
            ])

        with self.app.test_request_context('/items/?q="foo bar"'):
            req = parse_request('items')
            cursor = self.app.data.find('items', req, None)
            self.assertEquals(1, cursor.count())

        with self.app.test_request_context('/items/?q="bar foo"'):
            req = parse_request('items')
            cursor = self.app.data.find('items', req, None)
            self.assertEquals(0, cursor.count())

    def test_elastic_filter_callback(self):
        with self.app.app_context():
            self.app.data.insert('items_with_callback_filter', [
                {'uri': 'foo'},
                {'uri': 'bar'},
            ])

        with self.app.test_request_context():
            req = parse_request('items_with_callback_filter')
            cursor = self.app.data.find('items_with_callback_filter', req, None)
            self.assertEqual(1, cursor.count())

    def test_elastic_sort_by_score_if_there_is_query(self):
        with self.app.app_context():
            self.app.data.insert('items', [
                {'uri': 'foo', 'name': 'foo bar'},
                {'uri': 'bar', 'name': 'foo bar'}
            ])

        with self.app.test_request_context('/items/'):
            req = parse_request('items')
            req.args = {'q': 'foo'}
            cursor = self.app.data.find('items', req, None)
            self.assertEqual(2, cursor.count())
            self.assertEqual('foo', cursor[0]['uri'])

    def test_elastic_find_default_sort_no_mapping(self):
        self.app.data.es.indices.delete_mapping(INDEX, '_all')
        with self.app.test_request_context('/items/'):
            req = parse_request('items')
            req.args = {}
            cursor = self.app.data.find('items', req, None)
            self.assertEqual(0, cursor.count())


class TestElasticSearchWithSettings(TestCase):
    index_name = 'elastic_settings'

    def setUp(self):
        settings = {
            'DOMAIN': {
                'items': {
                    'schema': {
                        'slugline': {
                            'type': 'string',
                            'mapping': {
                                'type': 'string',
                                'fields': {
                                    'phrase': {
                                        'type': 'string',
                                        'index_analyzer': 'phrase_prefix_analyzer',
                                        'search_analyzer': 'phrase_prefix_analyzer'
                                    }
                                }
                            }
                        }
                    },
                    'datasource': {
                        'backend': 'elastic'
                    }
                }
            },
            'ELASTICSEARCH_URL': 'http://localhost:9200',
            'ELASTICSEARCH_INDEX': self.index_name,
            'ELASTICSEARCH_SETTINGS': ELASTICSEARCH_SETTINGS
        }

        self.app = eve.Eve(settings=settings, data=Elastic)
        with self.app.app_context():
            for resource in self.app.config['DOMAIN']:
                self.app.data.remove(resource)

            self.es = get_es(self.app.config.get('ELASTICSEARCH_URL'))

    def tearDown(self):
        with self.app.app_context():
            get_indices(self.es).delete(self.index_name)

    def test_elastic_settings(self):
        with self.app.app_context():
            settings = self.app.data.get_settings(self.index_name)
            analyzer = settings[self.index_name]['settings']['index']['analysis']['analyzer']
            self.assertDictEqual({
                'phrase_prefix_analyzer': {
                    'tokenizer': 'keyword',
                    'filter': ['lowercase'],
                    'type': 'custom'
                }
            }, analyzer)

    def test_put_settings(self):
        with self.app.app_context():
            settings = self.app.data.get_settings(self.index_name)
            analyzer = settings[self.index_name]['settings']['index']['analysis']['analyzer']
            self.assertDictEqual({
                'phrase_prefix_analyzer': {
                    'tokenizer': 'keyword',
                    'filter': ['lowercase'],
                    'type': 'custom'
                }
            }, analyzer)

            new_settings = deepcopy(ELASTICSEARCH_SETTINGS)

            new_settings['settings']['analysis']['analyzer']['phrase_prefix_analyzer'] = {
                'type': 'custom',
                'tokenizer': 'whitespace',
                'filter': ['uppercase']
            }

            self.app.config['ELASTICSEARCH_SETTINGS'] = new_settings
            self.app.data.put_settings(self.app, self.index_name)
            settings = self.app.data.get_settings(self.index_name)
            analyzer = settings[self.index_name]['settings']['index']['analysis']['analyzer']
            self.assertDictEqual({
                'phrase_prefix_analyzer': {
                    'tokenizer': 'whitespace',
                    'filter': ['uppercase'],
                    'type': 'custom'
                }
            }, analyzer)
