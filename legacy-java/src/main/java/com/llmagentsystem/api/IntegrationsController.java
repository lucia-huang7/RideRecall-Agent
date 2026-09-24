package com.llmagentsystem.api;

import com.llmagentsystem.integration.StandardMcpClient;
import com.llmagentsystem.intent.IntentEmbedding;
import com.llmagentsystem.tool.*;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;
import org.springframework.web.bind.annotation.*;
import java.util.*;

@RestController
public class IntegrationsController {
    private final IntentEmbedding embedding;
    private final StandardMcpClient mcp;
    private final ToolRegistry tools;
    private final com.llmagentsystem.storage.SemanticStorage semantic;
    public IntegrationsController(IntentEmbedding embedding, StandardMcpClient mcp, ToolRegistry tools, com.llmagentsystem.storage.SemanticStorage semantic) { this.semantic = semantic; this.embedding = embedding; this.mcp = mcp; this.tools = tools; }
    @GetMapping("/integrations/embedding") public Map<String, Object> embedding() { return embedding.status(); }
    @GetMapping("/integrations/mcp") public Map<String, Object> mcp() { return mcp.summary(); }
    @GetMapping("/integrations/semantic") public Map<String, Object> semantic() { return semantic.summary(); }
    public record Call(@NotBlank @Size(max = 64) @com.fasterxml.jackson.annotation.JsonProperty("name") @com.fasterxml.jackson.annotation.JsonAlias({"tool", "tool_name"}) String tool, Map<String, Object> arguments) {}
    @PostMapping("/integrations/mcp/call") public ToolResult<Object> call(@Valid @RequestBody Call input) {
        if (!tools.descriptor(input.tool()).getOrDefault("source", "").equals("external_mcp")) throw new org.springframework.web.server.ResponseStatusException(org.springframework.http.HttpStatus.NOT_FOUND, "MCP 工具未连接或未在配置白名单中");
        return tools.call(input.tool(), input.arguments() == null ? Map.of() : input.arguments(), "admin");
    }
}
