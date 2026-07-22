package com.kinderuber.integration;

import com.kinderuber.llm.LlmGateway;
import com.kinderuber.memory.*;
import com.kinderuber.trace.RequestTraceStore;
import com.fasterxml.jackson.databind.*;
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.condition.EnabledIfSystemProperty;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.*;
import org.springframework.test.context.*;
import dev.langchain4j.data.message.*;
import dev.langchain4j.agent.tool.ToolExecutionRequest;
import java.nio.file.*;
import java.time.*;
import java.util.*;
import java.util.concurrent.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@org.springframework.boot.test.autoconfigure.actuate.observability.AutoConfigureObservability
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@EnabledIfSystemProperty(named = "test.redis.port", matches = "\\d+")
class ApiIntegrationTest {
    static final String USER_TOKEN = "integration-user-token-at-least-24", ADMIN_TOKEN = "integration-admin-token-at-least-24";
    static Path directory;
    static { try { directory = Files.createTempDirectory("kinderuber-integration-"); } catch (Exception e) { throw new RuntimeException(e); } }
    @DynamicPropertySource static void properties(DynamicPropertyRegistry r) {
        r.add("spring.data.redis.port", () -> Integer.getInteger("test.redis.port"));
        r.add("spring.data.redis.password", () -> "");
        r.add("kinderuber.semantic.mode", () -> "disabled");
        r.add("MCP_WRITE_ENABLED", () -> "true");
        r.add("MCP_ENABLED", () -> "false"); r.add("INTENT_EMBEDDING_PROVIDER", () -> "local");
        r.add("KINDER_UBER_API_TOKENS", () -> "{\"" + USER_TOKEN + "\":{\"user_id\":\"authenticated-user\",\"role\":\"user\"},\"" + ADMIN_TOKEN + "\":{\"user_id\":\"admin\",\"role\":\"admin\"}}");
        r.add("kinderuber.storage.knowledge-path", () -> directory.resolve("knowledge.json").toString());
        r.add("kinderuber.storage.memory-path", () -> directory.resolve("memory.json").toString());
        r.add("kinderuber.eval.baseline-path", () -> directory.resolve("baseline.json").toString());
    }
    @Autowired TestRestTemplate http;
    @Autowired ObjectMapper mapper;
    @Autowired StringRedisTemplate redis;
    @Autowired ConversationStore conversations;
    @Autowired MemoryManager memory;
    @Autowired RequestTraceStore traces;
    @LocalServerPort int port;
    @MockBean LlmGateway llm;
    @MockBean dev.langchain4j.model.chat.ChatModel chatModel;
    @BeforeEach void stub() {
        when(llm.chat(anyString(), anyString(), anyDouble(), anyInt())).thenReturn("{\"intent\":\"refund\",\"confidence\":0.95}");
        when(llm.chatRequired(anyString(), anyString(), anyDouble(), anyInt())).thenAnswer(inv -> ((String) inv.getArgument(1)).contains("评分") ? "{\"relevance\":0.9,\"accuracy\":0.9,\"completeness\":0.9,\"helpfulness\":0.9}" : "{\"pass\":true,\"grounded\":true,\"need_escalation\":false}");
        when(llm.judgeChat(anyString(), anyString(), anyDouble(), anyInt(), anyString())).thenReturn("{\"relevance\":0.9,\"accuracy\":0.9,\"completeness\":0.9,\"helpfulness\":0.9}");
        when(chatModel.chat(any(dev.langchain4j.model.chat.request.ChatRequest.class))).thenAnswer(inv -> {
            List<ChatMessage> messages = ((dev.langchain4j.model.chat.request.ChatRequest) inv.getArgument(0)).messages();
            if (messages.getLast() instanceof ToolExecutionResultMessage) return dev.langchain4j.model.chat.response.ChatResponse.builder().aiMessage(new AiMessage("根据知识库，退款申请需要审核，请先提供订单号。")).finishReason(dev.langchain4j.model.output.FinishReason.STOP).build();
            return dev.langchain4j.model.chat.response.ChatResponse.builder().aiMessage(AiMessage.from(List.of(ToolExecutionRequest.builder().id("knowledge-call").name("search_knowledge_base").arguments("{\"query\":\"退款政策\",\"top_k\":3}").build()))).finishReason(dev.langchain4j.model.output.FinishReason.TOOL_EXECUTION).build();
        });
    }
    HttpHeaders headers(String token) { var headers = new HttpHeaders(); headers.setBearerAuth(token); headers.setContentType(MediaType.APPLICATION_JSON); return headers; }
    ResponseEntity<JsonNode> post(String path, Object body, String token) { return http.exchange(path, HttpMethod.POST, new HttpEntity<>(body, headers(token)), JsonNode.class); }
    ResponseEntity<JsonNode> get(String path, String token) { return http.exchange(path, HttpMethod.GET, new HttpEntity<>(headers(token)), JsonNode.class); }
    Map<String, Object> request(String conv, String client) { return Map.of("message", "我想申请退款", "user_id", "forged-user", "conv_id", conv, "client_request_id", client); }
    @Test void chatRetryIsAtomicScopedAndReturnsSameReceipt() {
        String conv = UUID.randomUUID().toString(), client = UUID.randomUUID().toString();
        var first = post("/chat", request(conv, client), USER_TOKEN); assertEquals(200, first.getStatusCode().value(), first.toString());
        assertEquals(true, first.getBody().path("knowledge_used").asBoolean());
        assertEquals("local-ngram", first.getBody().path("intent_embedding_source").asText());
        assertEquals("search_knowledge_base", first.getBody().path("tools_used").get(0).asText());
        var duplicate = post("/chat", request(conv, client), USER_TOKEN); assertEquals(first.getBody(), duplicate.getBody());
        assertEquals(2L, redis.opsForList().size("wm:" + ConversationStore.scope("authenticated-user", conv)));
        assertEquals(2L, redis.opsForList().size("history:" + ConversationStore.scope("authenticated-user", conv)));
        assertEquals(0L, redis.opsForList().size("wm:" + ConversationStore.scope("forged-user", conv)));
        var conflict = new LinkedHashMap<>(request(conv, client)); conflict.put("message", "另一条消息");
        assertEquals(409, post("/chat", conflict, USER_TOKEN).getStatusCode().value());
        var trace = get("/trace/tool/" + first.getBody().path("request_id").asText(), ADMIN_TOKEN);
        assertTrue(trace.getBody().path("found").asBoolean()); assertNotEquals("authenticated-user", trace.getBody().path("trace").path("user_id").asText());
    }
    @Test void concurrentDuplicateMessagesProduceOneTurn() throws Exception {
        String conv = UUID.randomUUID().toString(), client = UUID.randomUUID().toString();
        try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
            List<Future<ResponseEntity<JsonNode>>> results = new ArrayList<>();
            for (int i = 0; i < 3; i++) results.add(executor.submit(() -> post("/chat", request(conv, client), USER_TOKEN)));
            Set<String> ids = new HashSet<>();
            for (var result : results) { var reply = result.get(20, TimeUnit.SECONDS); assertEquals(200, reply.getStatusCode().value(), reply.toString()); ids.add(reply.getBody().path("request_id").asText()); }
            assertEquals(1, ids.size()); assertEquals(2L, redis.opsForList().size("wm:" + ConversationStore.scope("authenticated-user", conv)));
        }
    }
    @Test void expiredLeaseCannotCommitAnything() {
        String user = "lease-user", conv = UUID.randomUUID().toString();
        com.kinderuber.api.dto.ChatResponse reply;
        try { reply = mapper.readValue("{\"conv_id\":\"test\",\"response\":\"answer\"}", com.kinderuber.api.dto.ChatResponse.class); }
        catch (Exception ex) { throw new IllegalStateException(ex); }
        try (var lease = conversations.acquire(user, conv)) {
            redis.opsForValue().set("conversation_lock:" + ConversationStore.scope(user, conv), "new-owner", Duration.ofSeconds(15));
            // A stale lease is rejected by the commit Lua before any write, even before its next renewal runs.
            assertThrows(org.springframework.web.server.ResponseStatusException.class, () -> conversations.commit(lease, user, conv, "id", "msg", reply, Map.of(), 60));
            assertEquals(0L, redis.opsForList().size("wm:" + ConversationStore.scope(user, conv)));
        }
    }
    @Test void optimisticCompressionRetainsMessagesAppendedDuringSummarization() throws Exception {
        String user = "compress-user", conv = UUID.randomUUID().toString(), key = "wm:" + ConversationStore.scope(user, conv);
        for (int i = 0; i < 16; i++) redis.opsForList().leftPush(key, mapper.writeValueAsString(new ConversationMessage(i % 2 == 0 ? MessageRole.USER : MessageRole.ASSISTANT, "message-" + i, Instant.now(), Map.of())));
        when(llm.chatRequired(anyString(), contains("用 2-3 句话"), anyDouble(), anyInt())).thenAnswer(inv -> {
            redis.opsForList().leftPush(key, mapper.writeValueAsString(new ConversationMessage(MessageRole.USER, "new-message", Instant.now(), Map.of())));
            return "历史摘要";
        });
        memory.maintain(user, conv);
        var messages = memory.getWorkingMemory(user, conv);
        assertEquals(6, messages.size()); assertEquals("new-message", messages.getLast().content());
        assertEquals("message-11", messages.getFirst().content());
    }
    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.ValueSource(booleans = {true, false})
    void rejectedAnswerIsReplacedBeforeReturningAndPersisting(boolean qualityPass) throws Exception {
        when(llm.chatRequired(anyString(), contains("质量校验器"), anyDouble(), anyInt())).thenReturn("{\"pass\":" + qualityPass + ",\"grounded\":false,\"need_escalation\":true}");
        String conv = UUID.randomUUID().toString(), client = UUID.randomUUID().toString();
        var reply = post("/chat", request(conv, client), USER_TOKEN);
        assertEquals(200, reply.getStatusCode().value()); assertEquals("partial", reply.getBody().path("status").asText());
        assertEquals("answer_verification_rejected", reply.getBody().path("error_code").asText());
        assertFalse(reply.getBody().path("verified").asBoolean()); assertTrue(reply.getBody().path("escalated").asBoolean());
        String delivered = reply.getBody().path("response").asText(); assertTrue(delivered.contains("人工客服"));
        var rows = memory.getWorkingMemory("authenticated-user", conv);
        assertEquals(2, rows.size()); assertEquals(delivered, rows.getLast().content());
        assertEquals(reply.getBody(), post("/chat", request(conv, client), USER_TOKEN).getBody());
    }
    @Test void verificationOutageDoesNotPersistUnverifiedAnswerAndRetryCanRecover() {
        when(llm.chatRequired(anyString(), contains("质量校验器"), anyDouble(), anyInt())).thenThrow(new IllegalStateException("fixture_outage"));
        String conv = UUID.randomUUID().toString(), client = UUID.randomUUID().toString();
        var failed = post("/chat", request(conv, client), USER_TOKEN);
        assertEquals(503, failed.getStatusCode().value()); assertEquals("verification_unavailable", failed.getBody().path("error_code").asText());
        assertTrue(failed.getBody().path("retryable").asBoolean()); assertEquals(0L, redis.opsForList().size("wm:" + ConversationStore.scope("authenticated-user", conv)));
        when(llm.chatRequired(anyString(), contains("质量校验器"), anyDouble(), anyInt())).thenReturn("{\"pass\":true,\"grounded\":true,\"need_escalation\":false}");
        assertEquals(200, post("/chat", request(conv, client), USER_TOKEN).getStatusCode().value());
        assertEquals(2L, redis.opsForList().size("wm:" + ConversationStore.scope("authenticated-user", conv)));
    }
    @Test void summaryIsReplacedByMergedSummaryAndConcurrentUpdatesAreNotOverwritten() throws Exception {
        String user = "summary-user", conv = UUID.randomUUID().toString(), scope = ConversationStore.scope(user, conv);
        String key = "wm:" + scope, summaryKey = "summary:" + scope;
        for (int i = 0; i < 16; i++) redis.opsForList().leftPush(key, mapper.writeValueAsString(new ConversationMessage(MessageRole.USER, "message-" + i, Instant.now(), Map.of())));
        redis.opsForValue().set(summaryKey, "旧摘要");
        when(llm.chatRequired(anyString(), contains("用 2-3 句话"), anyDouble(), anyInt())).thenAnswer(inv -> {
            assertTrue(((String) inv.getArgument(1)).contains("旧摘要")); return "合并后的摘要";
        });
        memory.maintain(user, conv);
        assertEquals("合并后的摘要", redis.opsForValue().get(summaryKey)); assertEquals(5L, redis.opsForList().size(key));
        for (int i = 0; i < 10; i++) redis.opsForList().leftPush(key, mapper.writeValueAsString(new ConversationMessage(MessageRole.USER, "new-" + i, Instant.now(), Map.of())));
        when(llm.chatRequired(anyString(), contains("用 2-3 句话"), anyDouble(), anyInt())).thenAnswer(inv -> {
            redis.opsForValue().set(summaryKey, "其他维护任务的新摘要"); return "过期结果";
        });
        memory.maintain(user, conv);
        assertEquals("其他维护任务的新摘要", redis.opsForValue().get(summaryKey)); assertEquals(15L, redis.opsForList().size(key));
    }
    @Test void evaluationIsAsyncAndAdminOnly() throws Exception {
        assertEquals(403, post("/eval/run", Map.of(), USER_TOKEN).getStatusCode().value());
        var queued = post("/eval/run", Map.of("intent_cases", List.of(), "dialog_cases", List.of()), ADMIN_TOKEN); assertEquals(202, queued.getStatusCode().value());
        String id = queued.getBody().path("job_id").asText(); JsonNode job = null;
        for (int i = 0; i < 100; i++) { job = get("/eval/jobs/" + id, ADMIN_TOKEN).getBody(); if (job.path("status").asText().equals("completed")) break; Thread.sleep(20); }
        assertEquals("completed", job.path("status").asText()); assertEquals("empty", job.path("result").path("status").asText());
        assertEquals(404, get("/eval/jobs/not-found", ADMIN_TOKEN).getStatusCode().value());
    }
    @Test void conversationEvaluationUsesOwnedSnapshotAndDoesNotReplayChatOrWriteMemory() throws Exception {
        String conv=UUID.randomUUID().toString();
        var chat=post("/chat",request(conv,UUID.randomUUID().toString()),USER_TOKEN);
        assertEquals(200,chat.getStatusCode().value());
        String scope=ConversationStore.scope("authenticated-user",conv);
        List<String> original=redis.opsForList().range("history:"+scope,0,-1);
        assertEquals(2,original.size());
        assertTrue(original.getFirst().contains("evidence"));
        // Compression may discard working messages without changing the transcript used for evaluation.
        redis.delete("wm:"+scope);
        clearInvocations(chatModel);
        var queued=post("/eval/conversation",Map.of("conv_id",conv,"user_id","someone-else","max_turns",20),USER_TOKEN);
        assertEquals(202,queued.getStatusCode().value(),queued.toString());
        String id=queued.getBody().path("job_id").asText(); JsonNode job=null;
        for(int i=0;i<100;i++) { job=get("/eval/jobs/"+id,USER_TOKEN).getBody(); if(job.path("status").asText().equals("completed"))break;Thread.sleep(20); }
        assertEquals("completed",job.path("status").asText()); assertEquals("conversation",job.path("result").path("scope").asText());
        assertEquals(chat.getBody().path("response").asText(),job.path("result").path("results").get(0).path("metadata").path("response").asText());
        assertEquals(5,job.path("result").path("judge_few_shot_count").asInt());
        assertEquals(original,redis.opsForList().range("history:"+scope,0,-1));
        assertEquals(0L,redis.opsForList().size("wm:"+scope)); verifyNoInteractions(chatModel);
        assertEquals(404,get("/eval/jobs/"+id,ADMIN_TOKEN).getStatusCode().value());
        assertEquals(404,post("/eval/conversation",Map.of("conv_id",conv,"user_id","authenticated-user"),ADMIN_TOKEN).getStatusCode().value());
        assertEquals(400,post("/eval/conversation",Map.of("conv_id",conv,"max_turns",51),USER_TOKEN).getStatusCode().value());
        assertEquals(404,post("/eval/conversation",Map.of("conv_id","missing"),USER_TOKEN).getStatusCode().value());
    }
    @Test void transcriptRetentionIsBoundedAndReportsTruncation() throws Exception {
        String conv=UUID.randomUUID().toString(),user="authenticated-user",scope=ConversationStore.scope(user,conv);
        var response=post("/chat",request(conv,UUID.randomUUID().toString()),USER_TOKEN);
        var reply=mapper.treeToValue(response.getBody(),com.kinderuber.api.dto.ChatResponse.class);
        try(var lease=conversations.acquire(user,conv)) {
            for(int i=0;i<100;i++) conversations.commit(lease,user,conv,"fixture-"+i,"message-"+i,reply,Map.of(),86400);
        }
        var transcript=conversations.transcript(user,conv);
        assertEquals(200,transcript.messages().size());assertTrue(transcript.truncated());
        assertEquals("message-0",transcript.messages().getFirst().content());
        assertEquals("202",redis.opsForValue().get("history_count:"+scope));
        assertTrue(redis.getExpire("history:"+scope)>0);
    }
    @Test void transcriptFallsBackToLegacyMemoryAndSeedsItWhenNewChatArrives() throws Exception {
        String conv=UUID.randomUUID().toString(),scope=ConversationStore.scope("authenticated-user",conv);
        String key="wm:"+scope;
        redis.opsForList().leftPush(key,mapper.writeValueAsString(new ConversationMessage(MessageRole.USER,"旧问题",Instant.now(),Map.of())));
        redis.opsForList().leftPush(key,mapper.writeValueAsString(new ConversationMessage(MessageRole.ASSISTANT,"旧回复",Instant.now(),Map.of())));
        var before=conversations.transcript("authenticated-user",conv);
        assertEquals("working_memory_legacy",before.source()); assertTrue(before.truncated());
        assertEquals(200,post("/chat",request(conv,UUID.randomUUID().toString()),USER_TOKEN).getStatusCode().value());
        var after=conversations.transcript("authenticated-user",conv);
        assertEquals(4,after.messages().size());assertEquals("旧问题",after.messages().getFirst().content());
        assertEquals("conversation_history_with_legacy",after.source());assertTrue(after.truncated());
    }
    @Test void configuredExternalMcpClientDiscoversWhitelistAndEnforcesAgentScope() throws Exception {
        Path config = directory.resolve("mcp-fixture.json");
        Files.writeString(config, mapper.writeValueAsString(Map.of("servers", List.of(Map.of("name", "fixture", "transport", "streamable_http", "url", "http://127.0.0.1:" + port + "/mcp", "allowed_tools", List.of("search_local_knowledge"), "agents", List.of("technical"), "read_only", true, "headers_env", Map.of("Authorization", "FIXTURE_AUTH"))))));
        var registry = new com.kinderuber.tool.ToolRegistry(mapper);
        var external = new StandardMcpClient(registry, new org.springframework.mock.env.MockEnvironment().withProperty("FIXTURE_AUTH", "Bearer " + ADMIN_TOKEN), config.toString(), true);
        try {
            external.load(); external.reconnect();
            assertEquals(1, registry.available("technical").size()); assertTrue(registry.available("billing").isEmpty());
            String alias = registry.available("technical").getFirst().name();
            var result = registry.call(alias, Map.of("query", "退款", "top_k", 1), "technical");
            assertTrue(result.success(), result.error());
            assertTrue(registry.call(alias, Map.of("query", "退款", "top_k", 1), "technical").cached());
            assertFalse(registry.call(alias, Map.of("query", "退款"), "billing").success());
            Object bounded = external.bounded(Map.of("server", "fixture", "tool", "test", "content", "长文本".repeat(10000), "structuredContent", Map.of("count", 1)), 256);
            assertTrue(mapper.writeValueAsString(bounded).length() <= 256);
            assertTrue(((Map<?, ?>) bounded).containsKey("structuredContent"));
        } finally { external.close(); }
    }
    @Test void failedAgentDoesNotCreateConversationMessages() {
        doThrow(new IllegalStateException("model_unavailable")).when(chatModel).chat(any(dev.langchain4j.model.chat.request.ChatRequest.class));
        String conv = UUID.randomUUID().toString();
        var response = post("/chat", request(conv, UUID.randomUUID().toString()), USER_TOKEN);
        assertEquals(503, response.getStatusCode().value()); assertEquals("failed", response.getBody().path("status").asText());
        assertTrue(response.getBody().path("retryable").asBoolean()); assertEquals(0L, redis.opsForList().size("wm:" + ConversationStore.scope("authenticated-user", conv)));
    }
    @Test void uploadAndSearchValidateInputs() {
        assertEquals(400, post("/knowledge/add", Map.of("documents", List.of(Map.of("title", "", "content", "x"))), ADMIN_TOKEN).getStatusCode().value());
        assertEquals(400, post("/search?query=x&top_k=21", Map.of(), USER_TOKEN).getStatusCode().value());
        assertEquals(200, get("/integrations/embedding", ADMIN_TOKEN).getStatusCode().value());
    }
    @Test void officialMcpClientCanInitializeDiscoverAndCallOurServer() {
        var transport = io.modelcontextprotocol.client.transport.HttpClientStreamableHttpTransport.builder("http://127.0.0.1:" + port).endpoint("/mcp").customizeRequest(r -> r.header("Authorization", "Bearer " + USER_TOKEN)).build();
        try (var client = io.modelcontextprotocol.client.McpClient.sync(transport).requestTimeout(Duration.ofSeconds(5)).build()) {
            client.initialize(); assertEquals("search_local_knowledge", client.listTools().tools().getFirst().name());
            var result = client.callTool(new io.modelcontextprotocol.spec.McpSchema.CallToolRequest("search_local_knowledge", Map.of("query", "退款", "top_k", 2)));
            assertFalse(Boolean.TRUE.equals(result.isError())); assertFalse(result.content().isEmpty());
            assertEquals("kinderuber://knowledge/stats", client.listResources().resources().getFirst().uri());
            assertFalse(client.readResource(new io.modelcontextprotocol.spec.McpSchema.ReadResourceRequest("kinderuber://knowledge/stats")).contents().isEmpty());
        }
    }
    @Autowired com.kinderuber.tool.ToolRegistry registry;
    @Test void uncertainWriteIsDurablyBlockedFromRetryAndDoesNotFallBack() throws Exception {
        var calls=new java.util.concurrent.atomic.AtomicInteger();
        registry.register(new com.kinderuber.tool.ToolDefinition("write_integration_fixture","isolated fixture",Map.of("type","object"),Set.of("billing"),false,Duration.ofSeconds(1),Duration.ZERO,"external_mcp","fixture","write",args->{calls.incrementAndGet();return Map.of("success",true);}));
        when(chatModel.chat(any(dev.langchain4j.model.chat.request.ChatRequest.class))).thenAnswer(inv->{
            var messages=((dev.langchain4j.model.chat.request.ChatRequest)inv.getArgument(0)).messages();
            if(messages.getLast() instanceof ToolExecutionResultMessage) throw new IllegalStateException("fixture_model_failed_after_write");
            return dev.langchain4j.model.chat.response.ChatResponse.builder().aiMessage(AiMessage.from(List.of(ToolExecutionRequest.builder().id("write").name("write_integration_fixture").arguments("{}").build()))).finishReason(dev.langchain4j.model.output.FinishReason.TOOL_EXECUTION).build();
        });
        String conv="write-guard-"+UUID.randomUUID();var payload=Map.of("message","申请退款","conv_id",conv,"client_request_id","one-write");
        try {
            var first=http.exchange("/chat",HttpMethod.POST,new HttpEntity<>(payload,headers(USER_TOKEN)),Map.class);
            assertEquals(HttpStatus.SERVICE_UNAVAILABLE,first.getStatusCode());assertEquals("tool_outcome_unknown",first.getBody().get("error_code"));assertEquals(false,first.getBody().get("retryable"));
            var second=http.exchange("/chat",HttpMethod.POST,new HttpEntity<>(payload,headers(USER_TOKEN)),Map.class);
            assertEquals("tool_outcome_unknown",second.getBody().get("error_code"));assertEquals(1,calls.get());
            assertEquals("tool_outcome_unknown",conversations.receipt("authenticated-user",conv,"one-write").response().errorCode());
            assertTrue(memory.getWorkingMemory("authenticated-user",conv).isEmpty());
        } finally {registry.unregister("write_integration_fixture");}
    }

}
