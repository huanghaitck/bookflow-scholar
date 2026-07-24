# Bookflow Scholar 社交媒体素材包

配套图片：

1. `docs/assets/social/01-bring-your-own-api.png`
2. `docs/assets/social/02-context-and-page-markers.png`
3. `docs/assets/social/03-history-and-open-development.png`

## 中文长文案

我做了一个面向论文、书籍和专著的桌面翻译与重建工具：**Bookflow Scholar**。

它解决的不只是“把文字翻译成另一种语言”，而是 PDF 翻译里经常被忽略的另一半问题——结构。

很多现有流程会逐页抽取和翻译，于是跨页句子被切断，页眉页脚混进正文，脚注和尾注失去位置，地图、照片和图注被拆散，最后连原版第几页都无法对应。Bookflow Scholar 的处理顺序是：

**跨页恢复完整逻辑单元 → 使用你自己的文本/视觉 API 整体翻译 → 按真实页界回插 `【原页码】` → 重建可阅读的版面。**

你可以自己选择 Provider、模型和预算。复杂论文、历史书和扫描材料可使用多模态模型辅助识别版面；页眉、页脚、脚注和尾注会独立翻译并回到正确位置。具有版本学或史料价值的书名页、版权页、地图、照片和图版可以保留，`【245】` 这样的原页码标记则让不同版本之间的查证、引用与课堂讨论继续成立。

当前 RC 支持简体中文、英语、法语、德语、日语和西班牙语，已经覆盖六种语言两两双向的 30 个有向组合，并可生成原版、目标语言版和双语版。

项目公开源码与构建材料，欢迎研究者、译者、档案工作者、出版从业者和开发者试用、反馈和提交贡献。当前 RC 的正式开源许可仍在 1.0 前确定中。

项目与下载：
https://github.com/huanghaitck/bookflow-scholar

如果你遇到有代表性的复杂 PDF，欢迎告诉我：哪种版面最容易让翻译工具失效？

## English long post

I built **Bookflow Scholar**, a desktop translation and reconstruction tool for scholarly PDFs, books, and monographs.

It tackles more than language replacement. Many PDF translation pipelines flatten a publication into page-sized text chunks. Cross-page sentences break, headers and footnotes leak into body text, figures lose their captions, and the translated edition can no longer be cited against the original.

Bookflow Scholar follows a different sequence:

**Recover cross-page logical units → translate with your own text/vision APIs → reinsert `【original page】` at the true boundary → reconstruct a readable edition.**

You choose the providers, models, and budget. Multimodal analysis can help with complex papers, historical books, scans, maps, and plates. Headers, footers, footnotes, and endnotes are translated separately and rebuilt in their proper locations. Bibliographically meaningful title/copyright pages and historical images can be preserved, while markers such as `【245】` keep verification and citation possible across editions.

The current release candidate supports Simplified Chinese, English, French, German, Japanese, and Spanish, with all 30 directed language pairs exercised. It produces source, target-language, and bilingual editions.

The source and build materials are public, and reproducible feedback and contributions are welcome from researchers, translators, archivists, publishers, and developers. The formal open-source license for 1.0 is still being finalized.

Project and download:
https://github.com/huanghaitck/bookflow-scholar

What kind of PDF layout has been hardest for your translation workflow?

## 中文短文案

翻译一本书，不该把它压成一串失去位置的文本。

Bookflow Scholar 使用你自己的文本/视觉 API，先恢复跨页语境，再整体翻译，并把 `【原页码】` 回插到真实页界；页眉、脚注、尾注、地图、照片和图注各归其位。支持中英法德日西六语言，输出原版、目标语言版和双语版。

源码与构建材料公开，欢迎反馈与贡献：
https://github.com/huanghaitck/bookflow-scholar

## 推荐发布顺序

1. 首图使用“自有 API”卡片，明确工具是什么。
2. 第二张使用“跨页语境与原页码”卡片，解释核心技术差异。
3. 第三张使用“史料保留与开放协作”卡片，邀请目标用户参与反馈。
4. 正文首段优先使用中文，第二段附英文；技术社区可反过来。
5. 不上传含 API Key、真实私人文档、未脱敏日志或用户项目路径的截图。
