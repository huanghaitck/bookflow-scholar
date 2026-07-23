# Bookflow Scholar 使用手册（简体中文）

[下载](https://github.com/huanghaitck/bookflow-scholar/releases/tag/v0.8.0-rc.2) · [提交使用问题](https://github.com/huanghaitck/bookflow-scholar/issues/new?template=user_problem.yml) · [1.0 开发计划](../ROADMAP_1.0.md) · [返回首页](../../README.md)

## 它解决什么问题

Bookflow Scholar 是面向论文、书籍和专著的 Windows 桌面翻译与版面重建工具。它先恢复跨页的完整逻辑单元，再整体翻译，并在真实页界回插 `【原页码】`。Python 负责可重复的文档处理，多模态模型负责难以仅靠规则判断的版面和视觉对象。

主要改进包括：

- 正文、页眉、页脚、脚注和尾注分别切分、翻译并回到正确位置；
- 按上下文重建图片、地图、图表、题注和表格，避免无关版权图被错误放大；
- 术语按 Source、翻译单元和 occurrence/span 精确回流；
- 疑难页按对象非破坏性回流，不用整页文本覆盖正文；
- 输出原版、目标语言版和双语版，并按书名与语言动态命名；
- 支持暂停、恢复、重启续跑、取消和失败重试；
- 概览直接预览最终 PDF，并支持上一页、下一页和页码跳转；
- 支持简体中文、英语、法语、德语、日语和西班牙语，已覆盖 30 个有向语言组合。

## 从零开始使用

1. 下载并安装 `Bookflow-Scholar-0.8.0-rc.2-setup.exe`。若不想安装，可解压 portable ZIP 后运行 `Bookflow Scholar.exe`。
2. 首次打开后点击 **创建项目**。必须先有项目，PDF 才有明确的保存位置和上下文。
3. 打开项目，在设置中填写文本模型与视觉模型 Provider、模型名和 API Key，然后保存。Key 进入 Windows 凭据管理器，不写入项目文件。
4. 点击 **导入 PDF**，选择原文语言和目标语言。多 Source 项目必须明确选中当前 Source。
5. 点击 **开始**。进度页会显示当前阶段；可暂停、继续、取消，应用重启后也可续跑。
6. 处理完成后在概览中检查成品 PDF。用上一页、下一页或 `当前页/总页数` 跳转。
7. 只有检测到候选项时才会导出术语包或疑难页包。解压包，按包内同目标语言的官方提示词填写，再导入客户端。
8. 点击 **打开输出文件夹**，取得原版、目标语言版和双语版。

## 安装与安全

本候选版未签名，Windows 可能显示 SmartScreen 提示。运行前请核对 Release 页面公布的 SHA-256；不希望运行安装器时请使用 portable ZIP。[LibreOffice 官方下载](https://www.libreoffice.org/download/)是可选依赖，但建议安装，以使用已验证的 Office 文档渲染能力。

反馈时不要上传受版权或保密保护的原文，也不要粘贴 API Key、Authorization Header、完整日志中的私人路径或个人信息。请使用免费的 [GitHub 问题表单](https://github.com/huanghaitck/bookflow-scholar/issues/new?template=user_problem.yml)。
