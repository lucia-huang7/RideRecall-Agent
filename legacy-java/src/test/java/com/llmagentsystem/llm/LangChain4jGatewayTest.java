package com.llmagentsystem.llm;

import com.llmagentsystem.config.ChatModelConfig;
import com.llmagentsystem.config.LlmAgentSystemProperties;
import com.llmagentsystem.tool.ToolDefinition;
import com.fasterxml.jackson.databind.*;
import com.sun.net.httpserver.HttpServer;
import dev.langchain4j.agent.tool.ToolSpecification;
import dev.langchain4j.data.message.*;
import dev.langchain4j.model.chat.ChatModel;
import org.junit.jupiter.api.*;
import org.springframework.beans.factory.support.StaticListableBeanFactory;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.CopyOnWriteArrayList;
import static org.junit.jupiter.api.Assertions.*;

class LangChain4jGatewayTest {
    private final ObjectMapper mapper = new ObjectMapper();
    private final List<JsonNode> requests = new CopyOnWriteArrayList<>();
    private HttpServer server;
    private String base;

    @BeforeEach void start() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        base = "http://127.0.0.1:" + server.getAddress().getPort();
    }
    @AfterEach void stop() { server.stop(0); }
    private LangChain4jLlmGateway gateway(ChatModel model) {
        var beans = new StaticListableBeanFactory();
        if (model != null) beans.addBean("chatModel", model);
        return new LangChain4jLlmGateway(beans.getBeanProvider(ChatModel.class), new LlmAgentSystemProperties());
    }
    private AiMessage turn(ChatModel model, List<ChatMessage> messages, List<ToolSpecification> tools) {
        return new CheckedChatModel(model).chat(dev.langchain4j.model.chat.request.ChatRequest.builder().messages(messages)
                .toolSpecifications(tools).temperature(.2).maxOutputTokens(2048).build()).aiMessage();
    }
    private ToolSpecification specification() {
        return ToolSpecifications.from(new ToolDefinition("lookup", "Search policy", Map.of("type", "object", "properties", Map.of(
                "query", Map.of("type", "string", "minLength", 1), "count", Map.of("type", "integer", "minimum", 1, "maximum", 20)),
                "required", List.of("query"), "additionalProperties", false), Set.of("general"), true, Duration.ofSeconds(2), Duration.ZERO, "local", "", "", args -> args), mapper);
    }
    private void respond(String path, java.util.function.IntFunction<String> body) {
        server.createContext(path, exchange -> {
            requests.add(mapper.readTree(exchange.getRequestBody()));
            byte[] bytes = body.apply(requests.size()).getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes); exchange.close();
        });
        server.start();
    }
    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(booleans = {true, false})
    void aiServiceCompletesNativeToolRoundTripForBothProviders(boolean deepseek) {
        respond(deepseek ? "/anthropic/v1/messages" : "/v1/messages", round -> round == 1 ? """
                {"id":"msg1","type":"message","role":"assistant","model":"fixture","content":[{"type":"tool_use","id":"call-1","name":"lookup","input":{"query":"退款"}}],"stop_reason":"tool_use","usage":{"input_tokens":10,"output_tokens":5}}
                """ : """
                {"id":"msg2","type":"message","role":"assistant","model":"fixture","content":[{"type":"text","text":"退款需要审核"}],"stop_reason":"end_turn","usage":{"input_tokens":20,"output_tokens":5}}
                """);
        var model = deepseek ? new ChatModelConfig().deepSeekChatModel("fixture-key", base + "/anthropic", "deepseek-flash", 5)
                : new ChatModelConfig().anthropicChatModel("fixture-key", base, "claude-fixture", 5);
        var registry = new com.llmagentsystem.tool.ToolRegistry(mapper);
        registry.register(new ToolDefinition("lookup", "政策检索", Map.of("type", "object", "properties", Map.of("query", Map.of("type", "string")), "required", List.of("query")),
                Set.of("general"), true, Duration.ofSeconds(1), Duration.ZERO, "local", "", "", args -> Map.of("policy", "退款需审核")));
        var service = new com.llmagentsystem.agent.AgentAiService(model, registry, mapper);
        var request = com.llmagentsystem.agent.AgentRequest.of("查询政策", "u", "c", "", List.of(Map.of("role", "user", "content", "订单123"), Map.of("role", "assistant", "content", "已记录订单")));
        List<com.llmagentsystem.trace.ToolCallTrace> traces = new ArrayList<>();
        var result = service.reply(com.llmagentsystem.agent.AgentType.GENERAL, "客服", request, traces);
        assertEquals("退款需要审核", result.content()); assertEquals(2, requests.size());
        assertEquals(40, result.tokenUsage().totalTokenCount());
        assertEquals(1, traces.size()); assertFalse(request.evidence().isEmpty());
        assertTrue(requests.getLast().toString().contains("tool_result"));
        assertTrue(requests.getFirst().toString().contains("已记录订单"));
    }

    @Test void maxTokensReplyCannotPassStrictAuxiliaryCalls() {
        respond("/v1/messages", round -> """
                {"id":"msg1","type":"message","role":"assistant","model":"fixture","content":[{"type":"text","text":"半截答案"}],"stop_reason":"max_tokens","usage":{"input_tokens":1,"output_tokens":2}}
                """);
        var llm = gateway(new ChatModelConfig().anthropicChatModel("fixture-key", base, "fixture", 5));
        var failure = assertThrows(IllegalStateException.class, () -> llm.chatRequired("", "提取画像", 0, 512));
        assertEquals("model_response_truncated", failure.getMessage());
    }
    @Test void deepSeekUsesAnthropicProtocolForParallelToolCallsAndResults() {
        respond("/anthropic/v1/messages", round -> round == 1 ? """
                {"id":"msg1","type":"message","role":"assistant","model":"deepseek-flash","content":[{"type":"tool_use","id":"call-1","name":"lookup","input":{"query":"退款"}},{"type":"tool_use","id":"call-2","name":"lookup","input":{"query":"账单"}}],"stop_reason":"tool_use","stop_sequence":null,"usage":{"input_tokens":1,"output_tokens":2}}
                """ : """
                {"id":"msg2","type":"message","role":"assistant","model":"deepseek-flash","content":[{"type":"text","text":"依据政策回答"}],"stop_reason":"end_turn","stop_sequence":null,"usage":{"input_tokens":1,"output_tokens":2}}
                """);
        var model = new ChatModelConfig().deepSeekChatModel("fixture-key", base + "/anthropic", "deepseek-flash", 5);
        var llm = gateway(model);
        List<ChatMessage> messages = new ArrayList<>(List.of(SystemMessage.from("system"), UserMessage.from("退款与账单")));
        AiMessage tools = turn(model, messages, List.of(specification()));
        assertEquals(2, tools.toolExecutionRequests().size()); messages.add(tools);
        tools.toolExecutionRequests().forEach(call -> messages.add(ToolExecutionResultMessage.from(call, "{\"policy\":\"已核验资料\"}")));
        assertEquals("依据政策回答", turn(model, messages, List.of(specification())).text());
        JsonNode sent = requests.getFirst();
        assertEquals("deepseek-flash", sent.path("model").asText());
        assertEquals("disabled", sent.path("thinking").path("type").asText());
        assertEquals(2048, sent.path("max_tokens").asInt());
        assertEquals(1, sent.path("tools").get(0).path("input_schema").path("properties").path("count").path("minimum").asInt());
        assertEquals(2, requests.get(1).path("messages").findValuesAsText("tool_use_id").size());
    }
    @Test void anthropicUsesNativeToolUseAndToolResultMessages() {
        respond("/v1/messages", round -> round == 1 ? """
                {"id":"msg1","type":"message","role":"assistant","model":"claude-fixture","content":[{"type":"tool_use","id":"call-1","name":"lookup","input":{"query":"退款"}}],"stop_reason":"tool_use","stop_sequence":null,"usage":{"input_tokens":1,"output_tokens":2}}
                """ : """
                {"id":"msg2","type":"message","role":"assistant","model":"claude-fixture","content":[{"type":"text","text":"退款需审核"}],"stop_reason":"end_turn","stop_sequence":null,"usage":{"input_tokens":1,"output_tokens":2}}
                """);
        var model = new ChatModelConfig().anthropicChatModel("fixture-key", base, "claude-fixture", 5);
        List<ChatMessage> messages = new ArrayList<>(List.of(SystemMessage.from("system"), UserMessage.from("退款")));
        AiMessage output = turn(model, messages, List.of(specification()));
        assertTrue(output.hasToolExecutionRequests()); messages.add(output);
        messages.add(ToolExecutionResultMessage.from(output.toolExecutionRequests().getFirst(), "政策"));
        assertEquals("退款需审核", turn(model, messages, List.of(specification())).text());
        assertEquals("lookup", requests.getFirst().path("tools").get(0).path("name").asText());
        assertEquals(20, requests.getFirst().path("tools").get(0).path("input_schema").path("properties").path("count").path("maximum").asInt());
        assertTrue(requests.get(1).toString().contains("tool_result"));
        assertTrue(requests.get(1).toString().contains("call-1"));
    }
    @Test void judgeModelAndTokenBudgetAreAppliedPerRequest() {
        respond("/anthropic/v1/messages", round -> """
                {"id":"judge1","type":"message","role":"assistant","model":"judge-fixture","content":[{"type":"text","text":"{\\\"score\\\":1}"}],"stop_reason":"end_turn","stop_sequence":null,"usage":{"input_tokens":1,"output_tokens":2}}
                """);
        var model = new ChatModelConfig().deepSeekChatModel("fixture-key", base + "/anthropic", "deepseek-flash", 5);
        var llm = gateway(model);
        assertEquals("{\"score\":1}", llm.judgeChat("system", "judge", 0, 4096, "judge-fixture"));
        assertEquals("judge-fixture", requests.getFirst().path("model").asText());
        assertEquals(4096, requests.getFirst().path("max_tokens").asInt());
    }
    @Test void missingModelHasExplicitFailureAndOptionalPlainTextFallback() {
        var llm = gateway(null);
        assertThrows(IllegalStateException.class, () -> new com.llmagentsystem.agent.AgentAiService((ChatModel) null, new com.llmagentsystem.tool.ToolRegistry(mapper), mapper)
                .reply(com.llmagentsystem.agent.AgentType.GENERAL, "system", com.llmagentsystem.agent.AgentRequest.of("退款", "u", "c", "", List.of()), new ArrayList<>()));
        assertThrows(IllegalStateException.class, () -> llm.judgeChat("", "judge", 0, 4096, ""));
        assertTrue(llm.chat("", "退款", .2, 1024).contains("模型服务不可用"));
    }
}
