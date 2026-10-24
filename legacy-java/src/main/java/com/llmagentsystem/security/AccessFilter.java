package com.llmagentsystem.security;

import com.fasterxml.jackson.databind.*;
import io.micrometer.core.instrument.*;
import jakarta.servlet.*;
import jakarta.servlet.http.*;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.TimeUnit;

@Component
public class AccessFilter extends OncePerRequestFilter {
    public record Account(String userId, String role) {}
    private final Map<String, Account> tokens = new LinkedHashMap<>();
    private final ObjectMapper mapper;
    private final MeterRegistry metrics;
    private final Counter requests;
    private static final Set<String> READERS = Set.of("/chat", "/search", "/knowledge/stats", "/skills", "/mcp", "/mcp/", "/eval/conversation");
    public AccessFilter(Environment environment, ObjectMapper mapper, MeterRegistry metrics) throws Exception {
        this.mapper = mapper; this.metrics = metrics;
        this.requests = metrics.counter("llmagentsystem_chat_requests_total");
        String raw = environment.getProperty("LLM_AGENT_SYSTEM_API_TOKENS", "").strip();
        if (raw.isBlank()) {
            if (environment.getProperty("LLM_AGENT_SYSTEM_ENV", "development").equalsIgnoreCase("production")) throw new IllegalArgumentException("production 模式必须设置 LLM_AGENT_SYSTEM_API_TOKENS");
            return;
        }
        JsonNode entries = mapper.readTree(raw);
        if (!entries.isObject() || entries.isEmpty()) throw new IllegalArgumentException("LLM_AGENT_SYSTEM_API_TOKENS 必须为非空对象");
        entries.fields().forEachRemaining(e -> {
            JsonNode value = e.getValue();
            if (e.getKey().length() < 24 || !value.path("user_id").isTextual() || value.path("user_id").asText().isBlank()
                    || !Set.of("user", "admin").contains(value.path("role").asText())) throw new IllegalArgumentException("API Token 配置无效");
            tokens.put(com.llmagentsystem.util.Hashing.sha256(e.getKey()), new Account(value.path("user_id").asText(), value.path("role").asText()));
        });
    }
    public boolean enabled() { return !tokens.isEmpty(); }
    @Override protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain) throws ServletException, IOException {
        boolean chat = req.getRequestURI().equals("/chat") && req.getMethod().equals("POST");
        long started = System.nanoTime();
        if (chat) requests.increment();
        try {
            if (enabled() && !req.getRequestURI().equals("/health") && !req.getMethod().equals("OPTIONS")) {
                String auth = Optional.ofNullable(req.getHeader("Authorization")).orElse("");
                String supplied = com.llmagentsystem.util.Hashing.sha256(auth.startsWith("Bearer ") ? auth.substring(7) : "");
                Account account = null;
                for (var entry : tokens.entrySet()) if (MessageDigest.isEqual(entry.getKey().getBytes(StandardCharsets.US_ASCII), supplied.getBytes(StandardCharsets.US_ASCII))) account = entry.getValue();
                if (account == null) { error(res, 401, "需要有效 Bearer Token"); return; }
                boolean ownEvaluation = req.getMethod().equals("GET") && req.getRequestURI().matches("/eval/jobs/[^/]+");
                if (!READERS.contains(req.getRequestURI()) && !ownEvaluation && !account.role().equals("admin")) { error(res, 403, "该接口需要管理员权限"); return; }
                req.setAttribute("llmagentsystem.account", account);
            }
            chain.doFilter(req, res);
        } finally {
            if (chat) {
                long elapsed = System.nanoTime() - started;
                io.micrometer.core.instrument.Timer.builder("llmagentsystem_chat_latency").tag("status", Integer.toString(res.getStatus())).publishPercentileHistogram().register(metrics).record(elapsed, TimeUnit.NANOSECONDS);
                if (!res.isCommitted()) res.setHeader("Server-Timing", "chat;dur=" + elapsed / 1_000_000.0);
            }
        }
    }
    private void error(HttpServletResponse response, int status, String text) throws IOException {
        response.setStatus(status); response.setContentType("application/json;charset=UTF-8"); mapper.writeValue(response.getWriter(), Map.of("detail", text));
    }
}
