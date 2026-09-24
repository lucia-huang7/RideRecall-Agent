package com.llmagentsystem.config;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class ChatModelConfigTest {
    @Test void retiredModelsFailFastForOfficialApiButCustomProxiesKeepTheirOwnNames() {
        var config = new ChatModelConfig();
        assertThrows(IllegalArgumentException.class, () -> config.anthropicChatModel("fixture", "https://api.anthropic.com", "claude-3-5-sonnet-20241022", 5));
        assertNotNull(config.anthropicChatModel("fixture", "https://api.anthropic.com", "claude-sonnet-4-6", 5));
        assertNotNull(config.anthropicChatModel("fixture", "http://localhost:12345/proxy", "claude-3-5-sonnet-20241022", 5));
    }
    @Test void anthropicRootsGainV1AndLegacyDeepSeekRootsAreMigrated() {
        assertEquals("https://api.deepseek.com/anthropic/v1", ChatModelConfig.apiBase("https://api.deepseek.com/"));
        assertEquals("https://api.deepseek.com/anthropic/v1", ChatModelConfig.apiBase("https://api.deepseek.com/anthropic"));
        assertEquals("https://api.deepseek.com/anthropic/v1", ChatModelConfig.apiBase("https://api.deepseek.com/v1"));
        assertEquals("https://api.anthropic.com/v1", ChatModelConfig.apiBase("https://api.anthropic.com"));
        assertEquals("http://localhost:8080/proxy/v1", ChatModelConfig.apiBase("http://localhost:8080/proxy/v1/"));
    }
    @Test void emptyAndPlaceholderKeysDoNotCreateNetworkClients() {
        var config = new ChatModelConfig();
        assertNull(config.anthropicChatModel("", "https://api.anthropic.com", "claude", 5));
        assertNull(config.deepSeekChatModel("llmagentsystem-local-placeholder", "https://api.deepseek.com", "deepseek-flash", 5));
    }
}
