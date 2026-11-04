"""Check multi-show selection, serialized coordinator runs and explicit stage handoffs."""

import copy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / 'dags'))
import radiofrance_pipeline as parent
import process_episodes as pipeline


class XcomReader:
    """Expose the small coordinator output to the real Jinja renderer."""

    def __init__(self, state: pipeline.PipelineRuns) -> None:
        self.state = state

    def xcom_pull(self, task_ids: str) -> pipeline.PipelineRuns:
        assert task_ids == 'prepare_pipeline'
        return self.state


class PipelineTests(unittest.TestCase):
    def test_parent_resolves_and_queues_registry_selection(self) -> None:
        """Freeze public registry output and keep catalogue behind all mapped show successes."""
        self.assertEqual(parent.dag.task_ids, ['select_shows', 'process_shows', 'catalogue', 'monitor_run'])
        self.assertEqual(parent.catalogue.upstream_task_ids, {'process_shows'})
        self.assertEqual(parent.dag.params['shows'], [])
        self.assertFalse(parent.dag.params['all_enabled'])
        self.assertEqual(parent.dag.params.get_param('shows').schema['examples'], parent.SHOW_CHOICES)
        self.assertEqual(pipeline.dag.params.get_param('show').schema['examples'], pipeline.SHOW_CHOICES)
        self.assertNotIn('enum', pipeline.dag.params.get_param('show').schema)
        selected = [{'slug': 'new-show', 'url': 'https://www.radiofrance.fr/franceculture/podcasts/new-show', 'enabled': True}]
        with patch.dict(os.environ, {'RF_DATABASE_PASSWORD': 'test'}), patch.object(parent, 'resolve_programmes', return_value=selected) as resolve:
            requests = parent.select_shows.function({'shows': [], 'all_enabled': False,
                'new_show_url': [selected[0]['url']]}, 'manual__2026-11-03T14:30:00+00:00')
        self.assertEqual(resolve.call_args.args[0]['new_show_url'], [selected[0]['url']])
        self.assertEqual(requests[0]['conf'], {'show': 'new-show', 'show_url': selected[0]['url']})
        self.assertNotIn('password', str(requests))
        self.assertEqual(pipeline.dag.max_active_runs, 1)
        self.assertEqual(pipeline.dag.dag_id, 'process_episodes')
        self.assertEqual(parent.shows.partial_kwargs['trigger_dag_id'], 'process_episodes')

    def test_date_range_validation_and_child_handoff(self) -> None:
        """Freeze dates for all shows and reject reversed ranges before registry writes."""
        programmes = [{'slug': 'new-show', 'url': 'https://www.radiofrance.fr/franceculture/podcasts/new-show', 'enabled': True}]
        options: dict[str, object] = {'shows': ['new-show'], 'all_enabled': False, 'new_show_url': [],
            'from_date': '2026-10-01', 'to_date': '2026-10-31'}
        with patch.dict(os.environ, {'RF_DATABASE_PASSWORD': 'test'}), patch.object(parent, 'resolve_programmes', return_value=programmes) as resolve:
            requests = parent.select_shows.function(options, 'manual__2026-11-04T12:00:00+00:00')
            self.assertEqual(requests[0]['conf'].get('from_date'), '2026-10-01')
            self.assertEqual(requests[0]['conf'].get('to_date'), '2026-10-31')
            resolve.reset_mock()
            options['from_date'] = '2026-11-01'
            with self.assertRaises(ValueError):
                parent.select_shows.function(options, 'manual__test')
            resolve.assert_not_called()
        params = {'show': 'new-show', 'show_url': programmes[0]['url'],
            'from_date': '2026-10-01', 'to_date': '2026-10-31'}
        state = pipeline.prepare_pipeline.function('manual__2026-11-04T12:00:00+00:00', params)
        download = copy.copy(pipeline.download)
        download.render_template_fields({'ti': XcomReader(state), 'params': params})
        assert download.conf is not None
        self.assertEqual(download.conf['from_date'], '2026-10-01')
        self.assertEqual(download.conf['to_date'], '2026-10-31')

    def test_single_show_selection_uses_registered_url_when_blank(self) -> None:
        """Select an existing show without manually copying its URL into the form."""
        url = 'https://www.radiofrance.fr/franceculture/podcasts/new-show'
        with patch.object(pipeline, 'resolve_programmes', return_value=[{'slug': 'new-show', 'url': url, 'enabled': True}]):
            state = pipeline.prepare_pipeline.function('manual__2026-11-03T14:30:00+00:00', {'show': 'new-show', 'show_url': ''})
        self.assertEqual(state['show_url'], url)

    def test_order_waiting_and_failure_boundaries(self) -> None:
        """Serialize a whole show, defer stage waits, and never reset existing child runs."""
        self.assertEqual(pipeline.dag.task_ids, ['prepare_pipeline', 'download', 'chunk', 'embedding', 'database'])
        previous = 'prepare_pipeline'
        for operator in (pipeline.download, pipeline.chunk, pipeline.embedding, pipeline.database):
            self.assertEqual(operator.upstream_task_ids, {previous})
            self.assertTrue(operator.wait_for_completion)
            self.assertTrue(operator.deferrable)
            self.assertTrue(operator.fail_when_dag_is_paused)
            self.assertFalse(operator.reset_dag_run)
            self.assertFalse(operator.skip_when_already_exists)
            self.assertTrue(operator.doc_md)
            previous = operator.task_id

    def test_rendered_show_and_folder_handoffs_are_isolated(self) -> None:
        """Two shows share a parent timestamp while keeping their run manifests distinct."""
        run = 'shows__manual__2026-11-03T14:30:00.123456+00:00__new-show'
        params = {'show': 'new-show', 'show_url': 'https://www.radiofrance.fr/franceculture/podcasts/new-show'}
        state = pipeline.prepare_pipeline.function(run, params)
        other = pipeline.prepare_pipeline.function(run + '-two', {'show': 'other-show',
            'show_url': 'https://www.radiofrance.fr/franceinter/podcasts/other-show'})
        self.assertEqual(state['download']['folder'], '2026-11-03_14-30-00-123456Z/download/new-show')
        self.assertNotEqual(state['chunk']['folder'], other['chunk']['folder'])
        stages: list[tuple[pipeline.TriggerDagRunOperator, pipeline.Stage]] = [
            (pipeline.download, 'download'), (pipeline.chunk, 'chunk'),
            (pipeline.embedding, 'embedding'), (pipeline.database, 'database')]
        for operator, stage in stages:
            rendered = copy.copy(operator)
            rendered.render_template_fields({'ti': XcomReader(state), 'params': params})
            self.assertEqual(rendered.trigger_run_id, state[stage]['run_id'])
            assert rendered.conf is not None
            if operator.task_id != 'database':
                self.assertEqual(rendered.conf['show'], 'new-show')
                self.assertEqual(rendered.conf['output_folder'], state[stage]['folder'])
        self.assertEqual(parent.catalogue.command, ['--output-dir', '/data/catalogue', '--replace'])
