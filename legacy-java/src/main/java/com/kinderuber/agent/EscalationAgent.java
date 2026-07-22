package com.kinderuber.agent;

import com.kinderuber.skill.SkillManager;
import com.kinderuber.trace.ToolCallTrace;
import java.util.List;

/** Deterministic handoff: no model call and no simulated ticket submission. */
public class EscalationAgent extends BaseAgent {
    public EscalationAgent(AgentAiService aiService, SkillManager skills) { super(aiService, skills); }
    @Override public AgentType type() { return AgentType.ESCALATION; }
    @Override protected String systemPrompt() { return "负责人工升级交接，不模拟后台操作。"; }
    @Override public AgentResponse handle(AgentRequest request) {
        long start = System.nanoTime();
        var summary = HandoffSummary.create(request, request.urgency() == com.kinderuber.intent.UrgencyLevel.CRITICAL
                ? "紧急或高风险问题，需要人工优先核验" : "用户请求人工协助或超出当前自动处理范围");
        long elapsed = (System.nanoTime() - start) / 1_000_000;
        stats().record(request.requestId(), true, elapsed);
        return new AgentResponse(type(), summary.toCustomerText(), true, 1, elapsed, true, "", true, false, false, "",
                List.of(new ToolCallTrace("create_handoff_summary", true, false, false, false, elapsed, "")));
    }
}
