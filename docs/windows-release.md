# Windows 运行包

下载 [v0.2.3 Windows x64 运行包](https://github.com/Huoxixi/tutor-assistant/releases/download/v0.2.3/TutorAssistant-v0.2.3-windows-x64.zip)，完整解压后双击 `TutorAssistant.exe`。不要只下载 GitHub 自动生成的 Source code 源码压缩包。所有已发布版本见 [GitHub Releases](https://github.com/Huoxixi/tutor-assistant/releases)。

运行包内置 Python、网页资源、地区数据和微信读取依赖，适用于 Windows 10/11 x64。启动后自动打开浏览器；关闭启动窗口退出，关闭浏览器标签仅关闭页面。重复双击会打开现有实例；本地端口被其他软件占用时会选择备用端口。

数据保存于 `%LOCALAPPDATA%\TutorAssistant\data`，启动窗口可以直接打开。源码版原来的 `data/` 不会被自动读取或迁移。升级保留这个用户数据目录即可；跨用户迁移时需重新设置加密密钥。运行包不含作者的任何运行数据。

仍需用户登录自己的电脑版微信并明确授权读取；可选通勤需要用户自己的高德 Web 服务 Key 和独立授权。本地筛选始终不调用 AI。尚未为发行包购买数字签名，其他微信版本与真实路线需使用者自行确认，不能把打包自检当作真实微信采集验收。

## 从源码构建

在 Windows x64 安装 Python 3.13 x64，然后在项目目录执行：

```powershell
py -3.13 -m venv .build-venv
.\.build-venv\Scripts\python.exe -m pip install -r requirements-lock.txt -r requirements-build.txt
.\.build-venv\Scripts\python.exe tools/build_windows.py
```

构建使用 [PyInstaller](https://pyinstaller.org/en/stable/usage.html) 文件夹模式，产物和校验和写入 `dist/`。构建日志和临时文件不进入源码仓库。`--skip-build` 只重新整理许可与压缩已有产物，适合在自检后生成最终 ZIP。

冻结程序保留 `multiprocessing.freeze_support()`，确保微信读取子进程不会重复启动主界面，见 [PyInstaller 多进程说明](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#multi-processing)。

打包自检需传入一个尚不存在的目录：

```powershell
.\dist\TutorAssistant\TutorAssistant.exe --self-test D:\package-check-new
```

检查结束后查看该目录中的 `result.json`。自检启动隔离的空数据库，验证 Tk、子进程、微信模块导入、DPAPI、HTTP 与资源，无需连接或采集真实微信，也不向高德发请求。它不能证明所有目标机器的微信 UI 兼容性。

## 第三方分发

`pywechat127==1.9.8` 的未修改 `pyweixin` 模块以 `.py` 文件放在 `_internal/pyweixin/`，不藏入 PYZ，可以替换为兼容修改版；构建脚本收集其他依赖及动态库。包内附实际版本清单、可取得的许可文件、Python 许可、LGPL/GPL 文本以及校验过的固定版本 PyPI 源码归档。相同源码归档也作为 Release 附件单独提供；本项目的源码和构建脚本随对应 Git 标签发布。各组件的许可分别适用，详见 `THIRD_PARTY_NOTICES.md`。

## v0.2.1 微信兼容性修复

每次用户授权后的群列表或消息读取，都会在读取子进程内检查界面访问。已可访问则直接读取；否则仅对已验证的微信 4.1.15.13 x64 和固定文件 SHA-256 初始化运行时界面访问标志，回读并确认主窗口控件恢复。失败时回滚；版本、文件或运行代码不一致时停止并给出诊断。微信重启后下次读取会重新检测，不依赖调试工具留下的状态。程序启动、刷新、同意授权和本地筛选不会触发初始化。

真实读取验收与上述打包自检分别进行。下列命令明确发起一次真实读取，群名由使用者填写，只保存条数和无内容诊断，不保存群名或消息正文；它不能证明48小时覆盖：

```powershell
.\dist\TutorAssistant\TutorAssistant.exe --wechat-read-test D:\wechat-check-new --group "完整群名" --read-count 5
```

验收时先确认界面访问处于不可用状态，再执行实际 EXE；检查新目录 `result.json` 的 `ok`、`rows`，以及 `diagnostics/accessibility-activation.json` 的 `original_byte=0`、`state=activated`。随后在另一个新目录重复，确认界面已可访问时不再次写入。普通 `--self-test` 通过仍不代表真实读取通过。

## v0.2.2 按单号拆分合并消息

一条微信消息包含多个单号时，优先按单号切为多条订单。支持换行或分号、竖线连接的数字单号、带标签的字母数字单号及完整模板末尾编号；重复提及当前单号不会生成额外空订单。每单独立解析科目、课酬并保留对应原文，整条原始消息继续保存在本机。手机号、日期、课酬和门牌号不作为单号边界。没有单号的消息保留既有模板识别逻辑。

打包自检新增合成多单号消息校验，确认实际 EXE 的切分和每单科目、课酬隔离。v0.2.3 起，点击“开始筛选”会在自动备份后重新识别所选群中需要更新的旧消息。

## v0.2.3 使用流程修复

点击“开始筛选”会检测所选群的旧识别版本，在自动备份后重新识别已保存消息；无需重新读取微信。每条消息的派生结果在一个事务内替换，同单号收藏跟随新结果，无法对应的历史收藏保留原文并标为待确认。备份失败会停止旧数据更新并给出恢复提示。没有选择群来源时不会处理全库。

保存接单条件会真正触发本地筛选；切换群立即更新数量和结果，并忽略迟到的旧请求响应。请求等待设有上限，超时不会自动重发写操作。单个订单识别异常保留其原文并标待确认，不影响同一消息中的其余订单；超过100单的部分逐单保留为待确认。取消读取时为读取进程保留短暂收尾时间，以接收最后一批已读消息。任务进行中暂不允许改群来源、条件或收藏，完成或停止后恢复。已出及超过48小时的收藏显示明确状态。

本轮使用独立合成数据库验证；不会自动更新用户当前运行服务或重建主库。完整验收记录见项目`docs/user-journey-audit-20261020.md`。

发布包的 `docs/` 仅包含公开使用说明、合成数据预览及验收说明；内部样本复核文档不会进入运行包。重复整理已有产物时也会移除旧的内部文档副本。
