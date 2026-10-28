# RideRecall-Agent · Java backend

Java backend reference implementation for agent-based customer support, built with Java 21, Spring Boot and LangChain4j.

## Features

- Intent recognition and routing across general, technical, billing and escalation agents.
- Conversation memory with Redis and persistent semantic storage.
- Policy and document retrieval with Chroma, vector search and BM25 ranking.
- MCP tool integration, tool-call traces and circuit breakers.
- REST APIs for conversations, knowledge sources, evaluations and monitoring.

## Source

| Directory | Contents |
|---|---|
| [agent](legacy-java/src/main/java/com/llmagentsystem/agent/) | Agent profiles, routing, tool execution and response composition |
| [api](legacy-java/src/main/java/com/llmagentsystem/api/) | REST controllers and request/response types |
| [memory](legacy-java/src/main/java/com/llmagentsystem/memory/) | Conversation state and memory management |
| [knowledge](legacy-java/src/main/java/com/llmagentsystem/knowledge/) | Document ingestion and retrieval |
| [tool](legacy-java/src/main/java/com/llmagentsystem/tool/) | Tool registration, execution guards and circuit breakers |
| [configuration](legacy-java/src/main/resources/application.yml) | Service, model, memory and retrieval settings |

## Build and test

Use JDK 21 and the included Maven wrapper:

```bash
git clone --branch reference/java-backend https://github.com/lucia-huang7/RideRecall-Agent.git
cd RideRecall-Agent/legacy-java
./mvnw clean test
```

[Environment settings](legacy-java/.env.example) · [MCP service configuration](legacy-java/config/mcp_servers.json) · [Agent profiles](legacy-java/src/main/resources/agents/profiles.json)
