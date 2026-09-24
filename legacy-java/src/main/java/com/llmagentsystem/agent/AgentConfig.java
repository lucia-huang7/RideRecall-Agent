package com.llmagentsystem.agent;

import com.llmagentsystem.skill.SkillManager;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.List;
import java.util.Map;

@Configuration
public class AgentConfig {

    @Bean
    Map<AgentType, List<BaseAgent>> agentPool(AgentAiService aiService, SkillManager skillManager, com.llmagentsystem.config.LlmAgentSystemProperties properties) {
        Map<AgentType, List<BaseAgent>> pool = Map.of(
                AgentType.GENERAL, List.of(new GeneralAgent(aiService, skillManager)),
                AgentType.TECHNICAL, List.of(new TechnicalAgent(aiService, skillManager)),
                AgentType.BILLING, List.of(new BillingAgent(aiService, skillManager)),
                AgentType.ESCALATION, List.of(new EscalationAgent(aiService, skillManager))
        );
        pool.values().forEach(agents -> agents.forEach(agent -> agent.stats().configure(properties.getMonitor().getFallbackPenalty(), properties.getMonitor().getCooldownSeconds())));
        return pool;
    }
}
