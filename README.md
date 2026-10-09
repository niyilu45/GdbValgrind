# AiValgrind

Python 库与调用示例：把 Valgrind XML 转成可离线打开的 HTML，按错误类型浏览、搜索、去重；可选工程目录，嵌入每个调用栈帧对应行的上下各 10 行源码；通过网页选中错误，在当前 Linux / SSH 终端启动 Valgrind + GDB。

复制 `inc/` 目录及入口 `main.py`、`aivalgrind.py` 到 Linux，保持相对位置。使用示例数据时再复制 `examples/`。Python 3.9 或更新版本即可，无第三方 Python 依赖。报告转换也可在 Windows 运行；联合调试需要 Linux 的 `valgrind`、`vgdb` 和 `gdb`。

## 库结构与示例入口

```text
inc/
  __init__.py    # 对外公开的库函数
  api.py         # DebugOptions、导出报告、调试及浏览接口
  core.py        # XML 解析/去重、源码匹配、进程控制、本地服务
  capture.py     # 在 GDB 内执行的自动采集及初始化分析
  processes.py   # Ctrl+C、进程组终止、超时强制结束和子进程回收
  collect.py     # 首次采集、实时类型统计、持久化检查点
  xmlstream.py   # 增量 XML 解析与截断记录恢复
  templates.py   # 离线 HTML 模板
  cli.py         # 原命令行接口实现
main.py          # 五种场景的示例调用，不包含核心实现
aivalgrind.py    # 原命令兼容入口，仅转发到 inc.cli
examples/        # C 程序及演示 XML/HTML
tests/           # 回归测试、库 API 测试和 Linux 集成测试
```

`main.py` 中五个示例函数可直接阅读、修改或复制到自己的业务代码：

```bash
# 场景一：示例 XML 转为离线报告，并打印可选错误 ID
python3 main.py report

# 首次运行，实时显示错误种类和数量；Ctrl+C 后保留数据
python3 main.py collect --output-dir run-001 -- ./your_program 参数

# 场景二：只浏览报告
python3 main.py browse --xml errors.xml -p /path/to/project

# 场景三：针对选定错误设置源码断点
python3 main.py debug --xml errors.xml -p . --error 错误ID -- ./your_program 参数

# 场景四：网页启动自动分析，采集变量/越界/初始化信息
python3 main.py auto-values --xml errors.xml -p . --capture-dir captures -- ./your_program 参数
```

在自己的 Python 工程里直接调用，无需构造命令行参数：

```python
from inc import DebugOptions, export_report, load_report, debug_error, serve_report

# 导出函数返回已去重的报告字典，便于继续处理。
report = export_report("errors.xml", "report.html", project_dir="/path/to/project")
# 也可只解析：report = load_report("errors.xml", "/path/to/project")

options = DebugOptions(
    command=["./build/app", "argument with spaces"],
    cwd="/path/to/project",
    auto_values=True,
    capture_dir="./captures",
)

# 二选一：直接调试（使用报告中的真实错误 ID），或开启网页服务。
# exit_code = debug_error(report, "真实错误ID", options)
serve_report(report, options=options, port=8765)
```

公开接口为 `DebugOptions`、`load_report`、`render_html`、`export_report`、`debug_error`、`serve_report`、`collect_run`。库函数失败时抛出异常，不会调用 `sys.exit`；仅导入库不会启动服务或进程。`debug_error` 返回 GDB 退出码；`serve_report` 会阻塞到 Ctrl+C，应在主线程中调用。调试仍需要 Linux 交互终端；不传 `options` 的 `serve_report(report)` 只提供浏览。

`browse` 启动后，请保留提供网页服务的 SSH 终端。在网页选中错误，点击“生成调试命令”并复制；另开一个连接同一台 Linux 服务器的 SSH 终端，把完整命令粘贴到普通 Shell 中执行。等出现 `(gdb)` 后，才可以输入 `bt`（调用栈）、`info locals`（局部变量）、`print 变量名`（替换为实际变量）、`continue`（继续）或 `quit`（结束本次调试）。网页的“复制继续命令”得到的 `aiv-goto …` 也必须粘贴到已有会话的 `(gdb)` 中，不能在 Shell 中执行。`browse` 只提供网页服务；需要保存离线 HTML 请使用 `report`。

以下原有 `aivalgrind.py` 命令保持兼容。

## 首次采集、实时统计与中断后继续分析

```bash
# 首次运行不需要预先生成 XML，也不依赖 GDB
python3 aivalgrind.py collect --output-dir run-001 --interval 1 -- ./your_program 参数

# 可以中途 Ctrl+C。随后独立查看统计或生成 HTML
python3 aivalgrind.py summary run-001/errors.xml
python3 aivalgrind.py report run-001/errors.xml -p /path/to/project -o partial-report.html

# 即使 XML 被截断，也能选择其中已完整写出的错误重新运行、自动分析
python3 aivalgrind.py serve run-001/errors.xml -p /path/to/project \
  --auto-values --capture-dir captures -- ./your_program 参数
```

运行中增量读取已落盘 XML，默认每秒检查一次；统计变化时实时打印错误类型、去重位置数和次数。Valgrind 通常仅输出每个错误上下文的首次记录，重复次数在 `errorcounts` 中另行汇总。因此缺失最终计数时明确显示“至少 N 次”，**不把它冒充精确的重复发生总次数**；错误发现到显示的延迟还取决于 Valgrind 何时输出 XML。

采集目录必须是新目录，不覆盖旧结果。文件包括 `errors.xml`（原始结果）、`status.json`（原子替换的最新统计和 running/finished/interrupted/failed 状态）、`program.log`（目标程序标准输出）、`launcher.log`（标准错误）和 `valgrind.log`（Valgrind 文本日志，如有）。Ctrl+C 后先清理并回收进程，再读取最后写出的 XML 并保存统计。`collect` 支持 `--cwd` 和 `--stdin-file`；默认目标标准输入为空。

库调用：`collect_run(["./app", "arg"], "run-001", cwd="/project", interval=1)`。仅需报告处理时，`load_report`、`export_report`、`debug_error` 和 `serve_report` 也兼容不完整报告。

恢复规则：保留完整的 `<error>` 和已闭合的计数 `<pair>`，丢弃末尾未写完的错误；不会猜补缺失栈帧。完整记录的错误 ID 与正常结束报告一致。非截断造成的 XML 结构损坏仍会报错；没有任何完整错误时可以生成空报告，但无法选择不存在的错误调试。过早中断、尚未输出根节点时需重新采集。

报告会标记不完整。被中断运行可能没有退出时的泄漏检查，缺失的错误和历史变量值无法恢复。自动分析是重新执行，仍取决于错误能否复现；不会把已保存的部分 XML 当作历史内存快照。需要严格验证时使用 `--strict-xml`，库函数使用 `load_report(path, allow_partial=False)`。

## 1. 先自行生成 XML

保留与报告对应的程序、动态库、源码、参数和输入。使用调试符号、低优化级别编译更容易定位：

```bash
gcc -g -O0 examples/demo.c -o examples/demo
valgrind --tool=memcheck --xml=yes --xml-file=errors.xml \
  --leak-check=full --show-leak-kinds=all --track-origins=yes \
  ./examples/demo
```

`examples/demo.c` 故意包含越界写和泄漏，供验证使用。实际使用时替换成自己的程序。正常结束可获取更完整的计数和泄漏结果；中断时也可以恢复已完整写出的记录。对 fork/多进程程序，使用 `--xml-file=errors.%p.xml`，每个进程的 XML 分别处理。内置 `collect` 面向单个主程序；多进程共享一个 XML 可能交错损坏，应手动分进程采集。

## 2. 生成离线 HTML

```bash
python3 aivalgrind.py report errors.xml -o report.html

# 加入工程根目录后，报告包含源码上下各 10 行，错误行高亮
python3 aivalgrind.py report errors.xml \
  --project-dir /home/user/project -o report.html
```

把 `report.html` 下载到本机，直接用浏览器打开。报告的样式、数据和脚本全部内嵌，不依赖 CDN 或外部文件。指定工程目录后源码也会包含在 HTML 内，分享报告时请注意这一点。

左侧导航按类型筛选，中间列表支持搜索错误、函数和路径，右侧显示完整调用栈及源码。每条错误有稳定 ID。点击“生成调试命令”，命令自动使用 Python、脚本、XML、工程目录和目标程序的绝对路径，带入生成报告时提供的工程目录，以及 XML 记录的原程序参数，无需重新填写。

“筛选源文件”支持文件名、完整路径或部分路径，并统一识别 `/` 和 `\`。类型、关键词和源文件筛选同时生效；列表明确显示总位置数、筛选结果数和已显示数，点击“清除全部筛选”恢复全部结果。大报告先显示 100 条，可继续加载；折叠的源码在展开时加载。启动命令仅在参数包含空格或特殊字符时加双引号并转义，请保留必要引号，在 Linux Shell 中执行；`aiv-goto` 继续命令则在 `(gdb)` 中执行。

请在实际调试的 Linux 主机上生成报告，浏览器可以在另一台电脑打开 HTML；命令中的绝对路径属于生成报告的主机。`collect` 会在采集目录保存 `run.json`，报告优先使用其中记录的程序、参数、工作目录和标准输入文件，中断后也保留这些信息。外部 XML 没有工作目录时，默认使用工程目录（未提供则使用 XML 所在目录），报告会提示该假设。XML 和采集记录均没有程序信息时，会明确提示无法生成完整命令，不再提供需要手动替换的示例程序。已有 HTML 需要重新生成才能应用此功能。

已在调试某条错误时，可在网页选中另一条错误，点击“复制继续命令”，粘贴到当前 SSH 终端的 `(gdb)` 提示符：

```text
aiv-goto 错误ID
aiv-goto 错误ID 0:0
```

此命令需要支持 Python 的 GDB，以及新版脚本启动的同一报告会话。它在当前进程设置所选源码断点并继续执行，不需要重新输入工程目录或程序参数。导航会跟踪已到达的报告位置；目标已到达、报告序号早于当前位置或程序已结束时，提示是否重新运行。输入 `y` 后，启动脚本先清理旧进程，再用原参数、工作目录和标准输入文件启动新的 Valgrind/GDB 会话；输入 `n`（或其他非 y 输入）则保留当前现场。重跑不会保留手动添加的 GDB 断点和设置。普通 GDB `continue` 仍可使用。

这里的序号是 XML 中去重错误首次出现的顺序，不是浏览器筛选后的序号，也不保证多线程或不同输入下的实际发生顺序。导航定位的是源码位置，可能在真正出错之前命中，同一源码位置也可能对应多条错误；泄漏定位到分配位置。其他用户断点、信号或自动采集的内存错误可能提前暂停，此时可再次执行继续命令。程序退出仍未到达目标会给出提示，不会声称已复现该错误。

## 3. SSH 环境：网页选择错误并启动 GDB

在本机建立 SSH 端口转发并登录 Linux，远程和本地端口请保持一致：

```bash
ssh -L 8765:127.0.0.1:8765 user@linux-host
```

在这个 SSH 终端中启动服务，`--` 后面是实际程序及参数：

```bash
cd /home/user/project
python3 aivalgrind.py serve errors.xml --project-dir . \
  --port 8765 -- ./build/your_program argument1 argument2
```

本机浏览器打开 <http://127.0.0.1:8765>，选中错误，点击“在终端启动 GDB”，然后切回这个 SSH 终端：

1. 脚本重新启动程序，Valgrind 用 `--vgdb-error=0` 在启动时等待连接。
2. GDB 通过独立的 vgdb 管道和指定 PID 连接这次运行。
3. 自动优先选择主错误调用栈中工程源码的位置，设置断点并 `continue`。网页下拉框也可指定其他帧。
4. 在 GDB 中检查现场；执行 `quit` 后本次目标进程会清理，网页可选择下一条错误。任何阶段按 Ctrl+C 都会结束整个当前调试/服务，不再只是暂停 GDB。

```gdb
bt
info locals
print variable_name
next
continue
monitor v.info last_error
monitor leak_check full reachable any
quit
```

也可不传程序，只浏览报告：

```bash
python3 aivalgrind.py serve errors.xml --project-dir . --port 8765
```

服务仅监听 `127.0.0.1`，不直接公开到网络；调试接口校验 Origin、Host 和会话令牌，不接受网页提供的任意程序或命令。同一时间只允许一个调试会话。请通过上面的 `127.0.0.1` 地址访问，而不是 `localhost`；改端口时同步修改 SSH 转发两端和 `--port`。

## 4. 直接按错误 ID 调试

```bash
python3 aivalgrind.py debug errors.xml \
  --project-dir /home/user/project \
  --error 报告中显示的错误ID \
  --cwd /home/user/project \
  -- ./build/your_program argument1

# 指定第二个调用栈中的第一个帧；下标从 0 开始
python3 aivalgrind.py debug errors.xml --error 错误ID \
  --frame 1:0 -- ./build/your_program
```

`--cwd` 控制程序和 GDB 的工作目录，默认当前目录；`--project-dir` 只影响源码解析。程序继承启动脚本时的环境变量。

需要标准输入时，用 `--stdin-file input.txt` 重放输入。默认目标程序标准输入为 `/dev/null`，SSH 终端输入留给 GDB，不支持目标程序与 GDB 共用交互式 stdin。

如希望除了源码断点外，也在新运行中 Valgrind 检测到的每个错误处停下，加入 `--stop-on-error`：

```bash
python3 aivalgrind.py serve errors.xml -p . --stop-on-error \
  --stdin-file input.txt -- ./build/your_program
```

所有脚本选项放在 `--` 之前，目标程序及其选项放在之后。不会自动执行 XML 中记录的命令。

## 5. 自动提取错误发生时的变量值

在 `serve` 或 `debug` 后加入 `--auto-values`：

```bash
python3 aivalgrind.py serve errors.xml -p /home/user/project \
  --auto-values --capture-dir ./captures -- ./build/your_program argument1

python3 aivalgrind.py debug errors.xml -p . --error 错误ID \
  --auto-values --capture-dir ./captures -- ./build/your_program
```

此模式需要带 Python 支持的 GDB。它自动启用 Valgrind 错误暂停，跳过原来的源码断点，直接运行到实际内存错误，然后自动采集：

- 当前停止线程的调用栈、参数和局部变量（最多 16 帧，每帧 128 个变量）。
- 变量类型、可观察值，以及“已优化”或“不可读取”的原因；不调用目标程序函数。
- 自动查询 Valgrind 的初始化有效性位，标注已初始化、未初始化、部分未初始化、不可访问或无法检查；当前值为 0 不代表已经初始化。
- Valgrind 原始错误、访问地址和访问大小，以及可获得的分配/释放位置。
- 当 Valgrind 给出内存块边界时，解释块首/块尾越界或访问已释放内存；无法确定数组下标或期望值时不猜测。

同一会话内，相同错误诊断和调用路径只保存第一次现场及变量值。去重忽略十六进制地址、进程及线程编号，保留错误类型、访问大小和各调用位置；不同调用路径仍分别保存。后续重复错误不覆盖原文件、不再次打印变量。它们仍可能让 GDB 暂停，使用 `continue` 继续；此模式不会自动接管后续执行。

每次调试创建独立会话目录：

```text
captures/session-xxxxxxxx/
  capture.py          # 自动生成的 GDB 采集器
  error-0001.html     # 可下载到本机直接打开的现场报告
  error-0001.json     # 结构化变量与诊断
  error-0001.txt      # 文本现场
  error-0002.*        # 若遇到其他不同问题
  valgrind.log       # 退出调试时保存的原始日志
```

文件序号代表本次会话中首次遇到的不同问题，不是历史 XML 的错误编号。报告中的 `requested_error_id` 仅记录用户选择，**不表示本次捕获错误已与历史错误匹配**。泄漏在退出时检测，分配函数的局部变量往往已经不存在，不能据此恢复分配时的值。未初始化变量即使显示了某个数值，也不代表它是有效的业务数据。

### 未初始化成员的错误与警告

初始化检查随 `--auto-values` 自动启用，不需额外参数。结构体按调试符号中的成员布局检查，排除成员之间及末尾的填充字节，并在报告中给出如 `item.unused`、`items[2].count` 的成员路径及未初始化字节偏移。

- **错误事件**：Valgrind 已报告使用未初始化值（例如条件判断依赖未初始化数据），仍保留为错误。
- **成员警告**：额外扫描发现成员未初始化，但没有本次实际使用的直接证据，只给警告；不会因为同一结构体出现错误，就把其所有未初始化成员判成错误。
- **成员地址关联错误**：系统调用报告明确给出未初始化字节地址，且该地址落入某成员的未初始化字节范围，才将该成员标注为错误，并记录地址关联证据。

“没有使用证据”不等于“整个运行中从未使用”。普通未初始化条件判断通常不提供操作数地址，因此可能同时看到一条真实错误和若干成员警告；这比猜测某个成员就是根因更可靠。复制未初始化数据本身不一定触发 Memcheck 错误。只在现有错误暂停点进行扫描，不会为完全无报错的执行额外插入检查点。

每个变量最多检查前 256 字节，每次现场最多 256 次查询和 32768 字节。超限仅标记部分检查，绝不据此前缀判断整个变量已初始化。指针仅检查指针自身，不遍历指向的堆内存；寄存器变量、联合体活动成员、位域、浮点布局和缺失符号等会明确标为无法或部分检查。相关原始有效性位保存于 JSON 的 `initialization.raw_vbits`，成员警告/错误位于 `initialization_analysis.findings`。

网页会显示自动采集模式；点击“启动并采集变量”后，在 SSH 终端查看输出路径。现场报告不会自动合并回历史 XML 报告。每个值最多保存 4096 字符，长数组限制显示元素数量；快照可能包含程序运行数据，分享前请检查内容。

## 去重与源码匹配规则

- 按错误类型、归一化错误描述和所有调用栈（包括分配、释放、未初始化来源栈）分组；描述中的十六进制地址不参与去重。有文件和行号的帧不使用运行时绝对地址。
- 类型、源码行、调用者或来源栈不同的错误保留为不同条目；没有行号的帧保留地址，避免把同一函数内不同位置误合并。缺失符号时，不能保证跨地址变化去重。
- 优先使用 `errorcounts` 计数，缺失时一条 XML 错误记为一次。相同 `unique` 只计一次，合并不同 `unique` 的次数相加。泄漏字节/块数单独相加，不再乘出现次数；它们本身就是 XML 的汇总值。
- 源码匹配优先使用原路径，再尝试工程内路径后缀，最后匹配工程内唯一的同名文件。只读取工程目录内的文件；同名文件无法确定、文件缺失、行号越界时会提示。文本按 UTF-8 读取，非法字符替换显示。
- 错误 ID 由去重特征计算，因此同一份 XML 加不加工程目录都保持一致；二进制或编译路径变化后应重新生成报告。

## 复现能力与边界

XML 是诊断记录，不是进程快照。这里的联合调试是**重新运行并自动在相应源码位置设断点**，无法保证某次历史错误一定再次发生。多线程调度、输入、环境、网络状态或程序版本变化都可能影响结果。

同一源码行可能反复执行，第一次命中不一定就是发生错误的那次。可在 GDB 中使用 `condition`、`ignore` 或 `continue`，并配合 `--stop-on-error` 让 Valgrind 在实际错误处停下；该选项也会停在其他错误上，不会假定 XML 的 `unique` 是本次运行的错误编号。

泄漏记录通常给出分配路径，自动断点会停在分配附近，而不是退出时的检测现场。缺失源码位置时可退回函数断点；完全没有符号则拒绝复用旧进程绝对地址。共享库尚未加载时允许 pending 断点；如果 GDB 提示位置无法解析，请核对二进制和源码版本，必要时手动修改断点。

当前只启动并调试所提供的主程序，不自动跟踪每个子进程，不支持直接附加已有 PID。重跑时使用 Memcheck、完整泄漏检查和来源追踪，未重放原采集时所有自定义 Valgrind 选项。

## 中断、超时与进程清理

- Ctrl+C 会退出当前入口，命令行返回 130；库调用在完成清理后向调用方抛出 `KeyboardInterrupt`。Linux 上 SIGTERM、SSH 断连通常产生的 SIGHUP 也进入同一清理流程。
- GDB、Valgrind、vgdb 使用受管理的独立进程组。正常退出、启动失败、中断或异常都执行清理：先发送 SIGTERM，最多等待 2 秒，再发送 SIGKILL，最多再等待 2 秒。清理过程中重复按 Ctrl+C 不会打断回收。
- 直接子进程通过 Popen 回收；Linux 临时启用 subreaper，接收并回收同一受管理进程组内的孤儿后代，结束后恢复原设置。不使用 `waitpid(-1)`，避免抢走其他业务子进程的退出状态。subreaper 是进程级设置，库调试应放在主线程中，避免同时运行其他进程管理器。
- GDB 等待采用短超时轮询，启动探测最多等待 15 秒。网页连接读写有 5 秒超时，响应发送不持有状态锁；退出时关闭服务端口。GDB 修改过的终端模式在退出时恢复。
- 此清理范围覆盖保持在原进程组中的后代；目标自行调用 `setsid`/`setpgid` 脱离进程组的守护进程不在范围内。外部 SIGKILL 杀死脚本无法执行 Python 清理；内核不可中断睡眠进程也无法保证立即退出，脚本会报告清理超时而不是无限等待。

## 验证

```bash
python3 -m unittest discover -s tests -v
```

覆盖去重、计数、关联栈、源码上下文和边界、HTML 转义、路径解析、HTTP 调试接口、GDB 命令构造。在安装了 `cc`、`valgrind`、`vgdb`、`gdb` 的 Linux 上，还会自动运行真实集成测试：编译示例、采集 XML、连接 GDB、命中源码断点并继续到 Valgrind 报错。

仓库附带 Linux CI 配置。本次开发环境为 Windows，因此本地运行时真实 Linux 集成用例会跳过，需在 Linux 上执行上述命令完成验证。

实现参考：[Valgrind GDB 集成说明](https://valgrind.org/docs/manual/manual-core-adv.html)、[GDB 显式位置断点](https://sourceware.org/gdb/current/onlinedocs/gdb.html/Explicit-Locations.html)。
