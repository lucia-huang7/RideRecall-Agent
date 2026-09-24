package com.llmagentsystem.agent;

import com.llmagentsystem.intent.*;
import com.llmagentsystem.tool.*;
import com.llmagentsystem.llm.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.langchain4j.model.chat.ChatModel;
import dev.langchain4j.model.chat.request.ChatRequest;
import dev.langchain4j.model.chat.response.ChatResponse;
import dev.langchain4j.model.output.FinishReason;
import dev.langchain4j.data.message.AiMessage;
import org.junit.jupiter.api.Test;
import org.springframework.mock.env.MockEnvironment;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;
import static org.junit.jupiter.api.Assertions.*;

class AgentProfileTest {
    @Test void defaultRoleContractsMatchPythonAndConfigurationIsValidated() {
        var profiles=AgentProfiles.defaults();
        assertEquals(.3,profiles.forType(AgentType.GENERAL).temperature());assertEquals(900,profiles.forType(AgentType.GENERAL).maxOutputTokens());
        assertEquals(.1,profiles.forType(AgentType.TECHNICAL).temperature());assertEquals(1200,profiles.forType(AgentType.TECHNICAL).maxOutputTokens());
        assertEquals(0,profiles.forType(AgentType.BILLING).temperature());assertEquals(1100,profiles.forType(AgentType.BILLING).maxOutputTokens());
        assertTrue(profiles.forType(AgentType.BILLING).contract().contains("区分订单/实付/退款金额"));
        assertThrows(IllegalArgumentException.class,()->new AgentProfiles(new MockEnvironment().withProperty("LLM_AGENT_SYSTEM_TECHNICAL_TEMPERATURE","NaN")));
        assertThrows(IllegalArgumentException.class,()->new AgentProfiles(new MockEnvironment().withProperty("LLM_AGENT_SYSTEM_COMPOSER_TIMEOUT_S","0")));
    }
    @Test void roleParametersAndStructuredInputReachNativeAiServices() {
        var captured=new AtomicReference<ChatRequest>();
        ChatModel model=new ChatModel() { @Override public ChatResponse doChat(ChatRequest request) {
            captured.set(request);return ChatResponse.builder().aiMessage(AiMessage.from("已说明核验边界")).finishReason(FinishReason.STOP).build();
        }};
        var config=new AgentProfiles(new MockEnvironment().withProperty("LLM_AGENT_SYSTEM_BILLING_MODEL","billing-fixture")
                .withProperty("llmagentsystem.agents.billing.temperature","0.05").withProperty("LLM_AGENT_SYSTEM_BILLING_MAX_TOKENS","777"));
        var mapper=new ObjectMapper();var registry=new ToolRegistry(mapper);new RoleToolRegistration(registry).register();
        var ai=new AgentAiService(model,registry,mapper,PromptBudget.defaults(),LlmMetrics.isolated(),config);
        var request=AgentRequest.of("请核验账单","u","c","背景",List.of(),new IntentResult(IntentCategory.REFUND,.95,UrgencyLevel.LOW,"billing",Map.of("order_id",List.of("12345")),"",0,Map.of(),"local"));
        assertTrue(new BillingAgent(ai,null).handle(request).success());
        var call=captured.get();assertEquals("billing-fixture",call.modelName());
        assertEquals(.05,call.parameters().temperature());assertEquals(777,call.parameters().maxOutputTokens());
        assertTrue(call.messages().getFirst().toString().contains("处理流程"));
        assertTrue(call.messages().getLast().toString().contains("角色输入包"));assertTrue(call.messages().getLast().toString().contains("12345"));
        assertEquals(Set.of("check_billing_fields","compare_amounts"),new HashSet<>(call.toolSpecifications().stream().map(t->t.name()).toList()));
        assertEquals(config.fingerprint(),ai.withTools(registry.fork()).profiles().fingerprint());
    }
}
