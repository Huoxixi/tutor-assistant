# Windows 运行包

从 [GitHub Releases](https://github.com/Huoxixi/tutor-assistant/releases/latest) 下载 `TutorAssistant-v0.2.0-windows-x64.zip`，完整解压，双击 `TutorAssistant.exe`。不要只下载 GitHub 自动生成的 Source code 源码压缩包。

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
