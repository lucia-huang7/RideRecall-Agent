package com.kinderuber.agent;

import com.kinderuber.skill.SkillManager;

public class TechnicalAgent extends BaseAgent {

    public TechnicalAgent(AgentAiService aiService, SkillManager skillManager) {
        super(aiService, skillManager);
    }

    @Override
    public AgentType type() {
        return AgentType.TECHNICAL;
    }

    @Override
    protected String systemPrompt() {
        return "你是技术支持专家。专注故障排查、错误诊断、系统配置。提供步骤化方案，遇到后台操作明确升级处理。";
    }
}
