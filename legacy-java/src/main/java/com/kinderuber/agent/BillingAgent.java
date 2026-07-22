package com.kinderuber.agent;

import com.kinderuber.skill.SkillManager;

public class BillingAgent extends BaseAgent {

    public BillingAgent(AgentAiService aiService, SkillManager skillManager) {
        super(aiService, skillManager);
    }

    @Override
    public AgentType type() {
        return AgentType.BILLING;
    }

    @Override
    protected String systemPrompt() {
        return "你是账单服务专家。专注账单查询、退款申请、发票问题、订阅管理。涉及实际退款操作时说明需要人工审核。";
    }
}
