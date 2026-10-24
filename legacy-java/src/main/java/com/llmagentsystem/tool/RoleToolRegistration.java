package com.llmagentsystem.tool;

import com.llmagentsystem.agent.*;
import com.llmagentsystem.intent.IntentCategory;
import jakarta.annotation.PostConstruct;
import org.springframework.stereotype.Component;
import java.math.*;
import java.util.*;

/** Deterministic role tools. Request data is supplied by the application, never by model arguments. */
@Component
public class RoleToolRegistration {
    private final ToolRegistry registry;
    public RoleToolRegistration(ToolRegistry registry) { this.registry = registry; }
    @PostConstruct public void register() {
        register("general", "inspect_request_context", "查看本轮意图、紧急度、已知实体及上下文是否可用；不查询外部业务系统。",
                Map.of("focus", text(40)), List.of(), (request, args) -> Map.of(
                        "intent", intent(request), "intent_group", Objects.toString(request.intentGroup(), "other"),
                        "urgency", request.urgency() == null ? "LOW" : request.urgency().name(), "intent_confidence", request.intentConfidence(),
                        "entities", entities(request), "context_available", request.context() != null && !request.context().isBlank(),
                        "requested_focus", args.getOrDefault("focus", "general")));
        register("general", "suggest_required_fields", "根据意图和本轮已知实体，建议下一轮仅补充必要字段。",
                Map.of(), List.of(), (request, args) -> requiredFields(request));
        register("technical", "lookup_error_code", "解释常见 HTTP 错误码与低风险排查方向，不读取服务端日志。",
                Map.of("error_code", text(20)), List.of("error_code"), (request, args) -> errorCode((String) args.get("error_code")));
        register("technical", "build_diagnostic_plan", "按环境和复现情况生成排障步骤，不执行配置修改。",
                Map.of("environment", text(80), "reproduced", Map.of("type", "boolean")), List.of("environment", "reproduced"),
                (request, args) -> diagnosticPlan(args));
        register("billing", "check_billing_fields", "核对已提供的订单、金额、日期和支付渠道；字段齐全不代表已核验或可以退款。",
                Map.of("payment_channel", text(80)), List.of(), (request, args) -> billingFields(request, args));
        register("billing", "compare_amounts", "计算明确提供的两笔金额差值；不判断重复扣款、不执行退款，也不做币种转换。",
                Map.of("amount_a", amount(), "amount_b", amount()), List.of("amount_a", "amount_b"), (request, args) -> {
                    BigDecimal first = new BigDecimal(args.get("amount_a").toString()), second = new BigDecimal(args.get("amount_b").toString());
                    return Map.of("success", true, "amount_a", first, "amount_b", second,
                            "difference", first.subtract(second).setScale(2, RoundingMode.HALF_UP),
                            "interpretation", "仅为所提供金额的算术差值，未核验账单、重复扣款或退款结论，未进行币种转换");
                });
        register("escalation", "create_handoff_summary", "整理本轮意图、优先级和已知实体，不创建真实工单。",
                Map.of("reason", text(120)), List.of(), (request, args) -> HandoffSummary.create(request,
                        Objects.toString(args.get("reason"), "需要人工客服继续核验")));
    }
    private void register(String role, String name, String description, Map<String, Object> properties, List<String> required,
                          ToolDefinition.ContextHandler handler) {
        registry.register(ToolDefinition.contextual(name, description, Map.of("type", "object", "properties", properties,
                "required", required, "additionalProperties", false), Set.of(role), handler));
    }
    private Map<String, Object> text(int maximum) { return Map.of("type", "string", "maxLength", maximum); }
    private Map<String, Object> amount() { return Map.of("type", "number", "minimum", -1_000_000_000_000L, "maximum", 1_000_000_000_000L); }
    private Map<String, List<String>> entities(AgentRequest request) { return request.entities() == null ? Map.of() : request.entities(); }
    private String intent(AgentRequest request) { return request.intent() == null ? "other" : request.intent().name().toLowerCase(Locale.ROOT); }
    private boolean known(AgentRequest request, String field) { return !entities(request).getOrDefault(field, List.of()).isEmpty(); }
    private Map<String, Object> requiredFields(AgentRequest request) {
        List<String> fields = new ArrayList<>();
        IntentCategory intent = request.intent() == null ? IntentCategory.OTHER : request.intent();
        switch (intent) {
            case ORDER_STATUS, LOGISTICS -> { if (!known(request, "order_id")) fields.add("订单号或下单时间"); }
            case ACCOUNT, ACCOUNT_SECURITY -> { fields.add("登录方式或账号标识（不要提供密码）"); if (!known(request, "date")) fields.add("问题发生时间"); }
            case COMPLAINT, REQUEST -> { if (!known(request, "date")) fields.add("事件时间"); fields.add("期望处理方式"); }
            case OTHER -> fields.add("希望解决的具体问题");
            default -> { }
        }
        return Map.of("intent", intent(request), "required_fields", fields, "known_entities", entities(request));
    }
    private Map<String, Object> errorCode(String value) {
        String code = value.toUpperCase(Locale.ROOT).strip();
        String meaning = switch (code) { case "401" -> "认证失败"; case "403" -> "权限不足"; case "404" -> "资源或路径不存在"; case "500" -> "服务端处理异常"; default -> "暂未识别的错误码"; };
        List<String> steps = switch (code) {
            case "401" -> List.of("确认 Token/API Key 是否过期（不要发送完整密钥）", "确认请求时间戳和签名", "确认账号登录状态");
            case "403" -> List.of("确认账号或套餐权限", "确认资源权限和 IP 白名单");
            case "404" -> List.of("确认接口路径和环境", "确认资源标识是否正确");
            case "500" -> List.of("记录 request_id 和发生时间", "由有权限的技术人员核查依赖服务、参数及服务端日志");
            default -> List.of("补充完整错误信息、发生时间和运行环境");
        };
        return Map.of("error_code", code, "meaning", meaning, "next_steps", steps, "server_log_checked", false);
    }
    private Map<String, Object> diagnosticPlan(Map<String, Object> args) {
        List<String> steps = new ArrayList<>(List.of("复现并记录完整错误信息", "确认网络、DNS、代理和证书", "确认版本、配置和权限"));
        if (Boolean.TRUE.equals(args.get("reproduced"))) steps.add("用最小请求复现并记录 request_id");
        return Map.of("environment", args.get("environment"), "reproduced", args.get("reproduced"), "diagnostic_steps", steps, "configuration_changed", false);
    }
    private Map<String, Object> billingFields(AgentRequest request, Map<String, Object> args) {
        Map<String, Boolean> fields = new LinkedHashMap<>();
        for (String key : List.of("order_id", "amount", "date")) fields.put(key, known(request, key));
        fields.put("payment_channel", !Objects.toString(args.get("payment_channel"), "").isBlank());
        return Map.of("fields", fields, "missing_fields", fields.entrySet().stream().filter(e -> !e.getValue()).map(Map.Entry::getKey).toList(),
                "can_confirm_refund", false, "reason", "只检查提供的字段，不连接订单、支付或退款系统；字段齐全不代表核验通过");
    }
}
