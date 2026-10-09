# Product
<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
用户指定 Python 脚本与导出 HTML。实现采用 Python 标准库和离线内嵌 HTML/CSS/JavaScript。

## Users
通过 SSH 使用远程 Linux 的开发者；用户会自行把脚本复制到 Linux。

## Product Purpose
将已生成的 Valgrind XML 转为去重、按类型导航的报告，并选择错误进行 Valgrind/GDB 联合调试。

## Capabilities and Constraints
可选工程目录；展示对应错误行上下各十行。交互式 GDB 运行于 SSH 终端，网页通过端口转发访问。XML 不包含可恢复的完整进程状态。
