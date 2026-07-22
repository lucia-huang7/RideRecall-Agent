package com.kinderuber.agent;

import com.kinderuber.llm.LlmGateway;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.stereotype.Service;


@Service
public class AnswerVerifier {

    private final LlmGateway llmGateway;
    private final ObjectMapper objectMapper;
    private final com.kinderuber.llm.LlmMetrics metrics;

    @org.springframework.beans.factory.annotation.Autowired
    public AnswerVerifier(LlmGateway llmGateway, ObjectMapper objectMapper, com.kinderuber.llm.LlmMetrics metrics) {
        this.llmGateway = llmGateway; this.objectMapper = objectMapper; this.metrics = metrics;
    }
    public AnswerVerifier(LlmGateway llmGateway, ObjectMapper objectMapper) {
        this(llmGateway, objectMapper, com.kinderuber.llm.LlmMetrics.isolated());
    }

    public VerificationResult verify(String question, String answer, String context) {
        String prompt = """
                你是客服回答质量校验器。判断回答是否解决用户问题、是否基于上下文、是否需要转人工。
                用户问题: %s
                回答: %s
                上下文: %s
                返回 JSON: {"pass":true,"grounded":true,"need_escalation":false,"reason":"..."}
                """.formatted(question, answer, context == null ? "" : context);
        try {
            String raw = com.kinderuber.util.Tasks.within(java.time.Duration.ofSeconds(5), () -> llmGateway.chatRequired("", prompt, 0.0, 512));
            int start = raw.indexOf('{');
            int end = raw.lastIndexOf('}');
            var data = objectMapper.readTree(raw.substring(start, end + 1));
            for (String field : new String[]{"pass", "grounded", "need_escalation"})
                if (!data.path(field).isBoolean()) throw new IllegalArgumentException("invalid_verification_result");
            if (!data.path("pass").asBoolean() || !data.path("grounded").asBoolean()) metrics.fallback("verification", "rejected");
            return new VerificationResult(
                    data.path("pass").asBoolean(), data.path("grounded").asBoolean(),
                    data.path("need_escalation").asBoolean(), data.path("reason").asText(""), true
            );
        } catch (Exception ex) {
            metrics.fallback("verification", "unavailable");
            return new VerificationResult(false, false, true, "verification_unavailable", false);
        }
    }

    public record VerificationResult(boolean pass, boolean grounded, boolean needEscalation, String reason, boolean available) {
        public boolean accepted() { return available && pass && grounded; }
    }
}
