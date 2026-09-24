package com.llmagentsystem.intent;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class IntentEmbeddingTest {
    private final ObjectMapper mapper = new ObjectMapper();
    @Test void validatesEmbeddingIndicesNormDimensionAndTypes() throws Exception {
        var vectors = IntentEmbedding.validate(mapper.readTree("[{\"index\":1,\"embedding\":[0,2]},{\"index\":0,\"embedding\":[3,0]}]"), 2);
        assertArrayEquals(new double[]{1,0}, vectors.getFirst());
        for (String invalid : List.of("[]", "[{\"index\":0,\"embedding\":[0,0]}]", "[{\"index\":0,\"embedding\":[true,1]}]", "[{\"index\":2,\"embedding\":[1,1]}]", "[{\"index\":0.0,\"embedding\":[1,1]}]")) {
            assertThrows(IllegalArgumentException.class, () -> IntentEmbedding.validate(mapper.readTree(invalid), 1));
        }
    }
    @Test void nonJsonLlmFallbackDoesNotSuppressLocalIntentRecognition() {
        var recognizer = new IntentRecognizer((s,u,t,m) -> "模型暂时不可用", mapper, new IntentEmbedding("local", "", mapper));
        assertNotEquals(IntentCategory.OTHER, recognizer.recognize("申请退款", List.of()).intent());
    }
    @Test void localModeIsDeterministicAndRequiresNoKey() {
        var embedding = new IntentEmbedding("local", "", mapper);
        var match = embedding.match("申请退款", Map.of(IntentCategory.REFUND, List.of("申请退款"), IntentCategory.TECHNICAL, List.of("应用崩溃")));
        assertEquals(IntentCategory.REFUND, match.intent()); assertEquals("local-ngram", match.source());
        assertEquals(false, embedding.status().get("online_configured"));
    }
    @Test void malformedOnlineResponseFallsBackAsAWholeAndEntersCooldown() throws Exception {
        var server = com.sun.net.httpserver.HttpServer.create(new java.net.InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/embeddings", exchange -> { byte[] body = "{\"data\":[]}".getBytes(java.nio.charset.StandardCharsets.UTF_8); exchange.sendResponseHeaders(200, body.length); exchange.getResponseBody().write(body); exchange.close(); });
        server.start();
        try {
            var embedding = new IntentEmbedding("siliconflow", "test", mapper, java.net.URI.create("http://127.0.0.1:" + server.getAddress().getPort() + "/embeddings"));
            var match = embedding.match("退款", Map.of(IntentCategory.REFUND, List.of("退款")));
            assertEquals("local-ngram", match.source()); assertEquals(true, embedding.status().get("cooldown"));
        } finally { server.stop(0); }
    }
}
