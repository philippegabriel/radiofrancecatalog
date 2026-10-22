"""Offline checks for whole-pipeline stage ordering and exact artifact handoffs."""

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent.parent / 'dags'))
import radiofrance_pipeline as pipeline


class XcomReader:
    """Provide the real dictionary structure to Airflow's sandboxed template renderer."""

    def __init__(self, state: pipeline.PipelineRuns) -> None:
        self.state = state

    def xcom_pull(self, task_ids: str) -> pipeline.PipelineRuns:
        assert task_ids == 'prepare_pipeline'
        return self.state


class PipelineTests(unittest.TestCase):
    def test_order_waiting_and_failure_boundaries(self) -> None:
        """Wait without consuming a worker, and never reset existing immutable-producing runs."""
        self.assertEqual(pipeline.dag.task_ids, ['prepare_pipeline', 'download', 'chunk', 'embedding', 'database'])
        previous = 'prepare_pipeline'
        for operator in (pipeline.download, pipeline.chunk, pipeline.embedding, pipeline.database):
            self.assertEqual(operator.upstream_task_ids, {previous})
            self.assertTrue(operator.wait_for_completion)
            self.assertTrue(operator.deferrable)
            self.assertTrue(operator.fail_when_dag_is_paused)
            self.assertFalse(operator.reset_dag_run)
            self.assertFalse(operator.skip_when_already_exists)
            self.assertEqual(operator.allowed_states, ['success'])
            self.assertEqual(operator.failed_states, ['failed'])
            self.assertEqual(operator.retries, 0)
            self.assertTrue(operator.doc_md)
            previous = operator.task_id
        self.assertTrue(pipeline.dag.doc_md)

    def test_rendered_handoffs_match_child_output_convention(self) -> None:
        """Pass each predecessor's folder and create fresh run identities for a new pipeline run."""
        state = pipeline.prepare_pipeline.function('manual__first')
        other = pipeline.prepare_pipeline.function('manual__second')
        ti = XcomReader(state)
        stages: list[tuple[pipeline.TriggerDagRunOperator, pipeline.Stage]] = [
            (pipeline.download, 'download'), (pipeline.chunk, 'chunk'),
            (pipeline.embedding, 'embedding'), (pipeline.database, 'database')]
        for operator, stage in stages:
            rendered = copy.copy(operator)
            rendered.render_template_fields({'ti': ti})
            self.assertEqual(rendered.trigger_run_id, state[stage]['run_id'])
            assert isinstance(rendered.trigger_run_id, str)
            self.assertEqual(state[stage]['folder'], f'airflow-pipeline__manual__first__{stage}')
            self.assertNotEqual(state[stage]['run_id'], other[stage]['run_id'])
            if stage == 'chunk':
                self.assertEqual(rendered.conf, {'download_run': state['download']['folder']})
            elif stage == 'embedding':
                self.assertEqual(rendered.conf, {'chunk_run': state['chunk']['folder']})
            elif stage == 'database':
                self.assertEqual(rendered.conf, {'embedding_run': state['embedding']['folder']})
            self.assertEqual(rendered.trigger_dag_id, operator.trigger_dag_id)


if __name__ == '__main__':
    unittest.main()
