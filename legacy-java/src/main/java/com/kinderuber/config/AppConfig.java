package com.kinderuber.config;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableConfigurationProperties(KinderUberProperties.class)
public class AppConfig {
}
