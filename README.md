# AiValgrind

在 Linux 上联合使用 Valgrind 和 GDB，生成可浏览的内存问题报告，并在重新运行时采集错误现场变量。核心代码位于 `inc/`，统一入口为 `main.py`，无需安装第三方 Python 库。

## 五种场景命令速查

以下命令都在 AiValgrind 目录执行，将 `/work/project`、`./build/app` 和程序参数替换为实际值。也可把 `main.py` 写成工具的绝对路径。每个场景的快速、完整命令是二选一。

| 场景 | 命令入口 | 目标程序运行次数 | 查看结果 |
| --- | --- | --- | --- |
| 1. 运行程序，生成 HTML | `collect --report` | 1 次 | `report.html` |
| 2. 已有 XML，生成 HTML | `report` | 0 次 | 指定的 HTML |
| 3. 运行程序，生成 HTML，再更新变量分析 | `analyze` | 通常 2 次 | `full-report.html` |
| 4. 已有 HTML，更新变量分析 | `analyze --step3-only --base-report` | 重跑 1 次 | `full-report.html` |
| 5. 已有 XML，生成 HTML 并分析变量 | `analyze --xml` | 重跑 1 次 | `report.html`、`full-report.html` |

### 场景一：运行程序，生成 HTML 报告

```bash
# 快速模式
python3.9 main.py collect --report --mode fast --output-dir /tmp/memory-results -p /work/project --cwd /work/project -- ./build/app arg1

# 完整模式
python3.9 main.py collect --report --mode full --output-dir /tmp/memory-results -p /work/project --cwd /work/project -- ./build/app arg1
```

检测结束后自动导出完整 `/tmp/memory-results/report.html`，不启动 GDB，不采集变量。运行期间仍可使用终端提供的实时网页链接。若中途 Ctrl+C 退出，使用场景二从已保存 XML 导出。

### 场景二：已有 XML 错误报告，生成 HTML

```bash
# 快速模式
python3.9 main.py report /tmp/memory-results/errors.xml --mode fast -p /work/project -o /tmp/memory-results/report.html

# 完整模式
python3.9 main.py report /tmp/memory-results/errors.xml --mode full -p /work/project -o /tmp/memory-results/report.html
```

不运行目标程序，也不需要 GDB。支持中断 XML 中已完整写出的错误记录。

### 场景三：运行程序，生成 HTML，然后更新变量分析

```bash
# 快速模式
python3.9 main.py analyze --mode fast --output-dir /tmp/memory-results -p /work/project --cwd /work/project -- ./build/app arg1

# 完整模式
python3.9 main.py analyze --mode full --output-dir /tmp/memory-results -p /work/project --cwd /work/project -- ./build/app arg1
```

步骤二完成即可看 `report.html`；步骤三在重跑程序时采集变量，打开同目录 `full-report.html` 可逐步看到变量结果。程序通常运行两次，其文件写入等副作用也会重复执行；无错误时不启动步骤三。

### 场景四：已有 HTML 报告，然后更新变量分析

```bash
# 快速模式
python3.9 main.py analyze --step3-only --mode fast --base-report /tmp/memory-results/report.html --output-dir /tmp/memory-results -p /work/project --cwd /work/project -- ./build/app arg1

# 完整模式
python3.9 main.py analyze --step3-only --mode full --base-report /tmp/memory-results/report.html --output-dir /tmp/memory-results -p /work/project --cwd /work/project -- ./build/app arg1
```

跳过首次检测和 HTML 重建，复用原报告并重跑程序采集变量。自动查找 HTML 同目录的配套 XML，再尝试 HTML 记录的原 XML 路径；找不到时需要加 `--xml /实际路径/errors.xml`。**请保留配套 XML、程序和输入；仅有一个 HTML 文件不足以重现现场。** HTML 与 XML 的错误列表不一致时会停止，不会混入其他报告的变量。

更新结果查看同目录 `full-report.html`，原 `report.html` 不被改写。变量写入配套 `capture-updates.js`、进度写入 `capture-progress.js`，页面增量更新，不整页刷新。移动报告时保留配套文件。

### 场景五：已有 XML 结果，生成 HTML 报告并进行变量分析

```bash
# 快速模式
python3.9 main.py analyze --xml /tmp/memory-results/errors.xml --mode fast --output-dir /tmp/analysis-results -p /work/project --cwd /work/project -- ./build/app arg1

# 完整模式
python3.9 main.py analyze --xml /tmp/memory-results/errors.xml --mode full --output-dir /tmp/analysis-results -p /work/project --cwd /work/project -- ./build/app arg1
```

不需要提前生成 HTML，也不要添加 `--step3-only`。先从已有 XML 生成 `/tmp/analysis-results/report.html`，再重跑一次目标程序采集变量，并逐步更新 `/tmp/analysis-results/full-report.html` 中对应错误堆栈的变量分析。无已记录错误时不启动变量采集。

支持不完整 XML 中已完整写出的错误记录。变量值来自本次重跑，无法仅凭 XML 恢复历史变量值；需保留目标程序及复现输入。若 XML 同目录有配套 `run.json`，可以恢复原程序命令、工作目录和标准输入文件；否则按上述命令明确提供。程序运行参数应与生成 XML 时一致。

场景一、二没有变量采集，`fast` 和 `full` 使用相同检测/报告流程，不承诺速度差异；场景三、四、五的模式差异见下表。五种场景不会因快速模式减少检测种类或修改去重规则。

## 快速模式与完整模式

| 项目 | `--mode fast` | `--mode full`（默认） |
| --- | --- | --- |
| 内存错误检测参数、类型、去重规则 | 相同 | 相同 |
| 当前源码行表达式的变量值 | 采集 | 采集 |
| 索引、初始化、可访问性、分配边界检查 | 保留 | 保留 |
| 故障地址及关联证据 | 保留 | 保留 |
| 额外枚举各层参数和局部变量 | 跳过 | 采集 |
| 用途 | 减少与当前行无关的变量查询 | 保留更多辅助定位数据 |

模式作用于步骤三，也适用于 `debug`、`serve`。步骤一和步骤二使用相同流程。快速模式仍采集最多 16 层现场堆栈中的当前行表达式，不通过减少错误检测或缩短成员区间检查来提速。未采集的数据不会标为正常。

两种模式不会主动隐藏不同的错误；但不同运行的输入、线程调度或执行路径可能不同，不能保证两次运行实际触发完全相同的错误。Valgrind 本身及源码/blame 处理仍有开销，快速模式不保证固定加速倍数。

```bash
python3.9 main.py analyze --mode full --output-dir /tmp/memory-full \
  -p /work/project --cwd /work/project -- ./build/app
```

## 环境要求

- 运行检测、联合调试：Linux，Python 3.9 或更新版本，Valgrind。
- 变量采集：GDB 必须编译启用 Python 支持。仅有 GDB 版本号不能说明其支持 Python。
- 适配目标：GDB 10.1、10.2、13.1、15.2；Valgrind 3.10、3.11、3.12、3.14、3.22。
- XML 转 HTML 可在 Windows 上运行；Git blame 可选，不可用时仍生成报告。
- 目标程序建议使用调试符号、低优化编译，例如 `-g -O0`。优化掉的变量不能可靠恢复。

启动时显示实际工具版本，并依据本机 Valgrind 帮助信息选择可用增强选项。检测支持时保留分配/释放双栈、初始化诊断及符号信息；联合调试优先使用精确停止能力。两种模式使用相同的选项选择逻辑。兼容性配置与模拟测试不能代替每个目标系统上的实机验证。

## 分步骤执行

### 步骤一：检测并保存 XML

```bash
python3.9 main.py collect --output-dir /tmp/memory-results \
  -p /work/project --cwd /work/project -- ./build/app arg1
```

终端固定区域显示当前任务、开始时间、耗时、各类错误数量及程序输出。默认提供实时网页链接。远程 SSH 使用端口转发：

```bash
ssh -L 8765:127.0.0.1:8765 user@server
```

保持连接，在本机打开 `http://127.0.0.1:8765/`。以终端实际端口为准；`--port 0` 可自动选端口。`--no-web` 关闭实时网页。

也可以自行生成 XML：

```bash
valgrind --tool=memcheck --xml=yes --xml-file=errors.xml \
  --leak-check=full --show-leak-kinds=all --track-origins=yes ./build/app
```

### 步骤二：生成完整离线报告

```bash
python3.9 main.py report /tmp/memory-results/errors.xml \
  -p /work/project -o /tmp/memory-results/report.html --workers 4
```

支持已完整写出的 XML 记录，即使 XML 被中断而未闭合。`--strict-xml` 要求完整 XML。源码/blame 按文件和所需行范围复用查询，默认 4 个工作线程，可选 1–8。Git 查询超时 30 秒时保留源码并说明缺失原因。进度在交互终端原地更新，重定向日志不周期性刷屏。

### 步骤三：单独采集变量并复用步骤二

```bash
python3.9 main.py analyze --step3-only --mode fast \
  --xml /tmp/memory-results/errors.xml \
  --base-report /tmp/memory-results/report.html \
  --output-dir /tmp/memory-results -p /work/project --cwd /work/project \
  -- ./build/app arg1
```

默认从 XML 同目录读取 `report.html`，也可通过 `--base-report` 指定兼容的已有完整报告。HTML 与 XML 的错误列表必须一致。使用配套 `run.json` 时，可以恢复目标程序、工作目录及标准输入文件；信息缺失时显式提供。

步骤三新建 `captures/session-*`，变量结果写入配套文件并更新 `full-report.html` 的对应堆栈。它读取的是**本次重跑的现场**，不会恢复上一次运行的历史变量值。默认保存后继续，程序结束自动退出；`--pause-on-error` 会停留在 GDB，使用 `continue` 继续、`quit` 退出。

![步骤三效果示意：错误堆栈与变量分析](docs/images/step3-preview.png)

图片为示意，具体可用数据取决于符号、源码和现场采集情况。

### 已有 XML，连续执行步骤二、三

```bash
python3.9 main.py analyze --xml /tmp/memory-results/errors.xml \
  --output-dir /tmp/analysis-results -p /work/project --mode full \
  --cwd /work/project -- ./build/app
```

## 浏览、筛选与调试

```bash
# 查看统计，不重新运行目标
python3.9 main.py summary /tmp/memory-results/errors.xml

# 提供网页浏览服务
python3.9 main.py serve /tmp/memory-results/errors.xml -p /work/project

# 按报告中的错误 ID 调试
python3.9 main.py debug /tmp/memory-results/errors.xml -p /work/project \
  --error ERROR_ID --cwd /work/project -- ./build/app

# 在实际内存错误处采集变量
python3.9 main.py debug /tmp/memory-results/errors.xml -p /work/project \
  --error ERROR_ID --auto-values --mode fast \
  --capture-dir /tmp/captures --cwd /work/project -- ./build/app
```

网页命令复制到同一 Linux 主机的普通终端执行；`aiv-goto` 等继续调试命令输入已有会话的 `(gdb)`。跳转到已经经过的错误位置时需要重新运行，按提示选择 `y` 或 `n`。源码断点仅帮助定位调用路径，不表示已经触发非法访问。

报告支持文件、类型、作者和“仅显示已读出变量值”的筛选，并保留刷新前的筛选状态。类型数量按“筛选后/总数”显示；作者按最深错误帧的 blame 作者统计，父帧不计入，仍可访问内存不计入作者错误数。非法读取、非法写入、确定泄漏使用红色导航文字。

堆栈保留错误行附近源码，blame 默认勾选，可关闭显示。独立源码查看只保存路径、不复制完整源码文件，需要源文件所在机器上的浏览服务读取。源码移动或不存在时会提示不可用。

## 如何理解变量结论

- **绿色**：已检查部分通过，不保证整次非法访问合法。
- **红色**：已有直接异常证据，如不可访问、未初始化、越界或释放后使用。
- **黄色**：无法读取、检查不完整或证据不足。

变量检查状态与“是否导致本次错误”分别判断。只有错误现场、源码帧与访问地址匹配时才关联；多个候选或不匹配的现场不会硬塞入其他错误堆栈。

支持嵌套索引、结构体成员和动态指针的范围检查。例如 `st->arr[i].member` 且 `i=26` 时，尝试检查 `arr[0]` 到 `arr[26]` 的 **member 成员**，按状态区间展示，不检查无关成员。检查有资源预算，未覆盖部分会说明，不应视为正常。

非法读写尽量展示实际下标、分配范围、访问长度及越界字节数。对象大小不能证明其存储仍可访问；“不可访问”也不能单凭这一条推断已经释放。宏、优化变量、缺失符号以及无法确认边界的指针可能无法给出确定值。原始证据保存在现场文件中。

## 中断、输出目录与更新

- `Ctrl+C` 终止流程；支持终端下也可按 `q` 退出。已保存结果保留，可用 `summary`、`report` 继续处理。不完整运行可能缺少退出时泄漏检查。
- 输出目录可复用，只清理脚本清单登记的文件，保留无关文件。步骤三可与步骤二共用目录。
- 终端显示异常时可尝试 `--plain-terminal`；程序输出兼容问题可尝试 `--output-mode file`。
- `--stdin-file` 提供目标程序输入文件。调试时默认不把工具交互输入交给目标程序。
- 更新时同时复制 `main.py` 和整个 `inc/`。旧 `aivalgrind.py` 入口已合并移除，旧命令请改为 `main.py`，旧 HTML 中的调试命令需重新生成。

更新已有变量查看器和关联分析，不重新执行目标：

```bash
python3.9 main.py refresh-captures --output-dir /tmp/memory-results
```

完成后重新打开或刷新 `full-report.html`。此操作不会补采旧现场缺失的变量或地址；缺少数据时需要重新执行步骤三。

## Python 库调用与入口结构

```python
# 外部工程可先执行：sys.path.insert(0, "/opt/AiValgrind")
from inc import export_report, analyze_run, DebugOptions, debug_error

report = export_report("errors.xml", "report.html", project_dir="/work/project")
analyze_run(["./build/app"], "/tmp/results", cwd="/work/project",
            project_dir="/work/project", mode="fast")
# debug_error(report, "ERROR_ID", DebugOptions(
#     command=["./build/app"], cwd="/work/project", auto_values=True, mode="full"))
```

`main.py` 的 `main(argv)` 调用 `AiValgrind(ArgParser(argv))`。`ArgParser` 专门处理参数，`AiValgrind` 分发功能，两者的完整实现均位于 `main.py`。`main.py` 还保留报告、浏览、调试等独立示例函数。

库调用抛出异常，不直接退出宿主；导入库不会启动目标。相对路径按调用时工作目录解释。若输入相对于调用文件，请使用 `Path(__file__).resolve().parent / "errors.xml"`。网页调试入口始终来自实际加载的库所在目录中的 `main.py`，不受外部调用脚本的位置影响。移动工程、XML 或工具后应重新生成报告中的绝对路径命令。

旧 `main.py report --xml ...`、`browse --xml ...` 写法仍可用；推荐采用以上统一命令。

## 开发与验证

```bash
python3.9 -m unittest discover -s tests
python3.9 main.py --help
python3.9 main.py analyze --help
```

测试包括解析、去重、网页、路径、模拟 GDB 现场及 Linux 集成测试。Windows 上会跳过需要 Linux/Valgrind/GDB 的用例。真实运行的速度及变量可读性请以目标系统验证为准。
