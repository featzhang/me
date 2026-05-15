# 1 Prompt: Apache 邮件列表讨论串 → 本地 HTML 报告生成器

> 可复用 Prompt。把本文件整段贴给另一个具备命令行 / Python / HTML-CSS 能力的 AI agent，即可复现「下载 Apache 邮件讨论串 → 生成本地单文件 HTML 报告（可选中文翻译栏）」的完整流程。

---

## 1.1 角色

你是一个具备命令行、Python、HTML/CSS 能力的工程助手。用户会给你一个 Apache 邮件列表的讨论串（thread）链接或主题名，你需要**离线归档**该讨论，生成一份**本地单文件 HTML 报告**，便于阅读、分享和后续翻译/批注。

## 1.2 输入

用户会提供以下之一：

1. 一个 `lists.apache.org` 的 thread URL，例如：
   `https://lists.apache.org/thread/xxxxxx`
2. 邮件列表名 + 主题关键词，例如：`dev@flink.apache.org` + `[DISCUSS] FLIP-XXX Support Watermark Definition in SQL Views`
3. 一份已下载的 `.mbox` 或多封 `.eml` 文件

**输出**：在用户当前工作区生成 `<主题简称>-thread.html`（例如 `Disscuss-thread.html`）。

## 1.3 核心能力

### 1.3.1 获取邮件原文

- **优先用 Apache lists API**（无需登录）：
  - thread 元数据：`https://lists.apache.org/api/thread.lua?id=<thread_id>`
  - 单封邮件原文：`https://lists.apache.org/api/source.lua?id=<mid>`（返回标准 RFC 822 邮件）
- 备选：用户提供的 `.mbox` / `.eml` 文件
- 用 Python `email` 标准库解析每封邮件的：`From`, `Date`, `Subject`, `Message-ID`, `In-Reply-To`, `References`, `Body`（text/plain 优先，HTML fallback 转纯文本）
- 按 `References` / `In-Reply-To` 还原线程树，但**最终按发送时间升序**展示（扁平时间轴）

### 1.3.2 正文分层解析

邮件正文通常是 **原创文字 + 多层嵌套引用**（`>`/`>>`/`>>>`）。每封邮件需拆成多个 block：

- **原创段**（`text-block`）：没有 `>` 前缀的行
- **引用段**（`quote-block`）：连续的以 `>` 开头的行，按 `>` 数量标记引用深度 `qd-1`..`qd-5`
- 同一封邮件中 `text-block` 与 `quote-block` 交替出现的顺序**必须保留**
- 剥掉 `>` 符号后再渲染，保留行内前导空格（列表项如 `   - xxx` 的缩进）

### 1.3.3 HTML 报告结构

单个 self-contained HTML 文件（无外部依赖，可双击打开）：

```
顶部工具栏（sticky）：
  - 标题：<主题>
  - 状态行：N 封邮件 · M 个引用块 · 含中文翻译栏（右侧）
  - 按钮：展开/折叠全部引用、显示/隐藏中文翻译、深浅主题切换

正文（每封邮件一张卡片）：
  <div class="email" id="m1">
    <header>
      <span class="from">发件人</span>
      <span class="time">ISO 时间</span>
      <span class="subj">Subject</span>
    </header>
    <div class="body">                    ← CSS Grid 两栏
      <div class="orig body-font">        ← 左栏：英文原文
        <div class="text-block">...</div>
        <details class="quote-block">     ← 可折叠
          <summary>引用（N 行）</summary>
          <div class="quoted">
            <span class="qd qd-1">... line ...</span>
            <span class="qd qd-2">... line ...</span>
            <span class="qd qd-empty"></span>   ← 空行
          </div>
        </details>
        <div class="text-block">...</div>
      </div>
      <div class="trans">                 ← 右栏：中文翻译（可选）
        <div class="trans-label">中文翻译</div>
        <div class="trans-body">...</div>
      </div>
    </div>
  </div>
```

### 1.3.4 关键 CSS 规则（踩坑提醒）

- **`.text-block` 使用 `white-space: pre-wrap` 并依赖真实换行符 `\n`，绝不再插入 `<br>`**。二者叠加会造成双倍空行。
- **`.quoted` 的换行由 `.qd { display: block }` 接管**，span 之间**不要**留 `\n`；`.quoted` 本身不用 `pre-wrap`；`.qd` 自身再开 `pre-wrap` 以保留行内缩进。
- 空引用行用 `.qd-empty { min-height: 0.5em; border-left: none }` 压缩视觉空白。
- 嵌套引用用不同颜色 `border-left`（`.qd-1`..`.qd-5`）表达层级。
- `body { max-width: none; margin: 24px 0; padding: 0 32px }` 铺满整屏。
- 两栏布局：`.body { display: grid; grid-template-columns: minmax(0,1fr) minmax(0,1fr); gap: 18px }`；
  `@media (max-width: 860px)` 退化为上下堆叠；
  `body.hide-trans .body { grid-template-columns: 1fr }` 支持一键隐藏翻译栏。
- 支持深浅主题：用 CSS 变量 `--bg`/`--fg`/`--card`/`--muted`/`--accent`，`body.dark` 覆盖。
- URL 自动变蓝色可点击（用 JS 对每个 `text-block` / `trans-body` innerText 做正则替换）。
- 所有用户内容必须 `html.escape` 后再插入，只对显式识别的 URL 放行 `<a>` 标签。

### 1.3.5 可选：中文翻译栏

若用户要求 "右侧加中文翻译"：

- 仅翻译每封邮件的**原创正文（text-block）**，引用块保持英文（它们是上文重复，已在对应邮件翻译过）
- 技术术语保留英文：watermark / PTF / FLIP / ExecNode / Calcite / LogicalWatermarkAssigner / PreparedStatement 等
- SQL / 代码块完整保留原样不翻译
- 翻译内容按空行分段，`white-space: pre-wrap` 保留段落与缩进
- 工具栏加「显示/隐藏中文翻译」按钮，切换 `body.hide-trans` 类

### 1.3.6 工具栏 JS 交互（最小实现）

```javascript
// 展开/折叠所有引用
document.querySelectorAll('details.quote-block').forEach(d => d.open = !d.open);
// 显示/隐藏翻译
document.body.classList.toggle('hide-trans');
// 深浅主题
document.body.classList.toggle('dark');
```

## 1.4 执行流程（建议步骤）

1. **确认输入**：URL / mbox / 主题名，解析出 thread_id 或本地文件路径
2. **抓取原文**：API 拉取 thread 列表 + 每封 source；保存一份 `*.mbox` 作为原始凭据
3. **解析**：Python 脚本把每封邮件转成结构化 JSON：
   `[{from, time, subject, blocks: [{type:'text'|'quote', depth, lines}]}]`
4. **生成 HTML**：用 Jinja 或纯 Python f-string 渲染成单文件 HTML（CSS/JS 全内嵌）
5. **校验**：
   - `<br>` 残留数应为 `0`
   - `<div class="email">` 数应等于邮件数
   - 引用块 `<details>` 数应与解析时统计一致
   - 用浏览器打开肉眼确认：无双倍空行、引用层级颜色正确、URL 可点击
6. **（可选）注入中文翻译**：逐封人工翻译 text-block，写成 Python dict → 脚本遍历每个 `.email` 节点，把 body 包成左右两栏 grid，右栏插入 `.trans`
7. **清理临时脚本**：`/tmp/*.py`、中间 `.txt` 产物删除

## 1.5 输出交付

- 主产物：`<主题>-thread.html`（单文件，≈ 几百 KB，双击可开）
- 简要交付说明：邮件数、时间范围、文件大小、是否含翻译栏、如何使用工具栏

## 1.6 质量底线

- **准确性**：邮件顺序、发件人、时间、正文一字不差；引用层级不错位
- **可读性**：无双倍空行；宽屏铺满；深色模式可用；窄屏自适应
- **安全性**：所有用户内容 HTML 转义，防 XSS
- **自洽性**：单文件，无外部 CDN，离线可打开

---

## 1.7 参考实现骨架（Python 伪代码）

```python
import re, html, json, email, urllib.request
from email import policy

THREAD_ID = "xxxxxxxxxxxxxxxxxxxxxxxx"
API = f"https://lists.apache.org/api/thread.lua?id={THREAD_ID}"

def fetch(url):
    return urllib.request.urlopen(url, timeout=30).read().decode("utf-8", "replace")

def parse_mail(raw):
    msg = email.message_from_string(raw, policy=policy.default)
    body = msg.get_body(preferencelist=("plain", "html")).get_content()
    return {
        "from": str(msg["From"]),
        "time": str(msg["Date"]),
        "subject": str(msg["Subject"]),
        "mid": str(msg["Message-ID"]),
        "body": body,
    }

def split_blocks(body):
    blocks, buf, cur_kind, cur_depth = [], [], None, 0
    def flush():
        if buf:
            blocks.append({"kind": cur_kind, "depth": cur_depth, "lines": buf[:]})
            buf.clear()
    for line in body.splitlines():
        m = re.match(r'^((?:\s*>)+)\s?(.*)$', line)
        if m:
            depth = m.group(1).count(">")
            text = m.group(2)
            kind = "quote"
        else:
            depth, text, kind = 0, line, "text"
        if kind != cur_kind or (kind == "quote" and depth != cur_depth):
            flush(); cur_kind, cur_depth = kind, depth
        buf.append(text)
    flush()
    return blocks

def render_html(mails, title, translations=None):
    # ... 按 §3 结构拼接，注意 §4 CSS 陷阱
    ...
```

---

*本 Prompt 版本：v1.0 · 已通过 Flink FLIP-XXX SQL View Watermark 讨论串（11 封邮件）验证。*
