package com.kinderuber.tool;

import com.kinderuber.agent.*;
import com.kinderuber.trace.RequestTraceStore;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.atomic.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class WriteToolGuardTest {
    ToolDefinition write(AtomicInteger calls) { return new ToolDefinition("write_fixture","fixture",Map.of("type","object"),Set.of("billing"),false,Duration.ofSeconds(1),Duration.ZERO,"external_mcp","fixture","write_fixture",args->{calls.incrementAndGet();throw new IllegalStateException("outcome_unknown");}); }
    @Test void writesRequireOptInAndDurableReceiptAndCannotRepeatAfterFailure() {
        var count=new AtomicInteger();var off=new ToolRegistry(new ObjectMapper());off.register(write(count));assertTrue(off.available("billing").isEmpty());
        var on=new ToolRegistry(new ObjectMapper(),true);on.register(write(count));var plain=AgentRequest.of("fixture","u","c","",List.of());
        assertFalse(on.call("write_fixture",Map.of(),"billing",plain).success());
        var receiptCount=new AtomicInteger();var guarded=plain.withOperations(new ToolExecutionGuard(name->receiptCount.incrementAndGet()));
        assertFalse(on.call("write_fixture",Map.of(),"billing",guarded).success());
        assertFalse(on.call("write_fixture",Map.of(),"billing",guarded).success());
        assertEquals(1,count.get());assertEquals(1,receiptCount.get());assertTrue(guarded.operations().writeAttempted());
        assertTrue(on.fork().available("billing").isEmpty());
    }
    @Test void receiptFailurePreventsSideEffect() {
        var count=new AtomicInteger();var registry=new ToolRegistry(new ObjectMapper(),true);registry.register(write(count));
        var request=AgentRequest.of("fixture","u","c","",List.of()).withOperations(new ToolExecutionGuard(name->{throw new IllegalStateException("redis_down");}));
        assertFalse(registry.call("write_fixture",Map.of(),"billing",request).success());assertEquals(0,count.get());assertFalse(request.operations().writeAttempted());
    }
    @Test void specialistCannotFallBackAfterAnyWriteWasAttempted() throws Exception {
        var general=mock(BaseAgent.class);var billing=mock(BaseAgent.class);when(general.stats()).thenReturn(new AgentStats());when(billing.stats()).thenReturn(new AgentStats());
        var request=new AgentRequest("退款", "u","c","",List.of(),Map.of(),com.kinderuber.intent.IntentCategory.REFUND,"billing",com.kinderuber.intent.UrgencyLevel.LOW,1,"r",new ArrayList<>()).withOperations(new ToolExecutionGuard(name->{}));
        when(billing.handle(any())).thenAnswer(inv->{request.operations().beforeWrite("fixture",Map.of());return new AgentResponse(AgentType.BILLING,"失败",false,0,0,false,"",false,false,false,"failed");});
        var orch=new AgentOrchestrator(null,Map.of(AgentType.GENERAL,List.of(general),AgentType.BILLING,List.of(billing)),new RequestTraceStore());
        assertEquals("failed",orch.run(request).status());verify(general,never()).handle(any());
    }
}
