# buildflow-analyzer

读取 **Jenkins 真实构建历史**，做规则式故障分类与研发效能统计，输出 CSV / HTML / JSON 报告。

- 仅使用 Python **标准库**（`urllib`、`csv`、`json`），无需 `pip install`。
- 认证信息只从**环境变量**读取，不写入代码、报告或日志。
- 不安装任何 Jenkins 插件：阶段信息通过控制台日志中的 Pipeline 标记恢复。

---

## 快速开始

### 一条命令（Windows，推荐）

```bat
REM 在仓库根目录执行
copy tools\.env.example.cmd tools\.env.cmd
REM 编辑 tools\.env.cmd 填入 JENKINS_URL / JENKINS_USER / JENKINS_TOKEN / JENKINS_JOB
tools\run-analysis.cmd
```

`tools\run-analysis.cmd` 会自动加载 `tools\.env.cmd`（已被 `.gitignore` 排除，
不会进入版本库），设置 UTF-8 输出并调用分析器，报告写入 `reports\`。

### 直接调用 Python

```powershell
$env:JENKINS_URL   = "http://127.0.0.1:8080"
$env:JENKINS_USER  = "admin"
$env:JENKINS_TOKEN = "<你的 API Token>"
$env:JENKINS_JOB   = "buildflow-ci"

py -3 tools/buildflow_analyzer/run.py --limit 50 --outdir reports
```

> 本机 Python 3.13 位于 `D:\Python\python.exe`（`py` 启动器的注册表项已失效），
> 可直接使用该绝对路径调用；`run-analysis.cmd` 已内置该路径。

生成的文件：

| 文件 | 内容 |
|---|---|
| `reports/buildflow-builds.csv` | 每条构建一行，含构建编号、状态、Git Commit、失败阶段、故障分类与判断依据 |
| `reports/buildflow-report.html` | 成功率、耗时趋势图、失败类型分布、构建明细 |
| `reports/buildflow-summary.json` | 机器可读汇总，便于回归比对 |

## 常用参数

| 参数 | 说明 |
|---|---|
| `--limit N` | 读取最近 N 个构建（也可用 `JENKINS_BUILD_LIMIT`） |
| `--job NAME` | Job 名称（也可用 `JENKINS_JOB`）；支持 `folder/job` |
| `--url` / `--user` / `--token` | 覆盖环境变量 |
| `--outdir DIR` | 报告输出目录（默认 `reports`） |
| `--no-logs` | 不读控制台日志，更快但分类精度下降 |
| `--insecure` | 跳过 TLS 证书校验 |
| `--demo` | 用**演示数据**出报告，报告会明确标注 `DEMO` |
| `--classify-log FILE` | 离线分类一个控制台日志文件，不访问 Jenkins |

## 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `JENKINS_URL` | 是 | 例如 `http://127.0.0.1:8080` |
| `JENKINS_JOB` | 是 | 例如 `buildflow-ci` |
| `JENKINS_USER` | 否 | 用户名；Jenkins 默认拒绝匿名读取 API |
| `JENKINS_TOKEN` | 否 | API Token 或密码，与 `JENKINS_USER` 配套 |
| `JENKINS_BUILD_LIMIT` | 否 | 读取构建数上限，默认 50 |

Token 在 Jenkins 中生成：**用户 → Configure → API Token → Add new Token**。
若使用密码而非 Token，建议仅在本机临时会话中使用。

---

## 采集的数据

对每个构建读取：

- 构建编号、原始结果、是否仍在运行
- 开始时间（`timestamp`）与完成时间
- 执行耗时（`duration`）
- Git commit SHA 与分支（来自 Git 插件的 `BuildData` action）
- 失败阶段与相关控制台日志

### 失败阶段是怎么拿到的

本工具**不依赖任何插件**。Jenkins 的阶段 REST API 由额外的插件（Stage View /
Pipeline REST API）提供，本环境未安装，因此阶段边界从控制台日志中恢复：

```
[Pipeline] { (Maven Test + Coverage)      <- 阶段开始
...
[Pipeline] // stage                       <- 阶段结束
Stage "Maven Test" skipped due to earlier failure(s)
```

一个关键细节：Declarative Pipeline 在失败后仍会**进入**后续每个阶段再跳过其主体，
所以日志里最后一个阶段往往是**被跳过**的阶段，而不是真正失败的那个。
本工具取「最后一个真正执行过、且其主体不含 `skipped` 提示」的阶段作为失败阶段；
若某阶段有开始标记但没有结束标记，则视为构建死于该阶段内。

---

## 故障分类

固定优先级匹配，命中即返回；**证据不足时输出 `UNKNOWN`，不猜测原因**。

| 类型 | 含义 | 典型证据 |
|---|---|---|
| `GIT_CHECKOUT` | Git 拉取失败 | `dubious ownership`、`Could not read from remote repository`、`GitException` |
| `MAVEN_COMPILE` | Maven 编译失败 | `COMPILATION ERROR`、`cannot find symbol`、`maven-compiler-plugin` 失败 |
| `UNIT_TEST` | 单元测试失败 | `Tests run: N, Failures: 1`、`maven-surefire-plugin` 失败 |
| `SONAR_QUALITY_GATE` | 质量门禁失败 | `QUALITY GATE STATUS: FAILED` |
| `ENVIRONMENT` | 环境或依赖问题 | `Could not resolve dependencies`、`mvnw: Permission denied`、`mvnw: N: Syntax error` |
| `UNKNOWN` | 未知原因 | 无规则命中 |

分类结果同时记录：**失败类型、判断依据（命中哪条规则/哪个阶段）、错误摘要（原始日志行）**，
三者都会写入 CSV 与 HTML，便于人工复核。

`--classify-log` 可对任意历史日志离线复现分类结果。

---

## 统计口径

| 指标 | 口径 |
|---|---|
| 构建总次数 | Jenkins 返回的全部构建 |
| 已完成构建 | 结果为 SUCCESS / FAILURE / UNSTABLE |
| **构建成功率** | `成功 ÷ (成功 + 失败 + 不稳定)` |
| 平均耗时 | **仅统计已完成构建** |
| 耗时中位数 / 最短 / 最长 | 仅统计已完成构建 |
| 耗时趋势 | 已完成构建按时间排序后的最小二乘斜率（秒/小时） |
| 失败类型占比 | 占**已分类失败**的比例 |

**进行中（IN_PROGRESS）与已取消（ABORTED）的构建被排除在成功率与耗时统计之外**，
在报告中单独列出，避免它们拉低成功率或平均耗时。
显式判定为 `UNKNOWN` 的失败计入「未分类失败」，不计入任何故障类型占比。

### 冷启动对耗时趋势的影响

首次完整执行流水线时，Maven 与全部依赖都需要下载，耗时可能是后续构建的数倍。
这会把线性趋势斜率显著拉向「变快」，造成误导。

因此报告会自动识别这类构建并在趋势图下方给出说明，例如：

> **冷启动说明：** 构建 #3 耗时 505.4s，约为其余已完成构建中位数（74.7s）的 6.8 倍。
> 该次通常是首次完整执行流水线，包含 Maven 与全部依赖的首次下载（冷启动），
> 与其他构建不具可比性，会明显放大耗时趋势斜率，请结合本说明解读趋势。

识别方式是**按耗时判定而非按位置判定**：早期失败的构建只运行几秒，若按「第一个构建」
识别会选错对象。判定阈值可在 `analysis._detect_cold_start` 中调整
（默认：候选构建需 ≥30s，且耗时 ≥ 其余构建中位数的 2 倍）。

---

## 真实数据与演示数据

- 默认（不带 `--demo`）**始终读取真实 Jenkins 构建历史**。
- `--demo` 使用 `sample_data.py` 中的合成记录，仅用于演示报告与验证分类规则。
- 演示模式下，HTML 报告顶部会显示醒目的 **DEMO** 标记，控制台也会打印提示，
  避免与真实数据混淆。

## 测试

```powershell
py -3 -m unittest discover -s tools/buildflow_analyzer/tests -t . -v
```

覆盖：阶段解析（含「被跳过阶段」这一陷阱）、六类故障分类规则、`UNKNOWN` 回退、
成功率口径、失败占比口径、趋势方向、commit 提取。

## 目录结构

```
tools/buildflow_analyzer/
├── run.py                 # CLI 入口
├── jenkins_client.py      # Jenkins REST 客户端（stdlib）
├── classifier.py          # 阶段解析 + 规则式故障分类
├── analysis.py            # 效能统计
├── reporting.py           # CSV / HTML / JSON 渲染
├── models.py              # 数据模型与故障分类枚举
├── sample_data.py         # 演示数据（非真实数据）
└── tests/                 # 规则与口径单元测试
```
