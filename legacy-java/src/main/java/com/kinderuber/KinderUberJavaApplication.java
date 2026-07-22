package com.kinderuber;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class KinderUberJavaApplication {

    public static void main(String[] args) {
        boolean cli = java.util.Arrays.asList(args).contains("--cli");
        SpringApplication application = new SpringApplication(KinderUberJavaApplication.class);
        if (cli) application.setWebApplicationType(org.springframework.boot.WebApplicationType.NONE);
        var context = application.run(args);
        if (cli) context.close();
    }
}
