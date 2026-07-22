package com.kinderuber.api;

import com.kinderuber.agent.AgentOrchestrator;
import com.kinderuber.api.dto.BatchDocInput;
import com.kinderuber.api.dto.ChatRequest;
import com.kinderuber.api.dto.ChatResponse;
import com.kinderuber.api.dto.EvalRunRequest;
import com.kinderuber.knowledge.KnowledgeBaseService;
import com.kinderuber.knowledge.SearchResult;
import com.kinderuber.monitor.PerformanceMonitor;
import com.kinderuber.skill.SkillManager;
import com.kinderuber.tool.KnowledgeToolManager;
import com.kinderuber.tool.ToolResult;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.micrometer.prometheusmetrics.PrometheusMeterRegistry;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

@RestController
@Tag(name = "KinderUber API", description = "智能客服对话、知识库、监控和评测接口")
public class KinderUberController {

    private final com.kinderuber.chat.ChatService chats;
    private final com.kinderuber.evaluation.EvaluationJobs jobs;
    private final com.kinderuber.security.AccessFilter access;
    private final AgentOrchestrator orchestrator;
    private final KnowledgeToolManager knowledgeToolManager;
    private final KnowledgeBaseService knowledgeBaseService;
    private final PerformanceMonitor performanceMonitor;
    private final SkillManager skillManager;
    private final ObjectMapper objectMapper;
    private final PrometheusMeterRegistry prometheusMeterRegistry;

    public KinderUberController(
            com.kinderuber.chat.ChatService chats,
            com.kinderuber.evaluation.EvaluationJobs jobs,
            com.kinderuber.security.AccessFilter access,
            AgentOrchestrator orchestrator,
            KnowledgeToolManager knowledgeToolManager,
            KnowledgeBaseService knowledgeBaseService,
            PerformanceMonitor performanceMonitor,
            SkillManager skillManager,
            ObjectMapper objectMapper,
            PrometheusMeterRegistry prometheusMeterRegistry
    ) {
        this.chats = chats; this.jobs = jobs; this.access = access;
        this.orchestrator = orchestrator;
        this.knowledgeToolManager = knowledgeToolManager;
        this.knowledgeBaseService = knowledgeBaseService;
        this.performanceMonitor = performanceMonitor;
        this.skillManager = skillManager;
        this.objectMapper = objectMapper;
        this.prometheusMeterRegistry = prometheusMeterRegistry;
    }

    @GetMapping("/health")
    @Operation(summary = "健康检查", description = "返回服务状态和 Agent 路由统计。")
    public Map<String, Object> health() {
        return Map.of("status", "ok", "agents", access.enabled() ? Map.of() : orchestrator.stats());
    }

    @PostMapping("/chat")
    @Operation(summary = "智能客服对话", description = "执行完整对话链路：记忆读取、意图识别、多 Agent 路由、按需调用工具、回答校验和原子记忆提交。")
    public ResponseEntity<ChatResponse> chat(@Valid @RequestBody ChatRequest request, jakarta.servlet.http.HttpServletRequest http) {
        var account = (com.kinderuber.security.AccessFilter.Account) http.getAttribute("kinderuber.account");
        ChatResponse response = chats.chat(request, account == null ? null : account.userId());
        return ResponseEntity.status(response.status().equals("failed") ? 503 : 200).body(response);
    }

    @PostMapping("/search")
    @Operation(summary = "知识库检索", description = "对用户查询做查询改写、并行召回和 LLM rerank。")
    public Map<String, Object> search(
            @Parameter(description = "检索关键词或用户问题", example = "退款多久能到账") @RequestParam String query,
            @Parameter(description = "返回结果数量", example = "5") @RequestParam(name = "top_k", required = false) Integer topK,
            @Parameter(description = "兼容旧 Java 客户端") @RequestParam(name = "topK", required = false) Integer legacyTopK) {
        ToolResult<List<SearchResult>> result = knowledgeToolManager.searchWithRewrite(query, topK != null ? topK : legacyTopK != null ? legacyTopK : 5);
        return Map.of("query", query, "results", result.data(), "reranked", result.reranked(), "success", result.success(), "error", result.error() == null ? "" : result.error());
    }

    @PostMapping("/knowledge/add")
    @Operation(summary = "批量添加知识文档", description = "将文档切片后写入 Java 版持久化知识库。")
    public Map<String, Object> addKnowledge(@Valid @RequestBody BatchDocInput input) {
        List<Map<String, String>> docs = input.documents().stream()
                .map(d -> { Map<String, String> doc = new LinkedHashMap<>(); doc.put("title", d.title()); doc.put("content", d.content()); if (d.documentId() != null) doc.put("document_id", d.documentId()); return doc; })
                .toList();
        int added = knowledgeBaseService.addDocuments(docs);
        return Map.of("added_chunks", added, "total_chunks", knowledgeBaseService.docCount());
    }

    @PostMapping(value = "/knowledge/upload", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    @Operation(summary = "上传知识库文件", description = "支持上传 .txt、.md、.json 文件，最大 10MB。JSON 文件格式为数组：[{\"title\":\"...\",\"content\":\"...\"}]。")
    public Map<String, Object> uploadKnowledge(@Parameter(description = "知识库文件") @RequestParam("file") MultipartFile file) throws Exception {
        if (file.getSize() > 10L * 1024 * 1024) {
            throw new IllegalArgumentException("文件大小超过 10MB 限制");
        }
        String filename = file.getOriginalFilename() == null ? "unknown" : file.getOriginalFilename();
        if (!filename.toLowerCase(Locale.ROOT).matches(".*\\.(txt|md|json)$")) throw new IllegalArgumentException("仅支持 .txt、.md、.json 文件");
        String text = java.nio.charset.StandardCharsets.UTF_8.newDecoder().decode(java.nio.ByteBuffer.wrap(file.getBytes())).toString();
        List<Map<String, String>> docs;
        if (filename.toLowerCase(Locale.ROOT).endsWith(".json")) {
            docs = parseJsonDocs(text);
        } else {
            String title = filename.contains(".") ? filename.substring(0, filename.lastIndexOf('.')) : filename;
            docs = List.of(Map.of("title", title, "content", text));
        }
        int added = knowledgeBaseService.addDocuments(docs);
        return Map.of("message", "文件 " + filename + " 导入成功", "added_chunks", added, "total_chunks", knowledgeBaseService.docCount());
    }

    @GetMapping("/knowledge/stats")
    @Operation(summary = "知识库统计", description = "返回当前知识库片段数量。")
    public Map<String, Object> knowledgeStats() {
        return Map.of("total_chunks", knowledgeBaseService.docCount());
    }

    @GetMapping("/monitor")
    @Operation(summary = "监控摘要", description = "返回 Agent 指标、工具统计、告警和优化建议。")
    public Map<String, Object> monitor() {
        return performanceMonitor.summary();
    }

    @GetMapping("/trace/tool/{requestId}")
    @Operation(summary = "查看单次请求的工具轨迹", description = "返回指定 requestId 对应的工具调用详情。")
    public Map<String, Object> toolTrace(@Parameter(description = "请求 ID") @org.springframework.web.bind.annotation.PathVariable String requestId) {
        return orchestrator.getToolTrace(requestId)
                .<Map<String, Object>>map(trace -> Map.of("request_id", requestId, "found", true, "trace", trace))
                .orElseGet(() -> Map.of("request_id", requestId, "found", false, "trace", Map.of()));
    }

    @GetMapping("/trace/tools")
    @Operation(summary = "查看最近工具轨迹", description = "返回最近 N 次请求的工具调用详情。")
    public Map<String, Object> recentToolTraces(@RequestParam(defaultValue = "20") int limit) {
        return Map.of("items", orchestrator.getRecentToolTraces(limit));
    }

    @GetMapping("/skills")
    @Operation(summary = "Skills 摘要", description = "查看当前已加载的动态 Skills，便于确认热加载结果和排查解析错误。")
    public Map<String, Object> skills() {
        return skillManager.summary();
    }

    @PostMapping("/skills/reload")
    @Operation(summary = "重新加载 Skills", description = "运行时重新扫描 Skill 目录，不需要重启服务。")
    public Map<String, Object> reloadSkills() {
        skillManager.reload();
        return skillManager.summary();
    }

    @GetMapping(value = "/metrics", produces = "text/plain; version=0.0.4; charset=utf-8")
    @Operation(summary = "Prometheus 指标", description = "返回 Prometheus 文本格式指标，兼容 Python 版 /metrics 路径。")
    public ResponseEntity<String> metrics() {
        return ResponseEntity.ok(prometheusMeterRegistry.scrape());
    }

    @PostMapping(value = "/eval/run", consumes = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "运行评测", description = "运行意图识别和对话质量评测；请求体为空时使用内置默认用例。")
    public ResponseEntity<Map<String, Object>> eval(@Valid @RequestBody(required = false) EvalRunRequest request, jakarta.servlet.http.HttpServletRequest http) {
        return ResponseEntity.accepted().body(jobs.submit(request, authenticatedUser(http)));
    }

    @PostMapping(value = "/eval/conversation", consumes = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "评测当前用户的真实会话", description = "对提交时保存的原始问答快照评分；不生成新回复、不调用业务工具、不写回归基线。")
    public ResponseEntity<Map<String, Object>> evalConversation(@Valid @RequestBody com.kinderuber.api.dto.ConversationEvalRequest request,
                                                               jakarta.servlet.http.HttpServletRequest http) {
        return ResponseEntity.accepted().body(jobs.submitConversation(request, authenticatedUser(http)));
    }

    @GetMapping("/eval/jobs/{jobId}")
    public Map<String, Object> evalJob(@org.springframework.web.bind.annotation.PathVariable String jobId, jakarta.servlet.http.HttpServletRequest http) {
        return jobs.get(jobId, authenticatedUser(http));
    }

    private String authenticatedUser(jakarta.servlet.http.HttpServletRequest http) {
        var account = (com.kinderuber.security.AccessFilter.Account) http.getAttribute("kinderuber.account");
        return account == null ? null : account.userId();
    }

    private List<Map<String, String>> parseJsonDocs(String text) throws Exception {
        com.fasterxml.jackson.databind.JsonNode values = objectMapper.readTree(text);
        if (!values.isArray() || values.isEmpty() || values.size() > 100) throw new IllegalArgumentException("JSON 文档应为包含 1–100 条文档的数组");
        List<Map<String, String>> docs = new ArrayList<>();
        for (var value : values) {
            if (!value.isObject() || !value.path("title").isTextual() || !value.path("content").isTextual()) throw new IllegalArgumentException("文档 title 和 content 必须为字符串");
            Map<String, String> doc = new LinkedHashMap<>();
            doc.put("title", value.get("title").asText()); doc.put("content", value.get("content").asText());
            if (value.has("document_id")) {
                if (!value.get("document_id").isTextual()) throw new IllegalArgumentException("document_id 必须为字符串");
                doc.put("document_id", value.get("document_id").asText());
            }
            docs.add(doc);
        }
        return docs;
    }
}
