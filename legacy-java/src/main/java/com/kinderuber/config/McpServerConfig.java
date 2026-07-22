package com.kinderuber.config;

import com.kinderuber.knowledge.KnowledgeBaseService;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.modelcontextprotocol.json.jackson.JacksonMcpJsonMapper;
import io.modelcontextprotocol.server.*;
import io.modelcontextprotocol.server.transport.HttpServletStatelessServerTransport;
import io.modelcontextprotocol.spec.McpSchema;
import org.springframework.boot.autoconfigure.condition.ConditionalOnWebApplication;
import org.springframework.boot.web.servlet.ServletRegistrationBean;
import org.springframework.context.annotation.*;
import java.util.*;

@Configuration
@ConditionalOnWebApplication
public class McpServerConfig {
    @Bean
    HttpServletStatelessServerTransport mcpTransport() {
        return HttpServletStatelessServerTransport.builder().messageEndpoint("/mcp").jsonMapper(new JacksonMcpJsonMapper(new ObjectMapper())).build();
    }
    @Bean
    ServletRegistrationBean<HttpServletStatelessServerTransport> mcpServlet(HttpServletStatelessServerTransport transport) {
        var registration = new ServletRegistrationBean<>(transport, "/mcp", "/mcp/");
        registration.setAsyncSupported(true); return registration;
    }
    @Bean(destroyMethod = "close")
    McpStatelessSyncServer mcpServer(HttpServletStatelessServerTransport transport, KnowledgeBaseService knowledge) throws Exception {
        ObjectMapper mapper = new ObjectMapper();
        var schema = new McpSchema.JsonSchema("object", Map.of("query", Map.of("type", "string", "minLength", 1, "maxLength", 2000), "top_k", Map.of("type", "integer", "minimum", 1, "maximum", 20)), List.of("query"), false, null, null);
        var tool = McpSchema.Tool.builder().name("search_local_knowledge").description("搜索 KinderUber 本地客服知识库").inputSchema(schema)
                .annotations(new McpSchema.ToolAnnotations(null, true, false, true, false, false)).build();
        return McpServer.sync(transport).serverInfo("kinderuber-java", "0.1.0")
                .capabilities(McpSchema.ServerCapabilities.builder().tools(false).resources(false, false).build())
                .resources(new McpStatelessServerFeatures.SyncResourceSpecification(
                        McpSchema.Resource.builder().uri("kinderuber://knowledge/stats").name("knowledge_stats").mimeType("application/json").build(),
                        (context, request) -> new McpSchema.ReadResourceResult(List.of(new McpSchema.TextResourceContents("kinderuber://knowledge/stats", "application/json", "{\"total_chunks\":" + knowledge.docCount() + "}")))))
                .toolCall(tool, (context, request) -> {
                    try {
                        Map<String, Object> args = request.arguments();
                        if (!(args.get("query") instanceof String query) || query.isBlank() || query.length() > 2000) throw new IllegalArgumentException("Invalid query");
                        Object top = args.getOrDefault("top_k", 5);
                        if (!(top instanceof Integer) || (int) top < 1 || (int) top > 20 || args.keySet().stream().anyMatch(k -> !Set.of("query", "top_k").contains(k))) throw new IllegalArgumentException("Invalid arguments");
                        return new McpSchema.CallToolResult(mapper.writeValueAsString(Map.of("results", knowledge.search(query, (int) top))), false);
                    } catch (Exception ex) { return new McpSchema.CallToolResult("知识库检索失败：参数无效或存储不可用", true); }
                }).build();
    }
}
