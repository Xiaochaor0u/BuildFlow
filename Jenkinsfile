pipeline {
    agent any

    environment {
        SONAR_PROJECT_KEY = 'devops-ci-cd-pipeline'
        SONAR_HOST        = 'http://sonarqube:9000'
        // 制品版本前缀，实际版本号为 <前缀>.<构建号>
        ARTIFACT_VERSION_PREFIX = '0.0.1-SNAPSHOT'
    }

    options {
        // 保留最近 30 个构建（含制品），避免磁盘无限增长
        buildDiscarder(logRotator(numToKeepStr: '30', artifactNumToKeepStr: '30'))
        timeout(time: 30, unit: 'MINUTES')
    }

    stages {
        // 源码由 Jenkins SCM 检出到工作区
        stage('Checkout') {
            steps {
                sh 'git log --oneline -1'
            }
        }

        // 一次 clean package 同时完成：编译、单元测试、JaCoCo 覆盖率报告、可执行 JAR。
        // 后续阶段复用该 JAR，不再重复编译。
        stage('Maven Test + Coverage + Package') {
            steps {
                sh 'sh ./mvnw -B clean package'
            }
            post {
                always {
                    junit testResults: 'target/surefire-reports/*.xml', allowEmptyResults: true
                    archiveArtifacts artifacts: 'target/site/jacoco/jacoco.xml', allowEmptyArchive: true
                }
            }
        }

        // 质量门禁排在制品归档之前，因此门禁不通过时不会有任何制品被归档。
        // sonar.qualitygate.wait=true 让扫描器轮询服务端计算任务：
        // 门禁为 ERROR 时以非零退出码结束，使本 stage 失败并阻断后续 stage；
        // 不使用 catchError 吞掉失败。
        stage('SonarQube Quality Gate') {
            steps {
                withCredentials([string(credentialsId: 'SONAR_TOKEN_CRED', variable: 'SONAR_TOKEN')]) {
                    sh 'sh ./mvnw -B -Pquality sonar:sonar ' +
                       "-Dsonar.projectKey=${SONAR_PROJECT_KEY} " +
                       "-Dsonar.host.url=${SONAR_HOST} " +
                       '-Dsonar.login=$SONAR_TOKEN'
                }
            }
        }

        // 制品归档：仅在测试与质量门禁均通过后执行。
        // 复用 Maven 阶段生成的 JAR，并关联构建编号与 Git Commit SHA。
        stage('Archive Artifact') {
            steps {
                script {
                    // 用 Jenkins 原生步骤（writeFile/readFile/sh）生成归档内容，
                    // 不依赖 pipeline-utility-steps 插件（本环境未安装 findFiles/readdir）。
                    def commit = sh(script: 'git rev-parse HEAD', returnStdout: true).trim()
                    def shortCommit = commit.take(7)
                    def version = "${env.ARTIFACT_VERSION_PREFIX}.${env.BUILD_NUMBER}"
                    // BUILD_URL 未必注入到 script 上下文，缺失时按 Job 与构建号拼出稳定链接
                    // 构建链接由 currentBuild 提供，避免依赖可能未注入的环境变量
                    def buildUrl = currentBuild.absoluteUrl

                    // 复用 Maven 已打包的 JAR（不重新编译）。
                    // Spring Boot repackage 会把原始包改名为 *.jar.original，
                    // 它不以 .jar 结尾，因此不会被复制进来。
                    sh '''
                        set -e
                        mkdir -p artifact
                        cp target/devops-ci-cd-pipeline-*.jar artifact/
                        echo "reused packaged JAR (no recompilation)"
                    '''

                    // 归档前校验确实取到了制成品
                    if (!fileExists('artifact/devops-ci-cd-pipeline-0.0.1-SNAPSHOT.jar')) {
                        error('packaged JAR not found under target/ - nothing to archive')
                    }

                    def metadata = """{
  "application": "devops-ci-cd-pipeline",
  "artifact": "devops-ci-cd-pipeline-${env.ARTIFACT_VERSION_PREFIX}.jar",
  "version": "${version}",
  "build_number": "${env.BUILD_NUMBER}",
  "build_url": "${buildUrl}",
  "git_commit": "${commit}",
  "git_commit_short": "${shortCommit}",
  "jenkins_job": "${env.JOB_NAME}",
  "sonar_project_key": "${env.SONAR_PROJECT_KEY}"
}
"""
                    writeFile file: 'artifact/version-metadata.json', text: metadata

                    // SHA256 校验信息 + 归档内容展示
                    dir('artifact') {
                        sh 'sha256sum devops-ci-cd-pipeline-0.0.1-SNAPSHOT.jar > devops-ci-cd-pipeline-0.0.1-SNAPSHOT.jar.sha256'
                        echo "----- artifact directory -----"
                        sh 'ls -l'
                        echo "----- checksum -----"
                        sh 'cat devops-ci-cd-pipeline-0.0.1-SNAPSHOT.jar.sha256'
                        echo "----- version metadata -----"
                        sh 'cat version-metadata.json'
                    }

                    // 使用 Jenkins 原有制品归档机制，构建页面可直接下载
                    archiveArtifacts artifacts: 'artifact/*', fingerprint: true
                    currentBuild.description = "v${version} @ ${shortCommit}"
                }
            }
        }
    }

    post {
        always {
            junit testResults: 'target/surefire-reports/*.xml', allowEmptyResults: true
        }
    }
}
