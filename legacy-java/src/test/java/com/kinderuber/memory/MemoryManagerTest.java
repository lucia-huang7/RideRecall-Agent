package com.kinderuber.memory;

import com.kinderuber.config.KinderUberProperties;
import com.kinderuber.llm.LlmGateway;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.data.redis.core.*;
import java.nio.file.*;
import java.time.Instant;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class MemoryManagerTest {
    @TempDir Path directory;
    private final ObjectMapper mapper = new ObjectMapper().findAndRegisterModules();

    @SuppressWarnings("unchecked")
    private MemoryManager memory(LlmGateway gateway, StringRedisTemplate redis, int count) throws Exception {
        var properties = new KinderUberProperties();
        Path file = directory.resolve("memory.json");
        Files.writeString(file, "{\"episodic\":[],\"profiles\":{\"u\":{\"preferences\":[\"中文回复\"],\"entities\":{\"产品\":[\"产品A\"]}}}}");
        properties.getStorage().setMemoryPath(file.toString());
        ListOperations<String, String> lists = mock(ListOperations.class);
        when(redis.opsForList()).thenReturn(lists);
        when(redis.opsForValue()).thenReturn(mock(ValueOperations.class));
        List<String> rows = new ArrayList<>();
        for (int i = 0; i < count; i++) rows.add(mapper.writeValueAsString(new ConversationMessage(MessageRole.USER, "message-" + i, Instant.now(), Map.of())));
        when(lists.range(anyString(), eq(0L), anyLong())).thenReturn(rows);
        var memory = new MemoryManager(redis, mapper, properties, gateway); memory.init(); return memory;
    }

    @Test void outageNeverUsesPlainTextFallbackOrTrimsHistory() throws Exception {
        LlmGateway gateway = mock(LlmGateway.class);
        when(gateway.chat(anyString(), anyString(), anyDouble(), anyInt())).thenReturn("模型不可用，本地回复");
        when(gateway.chatRequired(anyString(), anyString(), anyDouble(), anyInt())).thenThrow(new IllegalStateException("model_unavailable"));
        var redis = mock(StringRedisTemplate.class);
        var memory = memory(gateway, redis, 20);
        memory.maintain("u", "c");
        assertEquals(List.of("中文回复"), memory.getContext("u", "c", "").userProfile().get("preferences"));
        assertEquals(20, memory.getWorkingMemory("u", "c").size());
        verify(gateway, never()).chat(anyString(), anyString(), anyDouble(), anyInt());
        verify(redis, never()).execute(any(org.springframework.data.redis.core.script.RedisScript.class), anyList(), any(Object[].class));
    }

    @Test void invalidOrEmptyExtractionPreservesExistingProfile() throws Exception {
        for (String output : List.of("服务暂时不可用", "{}", "{\"preferences\":true,\"entities\":{}}", "{\"preferences\":[],\"entities\":{}}")) {
            var gateway = mock(LlmGateway.class);
            when(gateway.chatRequired(anyString(), anyString(), anyDouble(), anyInt())).thenReturn(output);
            var memory = memory(gateway, mock(StringRedisTemplate.class), 1);
            memory.updateProfile("u", "c");
            assertEquals(List.of("中文回复"), memory.getContext("u", "c", "").userProfile().get("preferences"));
        }
    }

    @Test void validExtractionMergesFactsWithoutDiscardingOlderPreferences() throws Exception {
        var gateway = mock(LlmGateway.class);
        when(gateway.chatRequired(anyString(), anyString(), anyDouble(), anyInt())).thenReturn("```json\n{\"preferences\":[\"简洁\",\"中文回复\"],\"entities\":{\"产品\":[\"产品B\"]}}\n```");
        var memory = memory(gateway, mock(StringRedisTemplate.class), 1);
        memory.updateProfile("u", "c");
        Map<String, Object> profile = memory.getContext("u", "c", "").userProfile();
        assertEquals(List.of("中文回复", "简洁"), profile.get("preferences"));
        assertEquals(List.of("产品A", "产品B"), ((Map<?, ?>) profile.get("entities")).get("产品"));
    }
}
