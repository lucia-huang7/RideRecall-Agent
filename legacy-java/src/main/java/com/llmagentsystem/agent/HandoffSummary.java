package com.llmagentsystem.agent;

import java.util.*;

/** A handoff packet, not a submitted ticket or a claim that a human has accepted the case. */
public record HandoffSummary(String requestId, String reason, String intent, String urgency,
                             Map<String, List<String>> entities, boolean ticketCreated) {
    public static HandoffSummary create(AgentRequest request, String reason) {
        Map<String, List<String>> known = new LinkedHashMap<>();
        if (request.entities() != null) request.entities().forEach((key, values) -> {
            if (values != null && !values.isEmpty()) known.put(key, values.stream().limit(8).map(HandoffSummary::safe).toList());
        });
        return new HandoffSummary(request.requestId(), safe(reason), request.intent() == null ? "other" : request.intent().name().toLowerCase(Locale.ROOT),
                request.urgency() == null ? "LOW" : request.urgency().name(), Map.copyOf(known), false);
    }
    private static String safe(String value) {
        String text = Objects.toString(value, "").replaceAll("(?i)(?:bearer\\s+|sk-)[A-Za-z0-9._-]+", "[REDACTED]");
        return text.substring(0, text.offsetByCodePoints(0, Math.min(200, text.codePointCount(0, text.length()))));
    }
    public String toCustomerText() {
        Map<String, String> labels = Map.of("order_id", "订单号", "amount", "金额", "date", "相关时间", "error_code", "错误码", "product", "产品");
        String information = entities.isEmpty() ? "暂无可交接的订单、金额或错误码等信息" : entities.entrySet().stream()
                .map(e -> labels.getOrDefault(e.getKey(), e.getKey()) + "：" + String.join("、", e.getValue())).reduce((a, b) -> a + "；" + b).orElse("");
        String priority = switch (urgency) { case "CRITICAL" -> "紧急"; case "HIGH" -> "优先"; case "MEDIUM" -> "需要跟进"; default -> "普通"; };
        return "这个问题需要人工客服继续核验，以下信息可用于交接：\n\n"
                + "升级原因：" + reason + "\n优先级：" + priority
                + "\n已知信息：" + information + "\n\n"
                + "系统尚未接入人工工单队列，以上仅为交接摘要，未创建或提交工单。请通过现有客服渠道联系人工。"
                + "请不要发送密码、短信验证码或完整密钥。";
    }
}
