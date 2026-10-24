package com.llmagentsystem.agent;

import dev.langchain4j.model.chat.request.ChatRequestParameters;
import dev.langchain4j.service.Result;
import dev.langchain4j.service.UserMessage;

/** LangChain4j owns message exchange and tool execution; routing remains in the business layer. */
public interface AgentAssistant {
    Result<String> reply(@UserMessage String message, ChatRequestParameters parameters);
}
