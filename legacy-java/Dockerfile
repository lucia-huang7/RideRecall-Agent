FROM maven:3.9.9-eclipse-temurin-21 AS build
WORKDIR /workspace
COPY pom.xml .
COPY src ./src
RUN mvn -DskipTests package

FROM eclipse-temurin:21-jre
WORKDIR /app
ENV JAVA_OPTS=""
COPY --from=build /workspace/target/kinderuber-java-0.1.0.jar /app/kinderuber-java.jar
COPY skills /app/skills
COPY config/mcp_servers.json /app/config/mcp_servers.json
EXPOSE 8080
ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar /app/kinderuber-java.jar"]
