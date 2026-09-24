package com.llmagentsystem.llm;

import com.llmagentsystem.config.LlmAgentSystemProperties;
import dev.langchain4j.data.message.*;
import dev.langchain4j.model.chat.ChatModel;
import dev.langchain4j.model.chat.request.ChatRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Component;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

@Component
public class LangChain4jLlmGateway implements LlmGateway {
    private static final Logger log = LoggerFactory.getLogger(LangChain4jLlmGateway.class);
    private final ChatModel model;
    private final LlmAgentSystemProperties properties;
    private final LlmMetrics metrics;

    @org.springframework.beans.factory.annotation.Autowired
    public LangChain4jLlmGateway(ObjectProvider<ChatModel> models, LlmAgentSystemProperties properties, PromptBudget budget, LlmMetrics metrics) {
        this.model = new ManagedChatModel(models.getIfAvailable(), budget, metrics, "auxiliary");
        this.properties = properties; this.metrics = metrics;
    }
    public LangChain4jLlmGateway(ObjectProvider<ChatModel> models, LlmAgentSystemProperties properties) {
        this(models, properties, new PromptBudget(properties, new com.fasterxml.jackson.databind.ObjectMapper()), LlmMetrics.isolated());
    }

    @Override
    public String chat(String system, String user, double temperature, int tokens) {
        try { return chatRequired(system, user, temperature, tokens); }
        catch (RuntimeException ex) {
            if (!properties.getLlm().isFallbackEnabled()) throw ex;
            metrics.fallback("text", LlmMetrics.category(ex));
            log.warn("LangChain4j chat failed, using local fallback: {}", ex.getClass().getSimpleName());
            return fallback(user);
        }
    }

    @Override
    public String chatRequired(String system, String user, double temperature, int tokens) {
        return judgeChat(system, user, temperature, tokens, "");
    }

    @Override
    public String judgeChat(String system, String user, double temperature, int tokens, String modelName) {
        List<ChatMessage> messages = new ArrayList<>();
        if (system != null && !system.isBlank()) messages.add(SystemMessage.from(system));
        messages.add(UserMessage.from(user == null || user.isBlank() ? " " : user));
        var request = ChatRequest.builder().messages(messages).temperature(temperature).maxOutputTokens(tokens);
        if (modelName != null && !modelName.isBlank()) request.modelName(modelName);
        AiMessage answer = invoke(request.build());
        if (answer.text() == null || answer.text().isBlank()) throw new IllegalStateException("empty_model_response");
        return answer.text();
    }

    private AiMessage invoke(ChatRequest request) {
        if (model == null) throw new IllegalStateException("model_unavailable");
        var response = model.chat(request);
        if (response == null || response.aiMessage() == null) throw new IllegalStateException("empty_model_response");
        return response.aiMessage();
    }

    private String fallback(String user) {
        String prompt = user == null ? "" : user.toLowerCase(Locale.ROOT);
        if (prompt.contains("退款") || prompt.contains("refund"))
            return "当前模型服务不可用。退款申请需要核实订单和相关政策，请稍后重试或联系人工客服确认。";
        if (prompt.contains("报错") || prompt.contains("登录") || prompt.contains("error"))
            return "当前模型服务不可用。请记录错误码和发生时间，稍后重试或联系技术支持。";
        return "我已收到你的问题。当前模型服务不可用，系统返回了本地降级回复；请稍后重试或转人工处理。";
    }
}
