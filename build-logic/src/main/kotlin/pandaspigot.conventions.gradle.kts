plugins {
    java
    `java-library`
}

repositories {
    mavenCentral()

    maven(url = "https://oss.sonatype.org/content/groups/public")
    maven(url = "https://hub.spigotmc.org/nexus/content/groups/public")
}

group = "com.hpfxd.pandaspigot"
version = "1.8.8-R0.1-SNAPSHOT"

// Athena start - Java 25 でビルド・実行する (Java 8 互換は不要)
java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(25)
    }
}

tasks {
    withType<JavaCompile>().configureEach {
        options.encoding = "UTF-8"
        options.release = 25
    }
}
// Athena end
