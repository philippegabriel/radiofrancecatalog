"""Exercise MCP schemas and tool calls without downloading or loading a model."""
import asyncio
from pathlib import Path
import sys
import socket
from threading import Event
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import create_server
from retrieval import Retrieval


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_tools_are_readonly_and_context_is_not_an_argument(self) -> None:
        retrieval = Mock()
        server = create_server(lambda: retrieval)
        tools = await server.list_tools()
        self.assertEqual({tool.name for tool in tools},
                         {'search_podcasts', 'get_episode', 'get_passage'})
        for tool in tools:
            self.assertNotIn('ctx', tool.input_schema.get('properties', {}))
            if tool.name == 'search_podcasts':
                self.assertEqual(tool.input_schema['properties']['source']['default'], 'both')
            self.assertIsNotNone(tool.annotations)
            if tool.annotations:
                self.assertTrue(tool.annotations.read_only_hint)
                self.assertFalse(tool.annotations.destructive_hint)

    async def test_streamable_http_tools_and_single_lifespan(self) -> None:
        retrieval = Mock()
        first_update = Event()
        encoder = Mock()

        def encode(formatted: list[str]) -> list[list[float]]:
            """Require an actual HTTP progress event before allowing inference."""
            self.assertTrue(first_update.wait(timeout=5), 'Initial update was buffered or missing')
            return [[1.0] + [0.0] * 255]

        encoder.encode_formatted.side_effect = encode
        backend = Retrieval(encoder)
        backend.rows = Mock(return_value=[])
        retrieval.search.side_effect = backend.search
        retrieval.episode.return_value = {'episode_id': 'test_5', 'show': 'test',
            'title': 'Titre', 'description': None, 'web_url': None, 'podcast_url': None,
            'player_url': None, 'published': None, 'has_transcript': True}
        retrieval.passage.return_value = {'episode_id': 'test_5', 'strategy_id': 1,
            'chunk_no': 0, 'start_seq': 0, 'end_seq': 0, 'start_time': 0.0,
            'end_time': 1.0, 'text': 'Texte.', 'segments': []}
        factory = Mock(return_value=retrieval)
        server = create_server(factory)
        app = server.streamable_http_app(stateless_http=True, json_response=False)
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        http = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='on'))
        task = asyncio.create_task(http.serve(sockets=[sock]))
        try:
            for _ in range(200):
                if http.started or task.done():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(http.started)
            async with streamable_http_client(f'http://127.0.0.1:{port}/mcp') as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    self.assertEqual(len((await client.list_tools()).tools), 3)
                    calls = [('search_podcasts', {'query': 'histoire'}),
                             ('get_episode', {'episode_id': 'test_5'}),
                             ('get_passage', {'episode_id': 'test_5', 'strategy_id': 1, 'chunk_no': 0})]
                    for name, arguments in calls:
                        updates: list[tuple[float, float | None, str | None]] = []
                        complete = asyncio.Event()

                        async def receive(progress: float, total: float | None, message: str | None) -> None:
                            """Collect real streamed notifications, including before inference starts."""
                            updates.append((progress, total, message))
                            first_update.set()
                            if progress == total:
                                complete.set()

                        result = await client.call_tool(name, arguments, progress_callback=receive)
                        await asyncio.wait_for(complete.wait(), timeout=5)
                        self.assertEqual([entry[0] for entry in updates],
                                         [0, 1, 2, 3, 4] if name == 'search_podcasts' else [0, 1])
                        if name == 'search_podcasts':
                            messages = [entry[2] or '' for entry in updates]
                            for message, expected in zip(messages, ('query embedding', 'transcripts',
                                                                  'descriptions', 'Merging', 'complete')):
                                self.assertIn(expected, message)
                                self.assertIn('elapsed', message)
                        self.assertIsInstance(result, CallToolResult)
                        if isinstance(result, CallToolResult):
                            self.assertFalse(result.is_error)
                            self.assertIsNotNone(result.structured_content)
            factory.assert_called_once()
            retrieval.search.assert_called_once()
            self.assertEqual(retrieval.search.call_args.args, ('histoire', 'both', None, None, None, 10))
            self.assertTrue(callable(retrieval.search.call_args.kwargs['progress']))
        finally:
            http.should_exit = True
            await task
            sock.close()


if __name__ == '__main__':
    unittest.main()
