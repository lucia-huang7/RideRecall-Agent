package com.kinderuber.agent;

import com.kinderuber.skill.SkillManager;

public class GeneralAgent extends BaseAgent {

    public GeneralAgent(AgentAiService aiService, SkillManager skillManager) {
        super(aiService, skillManager);
    }

    @Override
    public AgentType type() {
        return AgentType.GENERAL;
    }

    @Override
    protected String systemPrompt() {
        return "你是 KinderUber 智能客服。友好、简洁地回答用户问题。如果问题超出能力范围，说明原因并建议转接专业客服。";
    }
}
