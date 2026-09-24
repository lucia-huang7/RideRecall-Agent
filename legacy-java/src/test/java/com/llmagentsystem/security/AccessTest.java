package com.llmagentsystem.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.mock.env.MockEnvironment;
import org.springframework.mock.web.*;
import java.util.concurrent.atomic.AtomicBoolean;
import static org.junit.jupiter.api.Assertions.*;

class AccessTest {
    private static final String TOKEN = "a-test-user-token-with-24-characters";
    @Test void productionRequiresValidTokens() {
        assertThrows(IllegalArgumentException.class, () -> new AccessFilter(new MockEnvironment().withProperty("LLM_AGENT_SYSTEM_ENV", "production"), new ObjectMapper(), new SimpleMeterRegistry()));
    }
    @Test void userIdentityComesFromTokenAndCannotReadAdminEndpoints() throws Exception {
        var env = new MockEnvironment().withProperty("LLM_AGENT_SYSTEM_API_TOKENS", "{\"" + TOKEN + "\":{\"user_id\":\"real-user\",\"role\":\"user\"}}");
        var filter = new AccessFilter(env, new ObjectMapper(), new SimpleMeterRegistry());
        var unauthorized = new MockHttpServletResponse(); filter.doFilter(new MockHttpServletRequest("GET", "/monitor"), unauthorized, (req,res) -> fail()); assertEquals(401, unauthorized.getStatus());
        var request = new MockHttpServletRequest("GET", "/monitor"); request.addHeader("Authorization", "Bearer " + TOKEN);
        var denied = new MockHttpServletResponse(); filter.doFilter(request, denied, (req,res) -> fail()); assertEquals(403, denied.getStatus());
        var chat = new MockHttpServletRequest("POST", "/chat"); chat.addHeader("Authorization", "Bearer " + TOKEN);
        AtomicBoolean called = new AtomicBoolean();
        filter.doFilter(chat, new MockHttpServletResponse(), (req,res) -> { called.set(true); assertEquals("real-user", ((AccessFilter.Account)req.getAttribute("llmagentsystem.account")).userId()); });
        assertTrue(called.get());
    }
}
