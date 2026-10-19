# 家教拾单 · 微信家教订单本地筛选助手

在自己的 Windows 电脑上，从已登录微信的指定群聊读取近 48 小时文字消息，使用本地规则切分订单、识别字段，并按接单条件筛选。

**字段识别和筛选默认不调用 AI。** 提取微信消息与筛选已有信息是两个独立操作；修改条件后，可以直接重新筛选，无需重复读取微信。

目前以源码方式运行，尚未提供安装包、在线账号系统或手机端。每位使用者需要在自己的电脑运行程序并登录自己的微信。

## 功能

- **先授权，再读取**：每次启动默认未授权。点击“同意并授权”后才可检查和连接微信，授权不会自动提取。
- **提取与筛选分开**：“开始提取信息”只读取并保存所选群消息；“开始筛选”只处理本机已有信息。
- **本地解析**：按单号和消息模板切单，识别科目、年级、地点、课酬、课时、教员要求等字段，保留原文依据。
- **条件筛选**：支持性别、院校层次，以及薪资、科目、年级、多个教学地区。明确满足全部适用硬条件才算通过，缺失或不确定的要求保留为“待确认”。
- **按单号去重**：相同单号合并显示，保留全部原消息来源；无单号记录不强行合并，最新版本冲突时待确认。
- **原文与收藏**：点击详情直接查看这一单的原消息，支持本机长期收藏。
- **进度与停止**：提取、筛选显示运行状态；可停止任务，保留已保存信息及已完成结果。
- **可选 AI 核验**：默认关闭。手动启用并配置模型后，可核验未确定条件；地点搜索需要受支持的服务。AI 不负责基础字段识别，也不会因微信授权而自动启用。

![授权界面示意，全部使用合成数据](docs/images/authorization-preview.png)

*上图使用合成群名和计数，不包含真实聊天记录。*

## 运行环境

| 项目 | 要求或验证范围 |
| --- | --- |
| 操作系统 | Windows，微信读取依赖 Windows UI Automation |
| Python | 3.11 及以上；推荐 3.13，本机验证版本为 3.13.14 |
| 微信 | 已登录的电脑版微信；本机曾验证 4.1.15.13，其他版本需测试 |
| 页面 | 浏览器访问 `http://127.0.0.1:8765` |
| AI / API 密钥 | 本地识别和筛选不需要，仅可选 AI 核验需要配置 |

请保持桌面解锁、微信登录，读取期间避免同时操作微信。检测到微信进程不等于已登录、历史记录可读或读取范围完整。

## 快速开始

### 1. 获取源码

```powershell
git clone https://github.com/Huoxixi/tutor-assistant.git
cd tutor-assistant
```

也可在 GitHub 选择 **Code → Download ZIP**，解压后进入项目目录。

### 2. 首次安装并启动

安装 Python 3.13 后，在项目目录打开 PowerShell：

```powershell
.\start.ps1 -InstallWeChat
```

脚本创建项目独立环境 `.venv313`，安装基础依赖和微信读取组件，再启动本地服务。首次安装依赖需要网络连接。

如果找不到 Python，请指定实际安装位置：

```powershell
.\start.ps1 -PythonPath 'C:\你的Python目录\python.exe' -InstallWeChat
```

如果 PowerShell 提示不允许运行脚本，可只对这一次启动使用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -InstallWeChat
```

安装完成后，后续双击 `start.cmd`，或运行 `.\start.ps1`。`start.cmd` 不会自动补装微信组件，首次使用请先执行带 `-InstallWeChat` 的命令。

### 3. 打开页面

浏览器访问 **http://127.0.0.1:8765/**。程序只监听本机回环地址，不发布到公网。关闭运行窗口或按 `Ctrl+C` 停止服务，已有数据保留。

### 手动安装

```powershell
py -3.13 -m venv .venv313
.\.venv313\Scripts\python.exe -m pip install -r requirements-wechat.txt
.\.venv313\Scripts\python.exe run.py
```

仅开发本地解析和筛选时，可安装 `requirements.txt`，无需安装或启动微信。`requirements-lock.txt` 是已验证 Windows 环境的版本快照，不是所有平台的通用锁文件。

## 使用方法

1. **登录微信**：在电脑版微信中登录自己的账号，不在本程序填写微信密码。
2. **确认授权**：阅读页面上方的读取范围，点击“同意并授权”。授权仅本次运行有效，刷新页面保留，重启程序后重新确认。
3. **设置条件**：进入“需求设置”，填写性别、院校层次、薪资、科目、年级和教学地区。保存后按新条件重新计算已有订单，不自动提取。
4. **选择群聊**：进入“微信群”，输入完整群名并添加，勾选来源。每次最多 10 个群，选择自动保存。
5. **提取信息**：点击“开始提取信息”，保存所选群近 48 小时内本机可访问的文字消息。完成或停止后查看读取状态。
6. **开始筛选**：点击“开始筛选”，对已保存的所选群消息切单、识别字段并筛选。已经提取过时，可直接从这一步开始。
7. **查看原文**：在“通过筛选”或“待确认”中查看详情、原消息及字段依据，按需收藏。

“撤销授权”会请求停止正在进行的微信读取，并阻止后续连接与提取。原消息、配置和收藏保留，未授权时仍可查看、筛选已有信息。

### 筛选结果

| 状态 | 含义 |
| --- | --- |
| 通过筛选 | 已识别字段明确满足全部适用硬条件 |
| 待确认 | 缺少关键字段、地点归属不确定，或有当前资料无法核实的额外要求 |
| 排除 | 至少一项硬条件明确不满足，不显示在主清单的两个标签中 |

不将缺失信息当作满足条件，不为增加通过数量放宽要求。单次课酬缺课时不当作时薪，月薪不擅自换算；原文只写城市而筛选要求具体区县时，不猜地点归属。

### 可选 AI 核验

在“模型设置”填写接口基础地址、模型名称及所需密钥，再勾选“启用 AI 核验未确定订单”并保存。基础字段始终由本地解析器识别。

开启后可能调用外部模型和搜索服务，并按服务方规则计费。程序遮盖已识别的联系方式、门牌等内容后，发送核验所需的脱敏信息；自动脱敏有边界，使用前请自行确认内容。通用模型接口不保证支持地点搜索；搜索不可用或证据不足时仍待确认。当前联网核验适配未验证所有服务商，请先少量测试。

## 开源技术与致谢

| 技术 | 本项目用途 | 许可 |
| --- | --- | --- |
| [Python](https://www.python.org/) | 本地规则、读取调度、业务逻辑 | PSF |
| [FastAPI](https://github.com/fastapi/fastapi) | 本地 HTTP API 与页面服务 | MIT |
| [Uvicorn](https://github.com/Kludex/uvicorn) | ASGI 服务进程 | BSD-3-Clause |
| [Pydantic](https://github.com/pydantic/pydantic) | 消息、条件和字段数据校验 | MIT |
| [HTTPX](https://github.com/encode/httpx) | 可选模型及搜索请求 | BSD-3-Clause |
| [SQLite](https://www.sqlite.org/copyright.html) | 本地消息、订单、收藏与配置 | Public Domain |
| [pywechat / pywechat127](https://github.com/Hello-Mr-Crab/pywechat) | Windows 微信导航适配，模块名 `pyweixin` | LGPLv3，见下文 |
| [pywinauto](https://github.com/pywinauto/pywinauto) / [pywin32](https://github.com/mhammond/pywin32) | Windows 控件操作、COM 与密钥加密支持 | BSD / PSF，见第三方说明 |
| [Vant Area Data](https://github.com/youzan/vant/tree/main/packages/vant-area-data) | 省、市、区县选择与匹配数据 | MIT |
| [pytest](https://github.com/pytest-dev/pytest) | 合成数据及隔离回归测试 | MIT |

前端使用原生 HTML、CSS 和 JavaScript，不依赖云端 CDN。Windows UI Automation 与 DPAPI 是系统能力，不是本项目的开源组件。微信客户端不随仓库分发，本项目不是微信官方产品。

微信依赖固定为 `pywechat127==1.9.8`，该发行包标注 LGPLv3；上游主分支许可文本与发行包元数据可能不同，请按所用版本核对。本项目通过正常 Python 导入使用组件，不复制或修改第三方包实现。

完整归属、已验证版本与许可链接见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。地区数据许可原文保留在 [app/assets/regions-LICENSE.txt](app/assets/regions-LICENSE.txt)。

## 数据与隐私

- 首次运行自动创建 `data/`，公开仓库不携带任何人的聊天数据库、筛选配置、收藏或密钥。
- 原消息、逐单文本、来源、订单、配置和收藏保存在 `data/tutor.sqlite3`。
- 可选模型密钥使用当前 Windows 用户的 DPAPI 加密，保存在主库旁的 `*.model-key.dpapi`，不写入 SQLite 设置或 API 响应。
- 密钥文件不能作为其他 Windows 账号的通用配置，更换电脑或账号后请重新填写密钥。
- 备份前停止程序，将整个 `data/` 复制到自己的安全位置；恢复时同样先停止程序。
- 提取时操作本机微信窗口，不自动发消息、抢单或联系发布者。
- 本地服务未实现多用户认证或远程配对，不要直接将端口暴露为公共网站。

## 常见问题与当前边界

**别人怎样使用？** 每个人在自己的 Windows 电脑运行程序、登录自己的微信。`127.0.0.1` 指访问者自己的电脑，不能用你的本地页面读取别人的微信。

**缺少读取组件？** 停止程序，运行 `.\start.ps1 -InstallWeChat`，再重新启动。只使用本地筛选不需要微信组件。

**微信已打开但读取失败？** 确认账号登录、完整群名一致、桌面解锁、控件可访问。微信版本、语言、缩放及控件可见性可能影响读取，请先用一个群测试。程序不自动修改微信进程或系统安全设置。

**“采集不完整”意味着没有符合的订单？** 不是。历史不足、时间不明、超时、控件不兼容或达到读取上限时，尚不能证明覆盖完整 48 小时。已读部分会保存，可先筛选再重试。

**支持图片、语音或合并转发？** 当前只处理可访问的文字消息，不做图片 OCR、语音或合并转发内容识别，也不提供全天后台监听。

## 开发与测试

```powershell
.\.venv313\Scripts\python.exe -m pytest -q
```

公开版本的 58 项测试使用合成消息、临时数据库和模拟微信读取，不依赖真实微信，不调用真实 AI 服务。私人复核报告、真实聊天语料、本机运行诊断及历史设计材料不包含在公开版本中。

```text
app/
  history_reader.py       微信历史读取与控件适配
  local_extraction.py    本地字段识别
  splitting.py           消息切单与单号识别
  matching.py            条件核对
  deduplication.py       同单号合并显示
  service.py             授权、任务调度和业务流程
  store.py               SQLite 持久化
  static/                本地网页界面
  assets/                地区数据与许可
tests/                   可公开的合成回归测试
tools/build_regions.py   地区源数据转换工具
run.py                   本地服务入口
start.ps1 / start.cmd    Windows 启动脚本
```

## 许可证

本项目原创代码采用 [MIT License](LICENSE)。第三方组件和数据保留各自许可证，本项目 MIT 许可不替代 LGPL、BSD、PSF 等第三方条款。
