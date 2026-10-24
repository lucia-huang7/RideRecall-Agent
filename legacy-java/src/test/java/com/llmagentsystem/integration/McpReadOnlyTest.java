package com.llmagentsystem.integration;

import com.llmagentsystem.tool.ToolRegistry;
import com.fasterxml.jackson.databind.*;
import io.modelcontextprotocol.client.McpSyncClient;
import io.modelcontextprotocol.spec.McpSchema;
import org.junit.jupiter.api.Test;
import org.springframework.mock.env.MockEnvironment;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class McpReadOnlyTest {
    private final ObjectMapper mapper=new ObjectMapper();
    McpSchema.Tool tool(Boolean readOnly, Boolean destructive) throws Exception {
        var schema=new McpSchema.JsonSchema("object",Map.of(),List.of(),false,null,null);
        return McpSchema.Tool.builder().name("catalog_fixture").description("Offline fixture").inputSchema(schema)
                .annotations(readOnly==null && destructive==null ? null : new McpSchema.ToolAnnotations(null,readOnly,destructive,null,null,null)).build();
    }
    @Test void onlyExplicitlyReadOnlyNonDestructiveToolsAreAllowed() throws Exception {
        assertFalse(StandardMcpClient.safeReadOnly(tool(null,null)));
        assertFalse(StandardMcpClient.safeReadOnly(tool(false,false)));
        assertFalse(StandardMcpClient.safeReadOnly(tool(true,true)));
        assertTrue(StandardMcpClient.safeReadOnly(tool(true,false)));
        assertTrue(StandardMcpClient.safeReadOnly(tool(true,null)));
    }
    @Test void discoveryRemovesAPreviouslySafeToolWhenItsAnnotationsChange() throws Exception {
        var registry=new ToolRegistry(mapper);
        var adapter=new StandardMcpClient(registry,new MockEnvironment(),"unused.json",false);
        var remote=mock(McpSyncClient.class);
        when(remote.listTools()).thenReturn(new McpSchema.ListToolsResult(List.of(tool(true,false)),null),
                new McpSchema.ListToolsResult(List.of(tool(false,true)),null));
        Class<?> type=Arrays.stream(StandardMcpClient.class.getDeclaredClasses()).filter(c->c.getSimpleName().equals("Connection")).findFirst().orElseThrow();
        var constructor=type.getDeclaredConstructors()[0];constructor.setAccessible(true);
        var connection=constructor.newInstance(mapper.readTree("{\"read_only\":true,\"allowed_tools\":[\"catalog_fixture\"],\"agents\":[\"technical\"]}"));
        var field=type.getDeclaredField("client");field.setAccessible(true);field.set(connection,remote);
        var discover=StandardMcpClient.class.getDeclaredMethod("discover",String.class,type);discover.setAccessible(true);
        discover.invoke(adapter,"fixture",connection);assertEquals(1,registry.available("technical").size());
        discover.invoke(adapter,"fixture",connection);assertTrue(registry.available("technical").isEmpty());
        verify(remote,never()).callTool(any(McpSchema.CallToolRequest.class));
    }
}
