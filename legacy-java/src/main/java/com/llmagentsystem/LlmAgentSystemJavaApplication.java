package com.llmagentsystem;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class LlmAgentSystemJavaApplication {

    public static void main(String[] args) {
        boolean cli = java.util.Arrays.asList(args).contains("--cli");
        SpringApplication application = new SpringApplication(LlmAgentSystemJavaApplication.class);
        if (cli) application.setWebApplicationType(org.springframework.boot.WebApplicationType.NONE);
        var context = application.run(args);
        if (cli) context.close();
    }
}
