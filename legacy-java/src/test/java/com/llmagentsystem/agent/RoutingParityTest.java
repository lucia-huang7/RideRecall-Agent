package com.llmagentsystem.agent;

import com.llmagentsystem.intent.*;
import com.llmagentsystem.trace.RequestTraceStore;
import org.junit.jupiter.api.Test;
import java.time.*;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class RoutingParityTest {
    BaseAgent agent(AgentType type) {
        BaseAgent agent = mock(BaseAgent.class);
        when(agent.stats()).thenReturn(new AgentStats());
        when(agent.handle(any())).thenReturn(new AgentResponse(type, type + "答复", true, 1, 1, false, "", true, false, false, ""));
        return agent;
    }
    @Test void compoundMessageIncludesLowScoringSecondaryDomain() {
        var orchestrator = new AgentOrchestrator(null, Map.of(AgentType.GENERAL,List.of(agent(AgentType.GENERAL)), AgentType.TECHNICAL,List.of(agent(AgentType.TECHNICAL)), AgentType.BILLING,List.of(agent(AgentType.BILLING))), new RequestTraceStore());
        var request = new AgentRequest("登录报错，同时申请退款", "u", "c", "", List.of(), Map.of(), IntentCategory.TECHNICAL_LOGIN, "technical", UrgencyLevel.LOW, .99, "r", new ArrayList<>());
        var result = orchestrator.run(request);
        assertEquals(AgentType.TECHNICAL, result.primaryAgent());
        assertEquals(List.of(AgentType.BILLING), result.supportingAgents());
        assertTrue(result.response().contains("BILLING答复"));
    }
    @Test void monitorCoolingSpecialistIsExcludedFromRouting() {
        var technical = agent(AgentType.TECHNICAL);
        for (int i=0;i<5;i++) technical.stats().record("r"+i,true,9000);
        technical.stats().setMonitorPenalty(.9);
        var orchestrator = new AgentOrchestrator(null, Map.of(AgentType.GENERAL,List.of(agent(AgentType.GENERAL)), AgentType.TECHNICAL,List.of(technical)), new RequestTraceStore());
        var result = orchestrator.run(new AgentRequest("登录报错", "u", "c", "", List.of(), Map.of(), IntentCategory.TECHNICAL_LOGIN, "technical", UrgencyLevel.LOW, .99, "r", new ArrayList<>()));
        assertEquals(AgentType.GENERAL,result.primaryAgent()); verify(technical,never()).handle(any());
    }
    @Test void oneRecoveryProbeRestoresMonitorDegradedAgent() {
        class MutableClock extends Clock {
            Instant time=Instant.parse("2026-10-17T00:00:00Z"); public ZoneId getZone(){return ZoneOffset.UTC;} public Clock withZone(ZoneId z){return this;} public Instant instant(){return time;}
        }
        var clock=new MutableClock();var stats=new AgentStats(clock);
        for(int i=0;i<5;i++) stats.record("r"+i,true,9000);
        stats.setMonitorPenalty(.9);assertFalse(stats.available());assertFalse(stats.admit("early"));
        clock.time=clock.time.plusSeconds(61);assertTrue(stats.available());assertTrue(stats.admit("probe"));assertFalse(stats.admit("second"));
        stats.record("probe",true,10);assertTrue(stats.available());assertEquals(0,stats.monitorPenalty());assertEquals("healthy",stats.recoveryState());
    }
}
