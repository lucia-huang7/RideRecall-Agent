package com.llmagentsystem.api;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;
import java.util.Map;

@RestControllerAdvice
public class ApiErrors {
    @ExceptionHandler({java.nio.charset.CharacterCodingException.class, com.fasterxml.jackson.core.JsonProcessingException.class})
    public ResponseEntity<?> malformed(Exception ex) { return ResponseEntity.badRequest().body(Map.of("detail", "文件必须是有效 UTF-8 文本或合法 JSON 文档数组")); }
    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<?> invalid(IllegalArgumentException ex) { return ResponseEntity.badRequest().body(Map.of("detail", ex.getMessage() == null ? "无效请求" : ex.getMessage())); }
    @ExceptionHandler(ResponseStatusException.class)
    public ResponseEntity<?> status(ResponseStatusException ex) { return ResponseEntity.status(ex.getStatusCode()).body(Map.of("detail", ex.getReason() == null ? "请求失败" : ex.getReason())); }
}
