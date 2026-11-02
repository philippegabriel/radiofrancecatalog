# Radio France MCP

The desktop-only implementation is now under [`mcp/`](../mcp/README.md).
It provides a persistent CPU query encoder, read-only retrieval tools, a Compose
container, and the `radiofrance-mcp` systemd service. See that README for setup,
testing, the tool contract, and desktop connection details.

Public hosting, tunnels, authentication for remote access, and LangChain remain
outside this implementation. Airflow continues to orchestrate the batch pipeline;
the MCP server reads the resulting catalogue and vectors independently.
