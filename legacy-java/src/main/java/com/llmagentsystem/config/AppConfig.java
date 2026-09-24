package com.llmagentsystem.config;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableConfigurationProperties(LlmAgentSystemProperties.class)
public class AppConfig {
}
