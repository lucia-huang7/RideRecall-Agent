package com.llmagentsystem.tool;

import com.llmagentsystem.agent.*;
import com.llmagentsystem.intent.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import java.math.BigDecimal;
import java.util.*;
import java.util.concurrent.*;
import static org.junit.jupiter.api.Assertions.*;

class RoleToolsTest {
    ToolRegistry registry() { var tools = new ToolRegistry(new ObjectMapper()); new RoleToolRegistration(tools).register(); return tools; }
    AgentRequest request(String user, Map<String,List<String>> entities) {
        return AgentRequest.of("咨询",user,"conversation-"+user,"背景",List.of(),
                new IntentResult(IntentCategory.REFUND,.9,UrgencyLevel.LOW,"billing",entities,"",0,Map.of(),"local"));
    }
    @Test void exposesOnlyTheRoleToolWhitelist() {
        var tools=registry();
        assertEquals(Set.of("lookup_error_code","build_diagnostic_plan"),new HashSet<>(tools.available("technical").stream().map(ToolDefinition::name).toList()));
        assertFalse(tools.call("compare_amounts",Map.of("amount_a",10,"amount_b",2),"general",request("u",Map.of())).success());
        assertFalse(tools.call("check_billing_fields",Map.of(),"billing").success());
        assertFalse(tools.call("check_billing_fields",Map.of("entities",Map.of("order_id",List.of("forged"))),"billing",request("u",Map.of())).success());
    }
    @Test void concurrentUsersNeverShareRequestFieldsOrCachedResults() throws Exception {
        var tools=registry();
        try(var executor=Executors.newVirtualThreadPerTaskExecutor()) {
            var tasks=List.of("alice","bob").stream().map(user->executor.submit(()->tools.call("inspect_request_context",Map.of(),"general",request(user,Map.of("order_id",List.of(user+"-order")))))).toList();
            for(int i=0;i<tasks.size();i++) {
                var result=tasks.get(i).get();assertTrue(result.success());assertFalse(result.cached());
                var fields=(Map<?,?>)((Map<?,?>)result.data()).get("entities");assertEquals(List.of(i==0?"alice-order":"bob-order"),fields.get("order_id"));
            }
        }
    }
    @Test void billingChecksKnownFieldsWithoutClaimingRefundApproval() {
        var req=request("u",Map.of("order_id",List.of("12345"),"amount",List.of("199.90"),"date",List.of("今天")));
        var data=(Map<?,?>)registry().call("check_billing_fields",Map.of("payment_channel","微信"),"billing",req).data();
        assertEquals(List.of(),data.get("missing_fields"));assertEquals(false,data.get("can_confirm_refund"));
    }
    @Test void amountsUseDecimalArithmeticAndRejectInvalidInputs() {
        var tools=registry(); var req=request("u",Map.of());
        var result=tools.call("compare_amounts",Map.of("amount_a",new BigDecimal("199.90"),"amount_b",new BigDecimal("179.80")),"billing",req);
        assertTrue(result.success());assertEquals(new BigDecimal("20.10"),((Map<?,?>)result.data()).get("difference"));
        assertFalse(tools.call("compare_amounts",Map.of("amount_a","199.90","amount_b",10),"billing",req).success());
    }
    @Test void technicalToolsExplainErrorsWithoutAccessingOrModifyingSystems() {
        var tools=registry();var req=request("u",Map.of());
        var error=(Map<?,?>)tools.call("lookup_error_code",Map.of("error_code","401"),"technical",req).data();
        assertEquals("认证失败",error.get("meaning"));assertEquals(false,error.get("server_log_checked"));
        var plan=(Map<?,?>)tools.call("build_diagnostic_plan",Map.of("environment","Docker","reproduced",true),"technical",req).data();
        assertEquals(4,((List<?>)plan.get("diagnostic_steps")).size());assertEquals(false,plan.get("configuration_changed"));
        assertFalse(tools.call("build_diagnostic_plan",Map.of("environment","Docker","reproduced","true"),"technical",req).success());
    }
    @Test void requiredFieldsDoNotAskForAnOrderAlreadyProvided() {
        var base=request("u",Map.of("order_id",List.of("12345")));
        var req=base.withIntent(new IntentResult(IntentCategory.LOGISTICS,1,UrgencyLevel.LOW,"query",base.entities(),"",0,Map.of(),"local"));
        var data=(Map<?,?>)registry().call("suggest_required_fields",Map.of(),"general",req).data();
        assertEquals(List.of(),data.get("required_fields"));
    }
    @Test void handoffIsDeterministicAndNeverCreatesATicketOrCallsAModel() {
        var req=request("u",Map.of("order_id",List.of("12345")));
        var summary=(HandoffSummary)registry().call("create_handoff_summary",Map.of("reason","用户要求人工"),"escalation",req).data();
        assertFalse(summary.ticketCreated());assertEquals(List.of("12345"),summary.entities().get("order_id"));
        var response=new EscalationAgent(null,null).handle(req);
        assertTrue(response.escalate());assertTrue(response.content().contains("12345"));assertTrue(response.content().contains("未创建或提交工单"));
        assertEquals("create_handoff_summary",response.toolCalls().getFirst().toolName());
    }
}
